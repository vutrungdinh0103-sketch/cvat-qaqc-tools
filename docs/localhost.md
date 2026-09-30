# Chạy QA/QC trên localhost (web UI + API, không cần Docker)

Tài liệu này là hướng dẫn **từng bước** để bật QA/QC tool thành một service chạy
trên máy của bạn tại `http://127.0.0.1:8081` — có giao diện web để nhập Task ID,
xem bảng lỗi, tải báo cáo JSON/CSV, và có API đúng như plugin CVAT UI cần.

Service dùng `http.server` của thư viện chuẩn Python nên **không cần FastAPI,
không cần Docker, không cần Node.js**.

## 1. Chuẩn bị (1 lần)

```powershell
cd <thư-mục-repo>
python -m pip install -r requirements.txt
```

## 2. Cách nhanh nhất: chạy thử với dữ liệu mẫu (không cần CVAT)

```powershell
python -m qaqc serve --demo
```

Terminal in ra:

```
===== QA/QC service =====
Giao diện    : http://127.0.0.1:8081/
Trạng thái   : http://127.0.0.1:8081/health
Báo cáo JSON : http://127.0.0.1:8081/tasks/<task_id>/report
Báo cáo CSV  : http://127.0.0.1:8081/tasks/<task_id>/report.csv
Chạy QA/QC   : POST http://127.0.0.1:8081/tasks/<task_id>/run
Nguồn dữ liệu: dữ liệu mẫu (--demo)
```

Mở **http://127.0.0.1:8081/** bằng trình duyệt. Trang tự chạy QA/QC cho task `1` và
hiển thị:

- thẻ thống kê: tổng lỗi, `error` / `warning` / `info`, số object đã quét, số frame
  có annotation, thời điểm quét, bộ rule đang dùng;
- bộ lọc: mức độ, rule, ô tìm kiếm trong mô tả;
- bảng lỗi: mức độ · rule · frame · job · nhãn · mô tả;
- nút **JSON** / **CSV** để tải báo cáo.

Dữ liệu mẫu (`qaqc/demo.py`) được tạo **có chủ đích** để kích hoạt **cả 9 rule đang
bật** (10 lỗi): box tràn khung, `width = 0`, box trùng nhau, biển số ngoài xe, box
1x1 px, nhãn lạ, thiếu attribute (shape + tag), đứt keyframe và đổi class giữa track.

> Muốn tự mở trình duyệt: thêm `--open`. Muốn đổi cổng: `--port 9000`.

## 3. Chạy với CVAT thật

1. Tạo file `.env` (một lần):

   ```powershell
   Copy-Item .env.example .env
   ```

   Điền `CVAT_HOST` (ví dụ `http://localhost:8080`) và `CVAT_TOKEN` (khuyến nghị
   cho bot/CI) hoặc `CVAT_USER`/`CVAT_PASS` — **phải là tài khoản có thật trên CVAT
   của bạn** (đừng dùng `admin/admin` mẫu của `.env.example`; sai tài khoản thì
   service vẫn chạy bình thường nhưng UI báo `HTTP 502` kèm *Unable to log in with
   provided credentials*). Xem username thật:
   `docker exec cvat_db psql -U root -d cvat -t -A -c "select username from auth_user;"`.

2. Chạy service:

   ```powershell
   python -m qaqc serve
   ```

   Khi khởi động, service đọc `.env` và in `Kết nối CVAT: http://localhost:8080`.
   Nếu thiếu cấu hình, lệnh **thoát ngay với mã 2** và thông báo rõ (không để bạn
   phát hiện lỗi muộn trên UI).

3. Trên trang web, nhập **Task ID** thật (ví dụ `42`) và bấm **Chạy QA/QC**.

### Tạo sẵn một task có lỗi Level 1 để demo

```powershell
python scripts/create_demo_task.py          # tạo task 12 frame + pre-annotation cố ý lỗi
python -m qaqc run <task_id> --rules rules/level1_v1.yaml    # hoặc --level 1
python -m qaqc publish <task_id> --rules rules/level1_v1.yaml --publish-severity warning
```

Sau đó mở `http://localhost:8080/tasks/<task_id>` để xem issue và nhảy tới frame lỗi.
Kịch bản đầy đủ: [`level1-demo.md`](level1-demo.md).

### Chạy từ file JSON (offline, không cần CVAT server)

```powershell
python -m qaqc serve --source-file tests/fixtures/task_anomalies.json
```

File JSON dùng đúng dạng payload của CVAT API:
`{"task": {...}, "frames": [...], "labels": [...], "annotations": {...}, "jobs": [...]}`.
Tiện để kiểm thử/demo mà không cần server, hoặc khi đã export annotations ra file.

## 4. Dùng API (giống hệt thứ plugin CVAT UI gọi)

| Endpoint | Ý nghĩa |
| --- | --- |
| `GET /` | Giao diện web (nhúng trong `qaqc/webui.py`) |
| `GET /health` | Trạng thái service: version, nguồn dữ liệu, rule đang bật, task đã cache |
| `GET /tasks/{id}/report` | Báo cáo JSON (schema `qaqc/1`, dùng cache theo `--cache-ttl`) |
| `GET /tasks/{id}/report.csv` | Báo cáo CSV (UTF-8 BOM, mở bằng Excel được ngay) |
| `POST /tasks/{id}/run` | Chạy QA/QC mới rồi trả báo cáo JSON (bỏ cache) |
| `POST /tasks/{id}/publish` | Đẩy lỗi QA/QC thành issue trên CVAT (luôn chạy lại QA/QC, idempotent theo fingerprint) |

Tham số query dùng chung cho `report`/`report.csv`/`run`:

| Tham số | Ví dụ | Ý nghĩa |
| --- | --- | --- |
| `rules` | `rules=driving_v1` hoặc `rules=rules/driving_v1.yaml` | Bộ rule dùng cho lần chạy |
| `level` | `level=1` | Chỉ chạy rule của cấp độ QA này (1 = Overall/Completeness, 2 = Detailed) |
| `only` | `only=invalid_size,duplicate_bbox` | Chỉ chạy các rule này |
| `disable` | `disable=empty_frame,tiny_box` | Tắt các rule này |
| `refresh` | `refresh=true` | Bỏ cache, tính lại |

`POST /tasks/{id}/publish` dùng thêm các tham số sau (và **luôn chạy lại QA/QC**, không
dùng cache vì đây là thao tác ghi):

| Tham số | Ví dụ | Ý nghĩa |
| --- | --- | --- |
| `severity` | `severity=warning` | Chỉ đẩy issue từ mức này trở lên (mặc định `error`) |
| `dry_run` | `dry_run=true` | Xem trước số issue sẽ tạo, **không ghi** lên CVAT |
| `reopen_resolved` | `reopen_resolved=false` | Không mở lại issue đã resolved (mặc định `true`) |
| `resolve_stale` | `resolve_stale=true` | Đánh dấu đã xử lý issue cũ không còn lỗi |
| `post_details` | `post_details=false` | Không đăng comment JSON chi tiết kèm issue |
| `max_issues_per_job` | `max_issues_per_job=5` | Giới hạn số issue tạo cho mỗi job |
| `max_issues_total` | `max_issues_total=20` | Giới hạn tổng số issue trong 1 lần gọi (`0` = không giới hạn) |

Ví dụ bằng PowerShell:

```powershell
# Tóm tắt trạng thái (có rules_by_level cho biết rule nào thuộc cấp độ nào)
Invoke-RestMethod 'http://127.0.0.1:8081/health' | ConvertTo-Json -Depth 3

# Chạy QA/QC Level 1 (Overall/Completeness) cho task 42
$r = Invoke-RestMethod 'http://127.0.0.1:8081/tasks/42/report?level=1'
$r.counts_by_level
$r.issues | Select-Object -First 10 severity, level, rule_id, frame, label, message

# Chạy QA/QC cho task 42, chỉ rule hình học
$r = Invoke-RestMethod 'http://127.0.0.1:8081/tasks/42/report?only=invalid_size,duplicate_bbox'
$r.counts_by_rule

# Tải CSV
Invoke-WebRequest 'http://127.0.0.1:8081/tasks/42/report.csv?level=1' -OutFile reports\task_42.csv

# Đẩy issue lên CVAT (mặc định chỉ lỗi `error`) - cần CVAT thật, không dùng --demo
Invoke-RestMethod 'http://127.0.0.1:8081/tasks/42/publish?level=1' -Method Post

# Xem trước, không ghi gì lên CVAT
Invoke-RestMethod 'http://127.0.0.1:8081/tasks/42/publish?severity=warning&dry_run=true' -Method Post
```

Ví dụ bằng `curl`:

```bash
curl 'http://127.0.0.1:8081/tasks/42/report?level=1&refresh=true'
curl -X POST 'http://127.0.0.1:8081/tasks/42/run?level=1'
curl -o task_42.csv 'http://127.0.0.1:8081/tasks/42/report.csv?level=1'
curl -X POST 'http://127.0.0.1:8081/tasks/42/publish?severity=warning&dry_run=true'
```

Mã lỗi: `400` cấu hình/đường dẫn rule sai (kể cả `level` khác 1/2, `severity` lạ) · `404`
sai đường dẫn/task id · `405` gọi `GET /tasks/{id}/run` hoặc `GET /tasks/{id}/publish` ·
`409` publish khi service chạy `--demo`/`--source-file` (không có CVAT để ghi) · `500`
thiếu `cvat-sdk` · `502` không lấy được dữ liệu (CVAT tắt/sai `.env`). Mọi lỗi đều trả
JSON `{"error": "..."}` để UI hiển thị được.

## 5. Ghép với plugin UI của CVAT (tuỳ chọn)

1. Chạy service như mục 2 hoặc 3 (mặc định cổng `8081`).
2. Cho plugin biết địa chỉ service — plugin tự xác định trên browser, theo thứ tự:
   - `localStorage['qaqc.serviceUrl']`:
     `localStorage.setItem('qaqc.serviceUrl', 'http://127.0.0.1:8081')` rồi tải lại trang;
   - tự động dùng `http://127.0.0.1:8081` khi UI mở ở localhost (demo/dev);
   - mặc định `/qaqc` + proxy nginx (xem `docs/plugin-install.md`).
3. Service đã bật CORS (`Access-Control-Allow-Origin: *`) nên UI của CVAT ở
   origin khác (`http://localhost:8080`) gọi được trực tiếp khi debug.

> Plugin nạp ở **build-time**, nên muốn thấy tab QA/QC trong UI của CVAT vẫn phải
> build lại image `cvat_ui` với `CLIENT_PLUGINS=qaqc` (`docker-compose.qaqc.yml`).
> Nhờ service này, bạn kiểm tra được toàn bộ phần backend/API trước khi build.

### Kiểm tra tab QA/QC sau khi build plugin UI

```powershell
npm install puppeteer-core        # 1 lần, không tải browser (dùng Chrome/Edge sẵn có)
node scripts/check_plugin_tab.mjs --task 9 --user dinhvt --password *** --run `
     --screenshot docs\img\qaqc-tab-live.png
```

Script mở Chrome headless, đăng nhập, vào `/tasks/9/quality-control` rồi xác nhận:
tab **QA/QC** có mặt, bảng lỗi đọc được (`15 lỗi / 9 frame ...`), QA service được gọi
(`GET /tasks/9/report?level=1`, thêm `POST /tasks/9/run?level=1` nếu truyền `--run`).
Exit code `0` = đạt. Không thấy tab thì xem mục *"Nếu không thấy tab QA/QC"* trong
[`plugin-install.md`](plugin-install.md) (lỗi hay gặp nhất là thiếu `dispatch` - V31).

## 6. Tuỳ chọn hữu ích

```powershell
python -m qaqc serve --port 9000                 # đổi cổng
python -m qaqc serve --demo --open               # tự mở trình duyệt
python -m qaqc serve --rules rules/level1_v1.yaml --open   # bộ rule Level 1 (cho demo)
python -m qaqc serve --level 1                   # chỉ chạy rule Level 1
python -m qaqc serve --only invalid_size,duplicate_bbox
python -m qaqc serve --cache-ttl 0               # tắt cache (luôn tính lại)
python -m qaqc serve --max-issues 200            # giới hạn số lỗi mỗi báo cáo
python -m qaqc serve --bind-host 0.0.0.0         # cho máy khác trong LAN truy cập
python -m qaqc serve -vv                         # log chi tiết (DEBUG)
```

⚠️ **Bảo mật:** mặc định service chỉ bind `127.0.0.1` (máy local). Khi dùng
`--bind-host 0.0.0.0`, hãy nhớ service **không có xác thực** — chỉ dùng trong mạng
nội bộ tin cậy.

## 7. Xử lý sự cố

| Hiện tượng | Nguyên nhân & cách sửa |
| --- | --- |
| Thoát ngay, `exit=2`, “Thiếu CVAT_HOST” | **Chưa tạo `.env`** — lệnh in sẵn đường dẫn `.env` bị thiếu và 4 cách sửa (gợi ý `--demo`, `Copy-Item .env.example .env`, truyền `--host/--user/--password`, hoặc `--env-file`). Dùng `python -m qaqc serve --demo --open` để xem thử ngay |
| Thoát ngay, `exit=2`, “Thiếu thông tin xác thực CVAT” | `.env` có `CVAT_HOST` nhưng chưa có `CVAT_TOKEN` lẫn `CVAT_USER`/`CVAT_PASS` — bổ sung một trong hai |
| UI báo `HTTP 502` (body có `Unable to log in with provided credentials`) | `.env` khai sai tài khoản/mật khẩu CVAT — thường do giữ nguyên `admin/admin` của `.env.example` trong khi tài khoản thật khác. Xem username thật: `docker exec cvat_db psql -U root -d cvat -t -A -c "select username from auth_user;"`; hoặc dùng Access Token (*Account → Security → Access tokens*) và khai `CVAT_TOKEN` |
| UI báo `HTTP 502` (body khác) | Không lấy được dữ liệu: CVAT tắt, sai `CVAT_HOST`/token, hoặc task không tồn tại |
| Tab QA/QC **từng hiện** rồi biến mất, trang QC chỉ còn tab *Requirements* | `cvat_ui` đã bị tạo lại bằng image gốc: chạy `docker compose up -d` (hoặc restart Docker Desktop) **thiếu file overlay** sẽ kéo `cvat/ui:v2.74.1` chạy lại (đã gặp thật). Kiểm tra `docker ps --format '{{.Names}} {{.Image}}'`; sửa: `docker compose -f docker-compose.yml -f docker-compose.qaqc.overlay.yml up -d --force-recreate cvat_ui` rồi Ctrl+Shift+R |
| Build lại image plugin **cùng tag** nhưng tab vẫn là bản cũ | Compose so sánh cấu hình chứ không so sánh image id: phải `... up -d --force-recreate cvat_ui` (hoặc `docker rm -f cvat_ui` rồi up) mới nạp bundle mới |
| UI báo `HTTP 400` | `rules` trỏ tới file `.yaml/.yml` không tồn tại, `only` chứa rule lạ, hoặc `level` khác 1/2 |
| `401 Unauthorized` khi lấy dữ liệu | Token sai/hết hạn. Tool gửi `Authorization: Token <token>` (xem V20 trong [`verified-behaviors.md`](verified-behaviors.md)); tạo token mới ở *Account → Security → Access tokens* rồi cập nhật `.env` |
| Task demo không có lỗi `required_attributes` | Attribute bắt buộc đang khai `default_value` → CVAT tự điền khi import (V22); bỏ `default_value` trong schema label |
| “service không phản hồi” | Service chưa chạy hoặc khác cổng/URL — kiểm tra `/health` bằng trình duyệt |
| Cổng đang bị chiếm | Đổi `--port`, hoặc tìm tiến trình: `Get-NetTCPConnection -LocalPort 8081` |
| Mở *Quality control* của task nhưng **không thấy tab QA/QC** | Plugin chưa vào store: kiểm tra image UI có plugin (`docker exec cvat_ui grep -c qualityControlPage.tabs.items /usr/share/nginx/html/assets/plugin_1.*.min.js`), plugin có `dispatch(actionCreators.addUIComponent(...))` chưa (**V31** - quên `dispatch` thì không có cảnh báo nào), và đã hard-refresh sau khi build. Tab chỉ nằm ở route `/tasks/<id>/quality-control` (V26) |
| Mở `http://localhost:8080/` bị “connection closed” sau khi restart Docker Desktop | Port-forward của Docker Desktop bị kẹt: `docker restart traefik cvat_ui` (đã gặp thật và khắc phục được) |
| Bảng lỗi trống | Task sạch, hoặc bộ lọc/`only` đang giới hạn — xem ô “0 lỗi” và danh sách rule |

## 8. Kiểm thử tính năng này

```powershell
python -m pytest tests/test_service.py -q     # service + mọi endpoint (offline)
python -m pytest -q                           # toàn bộ bộ test
python -m ruff check .
python -m ruff format --check .
```

## 9. Ghi chú thiết kế

- **Vì sao dùng stdlib thay vì FastAPI:** chạy được ngay bằng 1 lệnh, không thêm
  dependency, không cần uvicorn/Docker — phù hợp kiểm thử local và demo; endpoint
  được giữ **trùng khớp** với `cvat-ui/plugins/qaqc/src/ts/service-client.ts`.
- **Cache:** báo cáo được cache theo `(task_id, hash bộ rule)` trong `--cache-ttl`
  giây (mặc định 30) để UI bấm nhiều lần không phải tải lại annotation; `POST /run`
  và `refresh=true` luôn tính lại.
- **Ghi lên CVAT:** mọi endpoint báo cáo (`report`, `report.csv`, `run`) là **chỉ đọc**;
  riêng `POST /tasks/{id}/publish` **ghi** issue lên CVAT (idempotent theo fingerprint -
  bấm lại chỉ báo `skipped_existing`, không sinh issue trùng). Chế độ
  `--demo`/`--source-file` trả `409` vì không có CVAT để ghi, thiếu `cvat-sdk` → `500`,
  không kết nối được CVAT → `502`.
- **Vẫn có CLI:** cùng publisher đó chạy được bằng `python -m qaqc publish ...` (tiện cho
  batch/CI, có `--dry-run`) — xem `README.md`.
- **Giới hạn:** chạy đồng bộ trong process (phù hợp 1 máy, 1–vài người dùng). Nếu cần
  hàng đợi job/nhiều worker, xem mục “Lộ trình” trong `docs/architecture.md`.
