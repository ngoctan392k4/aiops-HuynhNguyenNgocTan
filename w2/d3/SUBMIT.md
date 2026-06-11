# EOD checkpoint

## Latency thực của endpoint bạn ra sao? Chạy 20 request liên tiếp với dataset 20 alert thật, đo p50 và p99 từ header X-Response-Time-Ms. Phase nào (validate / correlate / RCA / LLM / serialize) chiếm phần lớn? Phase nào sẽ scale linear nếu input gấp 10×, phase nào fixed cost?

Em test endpoint `/incident` bằng 20 request liên tiếp với dataset thật gồm 20 alerts. Latency được đo từ response header `X-Response-Time-Ms`:

* p50 = `2.10 ms`
* p99 = `3.60 ms`
* min = `1.80 ms`
* max = `1250.20 ms`
* success rate = `20 / 20`

Với `AIOPS_USE_LLM=false`, phase chiếm phần lớn latency là `correlate và RCA`. Trong đó correlation cần sort alerts, group theo session, group theo topology; RCA cần tính graph+temporal score và retrieve similar incidents từ history. Nếu bật LLM provider, LLM call sẽ trở thành bottleneck lớn nhất vì đó là outbound network call.

Nếu input tăng 10 lần, phase validate sẽ scale linear theo số alert. Correlation cũng tăng theo input vì phải sort timestamp và duyệt alert để group. Topology grouping có thể tăng mạnh nếu có nhiều service khác nhau trong cùng một session vì cần check path giữa các service trên graph. RCA tăng theo số cluster. Các phần như HTTP overhead, load graph/history, route handling và response middleware là fixed cost.

## LLM provider down hoặc 4 request đồng thời — endpoint handle ra sao? Test concurrency bằng ab -n 20 -c 4 -p body.json -T application/json http://localhost:8000/incident (Linux/Mac) hoặc Python concurrent.futures.ThreadPoolExecutor (Windows). Bottleneck đầu tiên bạn quan sát được là gì? Bạn có fallback path không?

Em test concurrency bằng `ThreadPoolExecutor(max_workers=4)` với 20 requests. Kết quả:

* total time = `10236.99 ms`
* success = `20 / 20`
* p50 header latency = `5.60 ms`
* p99 header latency = `7.20 ms`

Với `--workers 1`, endpoint vẫn handle được 4 request đồng thời ở dataset nhỏ vì pipeline hiện tại không gọi LLM và dataset chỉ có 20 alerts. Bottleneck đầu tiên là CPU-bound logic trong correlation và RCA, đặc biệt khi nhiều request cùng chạy trên một process. Nếu bật LLM, bottleneck đầu tiên sẽ chuyển sang outbound LLM call vì mỗi request phải chờ network, API provider.

Fallback path hiện tại chạy với `AIOPS_USE_LLM=false`. Khi LLM provider down, endpoint không fail toàn bộ request mà vẫn trả kết quả bằng graph+temporal scoring và retrieval từ incident history. Trade-off là reasoning có thể ít ngữ cảnh hơn LLM, nhưng on-call vẫn nhận được `clusters`, `root_cause`, `class`, `confidence`, `actions`, và `similar_incidents`.

## /healthz và /readyz của bạn check gì? Vì sao tách 2 endpoint thay vì gộp 1? Khi LLM API down, /readyz của bạn fail hay vẫn pass? Lý do?

`/healthz` là liveness check. Endpoint này kiểm tra process FastAPI còn sống hay không và trả `{"status":"ok"}`

`/readyz` là readiness check. Endpoint này dùng để kiểm tra service đã sẵn sàng nhận traffic hay chưa. Em đã thiết lập dùng để check graph đã load được, history đã load được, và pipeline dependency đã có. Chỉ nên nhạn request sau khi `/readyz` đã pass

Em tách 2 endpoint vì 2 endpoint phục vụ 2 mục đích khác nhau. Một service có thể vẫn alive nên `/healthz` pass, nhưng chưa load được `services.json` hoặc `incidents_history.json`, nên `/readyz` fail để từ chối nhận traffic

Khi LLM API down, `/readyz` của em vẫn pass do có fallback `AIOPS_USE_LLM=false` hoặc graph+retrieval fallback. Lý do là readiness không phụ thuộc hoàn toàn vào LLM. 