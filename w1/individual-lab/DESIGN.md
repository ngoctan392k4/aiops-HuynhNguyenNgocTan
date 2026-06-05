# Detection Approach — DESIGN.md

## Approach tôi dùng
Pipeline sử dụng rolling 3 sigma và quét dấu hiệu của logs nếu có lỗi thì lưu alert ngay 

## Tại sao chọn approach này
- Bộ data của ShopX mô phỏng lượng request như thực tế. Nếu áp dụng static threshold, hệ thống nhận False Alarm vào giờ cao điểm hoặc bỏ sót lỗi vào giờ thấp điểm. Rolling Mean và Rolling Std cho phép dải an toàn tự roll theo sát xu hướng thực tế của hệ thống.
- Việc tính độ lệch chuẩn sigma theo thời gian thực trên một window data giúp tự định lượng được biên độ nhiễu Gaussian của bộ data, loại bỏ việc on-call engineer phải phán đoán hoặc "hardcode".
- Phân tích Log có độ trễ bằng O(1). Các event lỗi lớn như Circuit Breaker Open sẽ kích hoạt cảnh báo ngay tại giây đầu tiên xuất hiện lỗi, tối ưu thời gian để xử lý sự cố

## Cách hoạt động
1. Giám sát logs: Đọc nhanh mảng logs. Nếu chứa mẫu chuỗi đặc trưng lỗi của hệ thống hạ tầng (`OutOfMemoryWarning`, `Circuit breaker OPEN`, `server overloaded`), pipeline sẽ xác định ngay lập tức loại Fault và kích hoạt ghi Alert.
2. Rolling 3 sigma với metrics: Metrics (`http_requests_per_sec`, `upstream_timeout_rate`, `memory_usage_bytes`) sẽ được đẩy vào các hàng đợi (`deque` kích thước 40). Thuật toán liên tục tính toán giá trị Rolling mean và Rolling std. Nếu giá trị hiện tại vượt mức Mean + 3 * sigma thì xác định đó là anomaly. Kết hợp logic đối chiếu chéo đa biến giữa các chỉ số liên đới (như RPS đi kèm Queue Depth), Alert sẽ được kích hoạt an toàn.

## Parameters tôi chọn
- `WINDOW_SIZE = 40`: Rolling window đủ lớn để thuật toán học và tích lũy được phân phối chuẩn của baseline hệ thống một cách ổn định, nhưng cũng đủ ngắn để không giữ lại quá lâu các điểm dữ liệu lỗi, tránh làm ô nhiễm window.
- `Z score > 3.0`: Dựa theo quy tắc của Gaussian, khoảng biến thiên +/- 3 sigma có thể cover được 99.7% không gian dữ liệu bình thường. Bất kỳ giá trị nào vượt mốc này được xem là anomaly.

## Cải thiện nếu có thêm thời gian
- Áp dụng phương pháp EWMA để đặt trọng số giảm dần theo lũy thừa cho các điểm dữ liệu quá khứ, giúp dải an toàn thích ứng nhanh hơn với các dữ liệu theo trend.
- Áp dụng phương pháp Isolation Forest để phát hiện anomaly dựa trên multivariate