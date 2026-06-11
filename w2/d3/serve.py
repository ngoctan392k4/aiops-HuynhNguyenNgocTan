from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import networkx as nx
from cachetools import TTLCache
from fastapi import FastAPI, HTTPException, Request
from prometheus_client import Counter, Histogram, make_asgi_app
from pydantic import BaseModel, Field


# Config

APP_VERSION = "1.0.0"

GAP_SEC = int(os.getenv("GAP_SEC", 120))
MAX_HOP = int(os.getenv("MAX_HOP", 1))
USE_LLM = os.getenv("USE_LLM", "false").lower() == "true"

CURRENT_DIR = Path(__file__).resolve().parent


if (CURRENT_DIR / "dataset").exists():
    DATASET_PATH = CURRENT_DIR / "dataset"
else:
    DATASET_PATH = CURRENT_DIR.parent / "d2" / "dataset"

SERVICES_PATH = DATASET_PATH / "services.json"
HISTORY_PATH = DATASET_PATH / "incidents_history.json"

GRAPH_LOADED_AT = datetime.now(timezone.utc).isoformat()
GRAPH_SOURCE = str(SERVICES_PATH)


# Logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("rca-serving")


# Metrics

REQUEST_COUNT = Counter(
    "incident_requests_total",
    "Total incident requests",
    ["status"],
)

REQUEST_LATENCY = Histogram(
    "incident_latency_seconds",
    "Incident endpoint latency in seconds",
)

CLUSTER_COUNT = Histogram(
    "clusters_per_request",
    "Number of clusters returned per request",
)

PIPELINE_FAILURES = Counter(
    "pipeline_failures_total",
    "Pipeline failures",
    ["stage"],
)


# FastAPI app

app = FastAPI(
    title="Incident Pipeline",
    version=APP_VERSION,
    description="Correlate alerts -> RCA -> recommended actions from history",
)

app.mount("/metrics", make_asgi_app())


# Schemas

class Alert(BaseModel):
    id: str
    ts: str
    service: str
    metric: str
    severity: str
    value: float
    threshold: float
    labels: Optional[dict[str, Any]] = Field(default_factory=dict)


class IncidentRequest(BaseModel):
    alerts: list[Alert]


class Cluster(BaseModel):
    cluster_id: str
    alert_count: int
    services: list[str]
    time_range: list[str]
    max_severity: str
    fingerprints: list[str]


class RCAResult(BaseModel):
    cluster_id: str
    graph_top3: list[tuple[str, float]]
    root_cause: str
    class_: str = Field(alias="class")
    confidence: float
    actions: list[str]
    reasoning: str
    similar_incidents: list[str]
    method: str

    model_config = {
        "populate_by_name": True
    }


class IncidentResponse(BaseModel):
    input_alerts: int
    output_clusters: int
    reduction_ratio: float
    clusters: list[Cluster]
    clusters_analyzed: int
    results: list[RCAResult]


# Utility

_llm_cache = TTLCache(maxsize=1000, ttl=3600)


def load_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_history(path: Path = HISTORY_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        logger.warning("incidents_history.json not found at %s. Using empty history.", path)
        return []

    raw = load_json(path)

    if isinstance(raw, dict):
        return raw.get("incidents", [])

    if isinstance(raw, list):
        return raw

    return []


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def fingerprint(alert: dict) -> str:
    return f"{alert['service']}|{alert['metric']}|{alert['severity']}"


# Correlation

class Deduper:
    def __init__(self):
        self.store: dict[str, dict] = {}  # fingerprint → cluster info
    
    def push(self, alert: dict) -> str:
        """
        Add alert vào store. Return cluster_id (fingerprint đóng vai trò cluster_id ở layer này).
        """
        fp = fingerprint(alert)
        ts = datetime.fromisoformat(alert['ts'].replace('Z', '+00:00'))
        
        if fp not in self.store:
            self.store[fp] = {
                'cluster_id': fp,
                'count': 1,
                'first_seen': ts,
                'last_seen': ts,
                'alerts': [alert['id']],
                'max_value': alert['value'],
            }
        else:
            c = self.store[fp]
            c['count'] += 1
            c['last_seen'] = ts
            c['alerts'].append(alert['id'])
            c['max_value'] = max(c['max_value'], alert['value'])
        
        return fp
    
    def clusters(self) -> list[dict]:
        return list(self.store.values())


def session_groups(alerts: list[dict], gap_sec: int = 120) -> list[list[dict]]:
    """
    Mỗi group là 1 'session' alert. Session kết thúc khi không alert nào trong gap_sec giây.
    
    Vì sao session tốt hơn tumbling cho incident:
    - Incident burst: 30 alert trong 90 giây → 1 session tự nhiên
    - Tumbling 5min: nếu incident span 4 phút 30 - 5 phút 30 → bị cắt thành 2 window
    - Session tự adapt kích thước theo burst pattern
    """
    if not alerts:
        return []
    
    sorted_alerts = sorted(alerts, key=lambda a: a['ts'])
    groups = [[sorted_alerts[0]]]
    
    for alert in sorted_alerts[1:]:
        ts = datetime.fromisoformat(alert['ts'].replace('Z', '+00:00'))
        last_ts = datetime.fromisoformat(groups[-1][-1]['ts'].replace('Z', '+00:00'))
        
        if (ts - last_ts).total_seconds() <= gap_sec:
            groups[-1].append(alert)
        else:
            groups.append([alert])
    
    return groups



def build_graph(services_json_path: str) -> nx.DiGraph:

    g = nx.DiGraph()
    data = json.loads(open(services_json_path).read())
    
    # Add service nodes
    for svc in data['services']:
        g.add_node(svc['name'], **{k: v for k, v in svc.items() if k != 'name'})
    
    # Add store nodes
    for store in data['stores']:
        g.add_node(store['name'], **{k: v for k, v in store.items() if k != 'name'})
    
    # Add edges
    for edge in data['edges']:
        g.add_edge(edge['from'], edge['to'], type=edge['type'])
    
    return g



def topology_group(alerts: list[dict], graph: nx.DiGraph, max_hop: int = 1) -> list[list[dict]]:
    """
    Group alerts nếu service của chúng cách nhau ≤ max_hop trên service graph.
    
    Lưu ý: dùng undirected version của graph cho khoảng cách — vì cascade
    có thể đi cả 2 chiều (upstream effect, downstream propagation tùy case).
    """
    if not alerts:
        return []
    
    undirected = graph.to_undirected()
    
    # Build mapping service → alerts ở service đó
    by_service = defaultdict(list)
    for a in alerts:
        by_service[a['service']].append(a)
    
    services_with_alerts = list(by_service.keys())
    
    # Union-Find
    parent = {s: s for s in services_with_alerts}
    
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    
    def union(x, y):
        parent[find(x)] = find(y)
    
    # Two services cùng group nếu khoảng cách ≤ max_hop trên graph
    for i, s1 in enumerate(services_with_alerts):
        for s2 in services_with_alerts[i+1:]:
            try:
                dist = nx.shortest_path_length(undirected, s1, s2)
                if dist <= max_hop:
                    union(s1, s2)
            except nx.NetworkXNoPath:
                continue  # 2 service không connected → không group
    
    # Collect groups
    groups_dict = defaultdict(list)
    for s in services_with_alerts:
        groups_dict[find(s)].extend(by_service[s])
    
    return list(groups_dict.values())



def correlate(alerts: list[dict], graph: nx.DiGraph, gap_sec: int = 49, max_hop: int = 1):
    """
    Pipeline:
      1. Sort alert by timestamp
      2. Cho mỗi session (time-window), apply topology grouping
      3. Output clusters
    """
    sessions = session_groups(alerts, gap_sec=gap_sec)
    
    all_clusters = []
    for session_idx, session_alerts in enumerate(sessions):
        # Trong mỗi session, topology-group
        topo_groups = topology_group(session_alerts, graph, max_hop=max_hop)
        for group_idx, group in enumerate(topo_groups):
            all_clusters.append({
                'cluster_id': f'c-{session_idx:03d}-{group_idx:03d}',
                'alert_count': len(group),
                'services': sorted(set(a['service'] for a in group)),
                # 'alert_ids': [a['id'] for a in group],
                'time_range': [min(a['ts'] for a in group), max(a['ts'] for a in group)],
                'max_severity': max(a['severity'] for a in group),
                "fingerprints": sorted(set(fingerprint(a) for a in group))
            })
    
    return all_clusters



# RCA

CLASS_ENUM = {
    "connection_pool_exhaustion",
    "slow_query",
    "memory_leak",
    "rebalance_storm",
    "deadlock",
    "network_partition",
    "bad_deploy",
    "config_push",
    "tls_expiry",
    "ddos",
    "other"
}


def earliest_time_by_service(alerts_raw):

    result = {}

    for alert in alerts_raw:
        service = alert.get("service")
        timestamp = parse_ts(alert.get("ts"))

        if not service or not timestamp:
            continue

        if service not in result or timestamp < result[service]:
            result[service] = timestamp

    return result


def temporal_scores(services):

    # Tính điểm cho các service theo thời điểm sớm nhất của alert 
    # Service alert sớm nhất được 1.0.
    # Service alert muộn nhất được 0.0.
    # Nếu thiếu dữ liệu thời gian thì cho 0.5.

    times = {
        service: EARLIEST.get(service)
        for service in services
        if EARLIEST.get(service) is not None
    }

    if len(times) <= 1:
        return {service: 0.5 for service in services}

    min_t = min(times.values())
    max_t = max(times.values())

    span = max((max_t - min_t).total_seconds(), 1.0)

    scores = {}

    for service in services:
        t = times.get(service)

        if t is None:
            scores[service] = 0.5
        else:
            scores[service] = 1.0 - ((t - min_t).total_seconds() / span)
            # print(service, scores[service])

    return scores


def graph_temporal_top_k(cluster, G, top_k=3, graph_weight=0.6, temporal_weight=0.4):

    services = [service for service in cluster["services"] if service]

    if not services:
        return []

    
    # Chỉ lấy subgraph gồm các service đang alert trong cluster
    subgraph = G.subgraph(services).copy()

    # PageRank trên graph A -> B.
    # Nếu checkout-svc -> payment-svc, payment-svc sẽ nhận edge vào nên có PageRank cao hơn.
    if subgraph.number_of_edges() > 0:
        pagerank_scores = nx.pagerank(subgraph, alpha=0.85)
    else:
        pagerank_scores = {
            service: 1 / len(services)
            for service in services
        }

    max_pr = max(pagerank_scores.values()) if pagerank_scores else 1.0

    pagerank_norm = {
        service: pagerank_scores.get(service, 0.0) / max_pr
        for service in services
    }

    time_scores = temporal_scores(services)

    scored = []

    for service in services:
        # terminal node = service không gọi tiếp service nào khác trong cluster
        # Ví dụ: edge-lb -> checkout-svc -> payment-svc
        # payment-svc có out_degree = 0 nên được bonus nhẹ.
        out_degree = subgraph.out_degree(service) if service in subgraph else 0
        terminal_bonus = 0.05 if out_degree == 0 else 0.0

        score = (
            graph_weight * pagerank_norm.get(service, 0.0)
            + temporal_weight * time_scores.get(service, 0.5)
            + terminal_bonus
        )

        score = min(score, 1.0)
        scored.append((service, round(float(score), 4)))

    scored.sort(key=lambda x: x[1], reverse=True)
    
    return scored[:top_k]



def tokenize_text(text):
    return set(re.findall(r"[a-zA-Z0-9_\-]+", str(text).lower()))


def incident_id(incident):
    return str(incident.get("id", "UNKNOWN"))


def incident_services(incident):
    services = incident.get("services_involved", [])

    if isinstance(services, str):
        services = [services]

    return sorted(set(services))


def incident_root_cause(incident):
    return incident.get("root_cause_service")


def incident_class(incident):
    root_class = incident.get("root_cause_class", "other")

    if root_class not in CLASS_ENUM:
        return "other"

    return root_class


def incident_actions(incident):
    actions = incident.get("remediation", [])

    if isinstance(actions, str):
        actions = [actions]

    if not actions:
        return ["Investigate manually"]

    return actions


def severity_norm(value):
    return str(value or "").strip().lower()


def keyword_similarity(cluster, history_incident):

    # Retrieval indicator: keyword_similarity + kNN-style top_k.
    # oot cause cũ có nằm trong cluster hiện tại thì + 0.4
    # service overlap giữa cluster và incident cũ thì +0.2 cho mỗi lần overlap
    # severity có giống nhau thì + 0.2


    cluster_services = set(cluster["services"])
    history_services = set(incident_services(history_incident))
    history_root = incident_root_cause(history_incident)

    score = 0.0

    # Nếu root cause của incident cũ nằm trong cluster hiện tại
    if history_root in cluster_services:
        score += 0.4

    # Service overlap
    overlap = len(cluster_services & history_services)
    score += min(0.4, 0.2 * overlap)

    # Severity giống nhau
    cluster_severity = severity_norm(cluster.get("severity"))
    history_severity = severity_norm(history_incident.get("severity"))

    if cluster_severity and cluster_severity == history_severity:
        score += 0.2


    return round(min(score, 1.0), 4)


def retrieve_similar(cluster, top_k=3, min_score=0.2):
    
    # Tìm top-K incident history giống cluster hiện tại nhất.

    scored = []

    for history_incident in HISTORY:
        similarity_score = keyword_similarity(cluster, history_incident)

        if similarity_score >= min_score:
            scored.append((history_incident, similarity_score))

    scored.sort(key=lambda x: x[1], reverse=True)

    return scored[:top_k]


def classify_from_top1_similar(cluster):

    # Classifier kNN-style:
    # Retrieve top-3 similar incidents
    # Lấy top-1 incident giống nhất
    # Copy class + actions từ top-1
    # Nếu không tìm được incident tương tự thì fallback


    similar_incidents = retrieve_similar(cluster, top_k=3)

    if not similar_incidents:
        return {
            "class": "other",
            "actions": ["Investigate manually"],
            "similar_incidents": [],
            "retrieval_score": 0.0
        }

    top1_incident, top1_score = similar_incidents[0]

    return {
        "class": incident_class(top1_incident),
        "actions": incident_actions(top1_incident),
        "similar_incidents": [
            incident_id(incident)
            for incident, score in similar_incidents
        ],
        "retrieval_score": top1_score
    }



def validate_result(item):
    required = ["cluster_id", "graph_top3", "root_cause", "class", "confidence", "actions", "reasoning", "similar_incidents", "method"]
    for k in required:
        if k not in item:
            return False, f"missing {k}"
    if not isinstance(item["graph_top3"], list) or not item["graph_top3"]:
        return False, "graph_top3 must be non-empty list"
    if item["class"] not in CLASS_ENUM:
        return False, "invalid class"
    if not isinstance(item["confidence"], (int, float)) or not (0 <= item["confidence"] <= 1):
        return False, "invalid confidence"
    if not isinstance(item["actions"], list) or not item["actions"]:
        return False, "actions must be non-empty list"
    return True, "ok"


def analyze_cluster(cluster, graph=None, history=None, earliest=None):
    """
    Analyze one cluster using D2 RCA logic.
    graph/history/earliest là optional để serve.py gọi được rõ ràng,
    nhưng vẫn giữ logic chính từ D2.
    """
    
    
    global HISTORY, EARLIEST

    
    if history is not None:
        old_history = HISTORY
        HISTORY = history
    else:
        old_history = None

    if earliest is not None:
        old_earliest = EARLIEST
        EARLIEST = earliest
    else:
        old_earliest = None

    try:
        if graph is None:
            graph = GRAPH

        graph_top3 = graph_temporal_top_k(cluster, graph, top_k=3)

        if not graph_top3:
            graph_top3 = [[cluster["services"][0] if cluster["services"] else "unknown", 0.1]]

        root_cause = graph_top3[0][0]
        base_conf = float(graph_top3[0][1])
        similar = retrieve_similar(cluster, top_k=3)

        if similar:
            top_incident, sim_score = similar[0]
            cls = incident_class(top_incident)
            actions = incident_actions(top_incident)
            similar_ids = [incident_id(h) for h, _ in similar]

            confidence = round(base_conf, 4)
            method = "graph+retrieval-knn"

            reasoning = (
                f"Graph+temporal scorer xếp {root_cause} cao nhất trong cluster {cluster['cluster_id']} "
                f"với root-cause confidence theo service graph + temporal là {base_conf:.2f}. "
                f"Retrieval tìm incident tương tự nhất là {incident_id(top_incident)} với similarity {sim_score:.2f}; "
                f"class/actions được lấy từ top-1 similar incident theo kNN-style."
            )
        else:
            cls = "other"
            actions = ["Investigate manually"]
            similar_ids = []
            confidence = round(max(0.1, min(base_conf, 0.65)), 4)
            method = "graph-only-fallback"
            reasoning = (
                f"Không tìm thấy incident lịch sử đủ tương tự cho cluster {cluster['cluster_id']}. "
                f"Fallback dùng top-1 từ graph+temporal là {root_cause}; class để other và yêu cầu điều tra thủ công."
            )

        item = {
            "cluster_id": cluster["cluster_id"],
            "graph_top3": [[s, float(score)] for s, score in graph_top3],
            "root_cause": root_cause,
            "class": cls,
            "confidence": float(confidence),
            "recommended_actions": actions,
            "reasoning": reasoning,
            "similar_incidents": similar_ids,
            "method": method,
        }

        ok, msg = validate_result(item)

        if not ok:
            item.update({
                "class": "other",
                "confidence": 0.3,
                "actions": ["Investigate manually"],
                "method": "schema-fallback",
                "reasoning": f"Schema invalid ({msg}); fallback to manual investigation."
            })

        return item

    finally:
        if old_history is not None:
            HISTORY = old_history

        if old_earliest is not None:
            EARLIEST = old_earliest

# Global

GRAPH = build_graph(SERVICES_PATH)
HISTORY = load_history(HISTORY_PATH)

EARLIEST = {}

# Pipeline 

def process_batch(alerts: list[dict[str, Any]]) -> dict[str, Any]:
    
    t0 = time.perf_counter()
    global EARLIEST
    EARLIEST = earliest_time_by_service(alerts)

    try:
        clusters = correlate(
            alerts=alerts,
            graph=GRAPH,
            gap_sec=GAP_SEC,
            max_hop=MAX_HOP,
        )
    except Exception as e:
        PIPELINE_FAILURES.labels(stage="correlate").inc()
        logger.exception("Correlation failed")
        raise RuntimeError(f"correlation failed: {e}") from e
 
    input_alerts = len(alerts)
    output_clusters = len(clusters)
    reduction_ratio = round(1 - (output_clusters / input_alerts), 4) if input_alerts else 0.0

    earliest = earliest_time_by_service(alerts)
    results = []

    for cluster in clusters:
        try:
            item = analyze_cluster(
                cluster=cluster,
                graph=GRAPH,
                history=HISTORY,
                earliest=earliest,
            )
        except Exception as e:
            PIPELINE_FAILURES.labels(stage="rca").inc()
            logger.exception("RCA failed for cluster %s", cluster.get("cluster_id"))
            raise RuntimeError(f"rca failed for {cluster.get('cluster_id')}: {e}") from e

        ok, msg = validate_result(item)
        if not ok:
            raise RuntimeError(f"Invalid RCA output for {cluster.get('cluster_id')}: {msg}")

        results.append(item)

    logger.info(
        "Processed incident input_alerts=%s output_clusters=%s clusters_analyzed=%s latency_ms=%.1f",
        input_alerts,
        output_clusters,
        len(results),
        (time.perf_counter() - t0) * 1000,
    )

    return {
        "input_alerts": input_alerts,
        "output_clusters": output_clusters,
        "reduction_ratio": reduction_ratio,
        "clusters": [
            {
                "cluster_id": c["cluster_id"],
                "alert_count": c["alert_count"],
                "services": c["services"],
                "time_range": c["time_range"],
                "max_severity": c["max_severity"],
                "fingerprints": c["fingerprints"],
            }
            for c in clusters
        ],
        "clusters_analyzed": len(results),
        "results": results,
    }


# Request body parser: JSON + JSONL

async def parse_incident_body(request: Request) -> list[dict[str, Any]]:

    raw_body = await request.body()

    if not raw_body:
        raise HTTPException(status_code=422, detail="Empty request body")

    text = raw_body.decode("utf-8").strip()

    try:
        data = json.loads(text)

        if isinstance(data, dict):
            if "alerts" not in data:
                raise HTTPException(
                    status_code=422,
                    detail="JSON object must contain 'alerts' field",
                )
            alerts_raw = data["alerts"]

        elif isinstance(data, list):
            alerts_raw = data

        else:
            raise HTTPException(
                status_code=422,
                detail="Body must be {'alerts': [...]}, JSON array, or JSONL",
            )

    except json.JSONDecodeError:
        alerts_raw = []

        for line_no, line in enumerate(text.splitlines(), start=1):
            line = line.strip()

            if not line:
                continue

            try:
                alerts_raw.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid JSONL at line {line_no}: {e.msg}",
                )

    if not isinstance(alerts_raw, list):
        raise HTTPException(status_code=422, detail="'alerts' must be a list")

    if not alerts_raw:
        raise HTTPException(status_code=400, detail="Empty alert list")

    validated_alerts = []

    for idx, item in enumerate(alerts_raw):
        try:
            validated_alerts.append(Alert.model_validate(item).model_dump())
        except Exception as e:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid alert at index {idx}: {e}",
            )

    return validated_alerts


# Middleware and endpoints

@app.middleware("http")
async def add_timing(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000

    response.headers["X-Response-Time-Ms"] = f"{duration_ms:.1f}"

    logger.info(
        "%s %s %s %.1fms",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )

    return response


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readyz() -> dict[str, Any]:
    checks = {
        "graph_loaded": GRAPH is not None and GRAPH.number_of_nodes() > 0,
        "history_loaded": HISTORY is not None and len(HISTORY) > 0,
    }

    if not all(checks.values()):
        raise HTTPException(status_code=503, detail=checks)

    return {
        "status": "ready",
        "checks": checks,
    }


@app.get("/version")
def version() -> dict[str, Any]:
    return {
        "app": APP_VERSION,
        "pipeline_config": {
            "gap_sec": GAP_SEC,
            "max_hop": MAX_HOP,
            "use_llm": USE_LLM,
            "method": "D1-correlate + D2-graph-retrieval-rca",
        },
        "graph": {
            "source": GRAPH_SOURCE,
            "loaded_at": GRAPH_LOADED_AT,
            "node_count": GRAPH.number_of_nodes() if GRAPH is not None else 0,
            "edge_count": GRAPH.number_of_edges() if GRAPH is not None else 0,
        },
        "history_count": len(HISTORY),
    }


@app.post("/incident", response_model=IncidentResponse)
async def post_incident(request: Request) -> IncidentResponse:
    alerts = await parse_incident_body(request)

    with REQUEST_LATENCY.time():
        try:
            result = process_batch(alerts)
            REQUEST_COUNT.labels(status="success").inc()
            CLUSTER_COUNT.observe(len(result["clusters"]))
            return IncidentResponse(**result)

        except HTTPException:
            REQUEST_COUNT.labels(status="error").inc()
            raise

        except Exception as e:
            REQUEST_COUNT.labels(status="error").inc()
            logger.exception("Pipeline error")
            raise HTTPException(
                status_code=500,
                detail=f"Pipeline error: {e}",
            ) from e
