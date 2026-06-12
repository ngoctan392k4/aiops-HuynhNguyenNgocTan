def evidence_override(query: dict, best_similarity: float, votes: dict[str, float]) -> tuple[str | None, str]:
    text = " ".join(query["log_templates"])
    rule = query["trigger_rule"]
    affected = set(query["affected_services"])
    if "informer" in rule or "k8s_api_throttle" in text or "cache stale" in text:
        return "page_oncall", "OOD gate: Kubernetes informer/cache-staleness evidence has no close historical match."
    if "certificate" in text or "x509" in text or "tls handshake" in text:
        return "page_oncall", "Human gate: certificate/TLS rotation is outside the auto-remediation catalog."
    if "nxdomain" in text or "dns" in text or "servfail" in text:
        return "dns_config_rollback", "Infra gate: DNS failure signatures match a reversible DNS config action."
    if "cart-redis" in affected and any(e["to"] == "cart-redis" and e["error_rate"] >= 0.08 for e in query["trace_edges"]):
        return "restart_pod", "Conflict gate: traces isolate cart-svc -> cart-redis, so unrelated payment logs are not trusted."
    if query.get("trigger_service") == "bb-edge" and ("t24-service" in affected or "t24-service" in text):
        return "rollback_service", "Cascade gate: deepest repeated errors and targets point to t24-service rather than the alerting edge."
    if best_similarity < 0.12 and max(votes.values(), default=0.0) < 0.08:
        return "page_oncall", "OOD gate: no neighbor has enough combined log/trace similarity."
    return None, ""