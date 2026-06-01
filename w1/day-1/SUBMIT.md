# Screenshots: plot kết quả anomaly detection (2 detector)
## Detector 1: 
![alt text](/w1/day-1/images/detector-1.png)


## Detector 2: 
![alt text](/w1/day-1/images/detector-2.png)

## Bảng so sánh precision/recall
![alt text](/w1/day-1/images/comparison-pre-recall.png)


# Log: output khi tune contamination

Contamination = 0.01
- Số anomaly phát hiện: 73
- Precision: 0.1370
- Recall:    0.0549
- F1-Score:  0.0784

Contamination = 0.02
- Số anomaly phát hiện: 145
- Precision: 0.1241
- Recall:    0.0989
- F1-Score:  0.1101

Contamination = 0.05
- Số anomaly phát hiện: 363
- Precision: 0.1956
- Recall:    0.3901
- F1-Score:  0.2606


# Model artifacts: file .pkl hoặc .joblib của Isolation Forest đã train (nhỏ, < 1MB)
Sử dụng thư viện joblist để lưu model Isolation Forest đã train với contamination=0.02 do recall = 1 (cao hơn so với của contamination = 0.01) và số lượng detect không nhiều như contamination = 0.03

File joblist: [isolation_forest_model](/w1/day-1/isolation_forest_model.joblib)

# Reflection: data thuộc loại gì, chọn method nào, tại sao, detector nào tốt hơn, trade-off, production choice
## Data
Data  ambient_temperature_system_failure.csv thuộc loại data có tính Seasonal với Period = 24h dựa trên phân tích đồ thị ACF.

## Method
Detector 1: Chọn STL Decomposition và 3σ vì thuật toán Loess của STL cho phép lấy ra thành phần theo chu kỳ 24h, giúp loại bỏ biến động tăng giảm nhiệt độ có tính chu kỳ diễn ra vào ngày và đêm. Phần residual thu được sau đó sẽ được dùng với 3σ để tìm ra anomaly

Detector 2: Sử dụng Isolation Forest để xử lý dữ liệu với các features với lag, rolling mean, rate of change,... Mô hình này không phụ thuộc vào giả định data distribution theo Gaussian và phù hợp trong việc cô lập các điểm anomaly nằm ở các vùng có density thấp


# Knowledge Check
![knowledge Check 1](/w1/day-1/images/kc-1.jpg)
![knowledge Check 2](/w1/day-1/images/kc-2.jpg)
![knowledge Check 3](/w1/day-1/images/kc-3.jpg)
![knowledge Check 4](/w1/day-1/images/kc-4.jpg)
![knowledge Check 5](/w1/day-1/images/kc-5.jpg)