# gap_sec được chọn và lý do chọn


# max_hop được chọn và lý do chọn


# 1 alert ID đã bị “miss” (không match cluster nào) - tại sao?


# Nếu có 10000 alert thay vì 200, code sẽ chậm ở đâu?


# EOD Checkpoint
## 1. Vì sao fingerprint cho dedup không include timestamp hay value? Cho ví dụ nếu include thì hệ thống behave ra sao.


## 2. Sự khác biệt giữa “duplicate” và “correlated” alert là gì? Ví dụ cụ thể từ lab dataset.



## 3. gap_sec = 30 (rất ngắn) vs gap_sec = 600 (rất dài) - mỗi cái sẽ ảnh hưởng output thế nào? 1 dòng cho mỗi case.



## 4. Trong scenario chính (payment-svc pool exhaustion), recommender-svc cũng alert (batch retrain). Correlator của bạn có gom recommender vào cluster chính không? Vì sao có / không?



## 5. Limitation lớn nhất của topology grouping mà bạn nhận ra? Suggest 1 cách khắc phục.


