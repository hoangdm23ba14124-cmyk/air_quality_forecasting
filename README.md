# Dự báo PM2.5 sau 1, 3 và 6 giờ

So sánh **Simple Linear, Multiple Linear và Polynomial Regression** trên
bốn thành phố: Hà Nội, Hải Phòng, Đà Nẵng, TP. Hồ Chí Minh. Mỗi phương pháp học
một mô hình riêng cho từng horizon. Persistence chỉ là phép tính tham chiếu trong metrics,
không được huấn luyện, lưu model hoặc đưa vào lựa chọn mô hình.

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
| Simple Linear | Một biến PM2.5(t), có intercept |
| Multiple Linear | Danh sách đặc trưng cố định, OLS |
| Polynomial | Mở rộng 4 biến số trong config thành bậc 2 hoặc 3; giữ các biến còn lại tuyến tính |

Ba phương pháp dùng `LinearRegression` (OLS) của scikit-learn; Polynomial mở rộng
đặc trưng bằng `PolynomialFeatures` trước khi fit.

Không scale target: đầu ra trực tiếp là µg/m³. Scaler của các features và phép mở
rộng polynomial được fit trong từng fold train, sau đó áp dụng nguyên trạng vào validation.

## Features của từng mô hình

Với cấu hình hiện tại, các mô hình sử dụng đầu vào như sau. Số cột không bao gồm intercept.

| Mô hình | Features đầu vào | Số cột đưa vào hồi quy |
|---|---|---:|
| Simple Linear Regression | Chỉ `pm25_current`, tức PM2.5 tại thời điểm t | 1 |
| Multiple Linear Regression | Toàn bộ đặc trưng trong bảng bên dưới | 42 |
| Polynomial Regression | Cùng 42 đặc trưng, mở rộng đa thức trên 4 biến được chọn | 52 khi bậc 2; 72 khi bậc 3 |

### Bộ 42 features của Multiple Linear

| Nhóm | Số lượng | Tên cột và ý nghĩa |
|---|---:|---|
| PM2.5 hiện tại | 1 | `pm25_current`: nồng độ tại giờ t |
| Khí tượng | 9 | `temperature_2m` (nhiệt độ), `relative_humidity_2m` (độ ẩm tương đối), `dew_point_2m` (điểm sương), `surface_pressure` (áp suất), `wind_speed_10m` (tốc độ gió), `precipitation` (lượng mưa), `cloud_cover` (độ che phủ mây), `boundary_layer_height` (độ cao lớp biên khí quyển), `shortwave_radiation` (bức xạ sóng ngắn) |
| Thời gian | 7 | `sin_hour`, `cos_hour`, `sin_month`, `cos_month`, `sin_dow`, `cos_dow`: mã hóa chu kỳ giờ, tháng và thứ trong tuần; `is_weekend`: chỉ báo cuối tuần |
| Khí tượng suy dẫn | 5 | `heat_humidity`: nhiệt độ × độ ẩm; `wind_u`, `wind_v`: hai thành phần gió; `dewpoint_depression`: nhiệt độ trừ điểm sương; `pressure_delta`: áp suất giờ t trừ giờ trước, cùng thành phố |
| PM2.5 trễ | 9 | `pm25_lag_{h}` với h = 1, 2, 3, 6, 12, 24, 48, 72, 168 giờ |
| Thống kê trượt PM2.5 | 8 | `pm25_roll_mean_{w}h` và `pm25_roll_std_{w}h` với w = 3, 6, 12, 24 giờ |
| Thành phố | 3 | `city_danang`, `city_haiphong`, `city_hcmc`; Hà Nội là nhóm tham chiếu, cả ba cột bằng 0 |

Hướng gió `wind_direction_10m` được dùng để tạo `wind_u` và `wind_v`, không đưa
trực tiếp vào hồi quy. Timestamp được chuyển thành các biến thời gian ở trên.
Các cột PM10, NO₂ và ozone trong dữ liệu nguồn không thuộc bộ features của mô hình.

### Mở rộng features cho Polynomial

Bốn biến được mở rộng là `pm25_current`, `temperature_2m`,
`relative_humidity_2m` và `wind_speed_10m`. Các biến còn lại giữ nguyên dạng tuyến tính.

Với bậc 2, bốn biến tạo thành **14 cột**: 4 biến gốc, 4 bình phương và 6 tích tương tác
đôi, chẳng hạn `pm25_current²` và `pm25_current × temperature_2m`.
Cộng với 38 cột còn lại, mô hình nhận **52 cột**, không tính intercept.
Bậc 3 tạo 34 cột đa thức, tức **72 cột** sau khi ghép với 38 cột còn lại.
Trong bộ kết quả `outputs/runs/current`, validation chọn **bậc 2 tại cả ba mốc**.
Hồi quy đa thức vẫn tuyến tính theo các hệ số, dù dùng biến đổi phi tuyến của đầu vào.

### Quy tắc thời gian và chuẩn hóa

Cả ba mốc +1, +3 và +6 giờ dùng cùng cách tạo features nhưng học mô hình riêng;
nhãn lần lượt là PM2.5(t+1), PM2.5(t+3), PM2.5(t+6).
Các cột nhãn không nằm trong danh sách features.

Độ trễ và thống kê trượt được tính riêng theo thành phố, chỉ dùng dữ liệu đến t.
Cửa sổ trượt bao gồm giờ t và yêu cầu đủ quan sát; không tự bỏ qua giờ thiếu.
Cách dùng dữ liệu giờ t chỉ hợp lệ khi dữ liệu đó đã sẵn có tại lúc phát hành dự báo.
`StandardScaler` được fit trên train của từng fold rồi áp dụng nguyên trạng lên validation;
trước test, scaler được fit lại trên development. Không fit scaler trên test.

Mã nguồn: [tạo features](air_quality_forecasting/src/features/engineering.py),
[chọn đầu vào và fit mô hình](air_quality_forecasting/src/models/experiment.py),
[cấu hình](air_quality_forecasting/config/config.yaml).

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

## Validation và test

1. Giữ cùng các thời điểm đủ feature và đủ 3 nhãn cho mọi phương pháp/horizon.
2. Chia bằng **timestamp duy nhất**, không cắt giữa các thành phố cùng giờ.
3. Sau khi lọc khoảng ngày local trong config, khoảng đầu 70% là train ban đầu; 15% tiếp theo chia thành 3 cửa sổ validation.
4. Train mở rộng qua từng cửa sổ. Loại mẫu có nhãn +6h chạm/vượt đầu validation;
   nhãn validation cũng không vượt cuối cửa sổ. Đây là khoảng loại trừ chung cho cả ba horizon.
5. Chọn bậc polynomial và phương pháp bằng RMSE gộp các cửa sổ validation.
6. Refit mỗi phương pháp với cấu hình đã chọn trên development (train + validation),
   vẫn loại nhãn chạm/vượt đầu test.
7. Đánh giá một lần trên 15% cuối. Không chọn lại phương pháp bằng thứ hạng test.

Các mô hình chia sẻ cùng dòng test. RMSE, MAE, R² và mức cải thiện so với persistence
được xuất riêng theo horizon và theo thành phố. R² không xác định khi target không biến
động; trường này được để trống trong CSV.
Mức cải thiện không xác định nếu baseline RMSE bằng 0.

Dự đoán âm không bị âm thầm cắt về 0; số lượng được ghi trong metrics để đánh giá hạn chế
của hồi quy tuyến tính. Hệ số tương quan không chứng minh quan hệ nhân quả.

## Đầu ra của mỗi run

- `config_used.yaml`, `manifest.json`: cấu hình thực dùng, mốc thời gian, phiên bản thư viện,
  Git commit/trạng thái thay đổi, hash mã nguồn và dữ liệu.
- `missing_data.csv`: số giờ thiếu từng biến/thành phố; không tự điền dữ liệu tương lai.
- `validation_scores.csv`, `selection.json`: điểm từng cấu hình/fold và lựa chọn trước test.
- `predictions.csv`: city, forecast_origin, horizon_hours, target_time, model, y_true, y_pred, y_baseline.
- `metrics.csv`, `city_metrics.csv`: 9 hàng tổng thể và 36 hàng theo thành phố với cấu hình mặc định.
- `models/`: 9 mô hình fit, mỗi bộ chứa scaler, feature order, horizon, tham số;
  khi chạy mới, chương trình cũng xuất bảng hệ số; các bảng phụ này đã được dọn khỏi bộ kết quả hiện tại.
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
scaler train-only, các chỉ số hồi quy, đúng ba họ mô hình, lưu/nạp model và luồng
train → predict không cần đáp án tương lai.

## Giới hạn của dữ liệu hiện có

Snapshot mặc định `data/raw_api/` được tải lại từ API, đủ 17.544 giờ/thành phố cho
2023–2024. `source.json` ghi nguồn, ngày và hash để kiểm tra snapshot chưa bị sửa.
Biến `boundary_layer_height` thiếu 4.368 giờ/thành phố; pipeline giữ NaN và chỉ
đánh giá các dòng đủ dữ liệu. Với cấu hình hiện tại còn 52.008/70.176 dòng đủ
feature và ba nhãn. Tỷ lệ chia tập áp dụng lên các thời điểm đủ điều kiện, nên
kết quả không đại diện cho các giờ thiếu dữ liệu. Không so trực tiếp RMSE giữa
các run nếu khoảng test hoặc tập dòng khác nhau.

- Nguồn: [Open-Meteo Historical Weather](https://open-meteo.com/en/docs/historical-weather-api)
  và [Air Quality / CAMS](https://open-meteo.com/en/docs/air-quality-api).
  Dữ liệu mô hình/tái phân tích không tương đương số đo độc lập tại trạm.
- Giả định dữ liệu cùng giờ sẵn có chưa được kiểm chứng với feed thực tế; cần kiểm tra
  độ trễ và đánh giá với dữ liệu trạm trước khi dùng làm hệ thống cảnh báo.
- Các kết quả phiên bản cũ đã được dọn bỏ vì baseline và gán nhãn thành phố bị sai.
  Bộ kết quả của ba mô hình nằm trong `air_quality_forecasting/outputs/runs/current/`.

Không đổi mô hình hay lưới tham số sau khi xem test chỉ để làm điểm test đẹp hơn.

## Các file chính

- `air_quality_forecasting/main.py`: xử lý dữ liệu, huấn luyện, so sánh và xuất kết quả.
- `air_quality_forecasting/src/models/experiment.py`: ba mô hình hồi quy và lựa chọn bằng validation.
- `air_quality_forecasting/predict.py`: dự đoán bằng mô hình đã lưu.
- `reports/`: bảng và biểu đồ phục vụ bài làm về ba mô hình hồi quy.
