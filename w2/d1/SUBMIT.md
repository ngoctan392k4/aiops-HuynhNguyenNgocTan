# gap_sec được chọn và lý do chọn
- gap_sec = 49s được chọn bằng cách nhìn histogram time_since_last_alert trong datasets và chọn ở mức 95th percentile của intra-incident gap.
- Nếu chọn gap_sec quá nhỏ như 30s, incident dài dễ bị tách thành nhiều group nhỏ. Còn nếu chọn gap_sec quá lớn như 600+ thì hệ thống dễ bắt các alerts trong các incident không liên quan, gây false correlation

# max_hop được chọn và lý do chọn
- max_hop hiện tại được thiết lập là 1 (max_hop = 1) vì sau khi quan sát mối liên hệ giữa các service từ service graph, có nhiều service liên kết gián tiếp với nhau. Do đó, nếu max_hop = 2 thì topology grouping sẽ gom nhóm quá rộng, khiến các alerts chỉ gần nhau về mặt cấu trúc nhưng không cùng incident vẫn bị đưa vào củng một cluster, làm tăng false correlation. Do đó, chọn max_hop = 1 giúp chỉ gom các service có quan hệ trực tiếp, tăng độ chính xác và giảm noise.

# 1 alert ID đã bị “miss” (không match cluster nào) - tại sao?
- 1 alert ID đã bị miss (không match cluster nào) là alert `a-0013` vì alert này không có mối quan hệ correlation trực tiếp với các service khác. Alert này do service `recommender-svc` chỉ ngẫu nhiên xảy ra trong khoảng thời gian gap_sec đã được thiết lập


# Nếu có 10000 alert thay vì 200, code sẽ chậm ở đâu?
Nếu có 10000 alert thay vì 200, code sẽ chậm nhất ở bước `topology_group()`, cụ thể là ở vòng lặp so sánh từng cặp service và gọi `nx.shortest_path_length()` để kiểm tra điều kiện <= max_hop. Khi số alert tăng, số cặp cần kiểm tra cũng tăng theo, làm cho thời gian xử lý nhiều hơn. 

# EOD Checkpoint
## 1. Vì sao fingerprint cho dedup không include timestamp hay value? Cho ví dụ nếu include thì hệ thống behave ra sao.
- Vì fingerprint giống như giống vân tay của mỗi người - mỗi fingerprint là hoàn toàn unique. Fingerprint là một subset không đổi bao gồm các fields cố định nhắm định danh các alert, các fields này không đổi giữa các lần fire. Trong khi đó, timestamp và value là các biến có tính dynamic theo thời gian nên những field này không được thêm vào fingerprint. 
- Nếu include timestamp hay value vào fingerprint, thì mỗi alert sẽ trở thành 1 fingerprint, từ đó tạo ra vô số mã fingerprint tương ứng với mỗi timestamp và value. Do vậy mà không có các alert nào duplicate với alert nào, từ đó dedup trở nên vô dụng


## 2. Sự khác biệt giữa “duplicate” và “correlated” alert là gì? Ví dụ cụ thể từ lab dataset.
- Duplicate alert là các alert bị lặp lại cùng một vấn đề như cùng service, cùng metric, cùng severity hoặc cùng fingerprint. 
    - Ví dụ :
    ```jsonl
        {"id": "a-0003", "ts": "2026-06-12T09:42:22Z", "service": "payment-svc", "metric": "latency_p99_ms", "severity": "crit",  "value": 1840, "threshold": 800,  "labels": {"env": "prod", "region": "ap-southeast-1"}}

        {"id": "a-0008", "ts": "2026-06-12T09:43:18Z", "service": "payment-svc", "metric": "latency_p99_ms", "severity": "crit",  "value": 1840, "threshold": 800,  "labels": {"env": "prod", "region": "ap-southeast-1"}}

        {"id": "a-0015", "ts": "2026-06-12T09:46:01Z", "service": "payment-svc", "metric": "latency_p99_ms", "severity": "crit",  "value": 1840, "threshold": 800,  "labels": {"env": "prod", "region": "ap-southeast-1"}}

    ```
    
    - Cả 3 alerts này đều có cùng fingerprint là `payment-svc|latency_p99_ms|crit`. Do đó, có thể nói fingerprint này bị duplicate

- Correlated alert là các alert khác nhau nhưng có liên quan với nhau trong một incident, thường xảy ra gần nhau (trong cùng 1 time window) và có quan hệ trên topology.
    - Ví dụ:
    ```json 
        {"id": "a-0004", "ts": "2026-06-12T09:42:30Z", "service": "payment-svc", "metric": "error_rate", "severity": "warn",  "value": 0.04, "threshold": 0.02, "labels": {"env": "prod", "region": "ap-southeast-1"}}
        
        {"id": "a-0005", "ts": "2026-06-12T09:42:45Z", "service": "checkout-svc","metric": "latency_p99_ms", "severity": "warn",  "value": 2100, "threshold": 1500, "labels": {"env": "prod", "region": "ap-southeast-1"}}

    ```

    - Alert của service `payment-svc` ở `error_rate` kéo theo alert metric `latency_p99_ms` của `checkout-svc`

## 3. gap_sec = 30 (rất ngắn) vs gap_sec = 600 (rất dài) - mỗi cái sẽ ảnh hưởng output thế nào? 1 dòng cho mỗi case.
- gap_sec = 30 giây sẽ gây phân mảnh các nhóm alert. Giả sử khi một sự cố kéo dài hơn 30 giấy, incidents này sẽ bị tách thành nhiều cluster độc lập vì các alert của incident xảy ra sau giây thứ 30 sẽ không được nhóm lại theo group đó
- gap_sec = 600 giây sẽ gây gom nhầm các alert không liên quan đến incidents vào chung một cluster vì các alerts này có thể xảy ra không quá 10 phút và gap_sec=600 sẽ gom chúng vào

## 4. Trong scenario chính (payment-svc pool exhaustion), recommender-svc cũng alert (batch retrain). Correlator của bạn có gom recommender vào cluster chính không? Vì sao có / không?
- Trong scenario chính (payment-svc pool exhaustion), correlator không có gom recommender-svc vào cluster chính vì khi cấu hình max_hop = 1, recommender-svc không có connection trực tiếp đến các service khác nên nó không có correlation với nhau.


## 5. Limitation lớn nhất của topology grouping mà bạn nhận ra? Suggest 1 cách khắc phục.
Limitation lớn nhất của topology grouping: Theo cách làm hiện tại thì việc tách các groups được thực hiện bằng cách dedup, session với gap_sec = 49, gom các alert theo cấu trúc servcie (dựa vào service graph). Với cách làm này, khi có 2 fingerprint khác nhau (khác metric name) nhưng nói về cùng một vấn đề như DB pool gần cạn thì dedup không thẻ giải quyết được vấn đề này. Ngoài ra, các alert dù là noise nếu xảy ra trong phạm vi gap_sec và max_hop thì đều được gom vào cluster, tức là không phân biệt đâu là root causes, đâu là cái bị lan, đâu là noise

Suggest cách khắc phục: Implement thêm một layer về semantic similarity giữa các alert thay vì chỉ dựa vào fingerprint chính xác. Approach đơn giản là dùng Jaccard similarity trên các token của metric name, ví dụ tách db_connection_pool_used_ratio thành các từ db, connection, pool, used, ratio, rồi so similarity giữa các alert. Một cách nâng cao hơn là dùng sentence-transformer để encode metric thành vector và tính cosine similarity. Nếu similarity threshold (ví dụ 0.8), thì coi như 2 alert có quan hệ về mặt ngữ nghĩa. 