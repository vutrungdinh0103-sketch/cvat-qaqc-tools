# Kiến trúc QA/QC tool

## 3 lớp, phụ thuộc một chiều

```
┌──────────────────────────── Lớp 3: Giao diện ─────────────────────────────┐
│ qaqc/cli.py (python -m qaqc run|publish|rules|validate)                   │
│ qaqc/legacy.py + checker.py (CLI cũ, giữ nguyên schema/exit code)          │
│ cvat-ui/plugins/qaqc (tab QA/QC trong CVAT UI - gọi service HTTP)          │
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
│ qaqc/rules/       : RuleContext + 11 rule (geometry/completeness/temporal) │
│ qaqc/engine.py    : chạy rule -> Finding -> Issue (fingerprint, job_id)    │
│ qaqc/report.py    : QAReport + writer JSON/CSV/JUnit                       │
│ qaqc/config.py    : CVATConfig (env) + RuleConfig (YAML, pydantic)         │
└───────────────────────────────────────────────────────────────────────────┘
```

**Lợi ích của việc tách lớp 1:** toàn bộ logic QA/QC test được **offline** bằng
`TaskData` dựng tay hoặc fixture JSON (183 test chạy trong ~2 giây, không cần CVAT
server). Chỉ 2 module (`data_source.py`, `publishers/cvat_issues.py`) import
`cvat_sdk`.

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

- **Đã xong (v0.2.0):** engine + 11 rule + cấu hình YAML + publisher issue + CLI mới
  + lớp tương thích CLI cũ + 183 test + tài liệu + skeleton plugin UI.
- **Tiếp theo (tuỳ chọn):** QA service HTTP (FastAPI + hàng đợi job) để plugin UI
  gọi thay vì CLI; đồng bộ fingerprint vào Redis/SQLite để tránh đọc lại issue;
  rule mới theo yêu cầu dự án (`cannot_overlap`, `require_nearby`, kiểm tra 3D).
