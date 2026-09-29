# CVAT QA/QC tools

Bộ công cụ kiểm tra chất lượng annotation (QA/QC) cho CVAT: phát hiện lỗi hình học,
lỗi đầy đủ nhãn/attribute và lỗi theo thời gian; kết xuất báo cáo (JSON/CSV/JUnit)
và **tự động tạo issue lên CVAT** (idempotent, chạy lại không sinh trùng).

Rule được chia theo 2 cấp độ của proposal:

- **Level 1 – Overall / Completeness Check**: empty frame, missing annotation, missing
  required label, unexpected label, missing attribute, suspicious object count,
  unlabeled frame range, duplicate annotation → `rules/level1_v1.yaml` hoặc `--level 1`.
- **Level 2 – Detailed validation**: hình học (`invalid_size`, `out_of_frame`,
  `tiny_box`, `duplicate_bbox`, `must_be_inside`) + temporal (`track_gap`,
  `track_class_change`) → `rules/driving_v1.yaml` hoặc `--level 2`.

Demo Level 1 trên CVAT thật (tạo task có sẵn lỗi + xem issue trong CVAT):
[`docs/level1-demo.md`](docs/level1-demo.md).

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

# 5) Chạy web UI + API trên localhost (không cần Docker/FastAPI)
python -m qaqc serve --demo     # thử ngay với dữ liệu mẫu (tự kích hoạt mọi rule)
python -m qaqc serve --open     # CVAT thật (đọc .env) → mở http://127.0.0.1:8081
```

Mã thoát dùng chung: `0` không có lỗi · `1` có lỗi ≥ ngưỡng `--fail-on` ·
`2` lỗi cấu hình · `3` lỗi kết nối/API CVAT.

## Các rule có sẵn

`level` = cấp độ QA (1 = Overall/Completeness, 2 = Detailed) - dùng cho cờ `--level`
và bộ rule `rules/level1_v1.yaml`.

| Nhóm | Rule | Level | Mặc định | Ý nghĩa |
| --- | --- | --- | --- | --- |
| Hình học | `invalid_size` | 2 | error | Box/polygon có `width <= 0` hoặc `height <= 0` (kể cả toạ độ bị đảo) |
| Hình học | `out_of_frame` | 2 | error | Box tràn ra ngoài khung ảnh quá dung sai |
| Hình học | `tiny_box` | 2 | warning | Box quá nhỏ (diện tích/cạnh ngắn dưới ngưỡng) |
| Hình học | `duplicate_bbox` | **1** | error | 2 object cùng frame trùng nhau với `IoU > 0.85` |
| Hình học | `must_be_inside` | 2 | error | Quan hệ chứa nhau (ví dụ `license_plate` phải nằm trong `vehicle`) |
| Đầy đủ | `missing_label` | **1** | error | Thiếu nhãn bắt buộc theo danh sách quy định |
| Đầy đủ | `required_attributes` | **1** | error | Thiếu/để trống attribute bắt buộc của label |
| Đầy đủ | `unexpected_label` | **1** | warning | Nhãn không nằm trong danh sách cho phép |
| Đầy đủ | `empty_frame` | **1** | warning | Frame không có annotation (kiểm tra độ phủ) |
| Đầy đủ | `empty_frame_range` | **1** | warning | Dải ≥ N frame liên tiếp không có annotation (unlabeled range) |
| Đầy đủ | `object_count` | **1** | warning | Số object trong frame bất thường (quá ít / quá nhiều / vượt xa trung vị) |
| Thời gian | `track_gap` | 2 | warning | Track có khoảng trống keyframe lớn (nội suy có thể sai) |
| Thời gian | `track_class_change` | 2 | error | Nghi ID switch/đổi class giữa 2 track liền nhau |

Mỗi rule có tham số riêng, khai báo trong YAML (xem `rules/driving_v1.yaml` và
`rules/level1_v1.yaml`) hoặc ghi đè nhanh từ CLI:

```powershell
python -m qaqc rules --level 1                       # xem rule Level 1 + mô tả
python -m qaqc run 42 --level 1                      # chỉ chạy rule Level 1
python -m qaqc run 42 --rules rules/level1_v1.yaml   # bộ rule Level 1 (tham số đã tune)
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
  service.py, webui.py, demo.py  # Lớp 3: service HTTP localhost + web UI + dữ liệu mẫu
checker.py            # CLI cũ (re-export, giữ nguyên hành vi + schema + exit code)
rules/                # Cấu hình rule theo dự án (YAML)
tests/                # 208 test pytest, chạy offline (không cần CVAT server)
docs/                 # Kiến trúc, hành vi đã kiểm chứng, hướng dẫn plugin + localhost
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
python -m pytest            # 208 test, ~6s, không cần CVAT server
python -m ruff check qaqc tests checker.py
python -m ruff format --check qaqc tests checker.py
```

Test quan trọng nhất là `tests/test_integration.py`: nó chạy toàn bộ luồng trên
một payload CVAT thô (`tests/fixtures/task_anomalies.json`) và khoá lại số lỗi
theo từng rule - nhờ đó phát hiện được cả lỗi chuẩn hoá tinh vi (ví dụ bug
keyframe của track bị mất `label_id`).

## Chạy trên localhost (web UI + API)

```powershell
python -m qaqc serve --demo        # dữ liệu mẫu, không cần CVAT (mở xem ngay)
python -m qaqc serve --open        # CVAT thật (đọc .env), tự mở trình duyệt
```

> Chạy `serve` khi **chưa có `.env`**: tool không “im lặng thoát” mà in rõ đường dẫn
> `.env` bị thiếu + 4 cách sửa (dùng `--demo`, `Copy-Item .env.example .env`, truyền
> `--host/--user/--password`, hoặc `--env-file`), rồi thoát với mã `2`.

Mở **http://127.0.0.1:8081/** để xem thẻ thống kê, bảng lỗi (lọc theo mức độ/rule/tìm
kiếm), và tải báo cáo JSON/CSV. Service dùng `http.server` của thư viện chuẩn → không
cần Docker/FastAPI/Node, và có đúng API mà plugin CVAT UI cần:

| Endpoint | Ý nghĩa |
| --- | --- |
| `GET /health` | Trạng thái service + nguồn dữ liệu + rule đang bật + `rules_by_level` |
| `GET /tasks/{id}/report` | Báo cáo JSON (`?rules=`, `?level=1`, `?only=`, `?disable=`, `?refresh=true`) |
| `GET /tasks/{id}/report.csv` | Báo cáo CSV (UTF-8 BOM, mở bằng Excel, có cột `level`) |
| `POST /tasks/{id}/run` | Chạy QA/QC mới, trả báo cáo JSON |

Hướng dẫn từng bước (kể cả ghép với plugin UI và xử lý sự cố):
[`docs/localhost.md`](docs/localhost.md). Demo Level 1 đầu-cuối trên CVAT thật:
[`docs/level1-demo.md`](docs/level1-demo.md).

## Plugin UI cho CVAT (tuỳ chọn)

CVAT hỗ trợ **plugin UI chính thức** (build-time qua `CLIENT_PLUGINS`), nên không
cần fork `cvat-ui`. Xem [`docs/plugin-install.md`](docs/plugin-install.md).

✅ Đã kiểm chứng trên CVAT 2.74.1: tab **QA/QC** hiện trong trang *Quality control* của
task và đọc trực tiếp kết quả từ service (ảnh: [`docs/img/qaqc-tab-live.png`](docs/img/qaqc-tab-live.png)).
Kiểm tra lại bằng Chrome headless sau mỗi lần build:

```powershell
npm install puppeteer-core     # 1 lần
node scripts/check_plugin_tab.mjs --task 9 --user dinhvt --password *** --run
```

> ⚠️ Khi sửa plugin: `actionCreators.addUIComponent(...)` **phải** được `dispatch(...)`,
> nếu không tab sẽ không xuất hiện mà **không có cảnh báo nào** (V31 trong
> [`docs/verified-behaviors.md`](docs/verified-behaviors.md)).
