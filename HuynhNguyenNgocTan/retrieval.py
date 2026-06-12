import re
import math
from collections import Counter, defaultdict

OUTCOME_WEIGHT = {"success": 1.0, "partial": 0.55, "failed": 0.15}

def normalize_text(s: str) -> str:
    s = s.lower()
    s = re.sub(r"\b[0-9a-f]{8,}\b", "<id>", s)
    s = re.sub(r"\b\d+(?:\.\d+)?(?:ms|s|mb|gb|%)?\b", "<num>", s)
    return re.sub(r"\s+", " ", s).strip()

def tokens(s: str) -> set[str]:
    stop = {"the", "a", "an", "to", "on", "in", "of", "for", "and", "or", "is", "has", "by", "with"}
    return {t for t in re.findall(r"[a-z0-9][a-z0-9_-]+", normalize_text(s)) if t not in stop and t != "num"}

def history_vector(entry: dict) -> dict:
    sigs = entry.get("log_signatures", [])
    return {
        "id": entry.get("id"),
        "root_cause_class": entry.get("root_cause_class"),
        "affected_services": entry.get("affected_services", []),
        "log_signatures": sigs,
        "log_tokens": set().union(*(tokens(s) for s in sigs)) if sigs else set(),
        "trace_signatures": entry.get("trace_signatures", []),
        "metric_signatures": entry.get("metric_signatures", []),
        "actions_taken": entry.get("actions_taken", []),
        "outcome": entry.get("outcome", "partial"),
        "mttr_minutes": entry.get("mttr_minutes", 60),
    }


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / max(1, len(a | b))

def parse_action(s: str, param_names: dict[str, list[str]]) -> dict:
    parts = s.split(":")
    name = parts[0] if parts else "page_oncall"
    return {"name": name, "params": dict(zip(param_names.get(name, []), parts[1:]))}

def metric_delta_ratio(delta: str) -> float:
    try:
        before, after = [float(x.strip()) for x in delta.replace("->", "|").split("|")]
    except Exception:
        return 0.0
    return abs(after - before) / max(abs(before), 1.0)

def similarity(query: dict, hist: dict) -> tuple[float, dict]:
    log_scores = []
    for q in query["log_templates"]:
        best = max((jaccard(tokens(q), tokens(h)) for h in hist["log_signatures"]), default=0.0)
        if best:
            log_scores.append(best)
    log_score = sum(log_scores[:5]) / max(1, min(5, len(log_scores))) if log_scores else 0.0
    token_score = jaccard(query["log_tokens"], hist["log_tokens"])

    q_edges = {(e["from"], e["to"]): e for e in query["trace_edges"]}
    edge_scores = []
    for h in hist["trace_signatures"]:
        key = (h.get("from"), h.get("to"))
        rev = (h.get("to"), h.get("from"))
        if key in q_edges or rev in q_edges:
            qe = q_edges.get(key) or q_edges.get(rev)
            er = 1.0 - min(abs(qe["error_rate"] - float(h.get("error_rate", 0))) / 0.6, 1.0)
            edge_scores.append(0.65 + 0.35 * er)
    trace_score = max(edge_scores) if edge_scores else 0.0
    service_score = jaccard(set(query["affected_services"]), set(hist["affected_services"]))

    q_metrics = {(m["service"], m["metric"]): abs(m["change"]) for m in query["metric_changes"]}
    metric_scores = []
    for m in hist["metric_signatures"]:
        key = (m.get("service"), m.get("metric"))
        if key in q_metrics:
            hd = metric_delta_ratio(m.get("delta", ""))
            metric_scores.append(1.0 - min(abs(q_metrics[key] - hd) / max(hd, 1.0), 1.0))
    metric_score = max(metric_scores) if metric_scores else 0.0

    score = 0.42 * log_score + 0.22 * token_score + 0.22 * trace_score + 0.10 * service_score + 0.04 * metric_score
    detail = {
        "log": round(log_score, 3),
        "tokens": round(token_score, 3),
        "trace": round(trace_score, 3),
        "services": round(service_score, 3),
        "metrics": round(metric_score, 3),
    }
    return score, detail

def retrieve_and_vote(query: dict, history: list[dict], actions_catalog: list[dict], top_k: int = 5) -> dict:
    param_names = {a["name"]: a.get("params", []) for a in actions_catalog}
    scored = []
    for entry in history:
        hv = history_vector(entry)
        score, detail = similarity(query, hv)
        scored.append((score, hv, detail))
    scored.sort(key=lambda x: x[0], reverse=True)

    votes = defaultdict(float)
    vote_evidence = defaultdict(list)
    for rank, (score, hv, detail) in enumerate(scored[:top_k], 1):
        weight_base = score * OUTCOME_WEIGHT.get(hv["outcome"], 0.5) / math.sqrt(rank)
        weight_base *= 0.75 + 1.0 / max(1.0, math.log2(float(hv["mttr_minutes"]) + 2.0))
        for raw_action in hv["actions_taken"]:
            action = parse_action(raw_action, param_names)
            votes[action["name"]] += weight_base
            vote_evidence[action["name"]].append({
                "neighbor": hv["id"],
                "root_cause_class": hv["root_cause_class"],
                "similarity": round(score, 3),
                "outcome": hv["outcome"],
                "weight": round(weight_base, 3),
                "match": detail,
            })
    return {"neighbors": scored[:top_k], "votes": dict(votes), "vote_evidence": dict(vote_evidence)}