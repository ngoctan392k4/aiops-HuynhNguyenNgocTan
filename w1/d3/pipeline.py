import sys
import os
import time
import queue
import threading
import pandas as pd


INPUT_FILE = "realKnownCause/machine_temperature_system_failure.csv"
OUTPUT_FILE = "features.parquet"

# Tạo queue => Fake vai trò của Kafka 
stream_queue = queue.Queue(maxsize=1000)

# Xác nhận producer xử lý xong
processing_complete = threading.Event()


def mock_producer(file_path):
    if not os.path.exists(file_path):
        print(f"File không tồn tại: {file_path}")
        processing_complete.set()
        return

    # Đọc CSV
    df = pd.read_csv(file_path)
    
    for idx, row in df.iterrows():
        event = row.to_dict()
        stream_queue.put(event, block=True)

    processing_complete.set()


def mock_consumer(window_size=12):
    
    # Lưu Features
    processed_features = []
    
    # Buffer với 12 event (do window = 12) gần nhất để tính toán
    window_buffer = []

    while True:
        try:
            event = stream_queue.get(block=True, timeout=1)
            
            # Add vào window buffer
            window_buffer.append(event)
            
            df_window = pd.DataFrame(window_buffer)
            df_window['value'] = pd.to_numeric(df_window['value'])
            
            current_record = event.copy()
            
            # Tính Rolling mean
            current_record['rolling_mean'] = df_window['value'].mean()
            
            # Tính Rolling std
            # ddof=0 để tránh lỗi NaN khi window chỉ mới có 1 event đầu tiền
            current_record['rolling_std'] = df_window['value'].std(ddof=0)
            
            # Tính Rate of change
            if len(window_buffer) >= 2:
                prev_value = window_buffer[-2]['value']
                current_record['rate_of_change'] = current_record['value'] - prev_value
            else:
                current_record['rate_of_change'] = 0.0

            # Lưu các features đã tính
            processed_features.append(current_record)
            
            # Sau khi xử lý nếu window buffer >= size (12) thì xóa cái cũ nhất để rolling tới cái tiếp theo
            if len(window_buffer) >= window_size:
                window_buffer.pop(0)
                
            
            stream_queue.task_done()

        except queue.Empty:
            # Nếu queue rỗng và producer xác nhận xong thì thoát
            if processing_complete.is_set():
                break

    # Lưu vào file parquet
    if processed_features:
        df_output = pd.DataFrame(processed_features)
        
        column_order = ['timestamp', 'value', 'rolling_mean', 'rolling_std', 'rate_of_change']
        df_output = df_output[column_order]
        
        df_output.to_parquet(OUTPUT_FILE, index=False)
        
        print(f"Đã lưu file thành công: '{OUTPUT_FILE}'")
    else:
        print("Không có features nào được extract từ data")


if __name__ == "__main__":
    start_time = time.time()

    # Tạo thread cho producer
    producer_thread = threading.Thread(target=mock_producer, args=(INPUT_FILE,))
    producer_thread.daemon = True
    producer_thread.start()

    # Chạy consumer tại luồng chính (không tạo thread riêng)
    mock_consumer(window_size=12)

    # Kết thúc producer thread
    producer_thread.join()
    
    execution_time = time.time() - start_time
    print(f"Processing time: {execution_time:.2f} giây")