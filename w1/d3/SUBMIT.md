# Screenshot architecture diagram
![Architecture](/w1/d3/architecture.png)


# Bảng cost estimate (copy từ output cost_model.py)
|Tier       | Build           | Buy Datadog (SaaS)|
| :--- | :--- | :--- |
|Small      | $232.54         | $462.0         |
|Medium     | $2325.4         | $4620.0        |
|Large      | $23254.0        | $46200.0 |

## So sánh build vs buy (Datadog SaaS) cho mỗi tier
- Ở Tier 1: Chi phí build chỉ khoảng $232.54, trong khi chi phí buy Datadog SaaS lên đến $462.0 
- Ở Tier 1: Chi phí build chỉ khoảng $2325.4, trong khi chi phí buy Datadog SaaS lên đến $4620.0 
- Ở Tier 1: Chi phí build chỉ khoảng $23254.0, trong khi chi phí buy Datadog SaaS lên đến $46200.0 
- Ở tất cả các tier, theo như tính toán thì cost buy Datadog SaaS có chi phí gần gấp đôi so với tự build

# Tóm tắt ADR decision
## Context
Currently our microservices application generates a massive volume of user behaviour logs (user clicks, login events, checkout history, view products, etc). These logs are pushed directly from application to PostgreSQL database. PostgreSQL is overwhelmed during peak traffic spikes by the high volume of concurrent write operations. As a result, this overwhelming cause connection pool exhaustion, spikes write query latency up to 8000ms and drops approximately 8% of incoming user logs due to  connection timeouts

## Decision
Introduce Kafka cluster between the application microservices and PostgreSQL. The application push user logs asynchronously to Kafka topics. Then, a dedicated consumer service consumes logs from Kafka and write to PostgreSQL at a controlled rate.

## Consequences
- PostgreSQL no longer drops user behaviour logs (replay from Kafka if backpressure). Kafka persists incoming events to disk, guaranteeing the durability of data. 
- Prevents pool connection exhaustion: the consumer service control write operation rate under processing capacity of PostgreSQL
- Latency: Application write latency drops from 8000ms to <5ms. +20ms end-to-end (acceptable for batch-log processing)
- Increases infrastructure cost by $599.25/month for the Kafka cluster with 3 brokers, 24GiB memory, 6vCPUs and 150GiB disk. On top of that, using Kafka requires additional operational to monitor partition offsets and broker health.

## Alternatives Considered
1. Scale PostgreSQL via Sharding/Replication - read-replicas do not solve write bottlenecks, and multi-master sharding introduces extreme architectural complexity and high licensing/infrastructure costs.
2. Direct push with rate limiting - risks data loss
3. In-Memory Redis Buffer - Redis stores data in RAM => risks running out of memory (OOM) or losing buffered logs during a sudden crash


# Reflection: nếu bạn được hire làm Platform Engineer cho startup 50-service vừa raise Series A, bạn sẽ recommend build hay buy? Tại sao?

Sau khi vừa raise Series A, Startup nên buy thay vì tự build

Sau Series A, startup cần tập trung 100% nguồn lực kỹ thuật để chứng minh sản phẩm đáp ứng được nhu cầu thị trường, hoàn thiện tính năng core của 50 services nhằm đáp ứng được người tiêu dùng và chiếm được thị phần tiêu dùng. Nếu chọn tự Build, đội ngũ phần mềm sẽ phải tốn từ 3-6 tháng để có được first value. Việc mua SaaS giúp hệ thống có ngay khả năng observability chỉ sau vài giờ tích hợp và có được first value chỉ sau 1-2 tuần.

Về khó khăn trong lúc vận hành: Sau giai đoạn raise Series A, startup chưa có đội ngũ Platform đủ kinh nghiệm để vận hành toàn bộ stack vì để tự build thì cần 2-3 SRE. Việc mua SaaS giúp giải gánh nặng vận hành. 