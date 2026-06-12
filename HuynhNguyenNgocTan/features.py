import re
from collections import Counter, defaultdict

def normalize_text(s: str) -> str:
    s = s.lower()
    s = re.sub(r"\b[0-9a-f]{8,}\b", "<id>", s)
    s = re.sub(r"\b\d+(?:\.\d+)?(?:ms|s|mb|gb|%)?\b", "<num>", s)
    return re.sub(r"\s+", " ", s).strip()

def tokens(s: str) -> set[str]:
    stop = {"the", "a", "an", "to", "on", "in", "of", "for", "and", "or", "is", "has", "by", "with"}
    return {t for t in re.findall(r"[a-z0-9][a-z0-9_-]+", normalize_text(s)) if t not in stop and t != "num"}

def series_change(values: list[list]) -> float:
    nums = [float(v[1]) for v in values if len(v) >= 2]
    if len(nums) < 4:
        return 0.0
    n = max(2, len(nums) // 4)
    before = sum(nums[:n]) / n
    after = sum(nums[-n:]) / n
    return (after - before) / max(abs(before), 1.0)

def extract_features(incident: dict) -> dict:
    log_templates = Counter()
    log_services = Counter()
    log_tokens = Counter()
    target_services = Counter()
    for entry in incident.get("logs", []):
        msg = entry.get("msg", "")
        if entry.get("level", "INFO") in {"ERROR", "WARN"}:
            log_templates[normalize_text(msg)] += 1
            log_services[entry.get("svc", "")] += 1
            log_tokens.update(tokens(msg))
            for m in re.finditer(r"(?:target=|to\s+|upstream\s+)([a-z0-9_-]+(?:-svc|-db|-redis|-events|-service|edge|esb))", msg.lower()):
                target_services[m.group(1)] += 1

    edge_stats = defaultdict(lambda: {"count": 0, "errors": 0, "p99_sum": 0.0, "seen": 0})
    for t in incident.get("traces", []):
        key = (t.get("from", ""), t.get("to", ""))
        edge_stats[key]["count"] += int(t.get("count", 0))
        edge_stats[key]["errors"] += int(t.get("error_count", 0))
        edge_stats[key]["p99_sum"] += float(t.get("p99_ms", 0.0))
        edge_stats[key]["seen"] += 1

    trace_edges = []
    trace_services = Counter()
    for (src, dst), st in edge_stats.items():
        rate = st["errors"] / max(st["count"], 1)
        p99 = st["p99_sum"] / max(st["seen"], 1)
        score = rate * 2.0 + min(p99 / 2500.0, 2.0)
        if rate >= 0.08 or p99 >= 900:
            trace_edges.append({"from": src, "to": dst, "error_rate": rate, "p99_ms": p99, "score": score})
            trace_services[src] += 1
            trace_services[dst] += 2
    trace_edges.sort(key=lambda e: e["score"], reverse=True)

    metric_changes = []
    metric_services = Counter()
    for name, values in incident.get("metrics_window", {}).get("samples", {}).items():
        if "." not in name:
            continue
        svc, metric = name.split(".", 1)
        change = series_change(values)
        if abs(change) >= 0.5:
            metric_changes.append({"service": svc, "metric": metric, "change": change})
            metric_services[svc] += abs(change)
    metric_changes.sort(key=lambda m: abs(m["change"]), reverse=True)

    service_score = Counter()
    for k, v in log_services.items():
        service_score[k] += v * 1.4
    for k, v in trace_services.items():
        service_score[k] += v * 2.0
    for k, v in target_services.items():
        service_score[k] += v
    for k, v in metric_services.items():
        service_score[k] += v * 0.6
    alert_service = incident.get("trigger_alert", {}).get("service")
    if alert_service:
        service_score[alert_service] += 0.5

    return {
        "incident_id": incident.get("incident_id", "").split("-2026")[0],
        "raw_incident_id": incident.get("incident_id", ""),
        "trigger_service": alert_service,
        "trigger_rule": incident.get("trigger_alert", {}).get("rule_id", ""),
        "log_templates": [t for t, _ in log_templates.most_common(20)],
        "log_tokens": set(log_tokens),
        "trace_edges": trace_edges[:12],
        "metric_changes": metric_changes[:12],
        "affected_services": [s for s, _ in service_score.most_common(8) if s],
    }
