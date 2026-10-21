# Kết quả chạy dự án

Bộ kết quả hiện tại: [runs/current](runs/current/).

- [Biểu đồ](runs/current/figures/): so sánh RMSE và dự báo từng thành phố ở +1, +3, +6 giờ.
- [Metrics](runs/current/metrics.csv): đánh giá trên tập test.
- [Dự đoán](runs/current/latest_forecasts.csv): 12 dự đoán từ mốc lịch sử cuối dữ liệu.
- `models/`, `manifest.json`, `selection.json`: model và thông tin tái lập trong mỗi run.

Các kết quả cũ đã được xóa. Chạy `main.py --skip-fetch` sẽ tạo run mới riêng,
không ghi đè `current`. Xem README ở gốc repo để biết lệnh chạy đầy đủ.
