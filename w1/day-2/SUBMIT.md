# Screenshots: plot template count time series, anomaly highlighted
![plot template count time series](/w1/day-2/images/ts_anomaly_plot.png)

# Log: output Drain3 (số template, top-10), tuning log (sim_th values + kết quả)
## Số lượng template
![alt text](/w1/day-2/images/num_temp.png)

## Top 10
![alt text](/w1/day-2/images/top10.png)

## Tuning log
![alt text](/w1/day-2/images/tuning.png)

# Chạy script trên 2 dataset
![alt text](/w1/day-2/images/dataset2.png)

- Dataset Spark có nhiều templates hơn (Spark có 27 templates trong khi HDFS có 17 templates)

# Reflection: Drain3 parse tốt không, template nào cho insight, metric vs log khác gì
## Drain3 parse tốt không
- Tốc độ xử lý thời gian thực cực nhanh, tốn ít tài nguyên RAM.
- Gom nhóm templates chính xác dựa trên Fixed-depth tree
- Tách dynamic parameter như IP, Block ID, Byte thành `<*>`

- Tuy nhiên nó phụ thuộc nhiều vào việc tinh chỉnh `drain_sim_th` và `drain_depth`
- Drain3 chỉ so sánh vị trí từ khóa, không hiểu ngữ nghĩa của các từ đồng nghĩa

## Các Template mang lại Insight
### Anomaly về disk
- Các template: [7], [9], [10], [14], [15].
- Các log ghi nhận hành vi Deleting block hoặc thông báo ngoại lệ exception while serving. Trong một hệ thống phân tán, khi số lượng log yêu cầu xóa hoặc báo lỗi ổ đĩa Spike, tức là các DataNode đang gặp sự cố hỏng hóc phần cứng hoặc mất đồng bộ tệp tin vật lý, giúp kỹ sư cô lập node lỗi để thay thế phần cứng kịp thời.

### Luồng ghi và nhân bản dữ liệu
- Các template: [4], [11], [16].
- Log ghi nhận quá trình Receiving block hoặc yêu cầu ask to replicate. Khi hệ thống xuất hiện các template này dưới dạng New Template hoặc Spike, tức là hệ thống đang phải chịu một tải trọng ghi dữ liệu rất lớn từ người dùng, hoặc NameNode phát hiện một số DataNode bị sập nên phải kích hoạt cơ chế nhân bản khẩn cấp để đảm bảo tính an toàn dữ liệu.

### Kiểm tra tính toàn vẹn của dữ liệu
- Các template: [6].
- Log chạy định kỳ của DataBlockScanner để xác thực cấu trúc dữ liệu (Verification succeeded). Nếu trong một khung giờ hệ thống báo lỗi nhưng tần suất xuất hiện của template này tụt giảm nghiêm trọng hoặc biến mất hoàn toàn, điều đó báo hiệu tiến trình quét kiểm tra ngầm đang bị treo hoặc quá tải CPU, đe dọa đến khả năng phát hiện sớm các khối dữ liệu bị hỏng (corrupted blocks).

## Phân biệt Metric vs Log
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