# Confidence của top-1 trong cluster lớn nhất bạn xử lý là bao nhiêu? Nếu phải set threshold để auto-rollback (không cần SRE confirm), bạn pick số nào? Lý do?

- Top-1 trong cluster lớn nhất mà em đã xử lý là `c-000-000` với retrieval score là `0.8` và confidence là `1.0`, class được dự đoán là `connection_pool_exhaustion`, và root cause trong incident tương tự là `payment-svc`. 
- Nếu phải set threshold để auto-rollback (không cần SRE confirm), em sẽ chọn threshold khoảng `0.9` vì mặc dù `0.8` đã đủ tốt để gợi ý hướng xử lý, nhưng chưa đủ an toàn để auto-rollback do rollback có thể ảnh hưởng đến môi trường production. Score từ `0.9` trở lên cho thấy incident hiện tại rất giống incident trong lịch sử, class và action đã được chứng minh hiệu quả trong history.

# Variant bạn chọn cho classifier (A rule-based / B free LLM / C paid LLM). Chạy thực tế ra sao? Trade-off với variant bạn không chọn?

- Em chọn variant A rule-based với retrieval-based classifier theo kiểu kNN. 
- Pipeline sẽ retrieve top 3 similar incidents bằng keyword similarity, sau đó lấy class và actions từ top 1 incident giống nhất. 
- Khi chạy thực tế:
    - Cluster `c-000-000` trả về class `connection_pool_exhaustion` với retrieval score `0.8`, action là `Rollback to v3.1. Scale pool 50 → 100 cushion. Add pool monitor alert > 80%.`. 
    - Cluster `c-000-001` trả về class `memory_leak` với retrieval score `0.6`, action là `Patch leak; rollback v3.0 trong khi chờ. Add gc.collect() trong handler`. 
- Variant này đơn giản, dễ thực hiện, không cần API key, deterministic và dễ debug. Tuy nhiên, trade-off là nó phụ thuộc vào độ chính xác của incident history. Nếu incident history thiếu hoặc pattern của incident hiện tại mới hoàn toàn, classifier có thể trả về class với độ tin cậy thấp.

# Đọc bảng Industry landscape (§6) — pipeline bạn xây gần product nào nhất? Trong domain GeekShop (e-commerce, alert volume cao, service map tương đối ổn định), lựa chọn đó hợp lý hay nên đổi?

- Pipeline được build xây gần với hướng của Dynatrace Davis nhất, vì nó dựa vào service graph, topology và ranking root cause candidate theo dependency. 
- Pipeline đã dùng graph, temporal score để chọn root cause, sau đó dùng incident history để bổ sung class và actions. 
- Trong domain GeekShop (e-commerce, alert volume cao, service map tương đối ổn định), lựa chọn này vẫn hợp lý. Vì e-commerce thường có data flow rõ như `edge-lb → checkout-svc → payment-svc`, nên RCA dựa vào service graph có khả năng phát hiện service nằm sâu nhất. Tuy nhiên nếu hệ thống có async/ event-driven service, Serverless / FaaS, Multi-tenant shared infra, Service mesh abstraction, pipeline nên bổ sung  bổ sung dimension khác (tenant, component, queue) hoặc fallback Causal Inference RCA
