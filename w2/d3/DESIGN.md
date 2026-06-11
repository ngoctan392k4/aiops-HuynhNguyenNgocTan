# Pipeline architecture

Endpoint `/incident` nhận một batch alert theo schema Pydantic gồm `id`, `ts`, `service`, `metric`, `severity`, `value`, `threshold`, và `labels`. Sau khi validate input, service chuyển alert thành dictionary rồi chạy 3 layer: correlation, RCA và response normalization. Layer 1 gọi hàm `correlate` từ bài W2/D1 để thực hiện correlation theo temporal và service graph. Layer 2 gọi `run_rca` từ W2/D2 để tiến hành thực hiện RCA. Layer 3 normalize output thành JSON gồm `clusters`, `root_cause`, `recommended_actions`, `similar_incidents`.

# Concrete decision

Em chọn `gap_sec=120s` và `max_hop=1`. 

`gap_sec=120s` đủ rộng để gom các alert cùng incident nhưng không quá rộng như 300s hoặc 600s, vì dễ gom nhầm hai incident không liên quan với nhau. 

`max_hop=1` giúp correlation bám sát quan hệ trực tiếp trong service graph, giảm false correlation khi topology có nhiều service liên đới gián tiếp.

# Latency budget breakdown

Latency budget mục tiêu là p99 dưới 10 giây. Với `AIOPS_USE_LLM=false`, phần correlation dự kiến chiếm khoảng 5-20ms cho batch nhỏ, RCA graph-only khoảng 5-30ms, validate JSON khoảng 1-5ms. Nếu bật LLM, outbound LLM call sẽ là bottleneck chính, có thể chiếm 80-90% latency. Vì vậy code có feature flag `AIOPS_USE_LLM=false` để fallback graph-only khi benchmark hoặc khi LLM provider bị lỗi.

# 1 production concern (concurrency hoặc fault tolerance) — handle thế nào

Production concern là concurrency trong môi trường máy yếu. 

Em đang chạy `uvicorn serve:app --workers 1`, nghĩa là chỉ một process xử lý request. Để tránh request bị treo lâu, endpoint không gọi LLM khi tắt flag, có middleware đo latency qua header `X-Response-Time-Ms`, và có `/metrics` cho Prometheus. Với nhiều worker, cache in-memory sẽ bị duplicate giữa các process, nên hiện tại chấp nhận single-worker/stateless cho lab.

# Trade-off: vì sao chọn FastAPI thay vì Flask/BentoML

Em chọn FastAPI thay vì Flask vì FastAPI có Pydantic validation native, tự trả 422 khi input sai schema, có OpenAPI docs tự động và hỗ trợ async tốt hơn cho workload có LLM call. Flask đơn giản hơn nhưng cần tự viết validation nhiều hơn. BentoML mạnh cho model-centric, nhưng hiện tại pipeline gồm correlation, RCA, enrichment nên BentoML có overhead và learning curve không cần thiết.
