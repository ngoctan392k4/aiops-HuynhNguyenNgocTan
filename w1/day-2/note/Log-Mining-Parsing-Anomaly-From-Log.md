# Log Mining + Parsing + Anomaly từ Log
Metric cho biết “cái gì đang sai” - anomaly: latency tăng, error rate tăng, throughput giảm. Nhưng metric không cho biết “tại sao”.

Để biết tại sao, bạn cần log. Log là text mà mỗi service ghi ra khi xử lý request, kết nối database, gặp lỗi, hoặc bất kỳ sự kiện nào. Khi metric báo “latency tăng”, bạn mở log và thấy: `"Connection timeout to db-primary at 10.0.1.5:5432 after 30s"` - bây giờ biết tại sao: database primary không phản hồi.

Vấn đề: 1 microservice production gen 10GB log/ngày. 100 service = 1TB/ngày. Mỗi dòng log khác nhau (IP khác, timestamp khác, parameter khác). `grep "timeout" *.log` cho ra 50,000 kết quả - cái nào quan trọng?

Buổi hôm nay giải quyết vấn đề này: biến triệu dòng log thành cấu trúc có thể phân tích bằng machine, rồi detect anomaly trên log giống như detect anomaly trên metric.

## 1. Nền Tảng: Log Trong Production Trông Như Thế Nào?
### Log không phải chỉ là text
Trong textbook, log trông gọn gàng:

```log
2024-01-15 10:23:45 ERROR Connection timeout to database
2024-01-15 10:23:46 INFO  Retry connection attempt 1
2024-01-15 10:23:47 INFO  Connection restored

```
Trong production, log trông như thế này:

```log
[2024-01-15T10:23:45.123Z] [payment-service-7b4f8c-2xvnm] [tid:a8f2e1] ERROR com.company.payment.DatabaseConnector - Connection timeout to db-primary at 10.0.1.5:5432 after 30000ms. Retries remaining: 2. Context: orderId=ORD-8834721, amount=150000, currency=VND
[2024-01-15T10:23:45.456Z] [payment-service-7b4f8c-2xvnm] [tid:b7c3d2] WARN  com.company.payment.CircuitBreaker - Circuit breaker OPEN for db-primary. Failure count: 15/20. Last failure: java.net.SocketTimeoutException
[2024-01-15T10:23:45.789Z] [auth-service-3a2c1d-k9wnp] [tid:c1d4e5] INFO  com.company.auth.TokenValidator - Token validated successfully for userId=USR-442918 in 12ms
[2024-01-15T10:23:46.012Z] [payment-service-7b4f8c-9htqr] [tid:d2e5f6] ERROR com.company.payment.DatabaseConnector - Connection timeout to db-replica-2 at 10.0.2.3:5432 after 30000ms. Retries remaining: 1. Context: orderId=ORD-8834722, amount=250000, currency=VND

```

Mỗi dòng chứa:

- Timestamp khác nhau (millisecond precision)
- Pod name khác nhau (Kubernetes tạo random suffix)
- Trace ID khác nhau (mỗi request 1 ID)
- Class path (Java package name)
- Dynamic parameters: IP, port, orderId, amount - mỗi dòng khác nhau
- Static structure: “Connection timeout to … at … after …ms” - giống nhau

Vấn đề cốt lõi: Dòng 1 và dòng 4 ở trên thực chất là cùng 1 loại sự kiện (connection timeout tới DB), chỉ khác IP và orderId. Nhưng string match cho thấy chúng hoàn toàn khác nhau. Grep "Connection timeout" tìm được cả 2, nhưng grep không thể:

- Gom chúng thành 1 nhóm “connection timeout events”
- Đếm “bao nhiêu connection timeout / giờ?” (vì mỗi dòng khác nhau)
- Phân biệt “connection timeout” vs “token validation” vs “circuit breaker” ở mức template

### Structured vs Unstructured Log

| Tiêu chí | Structured (JSON) | Unstructured (Plain text) |
| :--- | :--- | :--- |
| **Format** | `{"level":"ERROR","msg":"Connection timeout","host":"10.0.1.5"}` | `ERROR Connection timeout to 10.0.1.5:5432` |
| **Parse (Khả năng bóc tách)** | `JSON.parse()` - Cực kỳ đơn giản, có sẵn thư viện tối ưu ở mọi ngôn ngữ. | Dùng Regex / ML parser - Rất phức tạp, dễ vỡ khi định dạng log thay đổi nhẹ. |
| **Query** | Lọc chính xác theo trường dữ liệu (ví dụ: `level=ERROR AND host=10.0.1.5`). | Tìm kiếm toàn văn (Full-text search) như lệnh `grep`, tốn tài nguyên khi quét file lớn. |
| **Storage** | Lớn hơn $\approx 30\%$ do các tên trường (`level`, `msg`, `host`) bị lặp đi lặp lại ở mỗi dòng log. | Nhỏ hơn, tối ưu dung lượng đĩa vì chỉ lưu chuỗi ký tự thô. |
| **Adoption** | Phổ biến ở các Modern dịch vụ/Microservices hiện đại (Go, Node.js thường cấu hình mặc định log JSON). | Phổ biến ở hệ thống cũ (Legacy), Java (Logback cấu hình mặc định), hoặc các System logs của hệ điều hành. |


Thực tế: Hầu hết tổ chức có cả 2. Service mới log JSON, service cũ log plain text, system log (syslog, kernel) log format riêng. AIOps cần handle cả 2 - đây là lý do cần log parser.

## Log Volume - Tại sao scale quan trọng
Theo Splunk State of Observability 2024, enterprise trung bình ingest 1-5 TB log/ngày. Banking, telecom có thể lên 10+ TB/ngày.

| Scale (Quy mô) | Log volume/ngày | Số dòng/ngày | Lệnh `grep` có đủ? | 
| :--- | :--- | :--- | :--- |
| **Startup** <br>*(~10 microservices)* | 10 - 50 GB | ~100 Triệu dòng | **Chậm nhưng OK** |
| **Mid-size** <br>*(~100 microservices)* | 100 - 500 GB | ~1 Tỷ dòng | **Không - cần ELK/Loki** |
| **Enterprise** <br>*(~1000 microservices)* | 1 - 10 TB | ~10 Tỷ dòng | Không - cần streaming pipeline | 
| **Banking / Telecom** <br>*(Hạ tầng tài chính/viễn thông)* | 10+ TB | ~100 Tỷ dòng | **Cần sampling + tiered storage** | 

Chi phí: Splunk tính $150-200/GB ingested/tháng. 1TB/ngày × 30 ngày = 30TB = $4.5M-6M/năm chỉ cho log storage + indexing. Đây là lý do nhiều company chuyển sang Loki (cheaper, log-based) hoặc build custom pipeline.

## 2. Log Parsing - Biến Text Thành Structure
### Vấn đề: Tại sao cần parse?
Bạn có 1 triệu dòng log. Mỗi dòng khác nhau vì IP, timestamp, orderId, amount… khác nhau. Nhưng pattern lặp lại. “Connection timeout to X at Y:Z” xuất hiện 5000 lần với X, Y, Z khác nhau.

Log parsing = tách mỗi dòng log thành 2 phần:

- Template (static part): "Connection timeout to <*> at <*>:<*> after <*>ms" - phần giống nhau
- Parameters (dynamic part): ["10.0.1.5", "5432", "30000"] - phần thay đổi

Sau khi parse, 5000 dòng “Connection timeout…” trở thành 1 template với 5000 instances. Bây giờ có thể:

- Đếm: “Template này xuất hiện 5000 lần/giờ - bình thường là 10 lần/giờ = anomaly”
- So sánh: “Hôm qua template này không tồn tại - new template = có gì đó mới xảy ra”
- Cluster: “5 template liên quan tới DB, 3 template liên quan tới network”

### 2.1 Drain3 - Parser Phổ Biến Nhất
Drain3 là implementation Python của thuật toán Drain, được dùng rộng rãi nhất cho log parsing trong AIOps. Logpai (đội nghiên cứu log parsing hàng đầu) rank Drain là parser tốt nhất về accuracy + speed.

Ý tưởng thuật toán:

Drain dùng fixed-depth parse tree để nhóm log nhanh chóng. Mỗi dòng log đi qua tree từ root → leaf, tại mỗi level tree quyết định nhóm dòng này vào đâu:

- Level 1: Log length - nhóm theo số từ trong dòng. Dòng 15 từ và dòng 8 từ hiếm khi cùng template.
- Level 2: First token - nhóm theo từ đầu tiên (thường là level: ERROR, INFO, WARN).
- Level 3+: Similarity matching - so sánh với các template đã biết. Nếu giống ≥ threshold → gộp vào template đó. Nếu không giống → tạo template mới.
Parse Tree:

```shell
Root
├── Length=15
│   ├── "ERROR"
│   │   ├── Template-1: "Connection timeout to <*> at <*>:<*> after <*>ms"
│   │   └── Template-2: "Failed to process payment <*> amount <*>"
│   └── "WARN"
│       └── Template-3: "Circuit breaker OPEN for <*> failure count <*>"
├── Length=12
│   ├── "INFO"
│   │   ├── Template-4: "Token validated for userId=<*> in <*>ms"
│   │   └── Template-5: "Request completed <*> status=<*> duration=<*>ms"
│   └── ...
```

Khi log mới đến, Drain đi từ root → length → first token → check similarity với templates ở leaf. Nếu match → gộp vào template (cập nhật count). Nếu không match → tạo template mới.

Thời gian: O(1) per log line (fixed depth tree, không scan toàn bộ). Parse 1 triệu dòng trong vài giây.

```python 
from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig

config = TemplateMinerConfig()
config.drain_sim_th = 0.4        # similarity threshold: 0.0-1.0
                                  # thấp = ít template (gộp nhiều), cao = nhiều template (tách nhiều)
config.drain_depth = 4            # độ sâu parse tree: 3-6
                                  # sâu hơn = chính xác hơn nhưng chậm hơn

miner = TemplateMiner(config=config)

# Parse từng dòng log
log_lines = [
    "Connection timeout to db-primary at 10.0.1.5:5432 after 30000ms",
    "Connection timeout to db-replica at 10.0.2.3:5432 after 25000ms",
    "Token validated for userId=USR-442918 in 12ms",
    "Connection timeout to db-primary at 10.0.1.5:5432 after 30000ms",
    "Request completed /api/payment status=500 duration=30125ms",
]

for line in log_lines:
    result = miner.add_log_message(line)
    print(f"Cluster ID: {result['cluster_id']}")
    print(f"Template:   {result['template_mined']}")
    print(f"Change:     {result['change_type']}")
    print()

# Xem tất cả templates
print("=== All Templates ===")
for cluster in miner.drain.clusters:
    print(f"  [{cluster.cluster_id}] (count={cluster.size}): {cluster.get_template()}")


```

Output

```shell
Cluster ID: 1
Template:   Connection timeout to <*> at <*> after <*>
Count:      1

Cluster ID: 1
Template:   Connection timeout to <*> at <*> after <*>
Count:      2

Cluster ID: 2
Template:   Token validated for <*> in <*>
Count:      1

Cluster ID: 1
Template:   Connection timeout to <*> at <*> after <*>
Count:      3

Cluster ID: 3
Template:   Request completed <*> <*> <*>
Count:      1

```

3 dòng “Connection timeout…” → cùng Cluster 1 (template giống nhau, chỉ khác parameter).

Tuning `drain_sim_th` (similarity threshold):

| Ngưỡng `sim` | Ý nghĩa kỹ thuật | Khi nào nên áp dụng? | Hệ quả hệ thống |
| :--- | :--- | :--- | :--- |
| **0.2 - 0.3** | **Gộp Aggressive** -  nhiều log khác nhau thành 1 template | Khi file log cực kỳ đa dạng (**very diverse**), chứa nhiều tham số ngẫu nhiên biến động, và bạn muốn ép hệ thống sinh ra **càng ít template càng tốt**. | Các dòng log có cấu trúc hơi khác nhau vẫn bị gộp chung thành một template lớn. Có thể làm mất đi các chi tiết cảnh báo lỗi đặc thù. |
| **0.4 - 0.5** | **Cân bằng (Balance)** <br>*(Mức mặc định - Default)* | Phù hợp với **hầu hết các use case** giám sát hệ thống thông thường khi chưa rõ đặc tính phân phối của log. | Đạt sự cân bằng tối ưu: Giữ lại đủ các biến động quan trọng nhưng không làm bùng nổ số lượng template thừa. |
| **0.6 - 0.8** | **Tách nghiêm ngặt** <br>*(Tách rất chi tiết)* | Khi dữ liệu log đã có cấu trúc khá rõ ràng (**structured log**) từ trước, và bạn yêu cầu độ chính xác tuyệt đối, muốn mỗi biến thể dù là nhỏ nhất cũng phải thành template riêng. | Hệ thống trở nên cực kỳ khắt khe. Số lượng template sinh ra sẽ tăng vọt, dễ làm nhiễu hoặc gây quá tải cho các thuật toán phát hiện bất thường (Anomaly Detection) ở bước sau. |

Common mistake: sim_th quá cao (0.8+) → mỗi dòng log thành 1 template riêng → hàng triệu template → vô dụng. sim_th quá thấp (0.1) → gộp hết → 5 template cho 1 triệu dòng → mất thông tin.

### 2.2 So Sánh Parsers

| Parser | Approach | Speed | Accuracy | Khi nào dùng |
| :--- | :--- | :--- | :--- | :--- |
| **Drain3** | Fixed-depth tree | Rất nhanh (O(1)/line) | Cao | Default choice. Production, streaming. |
| **Spell** | Longest common subsequence | Trung bình | Cao | Log có cấu trúc ổn định |
| **Lenma** | Length + token matching | Nhanh | Trung bình | Simple use case |
| **LLM-based** | Prompt LLM phân loại log | Rất chậm (API call) | Rất cao | Khi cần semantic understanding, offline analysis |
| **Regex** | Viết tay regex cho mỗi pattern | - | Phụ thuộc regex | Log ít pattern, biết trước format |

Tại sao Drain3 thắng: Theo benchmark của Logpai trên 16 datasets, Drain đạt accuracy cao nhất (average F1 > 0.9) với speed nhanh nhất. Spell gần bằng accuracy nhưng chậm hơn 5-10x. LLM-based chính xác nhất nhưng không thể chạy real-time trên 1TB log/ngày.

#### LLM-based parsing - tương lai nhưng chưa production-ready:

```python
# Ý tưởng: dùng LLM phân loại log
prompt = """
Classify this log line into a template. Replace dynamic values with <*>.

Log: "Connection timeout to db-primary at 10.0.1.5:5432 after 30000ms"
Template: "Connection timeout to <*> at <*>:<*> after <*>ms"

Log: "Failed to authenticate user admin from IP 192.168.1.100"
Template:
"""
# LLM trả lời: "Failed to authenticate user <*> from IP <*>"


```
Chính xác hơn Drain vì hiểu semantic (“admin” là username, “192.168.1.100” là IP). Nhưng:

- 1 API call / log line → ở TB scale
- Latency: 200ms-2s / call → không real-time
- Dùng cho: offline analysis, bootstrap template list, hard-to-parse log

### 2.3 Drain3 Internals - Similarity Matching
Khi 1 log line mới đến leaf node của parse tree, Drain so sánh nó với các template đã tồn tại ở leaf đó. Cách so sánh:

Token-by-token matching:

Drain tokenize cả log line mới và template hiện tại theo khoảng trắng, rồi so sánh từng token:

- Token giống nhau → match
- Token <*> trong template → luôn match (wildcard)
- Token khác nhau → mismatch

Similarity score = số token match / tổng số token. Nếu score ≥ sim_th → gộp vào template (token mismatch trở thành <*>). Nếu score < sim_th → tạo template mới.

Ví dụ step-by-step:

```plaintext
Template hiện tại: "Connection timeout to <*> at <*> after <*>"
Log mới:          "Connection timeout to db-replica at 10.0.2.3 after 25000ms"

So sánh token:
  "Connection"  vs "Connection"   → match
  "timeout"     vs "timeout"      → match
  "to"          vs "to"           → match
  "<*>"         vs "db-replica"   → match (wildcard)
  "at"          vs "at"           → match
  "<*>"         vs "10.0.2.3"     → match (wildcard)
  "after"       vs "after"        → match
  "<*>"         vs "25000ms"      → match (wildcard)

Score = 8/8 = 1.0 ≥ 0.4 → MATCH → gộp vào template, count += 1

```


Một ví dụ khác:

```plaintext
Template hiện tại: "Connection timeout to <*> at <*> after <*>"
Log mới:          "Disk usage exceeded threshold on volume /data at 95%"

So sánh token:
  "Connection" vs "Disk"      → mismatch
  "timeout"    vs "usage"     → mismatch
  "to"         vs "exceeded"  → mismatch
  ...

Score = 1/9 = 0.11 < 0.4 → NO MATCH → tạo template mới

```

#### Tại sao fixed-depth tree?

Drain dùng tree có depth cố định (thường 3-4 level) thay vì tree depth không giới hạn. Lý do:

- Speed: Depth cố định → O(1) lookup per log line. Không cần scan toàn bộ tree.
- Memory: Giới hạn số node → memory bounded. Với variable depth, tree có thể grow vô hạn.
- Trade-off: Shallow tree (depth 3) → nhanh hơn, ít chính xác. Deep tree (depth 6) → chậm hơn, chính xác hơn. Depth 4 là balance tốt cho hầu hết log format.

Drain paper (He et al., ICWS 2017) chứng minh rằng fixed-depth tree với depth 4 đạt parsing accuracy tương đương variable-depth approach, nhưng nhanh hơn 10x.

### 2.4 Multiline Log - Edge Case Quan Trọng
Hầu hết log parser (kể cả Drain3) giả định 1 event = 1 dòng. Nhưng trong Java/Python, stack trace có thể là 20-50 dòng cho 1 event:

```plaintext
2024-01-15 10:23:45 ERROR PaymentService - Failed to process payment
java.sql.SQLException: Connection pool exhausted
    at com.zaxxer.hikari.pool.HikariPool.getConnection(HikariPool.java:155)
    at com.zaxxer.hikari.pool.HikariPool.getConnection(HikariPool.java:128)
    at com.company.payment.DatabaseConnector.execute(DatabaseConnector.java:42)
    at com.company.payment.PaymentProcessor.process(PaymentProcessor.java:89)
Caused by: java.net.SocketTimeoutException: connect timed out
    at java.net.PlainSocketImpl.socketConnect(PlainSocketImpl.java:103)
    ... 15 more

```

Đây là 1 event nhưng 10 dòng. Nếu feed từng dòng vào Drain3, mỗi dòng at com.xxx.yyy trở thành template riêng → hàng nghìn template rác từ stack trace.

Giải pháp:

- Pre-processing: Trước khi feed vào Drain3, merge multiline thành 1 dòng. Regex: dòng bắt đầu bằng whitespace hoặc at hoặc Caused by → append vào dòng trước.
- Log framework config: Cấu hình log framework (Logback, Log4j) output 1 event = 1 dòng JSON (structured logging).
- Fluentd/Fluent Bit multiline parser: Cấu hình ở collection layer, merge trước khi gửi tới storage.

Paper reference: Zhu et al., “Tools and Benchmarks for Automated Log Parsing” (ICSE 2019) - benchmark đầu tiên đo multiline handling, hầu hết parser score dưới 0.5 accuracy trên multiline log.

## 3. Từ Templates Tới Anomaly Detection
Sau khi parse log thành templates, bạn có thể biến log thành time series - giống metric, và dùng detector từ D1.

### 3.1 Template Count Time Series
Đếm số lần mỗi template xuất hiện per time window (VD: mỗi 5 phút):

```python
import pandas as pd
from collections import Counter

def create_template_timeseries(log_entries, window='5min'):
    """
    Biến parsed log thành time series per template.
    
    Args:
        log_entries: list of (timestamp, template_id) tuples
        window: aggregation window
    
    Returns:
        DataFrame: columns = template IDs, rows = time windows, values = count
    """
    df = pd.DataFrame(log_entries, columns=['timestamp', 'template_id'])
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    
    # Group by time window + template → count
    ts = df.groupby([pd.Grouper(key='timestamp', freq=window), 'template_id']).size()
    ts = ts.unstack(fill_value=0)  # pivot: rows=time, cols=template
    
    return ts

# Kết quả trông như:
#                     T-001  T-002  T-003  T-042
# 2024-01-15 10:00     120     45     30      5
# 2024-01-15 10:05     115     42     28      8
# 2024-01-15 10:10     118     48     31    200  ← T-042 spike!
# 2024-01-15 10:15     122     44     29    185  ← still high


```


Bây giờ T-042 (“Connection timeout to <*>”) có time series: [5, 8, 200, 185, ...]. Apply anomaly detector từ D1:

- 3σ: T-042 trung bình 5-10/window, std ~3. 
    - 200 = 63σ → anomaly
- Isolation Forest: Feed tất cả template counts cùng lúc → multivariate anomaly (T-042 spike + T-001 stable = bất thường)

### 3.2 New Template Detection
Một trong những signal mạnh nhất: template chưa từng xuất hiện trước đó.

```python 
known_templates = set()  # templates đã thấy trong training period

def detect_new_template(result):
    """
    Detect khi Drain3 tạo template mới.
    Template mới = có gì đó mới xảy ra trong hệ thống.
    """
    template = result.get_template()
    if template not in known_templates:
        known_templates.add(template)
        return True, template  # NEW TEMPLATE - investigate!
    return False, template


```
Tại sao quan trọng:

- Deploy mới → log message mới → new template. Nếu deploy gây lỗi, new template là signal đầu tiên.
- Attack mới → log pattern chưa từng thấy. VD: SQL injection attempt tạo log line mà template chưa tồn tại.
- Config change → service behavior thay đổi → log thay đổi.

Common mistake: Không filter template mới trong 1 giờ đầu sau deploy. Deploy mới luôn tạo new template (startup log, health check log). Cần grace period.


### 3.3 Log Sequence Anomaly Detection
Template count phát hiện “cái gì tăng/giảm bất thường”. Nhưng đôi khi số lượng bình thường, thứ tự bất thường.

Bình thường, 1 request đi qua sequence: `Login → Validate → Process → Commit → Response`. Nếu bạn thấy `Login → Validate → Commit → Process → Response` - số lượng mỗi template không đổi, nhưng thứ tự sai → có thể race condition hoặc bug.

Kỹ thuật:

- N-gram trên template sequence: Biến chuỗi template ID thành n-gram (VD: bigram [T1→T2], [T2→T3], [T3→T4]). Training: đếm frequency mỗi n-gram. Detection: n-gram chưa từng thấy = anomaly.
- LSTM trên sequence: Train LSTM predict “template tiếp theo given N template trước”. Nếu prediction confidence thấp → sequence bất thường. Đây là approach của DeepLog (Du et al., CCS 2017) - paper đầu tiên dùng DL cho log anomaly detection.
- Finite State Machine (FSM): Build FSM từ log sequence bình thường. Transition không tồn tại trong FSM = anomaly. Đơn giản hơn LSTM, nhưng chỉ phát hiện transition mới, không phát hiện “transition hiếm”.

Paper: Du et al., “DeepLog: Anomaly Detection and Diagnosis from System Logs through Deep Learning” (CCS 2017). Paper này introduce ý tưởng dùng LSTM predict next log template - nếu actual template khác predicted → anomaly. Cited 2000+ lần.

### 3.4 Inter-arrival Time Anomaly
Mỗi template có rhythm tự nhiên. VD: health check log xuất hiện mỗi 30 giây. Garbage collection log xuất hiện mỗi 5-10 phút. Nếu:

- Health check ngừng xuất hiện → service có thể đã crash
- GC log từ mỗi 5 phút → mỗi 10 giây → memory pressure, GC chạy liên tục

Kỹ thuật: Tính inter-arrival time (thời gian giữa 2 lần xuất hiện liên tiếp) cho mỗi template. Build distribution. Detect khi inter-arrival time nằm ngoài expected range.

Ưu điểm so với template count: phát hiện được absence (template biến mất) - template count = 0 có thể là bình thường (nếu window quá ngắn) hoặc bất thường (service crash). Inter-arrival time detect “template lẽ ra phải xuất hiện 30 giây trước mà chưa thấy”.

### 3.5 Parameter Value Anomaly
Cùng 1 template nhưng parameter khác thường. VD:

Template: "Query completed in <*>ms"
Bình thường: parameter = 5, 12, 8, 15 (milliseconds)
Bất thường: parameter = 45000 (45 giây!)
Template count không thay đổi (vẫn 1 query/request). Nhưng giá trị parameter bất thường.

Kỹ thuật: Sau khi Drain3 parse, extract parameter values. Với parameter dạng số, build distribution (mean, std) → detect outlier. Với parameter dạng string (IP, hostname), build frequency table → detect rare value (IP chưa từng thấy = possible new host hoặc attack).

Paper: He et al., “Experience Report: System Log Analysis for Anomaly Detection” (ISSRE 2016) - so sánh parameter-based vs template-based anomaly detection, kết luận: kết hợp cả 2 cho F1 tăng 15-20%.

### 3.6 So Sánh Các Technique
Markdown
| Technique | Detect gì | Miss gì | Complexity |
| :--- | :--- | :--- | :--- |
| **Template count** | Template spike/drop | Thứ tự sai, parameter sai | Thấp |
| **New template** | Behavior mới | Anomaly trong template cũ | Thấp |
| **Sequence (n-gram)** | Thứ tự bất thường | Parameter sai, count bình thường | Trung bình |
| **Sequence (LSTM)** | Pattern phức tạp, temporal | Cần data lớn, khó debug | Cao |
| **Inter-arrival time** | Template biến mất, rhythm thay đổi | Template mới | Trung bình |
| **Parameter value** | Giá trị bất thường trong template quen | Template mới | Trung bình |

Trong production, kết hợp template count + new template + parameter value cover được 80%+ use case mà không cần DL.


### 3.7 Log Embedding - TF-IDF và Semantic
Ngoài đếm, có thể embed template thành vector rồi tính similarity:

```python
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

templates = [
    "Connection timeout to <*> at <*>:<*> after <*>ms",
    "Connection refused to <*> at <*>:<*>",
    "Token validated for userId=<*> in <*>ms",
    "Request completed <*> status=<*> duration=<*>ms",
    "Disk usage at <*>% on volume <*>",
]

# TF-IDF: biến mỗi template thành vector dựa trên word frequency
vectorizer = TfidfVectorizer()
tfidf_matrix = vectorizer.fit_transform(templates)

# Similarity matrix: template nào giống template nào?
sim_matrix = cosine_similarity(tfidf_matrix)

# Template 0 và 1 giống nhau (cả 2 về connection) → similarity cao
# Template 0 và 2 khác nhau (connection vs token) → similarity thấp


```


Khi nào dùng TF-IDF:

- Cluster templates thành nhóm (DB-related, auth-related, network-related)
- Tìm template tương tự khi gặp template mới (“template mới này giống group nào?”)
- Visualization: plot templates trên 2D (t-SNE/UMAP) để thấy cluster

Sentence-Transformers - semantic embedding:

```python
from sentence_transformers import SentenceTransformer

model = SentenceTransformer('all-MiniLM-L6-v2')
embeddings = model.encode(templates)
# Hiểu semantic: "Connection timeout" và "Connection refused" gần nhau
# dù TF-IDF có thể cho similarity thấp vì từ khác nhau


```


## 4. Kết Hợp Metric + Log - Cross-Signal Analysis
Đây là lúc AIOps thực sự mạnh. Metric cho biết “cái gì”, log cho biết “tại sao”. Kết hợp = narrow down root cause nhanh gấp nhiều lần.

### Workflow thực tế
```plaiuntext

1. Metric anomaly detector trigger: "latency p99 tăng từ 200ms lên 1.2s"
   → Biết: có vấn đề. Không biết: vấn đề gì.

2. Filter log trong time window anomaly (±5 phút)
   → Thu hẹp: từ 1 triệu dòng log → 50,000 dòng

3. Parse log → template count
   → Tìm: template nào spike cùng lúc với metric anomaly?
   → Kết quả: T-042 "Connection timeout to <*>" spike 40x

4. Drill down template T-042
   → Parameters: IP 10.0.1.5 xuất hiện 80% → DB primary
   → Context: orderId cho thấy payment service bị ảnh hưởng

5. Kết luận: "Latency tăng vì DB primary (10.0.1.5) timeout.
   Payment service bị ảnh hưởng nặng nhất."

```

### Code kết hợp

```python 
def cross_signal_analysis(metric_anomalies, log_entries, miner, window_minutes=5):
    """
    Kết hợp metric anomaly với log analysis.
    
    Khi metric anomaly xảy ra, tìm log template nào spike cùng lúc.
    """
    results = []
    
    for anomaly_time in metric_anomalies:
        # Filter log trong window quanh anomaly
        start = anomaly_time - pd.Timedelta(minutes=window_minutes)
        end = anomaly_time + pd.Timedelta(minutes=window_minutes)
        
        window_logs = [
            (ts, line) for ts, line in log_entries
            if start <= ts <= end
        ]
        
        # Parse và đếm template
        template_counts = Counter()
        for ts, line in window_logs:
            result = miner.add_log_message(line)
            template_counts[result.get_template()] += 1
        
        # Top template = most likely related to anomaly
        top_templates = template_counts.most_common(5)
        
        results.append({
            'anomaly_time': anomaly_time,
            'log_count': len(window_logs),
            'top_templates': top_templates,
        })
    
    return results


```

## . Production Considerations
### 5.1 Log Sampling
Ở TB scale, không thể parse 100% log. Cần sampling strategy:

| Strategy | Mô tả | Trade-off |
| :--- | :--- | :--- |
| **Random sampling** | Lấy random 10% log | Đơn giản, nhưng miss anomaly hiếm |
| **Head-based** | Lấy 100% log từ 10% request | Giữ full context per request, miss 90% |
| **Tail-based** | Chỉ giữ log từ request lỗi/chậm | Tốt cho debug, miss slow-burn issue |
| **Adaptive** | Tăng sampling rate khi detect anomaly | Tốt nhất, phức tạp implement |

## 5.2 Log Pipeline Architecture
Trong production, log không đi thẳng từ service vào parser. Có cả 1 pipeline:

```
Service → Agent → Aggregator → Transport → Processing → Storage → Query
```
### Collection layer - Agent:

- Fluent Bit (C, lightweight, ~450KB memory): chạy trên mỗi node/pod, collect log từ stdout/file, forward tới aggregator. CNCF graduated project. Dùng cho: Kubernetes DaemonSet, IoT, edge.
- Fluentd (Ruby, plugin-rich): mạnh hơn Fluent Bit về plugin ecosystem (700+ plugins). Dùng cho: aggregator layer, complex routing.
- Vector (Rust, high-performance): mới hơn, performance tốt hơn Fluentd 5-10x. Datadog develop. Dùng cho: high-throughput pipeline.
- OpenTelemetry Collector: collect metric + log + trace cùng 1 agent. Vendor-neutral. Xu hướng mới - thay thế dedicated log agent.

Paper reference: Schipper et al., “A Benchmark for Log Data Processing Pipelines” (ICPE 2024) - benchmark Fluent Bit vs Fluentd vs Vector trên throughput, latency, resource usage.

### Transport layer:

- Direct push: Service → Storage. Đơn giản nhưng coupling cao, storage crash = mất log.
- Kafka: Buffer giữa collection và processing. Decouple producer/consumer. Replay capability (re-process log nếu parser bug). Dùng cho: > 100 service, cần durability.
- NATS: Nhẹ hơn Kafka, không persist by default. Dùng cho: low-latency, fire-and-forget log.

### Storage layer:

| Storage | Ưu | Nhược | Cost |
| :--- | :--- | :--- | :--- |
| **Elasticsearch (ELK)** | Full-text search mạnh, query linh hoạt | Resource heavy, đắt ở scale |  |
| **Loki (Grafana)** | Chỉ index label, không full-text → rẻ hơn 10x | Query hạn chế hơn ES | $ |
| **ClickHouse** | Column-oriented, query aggregate cực nhanh | Không tốt cho full-text search | $$ |
| **S3 + Parquet** | Rẻ nhất, lưu lâu dài | Không query real-time, cần Athena/Spark | $ (storage only) |

### Retention strategy - Hot/Warm/Cold:

- Hot (0-7 ngày): Elasticsearch/Loki - query nhanh, đắt
- Warm (7-30 ngày): Cheaper storage tier, slower query
- Cold (30-365 ngày): S3 Glacier - chỉ dùng khi cần forensic, query mất phút-giờ
- Delete (>365 ngày): Trừ khi compliance yêu cầu giữ lâu hơn

Doanh nghiệp lớn thường kết hợp: Fluent Bit → Kafka → Flink (parse + enrich) → Elasticsearch (7 ngày hot) + S3 (archive). Tổng cost cho 1TB/ngày: ~$15,000-30,000/tháng (tuỳ query volume).

## 5.3 Structured Logging Best Practice
Nếu bạn control service code, log structured (JSON) thay vì plain text:

```python 
# BAD: plain text, khó parse
logger.info(f"Payment processed for order {order_id} amount {amount} in {duration}ms")

# GOOD: structured JSON, dễ query
logger.info("Payment processed", extra={
    "order_id": order_id,
    "amount": amount,
    "duration_ms": duration,
    "currency": "VND",
})


```


Structured log → không cần Drain3 parse → trực tiếp query/aggregate. Nhưng legacy system không có structured log → vẫn cần parser.

## 6. Chọn Approach
| Scenario | Approach |
| :--- | :--- |
| **Có structured log (JSON)** | Trực tiếp aggregate + anomaly detect trên fields |
| **Có unstructured log, cần real-time** | Drain3 parse → template count → anomaly detect |
| **Cần semantic understanding** | TF-IDF hoặc sentence-transformer cluster templates |
| **Cần detect new behavior** | New template detection (Drain3 tạo cluster mới) |
| **Debug specific incident** | Cross-signal: metric anomaly time → filter log → find template spike |
| **Offline analysis, accuracy cao** | LLM-based parsing + deep analysis |


## KPI Đo Lường
| KPI | Công thức | Target | Giải thích |
| :--- | :--- | :---: | :--- |
| **Parsing accuracy** | Đúng template / tổng log | > 0.85 | Template extracted có đúng không (so với ground truth) |
| **Template count** | Số template unique | 100-500 cho 1 service | Quá ít = gộp quá nhiều, quá nhiều = tách quá nhiều |
| **Grouping accuracy** | Precision/recall của clustering | F1 > 0.8 | Log cùng loại có được gộp cùng template không |
| **Cross-signal TTD** | Metric anomaly → tìm root cause trong log | < 5 phút | Thời gian từ “biết có vấn đề” → “biết tại sao” |

## References
- [Drain3 GitHub](https://github.com/logpai/Drain3) - Implementation chính thức, Python, production-ready
- [He et al. “Drain: An Online Log Parsing Approach” (ICWS 2017)](https://jiemingzhu.github.io/pub/pjhe_icws2017.pdf) - Paper gốc
- [Logpai benchmark](https://github.com/logpai/logparser) - So sánh 13 parsers trên 16 datasets
- [Loghub datasets](https://github.com/logpai/loghub) - HDFS, BGL, Spark, Hadoop… dùng cho assignment
- [Splunk State of Observability 2024](https://www.splunk.com/en_us/form/state-of-observability.html) - Log volume statistics
- [Google SRE Book Ch.17 - Testing for Reliability](https://sre.google/sre-book/testing-reliability/) - Log analysis trong incident response