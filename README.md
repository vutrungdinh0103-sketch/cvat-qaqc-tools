# CVAT QA/QC tools

Bộ công cụ kiểm tra chất lượng annotation (QA/QC) cho CVAT: phát hiện lỗi hình học,
lỗi đầy đủ nhãn/attribute và lỗi theo thời gian; kết xuất báo cáo (JSON/CSV/JUnit)
và **tự động tạo issue lên CVAT** (idempotent, chạy lại không sinh trùng).

- Ngôn ngữ: Python 3.10+
- Thư viện: `cvat-sdk` (SDK chính thức), `shapely`, `numpy`, `pydantic`, `python-dotenv`, `PyYAML`

## Cài đặt

```powershell
python -m pip install -r requirements.txt          # chạy tool
python -m pip install -r requirements-dev.txt      # test + lint (tuỳ chọn)
Copy-Item .env.example .env                        # rồi điền CVAT_HOST + tài khoản/token
```

## Dùng nhanh

```powershell
# 1) Kiểm tra và xuất báo cáo (CLI mới, nhiều rule)
python -m qaqc run 42 --rules rules/driving_v1.yaml -o reports/task_42.json
python -m qaqc run 42 -o reports/task_42.csv --fail-on warning

# 2) Kiểm tra rồi đẩy lỗi thành issue trên CVAT (mặc định chỉ lỗi `error`)
python -m qaqc publish 42 --rules rules/driving_v1.yaml --publish-severity error
python -m qaqc publish 42 --dry-run            # xem trước, không ghi gì lên CVAT

# 3) Xem/kiểm tra cấu hình rule
python -m qaqc rules                            # danh sách rule
python -m qaqc rules --json                     # kèm schema tham số
python -m qaqc validate rules/driving_v1.yaml   # kiểm tra file YAML

# 4) CLI cũ vẫn chạy y nguyên (không phá CI đang có)
python checker.py 42 --output reports/task_42.json --fail-on-issues
```

Mã thoát dùng chung: `0` không có lỗi · `1` có lỗi ≥ ngưỡng `--fail-on` ·
`2` lỗi cấu hình · `3` lỗi kết nối/API CVAT.

## Các rule có sẵn

| Nhóm | Rule | Mặc định | Ý nghĩa |
| --- | --- | --- | --- |
| Hình học | `invalid_size` | error | Box/polygon có `width <= 0` hoặc `height <= 0` (kể cả toạ độ bị đảo) |
| Hình học | `out_of_frame` | error | Box tràn ra ngoài khung ảnh quá dung sai |
| Hình học | `tiny_box` | warning | Box quá nhỏ (diện tích/cạnh ngắn dưới ngưỡng) |
| Hình học | `duplicate_bbox` | error | 2 object cùng frame trùng nhau với `IoU > 0.85` |
| Hình học | `must_be_inside` | error | Quan hệ chứa nhau (ví dụ `license_plate` phải nằm trong `vehicle`) |
| Đầy đủ | `missing_label` | error | Thiếu nhãn bắt buộc theo danh sách quy định |
| Đầy đủ | `required_attributes` | error | Thiếu/để trống attribute bắt buộc của label |
| Đầy đủ | `unexpected_label` | warning | Nhãn không nằm trong danh sách cho phép |
| Đầy đủ | `empty_frame` | warning | Frame không có annotation (kiểm tra độ phủ) |
| Thời gian | `track_gap` | warning | Track có khoảng trống keyframe lớn (nội suy có thể sai) |
| Thời gian | `track_class_change` | error | Nghi ID switch/đổi class giữa 2 track liền nhau |

Mỗi rule có tham số riêng, khai báo trong YAML (xem `rules/driving_v1.yaml`) hoặc
ghi đè nhanh từ CLI:

```powershell
python -m qaqc run 42 --only invalid_size,duplicate_bbox
python -m qaqc run 42 --disable empty_frame --param duplicate_bbox.iou_threshold=0.9
python -m qaqc run 42 --param required_attributes.labels.vehicle="[vehicle_type, color]"
```

## Cấu trúc dự án

```
qaqc/                 # Lớp 1: engine thuần Python (không phụ thuộc CVAT)
  geometry.py         #   IoU, bbox, containment, polygon tự cắt
  model.py            #   model chuẩn hoá (track-aware): NormShape/NormTrack/TaskData
  normalize.py        #   dữ liệu thô CVAT (dict/object SDK) -> TaskData
  rules/              #   khung rule + 11 rule (geometry/completeness/temporal)
  engine.py           #   chạy rule -> QAReport (fingerprint, job_id, thống kê)
  report.py           #   schema báo cáo + writer JSON/CSV/JUnit
  config.py           #   cấu hình kết nối CVAT + cấu hình rule YAML
  data_source.py      # Lớp 2: tải task/job từ CVAT bằng cvat_sdk
  publishers/         # Lớp 2: tạo issue trên CVAT (idempotent theo fingerprint)
  cli.py, legacy.py   # Lớp 3: CLI mới + lớp tương thích CLI cũ
checker.py            # CLI cũ (re-export, giữ nguyên hành vi + schema + exit code)
rules/                # Cấu hình rule theo dự án (YAML)
tests/                # 183 test pytest, chạy offline (không cần CVAT server)
docs/                 # Kiến trúc + hành vi đã kiểm chứng + hướng dẫn plugin
cvat-ui/plugins/qaqc/ # Plugin UI (tab QA/QC trong CVAT) - xem docs/plugin-install.md
```

## Kiến trúc 3 lớp

1. **Engine** (`qaqc/*`, trừ `data_source.py`/`publishers/`): thuần Python, không
   import `cvat_sdk` → test offline bằng fixture JSON, chạy nhanh, dễ mở rộng rule.
2. **Cầu nối CVAT** (`data_source.py`, `publishers/`): dùng SDK chính thức
   (`cvat_sdk.Client`) để tải annotation và tạo issue; đây là nơi duy nhất gọi API.
3. **Giao diện** (`cli.py`, `legacy.py`, plugin UI): CLI cho CI, lớp tương thích
   ngược và tab QA/QC trong CVAT.

Chi tiết: [`docs/architecture.md`](docs/architecture.md) và
[`docs/verified-behaviors.md`](docs/verified-behaviors.md) (danh sách hành vi của
CVAT/SDK đã được **kiểm chứng bằng thực nghiệm**, kèm bằng chứng cụ thể).

## Kiểm thử

```powershell
python -m pytest            # 183 test, ~2s, không cần CVAT server
python -m ruff check qaqc tests checker.py
python -m ruff format --check qaqc tests checker.py
```

Test quan trọng nhất là `tests/test_integration.py`: nó chạy toàn bộ luồng trên
một payload CVAT thô (`tests/fixtures/task_anomalies.json`) và khoá lại số lỗi
theo từng rule - nhờ đó phát hiện được cả lỗi chuẩn hoá tinh vi (ví dụ bug
keyframe của track bị mất `label_id`).

## Plugin UI cho CVAT (tuỳ chọn)

CVAT hỗ trợ **plugin UI chính thức** (build-time qua `CLIENT_PLUGINS`), nên không
cần fork `cvat-ui`. Xem [`docs/plugin-install.md`](docs/plugin-install.md).
