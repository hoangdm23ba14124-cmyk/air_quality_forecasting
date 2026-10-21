# Dự báo PM2.5 sau 1, 3 và 6 giờ

So sánh **Simple Linear, Multiple Linear, Polynomial và Ridge Regression** trên
bốn thành phố: Hà Nội, Hải Phòng, Đà Nẵng, TP. Hồ Chí Minh. Mỗi phương pháp học
một mô hình riêng cho từng horizon; persistence là mốc so sánh bắt buộc.

## Bài toán và thời điểm phát hành

Tại thời điểm `t`, **sau khi dữ liệu của giờ t đã sẵn có**, dự đoán nồng độ PM2.5
ở `t+1`, `t+3`, `t+6` (µg/m³). Đây là nồng độ tại từng giờ, không phải trung bình
trong khoảng h giờ. Nếu dữ liệu thực tế có độ trễ, cần lùi giờ quan sát cuối cùng
hoặc bổ sung kiểm soát thời điểm dữ liệu được phát hành trước khi triển khai.

- Features dùng PM2.5 và khí tượng tới t, lịch thời gian, lag và rolling theo thành phố.
- Cột feature được khai báo rõ; target tương lai không thể tự lọt vào đầu vào.
- Không nội suy/backfill; giờ thiếu được giữ NaN. Không chấm điểm target tự điền.
- Rolling có thể chứa PM2.5(t), vì quy ước giờ t đã quan sát xong.
- Baseline cho mọi horizon là PM2.5(t), cùng thành phố.
- Không dùng khí tượng thực tế tại t+h, cũng không dùng PM2.5 thật ở các giờ trung gian
  để tạo dự báo h giờ phát hành tại t.
- Backtest là chuỗi dự báo phát hành mỗi giờ, dùng lịch sử mới quan sát được mỗi giờ;
  không phải dự báo một lần cho toàn bộ đoạn test.

## Mô hình

| Phương pháp | Đầu vào / cách fit |
|---|---|
| Persistence | PM2.5(t), không fit hệ số |
| Simple Linear | Một biến PM2.5(t), có intercept |
| Multiple Linear | Danh sách đặc trưng cố định, OLS |
| Polynomial | Mở rộng 4 biến số trong config thành bậc 2 hoặc 3; giữ các biến còn lại tuyến tính |
| Ridge | Cùng đặc trưng với Multiple Linear; phạt L2, không phạt intercept |

OLS và biến đổi đa thức dùng scikit-learn. Ridge tự triển khai bằng NumPy:

`L = mean((Xw + b - y)^2) + alpha * sum(w^2)`

Thí nghiệm dùng nghiệm trực tiếp để không lẫn sai số hội tụ với tác dụng regularization.
Gradient descent vẫn có trong `src/models/ridge_regression.py` để học và đối chiếu bằng test.
Với quy ước tổng bình phương của sklearn Ridge, `alpha_sklearn = n_train * alpha_here`.
`alpha=0` là hồi quy không phạt L2, không bị CLI bỏ qua.

Không scale target: đầu ra trực tiếp là µg/m³. Scaler của các features và phép mở
rộng polynomial được fit trong từng fold train, sau đó áp dụng nguyên trạng vào validation.

## Cài đặt và chạy

Python 3.10+; chạy các lệnh sau từ thư mục ngoài cùng của repo:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r air_quality_forecasting/requirements.txt
python air_quality_forecasting/main.py --skip-fetch
```

Mặc định sử dụng snapshot API trong `data/raw_api/` (có `source.json` và SHA-256) và tạo một thư mục run mới theo thời gian.
Đường dẫn mặc định được xác định theo vị trí mã nguồn, không phụ thuộc thư mục đang đứng.

Chạy với thư mục đầu ra cụ thể (đường dẫn do bạn cung cấp được tính từ thư mục đang đứng):

```bash
python air_quality_forecasting/main.py --skip-fetch --run-dir air_quality_forecasting/outputs/runs/my_experiment
```

Không ghi đè run đã tồn tại. Để tải mới dữ liệu **chưa được code nội suy**:

```bash
python air_quality_forecasting/main.py --fetch
```

Dữ liệu tải mới được lưu trong `run/raw/` kèm metadata và hash; snapshot mặc định không bị ghi đè. API phụ thuộc mạng,
phạm vi dữ liệu và hạn mức nhà cung cấp. `--raw-dir` cho phép chọn cache khác.
`--alpha 0` chỉ chạy ứng viên Ridge alpha=0; mặc định tìm trong lưới của config.

## Validation và test

1. Giữ cùng các thời điểm đủ feature và đủ 3 nhãn cho mọi phương pháp/horizon.
2. Chia bằng **timestamp duy nhất**, không cắt giữa các thành phố cùng giờ.
3. Sau khi lọc khoảng ngày local trong config, khoảng đầu 70% là train ban đầu; 15% tiếp theo chia thành 3 cửa sổ validation.
4. Train mở rộng qua từng cửa sổ. Loại mẫu có nhãn +6h chạm/vượt đầu validation;
   nhãn validation cũng không vượt cuối cửa sổ. Đây là khoảng loại trừ chung cho cả ba horizon.
5. Chọn bậc polynomial, alpha Ridge và phương pháp bằng RMSE gộp các cửa sổ validation.
6. Refit mỗi phương pháp với cấu hình đã chọn trên development (train + validation),
   vẫn loại nhãn chạm/vượt đầu test.
7. Đánh giá một lần trên 15% cuối. Không chọn lại phương pháp bằng thứ hạng test.

Các mô hình chia sẻ cùng dòng test. RMSE, MAE, R² và mức cải thiện so với persistence
được xuất riêng theo horizon và theo thành phố. R² không xác định khi target không biến
động; recall không xác định khi không có mẫu dương; các trường này được để trống trong CSV.
Mức cải thiện không xác định nếu baseline RMSE bằng 0.

Ngưỡng 35.4 là **ngưỡng phân loại thực nghiệm trên nồng độ từng giờ**, không phải AQI
hay kết luận về chuẩn sức khỏe EPA. Ngưỡng lấy từ config xuyên suốt bảng kết quả và biểu đồ.
Dự đoán âm không bị âm thầm cắt về 0; số lượng được ghi trong metrics để đánh giá hạn chế
của hồi quy tuyến tính. Hệ số tương quan không chứng minh quan hệ nhân quả.

## Đầu ra của mỗi run

- `config_used.yaml`, `manifest.json`: cấu hình thực dùng, mốc thời gian, phiên bản thư viện,
  Git commit/trạng thái thay đổi, hash mã nguồn và dữ liệu.
- `missing_data.csv`: số giờ thiếu từng biến/thành phố; không tự điền dữ liệu tương lai.
- `validation_scores.csv`, `selection.json`: điểm từng cấu hình/fold và lựa chọn trước test.
- `predictions.csv`: city, forecast_origin, horizon_hours, target_time, model, y_true, y_pred, y_baseline.
- `metrics.csv`, `city_metrics.csv`: 15 hàng tổng thể và 60 hàng theo thành phố với cấu hình mặc định.
- `models/`: 12 mô hình fit + 3 baseline, mỗi bộ chứa scaler, feature order, horizon, tham số;
  kèm bảng hệ số của các mô hình fit.
- `figures/`: so sánh RMSE; biểu đồ từng thành phố/horizon của phương pháp chọn bằng validation.

## Dự đoán khi chưa biết tương lai

```bash
python air_quality_forecasting/predict.py \
  --run-dir air_quality_forecasting/outputs/runs/my_experiment \
  --history-dir air_quality_forecasting/data/raw_api \
  --output air_quality_forecasting/outputs/latest_forecasts.csv
```

Kết quả có 12 dòng: 4 thành phố × 3 horizon. Không cần nhãn tương lai.
Mặc định lấy giờ mới nhất chung cho bốn thành phố; kiểm tra lịch sử đủ trước khi dự đoán,
không tự lùi sang giờ cũ nếu thiếu feature. Có thể đặt `--as-of 2024-12-30T08:00:00+07:00`.
Không được dùng model đã học từ thời điểm sau `--as-of` để giả lập dự báo quá khứ.
Chỉ nạp pickle do mình tạo/tin cậy, không nạp model tùy ý từ Internet.

## Kiểm thử

```bash
cd air_quality_forecasting
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Kiểm tra giờ/nhãn, thành phố, missing hours, tính nhân quả của feature, purge ranh giới,
scaler train-only, gradient và nghiệm Ridge, alpha=0, một lớp, lưu/nạp model và luồng
train → predict không cần đáp án tương lai.

## Giới hạn của dữ liệu hiện có

Snapshot mặc định `data/raw_api/` được tải lại từ API, đủ 17.544 giờ/thành phố cho
2023–2024. `source.json` ghi nguồn, ngày và hash để kiểm tra snapshot chưa bị sửa.
Biến `boundary_layer_height` thiếu 4.368 giờ/thành phố; pipeline giữ NaN và chỉ
đánh giá các dòng đủ dữ liệu. Với cấu hình hiện tại còn 52.008/70.176 dòng đủ
feature và ba nhãn. Tỷ lệ chia tập áp dụng lên các thời điểm đủ điều kiện, nên
kết quả không đại diện cho các giờ thiếu dữ liệu. Không so trực tiếp RMSE giữa
các run nếu khoảng test hoặc tập dòng khác nhau.

Cache gốc vẫn giữ trong `data/raw/` để đối chiếu; chỉ dùng khi truyền `--raw-dir`.

- Nguồn: [Open-Meteo Historical Weather](https://open-meteo.com/en/docs/historical-weather-api)
  và [Air Quality / CAMS](https://open-meteo.com/en/docs/air-quality-api).
  Dữ liệu mô hình/tái phân tích không tương đương số đo độc lập tại trạm.
- Cache gốc 2023–2024 đã qua pipeline cũ, có thể đã được nội suy. Không có mặt nạ thiếu
  ban đầu để khôi phục; việc sửa code không xóa được hạn chế của cache đó.
- Cache gốc kết thúc lúc 00:00 ngày 31/12/2024, thiếu 23 giờ cuối. Code tải mới đã sửa
  phạm vi ngày, nhưng không tự chế thêm dữ liệu cho cache.
- Giả định dữ liệu cùng giờ sẵn có chưa được kiểm chứng với feed thực tế; cần kiểm tra
  độ trễ và đánh giá với dữ liệu trạm trước khi dùng làm hệ thống cảnh báo.
- Các kết quả phiên bản cũ đã được dọn bỏ vì baseline và gán nhãn thành phố bị sai.
  Bộ kết quả chạy lại nằm trong `air_quality_forecasting/outputs/runs/current/`.

Không đổi mô hình hay lưới tham số sau khi xem test chỉ để làm điểm test đẹp hơn.
