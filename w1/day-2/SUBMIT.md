# Screenshots: plot template count time series, anomaly highlighted
![plot template count time series](/w1/day-2/images/ts_anomaly_plot.png)

# Log: output Drain3 (số template, top-10), tuning log (sim_th values + kết quả)
## Số lượng template
![alt text](/w1/day-2/images/num_temp.png)

## Top 10
![alt text](/w1/day-2/images/top10.png)

## Tuning log
![alt text](/w1/day-2/images/tuning.png)

# Reflection: Drain3 parse tốt không, template nào cho insight, metric vs log khác gì
## Drain3 parse tốt không
- Tốc độ xử lý thời gian thực cực nhanh, tốn ít tài nguyên RAM.
- Gom nhóm templates chính xác dựa trên Fixed-depth tree
- Tách dynamic parameter như IP, Block ID, Byte thành `<*>`

- Tuy nhiên nó phụ thuộc nhiều vào việc tinh chỉnh `drain_sim_th` và `drain_depth`
- Drain3 chỉ so sánh vị trí từ khóa, không hiểu ngữ nghĩa của các từ đồng nghĩa

## Các Template mang lại Insight


## 3. Phân biệt Metric vs Log
- Metric
  - Định kỳ thu thập thống kê như CPU %, RAM Free, Request/s
  - Dung lượng rất nhỏ, lưu trữ lâu dài
  - Giúp phát hiện tổng quan hệ thống có đang bất ổn hay không.
- Logs
  - Là text mô tả chi tiết một sự kiện vừa xảy ra trong hệ thống
  - Cấu trúc phức tạp, dung lượng cực kỳ lớn
  - Logs cho biết nguyên nhân gốc rễ của các vấn đề 


# Knowledge Check
![knowledge Check 1](/w1/day-2/images/kc-1.jpg)
![knowledge Check 2](/w1/day-2/images/kc-2.jpg)
![knowledge Check 3](/w1/day-2/images/kc-3.jpg)
![knowledge Check 4](/w1/day-2/images/kc-4.jpg)
![knowledge Check 5](/w1/day-2/images/kc-5.jpg)