# Cluster chính: root cause là gì + lý do

- Cluster chính được phân tích là `c-000-000`. 
- Kết quả dự đoán: 
    - Root cause là `payment-svc` 
    - Class là `connection_pool_exhaustion`
    - Similar incidents gồm `INC-2025-11-08`, `INC-2026-03-20`, và `INC-2025-08-17` 
- Dựa theo các incident history gần nhất, root cause phù hợp nhất là `payment-svc` vì các service trong cluster hiện tại có mức overlap cao với các incidents cũ, đặc biệt `payment-svc` từng xuất hiện như root cause trong các incident tương tự. Retrieval score của top-1 similar incident là `0.8`, cho thấy pattern hiện tại khá giống với lỗi connection pool trước đây.

# Confidence — có dám deploy auto-remediation dựa trên output này không?

- Với confidence hiện tại, em chưa dám deploy auto-remediation hoàn toàn tự động dựa trên output này. Action như tăng connection pool từ `50` lên `100` có thể được thực hiện nếu có monitor, nhưng rollback version vẫn nên cần SRE xác nhận. Do đó, auto-remediate sẽ được deploy khi confidence từ `0.9` trở lên và class với action thật sự trùng khớp với incident lịch sử.

# 1 case mà bạn không chắc — vì sao
- Case em không chắc là cluster `c-000-001`. Cluster  `c-000-001`y được dự đoán là `memory_leak`, nhưng top 3 similar incidents đều có retrieval score chỉ khoảng `0.6`. Ngoài ra, trong top 3 similar incident thì chỉ có 1 incident là memory leak trong khi 2 cái còn lại là other. Do vậy, em nghĩ cluster này nên được xem thủ công thay vì automation

# Bonus 
## Bonus được chọn và lý do
- Chọn Bonus 3 bằng cách dùng Groq API free tier để enrich kết quả RCA. 
    - Pipeline RCA vẫn giữ service graph và temporal score để tạo `graph_top3`, sau đó retrieve top 3 similar incidents từ `incidents_history.json`.
    - Thay vì copy class và actions từ top 1 similar incident history, bonus 3 sẽ gửi cluster context, graph_top3 và similar incidents cho Groq LLM với prompt yêu cầu trả về JSON gồm root_cause, class, confidence, actions, reasoning và similar_incidents.
- Kết quả nhận được từ Groq sẽ được compare với kNN top-1. Nếu Groq trả class giống kNN thì confidence vào class cao hơn, nếu không thì fallback về kNN.
- Khi dùng LLM với API cần phải chấp nhận latency có thể cao hơn do phụ thuộc vào model bên thứ 3. Ngoài ra có nguy cơ bị hallucination, do đó cần phải validate kỹ.

## Kết quả so sánh bonus 3 và kNN
Cluster: c-000-000  
Graph top1: ('payment-svc', 1.0)  
kNN class: connection_pool_exhaustion  
Groq valid: True  
Groq class: connection_pool_exhaustion  
Groq root cause: payment-svc  
Groq confidence: 0.8  
Compare: kNN class=connection_pool_exhaustion vs Groq   class=connection_pool_exhaustion  

Cluster: c-000-001  
Graph top1: ('recommender-svc', 0.85)  
kNN class: memory_leak  
Groq valid: True  
Groq class: memory_leak  
Groq root cause: recommender-svc  
Groq confidence: 0.6  
Compare: kNN class=memory_leak vs Groq class=memory_leak  