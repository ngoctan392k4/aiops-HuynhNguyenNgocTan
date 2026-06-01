# Screenshots: plot kết quả anomaly detection (2 detector)
## Detector 1: 
![alt text](/w1/day-1/images/detector-1.png)


## Detector 2: 
![alt text](/w1/day-1/images/detector-2.png)

# Log: output khi tune contamination
![alt text](/w1/day-1/images/output-tune.png)


# Reflection: data thuộc loại gì, chọn method nào, tại sao, detector nào tốt hơn, trade-off, production choice
## Data
Data  ambient_temperature_system_failure.csv thuộc loại data có tính Seasonal với Period = 24h dựa trên phân tích đồ thị ACF.

## Method
Detector 1: Chọn STL Decomposition và 3σ vì thuật toán Loess của STL cho phép lấy ra thành phần theo chu kỳ 24h, giúp loại bỏ biến động tăng giảm nhiệt độ có tính chu kỳ diễn ra vào ngày và đêm. Phần residual thu được sau đó sẽ được dùng với $3\sigma$ để tìm ra anomaly

Detector 2: Sử dụng Isolation Forest để xử lý dữ liệu với các features với lag, rolling mean, rate of change,... Mô hình này không phụ thuộc vào giả định data distribution theo Gaussian và phù hợp trong việc cô lập các điểm anomaly nằm ở các vùng có density thấp


# Knowledge Check
![knowledge Check 1](/w1/day-1/images/kc-1.jpg)
![knowledge Check 2](/w1/day-1/images/kc-2.jpg)
![knowledge Check 3](/w1/day-1/images/kc-3.jpg)