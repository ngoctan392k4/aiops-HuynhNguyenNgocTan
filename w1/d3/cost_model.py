def calculate_cost(tier, services, log_gb, event_sec):
    # Chi phí lưu log trên S3 standard tại Singapore: $0.025 per GB
    # Giả sử mỗi service chạy trên 1 VM EC2 on-demand plan với 2vCPU (t3.micro): $0.0132/ h
    # Giả sử chi phí data transfer khác region là 10$ cho mỗi service
    
    days_per_month = 30
    
    storage_cost = (log_gb * days_per_month) * 0.025
    compute_cost = services * 0.0132 * 24 * days_per_month
    network_cost = 10 * services 
    total_build = storage_cost + compute_cost + network_cost
    
    # Chi phí Logs - Ingestion là $0.10/ GB/ month nếu tính theo bill annually
    # Chi phí APM là $31/service/tháng nếu tính theo bill annually
    # 100K EPS tương đương 2,000 active custom metrics - giá khoảng $0.10 /100 metrics/ month
    
    datadog_log_cost = (log_gb * days_per_month * 0.1)
    datadog_apm_cost = services * 31
    active_custom_metrics = (event_sec / 100000) * 2000
    datadog_metric_cost = active_custom_metrics / 100 * 0.1
    total_buy = datadog_log_cost + datadog_apm_cost + datadog_metric_cost
    
    return {
        "Tier": tier,
        "Build": round(total_build, 2),
        "Buy": round(total_buy, 2)
    }

tiers = [
    ("Small", 10, 50, 100000),
    ("Medium", 100, 500, 1000000),
    ("Large", 1000, 5000, 10000000)
]

print(f"{'Tier':<10} | {'Build':<15} | {'Buy Datadog (SaaS)':<15}")
for t in tiers:
    res = calculate_cost(*t)
    print(f"{res['Tier']:<10} | ${res['Build']:<14} | ${res['Buy']:<14}")