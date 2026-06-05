import json
import math
import os
from collections import deque
from datetime import datetime, timezone
from fastapi import FastAPI, Request
import uvicorn

app = FastAPI(title="ShopX AIOps Streaming Anomaly Pipeline")
ALERTS_FILE = "alerts.jsonl"


WINDOW_SIZE = 40  # window size for rolling mean

# Streaming State quản lý qua Dict 
pipeline_state = {
    "fault_detected": False,
    "history_rps": deque(maxlen=WINDOW_SIZE),
    "history_timeout": deque(maxlen=WINDOW_SIZE),
    "history_memory": deque(maxlen=WINDOW_SIZE)
}

def calculate_rolling_3_sigma_upper_bound(history_queue, current_value) -> bool:
    
    # Hàm tính Z-Score theo Rolling Window.
    # True nếu value > 3 sigma
    
    # Tính toán Rolling Mean và Std
    if len(history_queue) < int(WINDOW_SIZE / 2):
        history_queue.append(current_value)
        return False
        
    mean = sum(history_queue) / len(history_queue)
    variance = sum((x - mean) ** 2 for x in history_queue) / len(history_queue)
    std_dev = math.sqrt(variance)
    
    if std_dev < 1e-4:
        std_dev = 1e-4
        
    # Thêm giá trị hiện tại vào hàng đợi queue 
    history_queue.append(current_value)
    
    # Check anomaly detection
    return current_value > (mean + 3.0 * std_dev)

def write_alert(alert_type: str, severity: str, message: str, timestamp: str = None):
    if pipeline_state["fault_detected"]:
        return  # Nếu là chuỗi spike thì chỉ ghi 1 lần

    pipeline_state["fault_detected"] = True
    if not timestamp:
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        
    alert_payload = {
        "timestamp": timestamp,
        "type": alert_type,
        "severity": severity,
        "message": message
    }
    
    with open(ALERTS_FILE, "a") as f:
        f.write(json.dumps(alert_payload) + "\n")
    print(f"\n[ALERT FIRED] {alert_type.upper()} - {message}\n")

@app.post("/ingest")
async def ingest(request: Request):
    if pipeline_state["fault_detected"]:
        return {"status": "skipped", "reason": "fault already handled"}
        
    payload = await request.json()
    metrics = payload.get("metrics", {})
    logs = payload.get("logs", [])
    timestamp = payload.get("timestamp")

    # Nếu Log có lỗi thì báo lỗi luôn 
    for log in logs:
        message = log.get("message", "")
        level = log.get("level", "")
        
        if "Circuit breaker OPEN" in message or "payment-service" in message:
            write_alert("dependency_timeout", "critical", f"Log pattern match: {message}", timestamp)
            return {"status": "ok"}
            
        if "OutOfMemoryWarning" in message or ("GC pause exceeded" in message and level == "ERROR"):
            write_alert("memory_leak", "critical", f"Log pattern match: {message}", timestamp)
            return {"status": "ok"}
            
        if "server overloaded" in message or "Request rejected" in message:
            write_alert("traffic_spike", "critical", f"Log pattern match: {message}", timestamp)
            return {"status": "ok"}

    # Rolling 3 sigma
    current_rps = metrics.get("http_requests_per_sec", 0.0)
    current_timeout = metrics.get("upstream_timeout_rate", 0.0)
    current_memory = metrics.get("memory_usage_bytes", 0.0)

    # Đánh giá anomoly
    is_rps_anomaly = calculate_rolling_3_sigma_upper_bound(pipeline_state["history_rps"], current_rps)
    is_timeout_anomaly = calculate_rolling_3_sigma_upper_bound(pipeline_state["history_timeout"], current_timeout)
    is_memory_anomaly = calculate_rolling_3_sigma_upper_bound(pipeline_state["history_memory"], current_memory)

    # Traffic Spike
    if is_rps_anomaly and metrics.get("queue_depth", 0) > 10 and current_rps > 150:
        write_alert("traffic_spike", "critical", f"Traffic spike detected on RPS: {current_rps:.2f} req/s", timestamp)
        return {"status": "ok"}

    # Dependency Timeout
    if is_timeout_anomaly and metrics.get("http_p99_latency_ms", 0) > 400 and current_timeout > 5.0:
        write_alert("dependency_timeout", "critical", f"Dependency Timeout detected on Upstream Timeout: {current_timeout:.2f}%", timestamp)
        return {"status": "ok"}

    # Memory Leak
    if is_memory_anomaly and current_memory > 1.2e9 and metrics.get("jvm_gc_pause_ms_avg", 0) > 30:
        write_alert("memory_leak", "critical", f"Memory Leak detected on Memory usage: {current_memory / 1e6:.1f} MB", timestamp)
        return {"status": "ok"}

    return {"status": "ok"}

if __name__ == "__main__":
    if os.path.exists(ALERTS_FILE):
        os.remove(ALERTS_FILE)
    uvicorn.run(app, host="0.0.0.0", port=8000)
