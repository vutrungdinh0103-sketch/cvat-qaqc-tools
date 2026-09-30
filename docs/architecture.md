# Kiến trúc QA/QC tool

## 3 lớp, phụ thuộc một chiều

```
┌──────────────────────────── Lớp 3: Giao diện ─────────────────────────────┐
│ qaqc/cli.py (python -m qaqc run|publish|rules|validate|serve)             │
│ qaqc/legacy.py + checker.py (CLI cũ, giữ nguyên schema/exit code)          │
│ cvat-ui/plugins/qaqc (tab QA/QC trong CVAT UI - gọi service HTTP)          │
│ qaqc/service.py + qaqc/webui.py (service HTTP localhost: web UI + API)     │
└───────────────▲───────────────────────────────────────────▲───────────────┘
                │ QAReport (pydantic)                       │ JSON HTTP
┌───────────────┴──────────── Lớp 2: Cầu nối CVAT ──────────┴───────────────┐
│ qaqc/data_source.py  : Task/Job -> TaskData (cvat_sdk.Client)             │
│ qaqc/publishers/     : QAReport -> Issue trên CVAT (idempotent theo fp)    │
└───────────────▲───────────────────────────────────────────────────────────┘
                │ TaskData (model chuẩn hoá, không phụ thuộc SDK)
┌───────────────┴───────────────── Lớp 1: Engine ───────────────────────────┐
│ qaqc/model.py     : NormShape / NormTrack / NormTag / TaskData (track-aware)│
│ qaqc/geometry.py  : IoU, bbox, containment, polygon tự cắt                 │
│ qaqc/rules/       : RuleContext + 13 rule (geometry/completeness/temporal) │
│                     Level 1 = 7 rule (Overall/Completeness), Level 2 = 6     │
│ qaqc/engine.py    : chạy rule -> Finding -> Issue (fingerprint, job_id)    │
│ qaqc/report.py    : QAReport + writer JSON/CSV/JUnit                       │
│ qaqc/config.py    : CVATConfig (env) + RuleConfig (YAML, pydantic)         │
└───────────────────────────────────────────────────────────────────────────┘
```

**Lợi ích của việc tách lớp 1:** toàn bộ logic QA/QC test được **offline** bằng
`TaskData` dựng tay hoặc fixture JSON (246 test chạy trong ~7 giây, không cần CVAT
server). Chỉ 2 module (`data_source.py`, `publishers/cvat_issues.py`) import
`cvat_sdk` — service HTTP cũng không cần import SDK khi chạy `--demo`/`--source-file`.

## Luồng dữ liệu

1. `data_source.fetch_task()` / `fetch_job()` gọi SDK, lấy `annotations`, `labels`,
   `frames_info`, `jobs` rồi chuyển cho `normalize.build_task_data()`.
2. `build_task_data()` tạo `TaskData`:
   - shape độc lập + **keyframe của track được làm phẳng** vào một danh sách object,
     mỗi object có `object_key` ổn định (`s<id>` hoặc `t<id>@f<frame>`),
   - nhãn/attribute của track được kế thừa xuống keyframe (V4),
   - thống kê `shapes_total`, `shapes_skipped`, `skipped_reasons` và cảnh báo.
3. `QAQCEngine.run(data)` chạy các rule đang bật → `Finding` (thô) → `Issue`:
   - gắn `fingerprint` ổn định (dựa trên `RuleConfig.rule_hash(rule_id)`, task, frame,
     object_keys, discriminator),
   - gắn `job_id` từ `frame` (`TaskData.job_for_frame`),
   - sắp xếp theo mức độ rồi frame, khử trùng theo fingerprint, cắt theo `--max-issues`.
4. `QAReport.write()` xuất JSON/CSV/JUnit; `CvatIssuePublisher.publish()` tạo issue
   trên CVAT (bỏ qua fingerprint đã tồn tại, tuỳ chọn resolve issue cũ).

## Vì sao fingerprint là trung tâm của idempotency

API CVAT **không có** trường metadata cho issue, nên `fp=xxxxxxxx` được nhúng
trong `message` (dạng `[QA][sev=error][fp=1a2b3c4d][v=1] ...`). Khi chạy lại:

- fingerprint trùng → bỏ qua (không tạo issue mới),
- issue cũ đã `resolved` → mặc định vẫn bỏ qua; bật `--reopen-resolved` để mở lại,
- fingerprint cũ không còn xuất hiện → `--resolve-stale` tự đánh dấu đã xử lý.

Fingerprint dùng `rule_hash` **của riêng rule** (không phải hash cả bộ cấu hình) để
việc bật/tắt rule khác không làm "reset" toàn bộ issue đã tạo.

## Service HTTP localhost (`qaqc/service.py` + `qaqc/webui.py`)

`python -m qaqc serve` biến engine thành một service HTTP nhỏ **dùng `http.server`
của thư viện chuẩn** (không thêm dependency, không cần uvicorn/Docker), phục vụ 2
mục đích: (1) trang web để con người xem kết quả ngay trên máy, (2) API đúng như
plugin CVAT UI cần.

| Thành phần | Vai trò |
| --- | --- |
| `QAQCService` | Nguồn dữ liệu (demo → file JSON → CVAT), cấu hình rule hiệu lực, cache báo cáo, map lỗi → mã HTTP |
| `QAQCServer` / `QAQCHandler` | `ThreadingHTTPServer` + routing/JSON/CSV/CORS/`OPTIONS` |
| `qaqc/webui.py` | Trang HTML (nhúng trong module, không CDN) gọi chính các endpoint trên |
| `qaqc/demo.py` | `TaskData` mẫu kích hoạt cả 9 rule đang bật → `serve --demo` luôn có kết quả để xem |
| `load_task_data_from_file()` | Đọc payload CVAT từ file JSON để chạy offline (test/demo) |

Điểm cần nhớ khi bảo trì:

- Endpoint phải **trùng** với `cvat-ui/plugins/qaqc/src/ts/service-client.ts`
  (`/health`, `/tasks/{id}/report`, `/tasks/{id}/report.csv`, `/tasks/{id}/run`,
  `POST /tasks/{id}/publish`);
  đổi API ở đây thì phải đổi cả plugin.
- CORS (`Access-Control-Allow-Origin: *`) là **bắt buộc** để UI CVAT (origin khác)
  gọi được; mặc định bind `127.0.0.1` nên vẫn chỉ dùng nội bộ máy.
- Cache theo `(task_id, RuleConfig.config_hash)` → đổi tham số rule sẽ tự tính lại;
  `POST /run` và `?refresh=true` luôn bỏ cache.
- `--demo`/`--source-file` giúp test end-to-end mà không cần CVAT (đây cũng là cách
  `tests/test_service.py` chạy 41 test offline).

## Quyết định thiết kế đáng chú ý

| Quyết định | Lý do |
| --- | --- |
| `rectangle` không chuẩn hoá `min/max` khi đọc `points` | Phải phát hiện được box bị đảo toạ độ (`w <= 0`) |
| Polygon tự cắt được `shapely.make_valid` thay vì lỗi | Dữ liệu thật có polygon lỗi; tool phải báo lỗi chứ không crash |
| `ellipse` lấy bbox từ `[cx, cy, rx, ry]` | Nếu hiểu sai thành 4 góc, mọi ellipse sẽ bị báo tràn khung sai |
| Rule không có cấu hình thì "im lặng" (không báo gì) | Bật mặc định an toàn cho mọi dự án (`missing_label`, `must_be_inside`...) |
| Rule lỗi bị cô lập, chỉ ghi vào `warnings` | Một rule hỏng không làm mất toàn bộ kết quả QA/QC |
| `--max-issues-per-job` lấy fingerprint trước khi cắt | Tránh resolve oan các issue bị cắt do giới hạn |
| CLI cũ giữ trong `qaqc/legacy.py` + `checker.py` shim | Không phá CI đang chạy `python checker.py` (schema/exit code y hệt) |
| Plugin UI thay vì fork `cvat-ui` | CVAT có registry plugin chính thức (V9–V11) → patch footprint ~0 |

## Lộ trình

- **Đã xong (v0.2.0):** engine + 13 rule + cấu hình YAML + publisher issue + CLI mới
  + lớp tương thích CLI cũ + 183 test + tài liệu + skeleton plugin UI.
- **Đã xong (v0.2.x):** QA service HTTP chạy localhost (`python -m qaqc serve`, web UI
  + API JSON/CSV + CORS, dữ liệu mẫu để demo, gợi ý sửa khi thiếu `.env`) + 25 test
  offline → tổng **208 test**.
- **Đã xong (v0.3.0 - demo Level 1 trên CVAT):** 2 rule mới `empty_frame_range` +
  `object_count`, metadata **`level`** (1 = Overall/Completeness, 2 = Detailed) cho mọi
  rule, `--level 1|2` (CLI) + `?level=` (service) + bộ lọc cấp độ trong plugin tab,
  `rules/level1_v1.yaml`, script `scripts/create_demo_task.py` (tạo task có sẵn đủ 8 lỗi
  Level 1), sửa 3 bug thật khi chạy với CVAT 2.74.1 (V6: `spec_id` là id; V20: xác thực
  token; **V31: plugin tab phải `dispatch(actionCreators.addUIComponent(...))`** - thiếu
  `dispatch` thì tab im lặng không hiện). Tab QA/QC đã hiện và đọc được 15 lỗi của task
  demo trên CVAT thật (`docs/img/qaqc-tab-live.png`); script kiểm tra lại:
  `scripts/check_plugin_tab.mjs` (Chrome headless) → tổng **232 test** (offline) + 1
  kiểm tra UI tùy chọn; kịch bản demo: [`level1-demo.md`](level1-demo.md).
- **Đã xong (v0.3.x - đẩy issue lên CVAT từ UI):** `POST /tasks/{id}/publish` (service
  chạy lại QA/QC rồi tạo issue bằng **đúng** `CvatIssuePublisher` của CLI, idempotent theo
  fingerprint; tham số `severity`/`dry_run`/`resolve_stale`/`reopen_resolved`/`post_details`/
  `max_issues_*`), `publish_supported` trong `/health`, `publishTaskIssues()` trong
  `service-client.ts` + nút **Đẩy issue lên CVAT** và ô chọn mức độ trong tab QA/QC →
  tổng **246 test**.
- **Tiếp theo (tuỳ chọn):** hàng đợi job + nhiều worker cho service (FastAPI/uvicorn
  nếu cần mở rộng); đồng bộ fingerprint vào Redis/SQLite để tránh đọc lại issue;
  rule mới theo yêu cầu dự án (`cannot_overlap`, `require_nearby`, kiểm tra 3D).
