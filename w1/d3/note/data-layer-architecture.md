# Data Layer Architecture + Observability Pipeline
Detect anomaly trên metric (D1) và mine pattern từ log (D2) đều giả định data đã có sẵn - pd.read_csv("metric.csv") hoặc open("hdfs.log"). Trong thực tế, data không tự nhiên có. Hệ thống production có 100-1000 service, mỗi service emit metric mỗi 15 giây, log mỗi event, trace mỗi request. Tổng lại: hàng triệu data points / phút, hàng terabyte / ngày.

Data đó chảy về đâu? Lưu thế nào? Query ra sao? Đây là “data layer” - backbone của mọi AIOps system. Anomaly detector của bạn chỉ tốt như data layer đứng sau nó. Pipeline chậm 5 phút → TTD chậm 5 phút. Storage đắt → không lưu được lâu → ML model không có training data.

Nội dung liên quan đến architecture: vẽ được sơ đồ data layer cho 1 hệ thống thật, hiểu trade-off giữa các lựa chọn, và viết được ADR (Architecture Decision Record) cho 1 quyết định lớn.

## 1. Three Pillars of Observability
Khái niệm “observability” được Google SRE Book và Charity Majors (Honeycomb) phổ biến từ 2017-2018. Nó dựa trên 3 trụ cột - mỗi cái trả lời 1 câu hỏi khác nhau về hệ thống:

### Metric - “Cái gì đang sai?”
Định nghĩa: Số (number) theo thời gian. Mỗi data point gồm: timestamp, name (cpu_usage), value (75.2), labels (host=server-1, service=payment).

Ví dụ:
```shell
2024-01-15T10:23:45Z  cpu_usage  75.2  {host="srv-1", service="payment"}
2024-01-15T10:23:46Z  cpu_usage  76.8  {host="srv-1", service="payment"}
2024-01-15T10:23:45Z  latency_p99  234  {service="payment", endpoint="/checkout"}
```

Đặc điểm:

- Nhẹ: 1 data point ~50-100 bytes
- Aggregatable: tính được mean, sum, percentile dễ dàng
- Lưu được lâu (30+ ngày) với cost thấp
- Query nhanh (Prometheus có thể query 1M data points trong < 1 giây)

Giới hạn: Metric cho biết “CPU 75%” nhưng không cho biết “tại sao”. Không có context per-request.

Tools production: Prometheus (open-source, de-facto standard cho K8s), CloudWatch (AWS), Datadog (SaaS), VictoriaMetrics (Prometheus-compatible nhưng scale hơn).

### Log - “Tại sao sai?”
Định nghĩa: Text records, mỗi record là 1 event xảy ra trong hệ thống.

Ví dụ structured (JSON):
```json
{"ts":"2024-01-15T10:23:45.123Z","level":"ERROR","service":"payment","msg":"Connection timeout","host":"db-primary","port":5432,"timeout_ms":30000,"order_id":"ORD-8834"}
```

Đặc điểm:

- Chi tiết: chứa context đầy đủ (order ID, user ID, stack trace)
- Nặng: 1 service có thể gen 10GB/ngày
- Query phức tạp: cần full-text search hoặc parsing trước
- Storage cost cao

Giới hạn: Search trên TB data chậm và đắt. Bạn không thể grep 1TB log mỗi lần investigate.

Tools: ELK Stack (Elasticsearch + Logstash + Kibana - mạnh nhưng đắt), Loki (Grafana - chỉ index labels, rẻ hơn 10x), Splunk (enterprise - $150-200/GB ingested), ClickHouse (column-oriented, fast aggregation).

### Trace - “Ở đâu trong hệ thống?”
Định nghĩa: Record của 1 request đi qua hệ thống. Mỗi trace gồm nhiều span - mỗi span là 1 operation tại 1 service.

Ví dụ:
```shell
Trace ID: abc-123
├─ [api-gateway] /checkout                        250ms total
│  ├─ [auth-service] validateToken                 12ms
│  ├─ [cart-service] getCart                       45ms
│  ├─ [payment-service] processPayment             180ms ← SLOW
│  │  ├─ [db-primary] SELECT ... FROM accounts     5ms
│  │  └─ [external-api] stripe.createCharge        170ms ← BOTTLENECK
│  └─ [notification-service] sendEmail             8ms
```

Đặc điểm:

- Show path: request đi đâu, qua service nào, mất bao lâu mỗi service
- Bottleneck identification: nhìn 1 trace biết ngay service nào chậm
- Heavy: 1 trace có thể có 50-100 spans → storage cost cao

Giới hạn: Không thể trace 100% request ở scale lớn → cần sampling (1%, 0.1%, hoặc tail-based). Sampled data → có thể miss anomaly hiếm.

Tools: Jaeger (CNCF, open-source), Zipkin (Twitter open-source), AWS X-Ray, Datadog APM, OpenTelemetry (chuẩn vendor-neutral, đang chiếm lĩnh thị trường).

So sánh nhanh
| Pillar | Trả lời | Volume | Cost | Query speed |
| :--- | :--- | :--- | :--- | :--- |
| **Metric** | Cái gì sai? | Nhỏ (~50B/point) | Thấp | Rất nhanh |
| **Log** | Tại sao? | Lớn (1-10TB/day) | Cao | Chậm trên big data |
| **Trace** | Ở đâu trong system? | Trung bình (sampled) | Trung bình | Trung bình |

Trong AIOps: Cần cả 3. Metric anomaly trigger → drill xuống trace tìm slow service → đọc log của service đó tìm exact error. Đây là pattern `“metric → trace → log”` mà mọi production debugging follow.

## 2. Pipeline Architecture - Data Đi Từ Đâu Đến Đâu
Data observability đi qua 5 stage:
```plaintext
[Service] → [Collection] → [Transport] → [Processing] → [Storage] → [Query/AI]
```

### 2.1 Collection - Lấy Data Từ Service
Vấn đề: Mỗi service produce metric/log/trace theo cách khác nhau. Service Java dùng Logback, Node.js dùng Winston, Go có structured logging riêng. Metric thì có Prometheus client, StatsD, custom HTTP endpoint… Không có chuẩn chung.

Giải pháp: `OpenTelemetry (OTel) - CNCF standard`. 1 SDK cho cả 3 pillars, vendor-neutral, hỗ trợ mọi ngôn ngữ. Service code instrument 1 lần với OTel SDK → output đi đến bất kỳ backend nào (Prometheus, Jaeger, Datadog, custom).

OTel có 2 component chính:

- SDK: thư viện embedded trong service (Java, Python, Go, Node.js, …). Service code emit telemetry qua SDK.
- Collector: standalone process nhận data từ SDK, transform, forward tới backend. Chạy như sidecar (mỗi pod K8s 1 collector), DaemonSet (mỗi node 1 collector), hoặc gateway (cluster có 1 cluster collector).

Architecture choice - Agent layer:

| Tool | Đặc điểm | Khi nào dùng |
| :--- | :--- | :--- |
| **Fluent Bit** | C, ~450KB memory, lightweight | Kubernetes DaemonSet, IoT, edge |
| **Fluentd** | Ruby, 700+ plugins, mạnh hơn | Aggregator layer, complex routing |
| **Vector** | Rust, performance cao 5-10x Fluentd | High-throughput pipeline (Datadog backed) |
| **OTel Collector** | Unified cho metric+log+trace | Modern stack, multi-signal |

Ref: Schipper et al., “A Benchmark for Log Data Processing Pipelines” (ICPE 2024) - benchmark Fluent Bit vs Fluentd vs Vector trên throughput, latency, resource usage.

## 2.2 Transport - Buffer Giữa Producer và Consumer
Vấn đề: 1000 service push telemetry data thẳng vào storage (Prometheus, Elasticsearch). DB không xử lý kịp → crash hoặc drop data.

Giải pháp: Message Queue giữa collection layer và processing layer.

Kafka là choice phổ biến nhất:

- Throughput cực cao: 1M+ messages/giây trên cluster nhỏ
- Persist data: nếu downstream chết, data không mất, replay được khi recover
- Decouple: producer không cần biết consumer là ai
- Multi-consumer: nhiều system cùng đọc 1 stream (1 stream feed Elasticsearch + S3 + ML pipeline đồng thời)

Khi nào KHÔNG cần Kafka:

- Service < 10, throughput thấp → Kafka là overkill
- Latency-critical (< 10ms end-to-end) → Kafka adds ~5-20ms latency
- Đã có managed service như Kinesis/Pub-Sub thì dùng luôn

Alternative: NATS (lightweight, không persist by default), Pulsar (Kafka-like nhưng multi-tenant tốt hơn), Kinesis (AWS managed), Google Pub/Sub.

Trade-off Kafka vs direct push:

| Aspect | Direct push | Via Kafka |
| :--- | :--- | :--- |
| **Latency** | Lower (< 50ms) | Higher (+5-20ms) |
| **Reliability** | Lost data nếu storage down | Replay được |
| **Scaling** | Producer limit by storage speed | Producer scale independently |
| **Cost** | Thấp | Cao (Kafka cluster cost) |
| **Operational complexity** | Đơn giản | Phức tạp (broker, ZooKeeper/KRaft, monitoring) |

Operational complexity	Đơn giản	Phức tạp (broker, ZooKeeper/KRaft, monitoring)

### 2.3 Processing - Transform & Enrich
Trước khi store, data thường cần xử lý:

- Parse: log text → structured fields (Drain3 từ D2)
- Enrich: thêm context (geo từ IP, service metadata, user info)
- Filter: bỏ data thừa (health check log)
- Aggregate: rollup metric từ second-level → minute-level cho long-term storage
- Compute features: rolling mean, rate of change cho ML model

Stream processing engines:

| Engine | Best for |
| :--- | :--- |
| **Flink** | Stateful streaming, exactly-once semantics, complex windowing - production-grade for AIOps |
| **Spark Streaming** | Mature, integrates với existing Spark batch |
| **Kafka Streams** | Java/Scala only, embedded library, tight integration với Kafka |
| **Materialize / RisingWave** | SQL-on-streams, dễ dùng cho team không có streaming expertise |

Khi nào cần stream processing thật:

- Real-time feature engineering cho ML (anomaly detection trên rolling window)
- Stream-stream join (correlate metric anomaly với log spike)
- Complex event processing (detect sequence: A → B → C trong 60s)

Khi nào KHÔNG cần:

- Chỉ aggregate đơn giản → làm ở storage layer (Prometheus recording rules)
- Batch acceptable (10-min latency OK) → dùng cron + script đơn giản hơn nhiều

### 2.4 Storage - Lưu Ở Đâu?
Đây là quyết định ảnh hưởng cost nhiều nhất. Mỗi loại data có storage tối ưu khác nhau:

#### Time-series Database (cho Metric):

- Prometheus: local TSDB, retention 15 ngày default, query language PromQL mạnh. Limit: single-node, không HA tốt cho long retention.
- VictoriaMetrics: Prometheus-compatible, scale tốt hơn 10x, retention nhiều tháng/năm
- InfluxDB: purpose-built TSDB, mạnh ở high cardinality
- TimescaleDB: PostgreSQL extension, SQL-friendly nhưng chậm hơn purpose-built TSDB

#### Document/Search Store (cho Log):

- Elasticsearch: full-text search mạnh, query linh hoạt nhưng đắt (RAM-heavy)
- Loki: chỉ index labels (service, level, host), không full-text. Rẻ hơn ES 10x. Query bị giới hạn - phải filter theo label trước khi grep nội dung
- ClickHouse: column-oriented, aggregate query nhanh, dùng được cho log + metric
- OpenSearch: AWS fork của Elasticsearch sau khi Elastic đổi license

#### Object Store (cho archive):

- S3 / GCS / Azure Blob: rẻ nhất ($0.023/GB/month S3 standard), nhưng query trực tiếp chậm
- Parquet on S3: columnar format, query được qua Athena/Spark/DuckDB
- S3 Glacier: rẻ hơn nữa ($0.004/GB/month) nhưng retrieve mất giờ - chỉ dùng cho compliance archive

#### Hot/Warm/Cold tiering:

```plaintext
[0-7 ngày]   Hot:  Elasticsearch / Loki         - query nhanh, đắt
[7-30 ngày]  Warm: Cheaper ES tier / VM disk    - query OK, vừa tiền  
[30-365 ngày] Cold: S3 + Parquet                - query chậm, rẻ
[>365 ngày]  Archive: S3 Glacier                - chỉ compliance, retrieve mất giờ

```

#### Cost example (banking 1TB log/day):
- All hot (ES): 30 days × 1TB × 150/GB = 4.5M/month
- Tiered: 7d ES (1M) + 23d Loki (100K) + 1y S3 (800) = 1.1M/month
- Tiering tiết kiệm 75% mà vẫn query được hot data trong 7 ngày (đủ cho most incident)

### 2.5 Query Layer - Consumer của Data
Cuối cùng, data được consume bởi:

- Dashboard: Grafana, Datadog UI, custom React dashboard
- Alerting: Prometheus Alertmanager, PagerDuty, custom rule engine
- ML Pipeline: anomaly detection model, RCA assistant, forecasting
- Ad-hoc query: Athena, Trino, ClickHouse trên cold data cho post-mortem

## 3. Feature Store - Khi Cần ML
Vấn đề: Anomaly detection model cần features (rolling mean 1h, rate of change 5min, hour-of-day, …). Training thì compute features từ batch data (Spark trên S3). Inference real-time thì cần features ở < 100ms latency. Training và inference dùng cùng features nhưng compute path khác nhau → drift.

Giải pháp: Feature Store - store features 1 lần, serve cho cả training và inference.

Architecture:
```plaintext
Stream → Compute features → Online store (Redis) → Inference
                          → Offline store (S3) → Training
```

Tools:

- Feast (open-source, CNCF) - popular, dễ deploy, support Redis/DynamoDB online + S3/BigQuery offline
- Tecton (managed, expensive) - production-grade
- Hopsworks (open-source + managed) - full ML platform

Khi nào cần feature store cho AIOps:

- Multi-model: > 5 ML model dùng chung features
- Team size: > 3 ML engineer
- Real-time inference: < 100ms latency requirement

Khi nào KHÔNG cần (overkill):

- 1 model, batch inference → Spark job đủ
- Small team - feature store thêm operational burden không cần thiết
- Early stage - start với SQL view + Redis manual đơn giản hơn

Practical advice: Hầu hết AIOps project start không có feature store. Khi nào pain point xuất hiện (training-serving drift, code duplication giữa training và inference) thì mới adopt.

## 4. Schema Registry & Data Contract
Vấn đề real-world: Service A team deploy version mới, thay đổi log format (rename field, change type). Pipeline B đang parse log của A → break. Không ai biết cho đến khi anomaly detection bắt đầu false alarm.

Giải pháp: Schema Registry

- Định nghĩa schema cho mỗi data stream (metric format, log fields, trace structure)
- Producer phải register schema trước khi push data
- Consumer validate data theo schema khi đọc
- Schema có versioning + compatibility rules (backward, forward, full)

Tools:

- Confluent Schema Registry (Avro/Protobuf/JSON Schema) - Kafka ecosystem
- AWS Glue Schema Registry - AWS native
- Apicurio - open-source

Data Contract (concept rộng hơn schema):

- Producer team commit: “log field X sẽ là string, không null, max 256 chars”
- Consumer team rely on contract đó
- Breaking change cần version bump + migration plan + deprecation period (vd: 90 ngày)

Trade-off:

- Strict contract → ít bug cross-team nhưng chậm development (mỗi schema change cần review)
- Loose contract → fast iteration nhưng integration hell ở scale

Big tech như Netflix, Uber adopt strict data contract sau khi gặp đau quá nhiều. Startup thường loose contract đến khi service count > 50.

## 5. Architecture Decision Records (ADR)
Khi build AIOps platform, bạn đưa ra hàng chục quyết định kiến trúc: Kafka vs direct push? Elasticsearch vs Loki? Build vs buy Datadog? Mỗi quyết định ảnh hưởng cost + complexity hàng năm.

ADR = lightweight document ghi lại 1 decision: context, options considered, decision made, consequences.

Format chuẩn (Michael Nygard, 2011):

```md
# ADR-001: Use Kafka for log transport

## Status
Accepted (2024-01-15)

## Context
Currently 50 services push log directly to Elasticsearch. ES is overwhelmed at peak (15K events/sec), drops ~5% events. Cost is $30K/month.

## Decision  
Introduce Kafka cluster between services and ES. Services push to Kafka, Logstash consumes from Kafka and writes to ES at controlled rate.

## Consequences
+ ES no longer drops events (replay from Kafka if backpressure)
+ Future: feature engineering pipeline can consume same Kafka stream
+ Latency: +10ms end-to-end (acceptable for log, not for real-time alerting)
+ Operational complexity: must maintain Kafka cluster (~$2K/month, 1 SRE 20% time)
+ Cost: +$2.5K/month total (Kafka + cluster ops), -$3K/month from reduced ES drops cost

## Alternatives considered
1. Scale ES horizontally - more expensive ($50K/month for 3x throughput)
2. Direct push with rate limiting - risks data loss
3. Vector aggregator without Kafka - no replay capability


```

Khi nào viết ADR:

- Quyết định affect > 1 team
- Reversal cost cao (> 1 tháng work để undo)
- Decision sẽ bị question lại trong 6-12 tháng tới (“tại sao mình dùng X?”)

Khi không cần:

- Minor changes (tool version bump, refactor)
- Spike/POC

Reference: [Michael Nygard’s original ADR post (2011)](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions) - 1 trang, vẫn relevant.

## 6. Cost Model
Mỗi component trong data layer có cost driver khác nhau:

| Component | Cost driver | Tối ưu cost |
| :--- | :--- | :--- |
| **Storage** | GB stored × retention days | Tier: hot/warm/cold; downsample old data |
| **Ingest** | Events/sec or GB/day | Filter at source; sampling; reduce log verbosity |
| **Egress** | GB transferred cross-AZ/region | Co-locate; cache at edge |
| **Compute** | CPU-hours / RAM-hours | Right-size; spot instances; auto-scale down off-peak |
| **GPU (ML)** | GPU-hours | Batch inference; quantize model; CPU when latency allows |

Sample cost cho 100-service org:

| Item | Volume | Cost/month |
| :--- | :--- | :--- |
| **Metric (Prometheus + VictoriaMetrics)** | 1M datapoints/sec, 30d retention | $2,000 |
| **Log (Loki + S3)** | 500GB/day | $4,500 |
| **Trace (Jaeger, 1% sampling)** | 10M traces/day | $1,500 |
| **Kafka cluster (3 brokers)** | 100K msg/sec | $2,500 |
| **Compute (Flink processing)** | 16 cores, 64GB RAM | $1,200 |
| **Total** | | ~$11.7K/month |

Nếu dùng SaaS (Datadog): cùng workload ~$30-50K/month, nhưng team không tốn time vận hành stack → trade-off cost vs people time.

Build vs Buy framework:

| Factor | Build (self-host) | Buy (Datadog/New Relic) |
| :--- | :--- | :--- |
| **Cost** | $10-15K/month infra | $30-50K/month subscription |
| **Team need** | 2-3 SRE để vận hành stack | 0 dedicated |
| **Customization** | Unlimited | Limited to vendor features |
| **Time to first value** | 3-6 tháng | 1-2 tuần |
| **Lock-in** | Low (open-source) | High |

Most company < 500 engineers → buy (Datadog) hợp lý hơn. Big tech > 1000 engineers → build vì scale economics + customization needs.

## References
- [Google SRE Book Ch.6](https://sre.google/sre-book/monitoring-distributed-systems/) - Monitoring Distributed Systems, định nghĩa Four Golden Signals
- [Peter Bourgon - Metrics, Tracing, Logging (2017)](https://peter.bourgon.org/blog/2017/02/21/metrics-tracing-and-logging.html) - defining post về 3 pillars
- [OpenTelemetry docs](https://opentelemetry.io/docs/) - CNCF standard cho observability
- [Honeycomb - What is Observability?](honeycomb.io/what-is-observability) - Charity Majors định nghĩa modern observability
- [Confluent - Schema Registry](https://docs.confluent.io/platform/current/schema-registry/) - schema management cho Kafka
- [Michael Nygard - ADR (2011)](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions) - paper gốc về ADR
- [Datadog Watchdog Architecture](https://www.datadoghq.com/blog/watchdog/) - production AIOps platform tham khảo
- [Netflix Atlas](https://netflixtechblog.com/introducing-atlas-netflixs-primary-telemetry-platform-bd31f4d8ed9a) - Netflix’s custom metric platform, scale lessons