from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from features import extract_features
from retrieval import retrieve_and_vote
from decision import evidence_override

OUTCOME_WEIGHT = {"success": 1.0, "partial": 0.55, "failed": 0.15}


def load_actions(path: Path) -> list[dict]:
    try:
        import yaml

        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        actions = []
        current = None
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("- name:"):
                if current:
                    actions.append(current)
                current = {"name": line.split(":", 1)[1].strip()}
            elif current and ":" in line:
                key, value = [p.strip() for p in line.split(":", 1)]
                if value.startswith("["):
                    current[key] = [v.strip() for v in value.strip("[]").split(",") if v.strip()]
                else:
                    try:
                        current[key] = int(value)
                    except ValueError:
                        current[key] = value
        if current:
            actions.append(current)
        return actions


def choose_service(query: dict, action_name: str) -> str:
    text = " ".join(query["log_templates"])
    affected = query["affected_services"]
    if query.get("trigger_service") == "bb-edge" and ("t24-service" in affected or "t24-service" in text):
        return "t24-service"
    if action_name == "restart_pod":
        for edge in query["trace_edges"]:
            if edge["to"] == "cart-redis" or edge["from"] == "cart-svc":
                return "cart-svc"
            if edge["from"] in affected:
                return edge["from"]
    if "payment-svc" in affected or "payment-svc" in text:
        return "payment-svc"
    if "esb" in affected or "esb" in text:
        return "esb"
    return affected[0] if affected else query.get("trigger_service") or "platform-team"


def build_params(action_name: str, query: dict) -> dict:
    service = choose_service(query, action_name)
    if action_name == "rollback_service":
        return {"service": service, "target_version": "previous"}
    if action_name == "increase_pool_size":
        return {"service": service, "from_value": "50", "to_value": "100"}
    if action_name == "restart_pod":
        return {"service": service, "pod_selector": "default"}
    if action_name == "dns_config_rollback":
        return {"configmap_name": "dns-config", "target_revision": "previous"}
    if action_name == "network_policy_revert":
        return {"policy_name": "last-known-good"}
    if action_name == "page_oncall":
        return {"team": "platform-team"}
    return {}


def select_action(query: dict, retrieved: dict, actions_catalog: list[dict]) -> dict:
    votes = retrieved["votes"]
    best_similarity = retrieved["neighbors"][0][0] if retrieved["neighbors"] else 0.0
    override, override_reason = evidence_override(query, best_similarity, votes)
    action_name = override or max(votes, key=votes.get, default="page_oncall")
    catalog = {a["name"]: a for a in actions_catalog}
    meta = catalog.get(action_name, {})
    blast_check = "passed"
    if int(meta.get("blast_radius_services", 0) or 0) > 3 and not override:
        action_name = "page_oncall"
        blast_check = "failed-high-blast-radius-escalated"

    total_vote = sum(max(v, 0.0) for v in votes.values()) or 1.0
    confidence = min(0.95, max(0.2, (votes.get(action_name, 0.0) / total_vote) * 0.55 + best_similarity * 0.75))
    if override:
        confidence = max(confidence, 0.72 if action_name != "page_oncall" else 0.66)

    edges = ", ".join(f"{e['from']}->{e['to']} err={e['error_rate']:.2f} p99={e['p99_ms']:.0f}ms" for e in query["trace_edges"][:3]) or "none"
    vote_summary = {k: round(v, 3) for k, v in sorted(votes.items(), key=lambda kv: kv[1], reverse=True)}
    neighbors = [{
        "id": hv["id"],
        "root_cause_class": hv["root_cause_class"],
        "similarity": round(score, 3),
        "outcome": hv["outcome"],
        "actions_taken": hv["actions_taken"],
        "match": detail,
    } for score, hv, detail in retrieved["neighbors"][:3]]

    justification = [
        f"Top affected services from logs/traces/metrics: {', '.join(query['affected_services'][:5]) or 'none'}.",
        f"Dominant trace edges: {edges}.",
        f"Outcome-weighted action votes: {json.dumps(vote_summary, sort_keys=True)}.",
    ]
    if override_reason:
        justification.append(override_reason)

    return {
        "incident_id": query["incident_id"],
        "raw_incident_id": query["raw_incident_id"],
        "selected_action": action_name,
        "params": build_params(action_name, query),
        "confidence": round(confidence, 3),
        "consensus_score": round(votes.get(action_name, 0.0) / total_vote, 3),
        "top_3_neighbors": neighbors,
        "vote_evidence": retrieved["vote_evidence"].get(action_name, [])[:4],
        "selected_action_meta": meta,
        "blast_radius_check": blast_check,
        "justification": justification,
    }


def decide(incident_path: Path, history_path: Path, actions_path: Path) -> dict:
    incident = json.loads(incident_path.read_text(encoding="utf-8"))
    history = json.loads(history_path.read_text(encoding="utf-8"))
    actions_catalog = load_actions(actions_path)
    query = extract_features(incident)
    retrieved = retrieve_and_vote(query, history, actions_catalog)
    return select_action(query, retrieved, actions_catalog)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    d = sub.add_parser("decide")
    d.add_argument("--incident", required=True)
    d.add_argument("--history", default="incidents_history.json")
    d.add_argument("--actions", default="actions.yaml")
    d.add_argument("--audit", default="audit.jsonl")
    args = parser.parse_args()
    if args.cmd != "decide":
        parser.print_help()
        return 1
    out = decide(Path(args.incident), Path(args.history), Path(args.actions))
    print(json.dumps(out, indent=2))
    with open(args.audit, "a", encoding="utf-8") as f:
        f.write(json.dumps(out, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
