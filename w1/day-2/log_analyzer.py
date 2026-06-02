import sys
from collections import Counter
from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig


def init_drain():
    config = TemplateMinerConfig()
    config.drain_sim_th = 0.4
    config.drain_depth = 4
    config.drain_max_children = 100

    return TemplateMiner(config=config)


def analyze(logfile):
    miner = init_drain()

    cluster_ids = []
    templates_map = {}

    with open(logfile, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            result = miner.add_log_message(line)

            cluster_id = result["cluster_id"]
            cluster_ids.append(cluster_id)

            templates_map[cluster_id] = result["template_mined"]

    total_lines = len(cluster_ids)

    counter = Counter(cluster_ids)
    unique_templates = len(counter)

    split_idx = int(total_lines * 0.8)

    recent = cluster_ids[split_idx:]
    old = cluster_ids[:split_idx]

    recent_counter = Counter(recent)
    old_counter = Counter(old)
    
    # Get top 5 template    
    top5 = counter.most_common(5)

    # Detect spike
    spike = []

    for cid, recent_count in recent_counter.items():
        old_count = old_counter.get(cid, 0)

        avg_old = old_count / max(1, len(old))

        if old_count == 0 and recent_count >= 3:
            spike.append((cid, recent_count, "NEW SPIKE"))

        elif avg_old > 0 and recent_count > max(2, 1.5 * avg_old):
            spike.append((cid, recent_count, "SPIKE"))

    # Detect new template
    new_templates = [cid for cid in recent_counter if cid not in old_counter]

    print("Log Analyzer")
    print(f"Total logs: {total_lines}")
    print(f"Unique templates (clusters): {unique_templates}")

    print("\nTop-5 templates:")
    for cid, cnt in top5:
        pct = cnt / total_lines * 100
        print(f"- {cnt} ({pct:.2f}%) -> [{cid}] {templates_map.get(cid, '')}")

    print("\nSpike templates:")
    for cid, cnt, tag in spike:
        print(f"- [{tag}] {cnt} -> [{cid}] {templates_map.get(cid, '')}")

    print("\nNew templates:")
    for cid in new_templates:
        print(f"- [{cid}] {templates_map.get(cid, '')}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python log_analyzer.py <logfile>")
        sys.exit(1)

    analyze(sys.argv[1])