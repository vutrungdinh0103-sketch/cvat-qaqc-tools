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

   Điền `CVAT_HOST` (ví dụ `http://localhost:8080`) và `CVAT_USER`/`CVAT_PASS`
   hoặc `CVAT_TOKEN` (khuyến nghị cho bot/CI).

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

Tham số query dùng chung cho `report`/`report.csv`/`run`:

| Tham số | Ví dụ | Ý nghĩa |
| --- | --- | --- |
| `rules` | `rules=driving_v1` hoặc `rules=rules/driving_v1.yaml` | Bộ rule dùng cho lần chạy |
| `level` | `level=1` | Chỉ chạy rule của cấp độ QA này (1 = Overall/Completeness, 2 = Detailed) |
| `only` | `only=invalid_size,duplicate_bbox` | Chỉ chạy các rule này |
| `disable` | `disable=empty_frame,tiny_box` | Tắt các rule này |
| `refresh` | `refresh=true` | Bỏ cache, tính lại |

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
```

Ví dụ bằng `curl`:

```bash
curl 'http://127.0.0.1:8081/tasks/42/report?level=1&refresh=true'
curl -X POST 'http://127.0.0.1:8081/tasks/42/run?level=1'
curl -o task_42.csv 'http://127.0.0.1:8081/tasks/42/report.csv?level=1'
```

Mã lỗi: `400` cấu hình/đường dẫn rule sai (kể cả `level` khác 1/2) · `404` sai đường
dẫn/task id · `405` gọi `GET /tasks/{id}/run` · `502` không lấy được dữ liệu (CVAT
tắt/sai `.env`). Mọi lỗi đều trả JSON `{"error": "..."}` để UI hiển thị được.

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
| UI báo `HTTP 502` | Không lấy được dữ liệu: CVAT tắt, sai `CVAT_HOST`/token, hoặc task không tồn tại |
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
- **Chỉ đọc:** service **không ghi** gì lên CVAT. Muốn đẩy lỗi thành issue
  (idempotent), dùng `python -m qaqc publish ...` — xem `README.md`.
- **Giới hạn:** chạy đồng bộ trong process (phù hợp 1 máy, 1–vài người dùng). Nếu cần
  hàng đợi job/nhiều worker, xem mục “Lộ trình” trong `docs/architecture.md`.
