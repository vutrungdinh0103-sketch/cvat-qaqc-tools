# Demo Level 1 – Overall / Completeness Check (trên CVAT thật)

Kịch bản demo từng bước để chứng minh 8 kiểm tra **Level 1** của proposal chạy được
trên CVAT tại `http://localhost:8080/`, và lỗi hiện **ngay trong CVAT** (tab *Issues*).

Kết quả đã kiểm chứng trên CVAT **v2.74.1** + `cvat-sdk` 2.75 (xem
[`verified-behaviors.md`](verified-behaviors.md) V20-V26).

## 1. Level 1 gồm những gì (và rule nào làm việc đó)

| # | Mục trong proposal | Rule | Ghi chú |
| --- | --- | --- | --- |
| 1 | Empty frame | `empty_frame` | frame không có object/tag nào |
| 2 | Missing annotation | `object_count` (`min_objects`) | frame ít object hơn quy định |
| 3 | Missing required label | `missing_label` | nhãn bắt buộc vắng mặt (theo frame/toàn task) |
| 4 | Unexpected label | `unexpected_label` | nhãn ngoài `allowed_labels` |
| 5 | Missing attribute | `required_attributes` | thiếu hoặc để trống attribute bắt buộc |
| 6 | Suspicious object count | `object_count` (`max_objects`, `outlier_ratio`) | quá nhiều / vượt xa trung vị |
| 7 | Unlabeled frame range | `empty_frame_range` | ≥ N frame trống **liên tiếp** |
| 8 | Duplicate annotation | `duplicate_bbox` | 2 object cùng frame, IoU > 0.85 |

Xem danh sách đúng theo code: `python -m qaqc rules --level 1`.

## 2. Chuẩn bị (một lần)

```powershell
cd <thư-mục-repo>
python -m pip install -r requirements.txt

# .env: CVAT_HOST + token (Account -> Security -> Access tokens) hoặc user/password
Copy-Item .env.example .env
notepad .env
```

Kiểm tra kết nối trước khi demo (nên in ra danh sách task, không lỗi):

```powershell
python -m qaqc run 1 --level 1 --fail-on none    # task 1 bất kỳ; lỗi 401 -> xem mục 7
```

## 3. Bước 1 – Tạo task demo có sẵn lỗi Level 1

```powershell
python scripts/create_demo_task.py --name "QAQC Level 1 demo"
```

Script sinh 12 ảnh PNG 640×360 rồi tạo task trên CVAT **kèm pre-annotation cố ý sai**:

| Frame | Lỗi cố ý | Rule sẽ báo |
| --- | --- | --- |
| 0 | xe + biển số hợp lệ | (không lỗi - dùng làm tham chiếu) |
| 1 | xe hợp lệ | (không lỗi) |
| 2, 3, 4 | không có annotation | `empty_frame`, `empty_frame_range`, `object_count`, `missing_label` |
| 5 | xe thiếu attribute `color` | `required_attributes` |
| 6 | chỉ có `pedestrian` | `missing_label` |
| 7 | hai xe gần như trùng khít | `duplicate_bbox` |
| 8 | có nhãn `forklift` | `unexpected_label` |
| 9 | 12 xe trong một frame | `object_count` (quá nhiều, vượt trung vị) |
| 10, 11 | xe hợp lệ | (không lỗi) |

Cuối lệnh, script in ra **task id**, **job** và link mở task / Quality control.

## 4. Bước 2 – Chạy QA Level 1 (CLI)

```powershell
# bộ rule Level 1 đã tune sẵn (khuyến nghị cho demo)
python -m qaqc run <task_id> --rules rules/level1_v1.yaml -o reports/level1_task_<task_id>.json

# hoặc không cần file cấu hình - tương đương:
python -m qaqc run <task_id> --level 1
```

Kết quả đã kiểm chứng trên task demo (15 lỗi, đủ cả 8 mục):

```text
Tổng số lỗi: 15 (error=6, warning=9, info=0)
Theo rule: duplicate_bbox=1, empty_frame=3, empty_frame_range=1, missing_label=4,
           object_count=4, required_attributes=1, unexpected_label=1
Theo cấp độ: Level 1 (overall)=15
```

Báo cáo JSON/CSV có thêm `level` cho từng lỗi và `counts_by_level` cho cả báo cáo:

```powershell
python -m qaqc run <task_id> --level 1 -o reports/level1.csv      # mở bằng Excel (UTF-8 BOM)
```

## 5. Bước 3 – Đưa lỗi vào CVAT (native Issues)

```powershell
python -m qaqc publish <task_id> --rules rules/level1_v1.yaml --publish-severity warning
```

Đã kiểm chứng: `Publisher CVAT: tạo mới 15, ...` và CVAT có đúng 15 issue
(`GET /api/issues?task_id=<id>`) - mỗi issue gắn **job + frame + position** nên bấm vào
là **nhảy thẳng tới frame lỗi** trong trình annotate.

Kiểm tra lại/không tạo trùng: chạy lại đúng lệnh publish → `tạo mới 0, đã tồn tại 15`
(idempotent theo `fingerprint`). Cờ hữu ích:

| Cờ | Ý nghĩa |
| --- | --- |
| `--dry-run` | mô phỏng, không ghi lên CVAT |
| `--publish-severity warning` | đẩy cả `warning` (mặc định chỉ đẩy `error`) |
| `--resolve-stale` | tự đánh dấu `resolved` cho issue cũ không còn lỗi |
| `--reopen-resolved` | mở lại issue đã resolved nếu lỗi vẫn còn |
| `--max-issues-per-job 50` | chặn spam issue (mặc định 50/job) |

Mở CVAT: `http://localhost:8080/tasks/<task_id>` → panel **Issues** (hoặc *Review*)
liệt kê 15 lỗi theo frame; click 1 issue để nhảy tới frame đó.

## 6. Bước 4 – Xem kết quả như một tính năng của CVAT

Có 2 cách, dùng được đồng thời:

### 6a. QA service + web UI (không cần build gì)

```powershell
python -m qaqc serve --rules rules/level1_v1.yaml --open
```

- Trang QA/QC ở `http://127.0.0.1:8081/`: nhập Task ID → bảng lỗi + tải JSON/CSV.
- API (đúng thứ plugin CVAT UI gọi):

```powershell
Invoke-RestMethod 'http://127.0.0.1:8081/tasks/<task_id>/report?level=1'   # JSON
Invoke-RestMethod 'http://127.0.0.1:8081/tasks/<task_id>/report.csv?level=1'
Invoke-RestMethod 'http://127.0.0.1:8081/tasks/<task_id>/run?level=1' -Method Post
```

### 6b. Tab **QA/QC** trong CVAT UI (đã build sẵn image `cvat/ui:qaqc-2.74.1`)

Plugin trong repo (`cvat-ui/plugins/qaqc`) thêm tab QA/QC vào trang *Quality control*
của task, có bộ lọc **Level 1 / Level 2 / tất cả** và cột `Cấp độ`. Image đã build
trên CVAT 2.74.1 và `cvat_ui` đang dùng image đó:

```powershell
# đã chạy sẵn; chỉ cần khi muốn bật lại/rollback
cd C:\cvat-day2
docker compose -f docker-compose.yml -f docker-compose.qaqc.overlay.yml up -d cvat_ui   # dùng image có plugin
docker compose up -d cvat_ui                                                           # rollback về UI gốc
```

Mở `http://localhost:8080/tasks/<task_id>/quality-control` → chọn tab **QA/QC**
(bộ lọc mặc định là *Level 1 – Overall/Completeness*, dữ liệu lấy từ QA service ở
`http://127.0.0.1:8081`). Chi tiết build/lỗi thường gặp: [`plugin-install.md`](plugin-install.md).

Sau khi build, mở `http://localhost:8080/tasks/<task_id>/quality-control` → tab
**QA/QC**.

**Trạng thái kiểm chứng (29/09/2026, CVAT 2.74.1):** tab hiện và đọc được 15 lỗi của
task demo — ảnh chụp: [`img/qaqc-tab-live.png`](img/qaqc-tab-live.png). Kiểm tra lại
tự động (bắt cả lỗi "build xong nhưng tab không hiện"):

```powershell
npm install puppeteer-core        # 1 lần, không tải browser
node scripts/check_plugin_tab.mjs --task <task_id> --user dinhvt --password *** --run
```

> ⚠️ Lưu ý quan trọng khi sửa plugin: `actionCreators.addUIComponent(...)` phải được
> `dispatch(...)`; nếu không, tab **không hiện mà không có cảnh báo nào** (V31 trong
> [`verified-behaviors.md`](verified-behaviors.md)).

## 7. Kịch bản nói khi demo (khoảng 5 phút)

1. **Vấn đề** (30s): reviewer phải mở từng frame; lỗi "thiếu nhãn/thiếu attribute/frame
   trống" rất tốn thời gian nhưng kiểm tra tự động được.
2. **Task demo** (30s): mở `http://localhost:8080/tasks/<task_id>` - 12 frame, frame
   2-4 trống, frame 9 có 12 xe, frame 8 có `forklift`... đều do dữ liệu cố ý.
3. **Chạy QA** (60s): `python -m qaqc run <task_id> --rules rules/level1_v1.yaml` →
   in ra 15 lỗi kèm nhóm theo rule và theo cấp độ.
4. **Lỗi nằm trong CVAT** (90s): `python -m qaqc publish ...` → mở panel *Issues*,
   click issue frame 5 (`thiếu attribute 'color'`) → CVAT nhảy đúng frame/box.
5. **Trong UI** (60s): tab **QA/QC** (nếu đã build) hoặc web UI localhost - đổi bộ lọc
   *Level 1 / Level 2 / tất cả* để thấy phạm vi từng cấp độ; chọn mức độ rồi bấm
   **Đẩy issue lên CVAT** trong tab để làm bước 4 **không cần gõ lệnh**.
6. **Chạy trên task thật** (60s): `python -m qaqc run 3 --rules rules/level1_v1.yaml`
   (task DAY08) - lưu ý kết quả phản ánh **cấu hình nhãn của dự án**: nếu project dùng
   nhãn `car` thay vì `vehicle` thì `unexpected_label`/`missing_label` sẽ báo rất nhiều.
   Đúng quy trình là chỉnh `rules/level1_v1.yaml` cho khớp guideline của dự án rồi chạy.
7. **Idempotent** (30s): chạy lại publish → `tạo mới 0, đã tồn tại 15` (không spam issue).

## 8. Điều chỉnh cho dự án thật

Sao chép `rules/level1_v1.yaml` thành ví dụ `rules/<project>.yaml` rồi sửa 3 nhóm tham số:

- `unexpected_label.allowed_labels`: đúng danh sách nhãn của guideline.
- `required_attributes.labels`: attribute bắt buộc theo từng nhãn.
- `missing_label.labels` + `min_count`: nhãn nào phải có ở mỗi frame (hoặc `scope: task`).
- `object_count`: `min_objects`/`max_objects`/`outlier_ratio` theo mật độ object thực tế.
- `empty_frame_range.min_run_frames`: video dài có thể đặt 10-30 frame.

Kiểm tra cấu hình trước khi chạy thật: `python -m qaqc validate rules/<project>.yaml`.

## 9. Xử lý sự cố

| Hiện tượng | Nguyên nhân & cách sửa |
| --- | --- |
| `401 Unauthorized` khi gọi CVAT | Token sai/hết hạn. Tool gửi `Authorization: Token <token>` - xem V20 trong `verified-behaviors.md`. Tạo token lại: `Account -> Security -> Access tokens`, cập nhật `.env` |
| `Server version '2.74.1' is not compatible with SDK version '2.75.0'` | Cảnh báo của `cvat-sdk` khi server cũ hơn 1 phiên bản. Tool vẫn chạy ổn (đã kiểm chứng); muốn hết cảnh báo thì nâng CVAT lên 2.76 |
| Task tạo ra nhưng **không có lỗi `required_attributes`** | Attribute bắt buộc đang có `default_value` → CVAT tự điền khi import (V22). Bỏ `default_value` trong schema label |
| `level=3` / `--level 3` | Chỉ nhận 1 hoặc 2 (CLI báo lỗi, API trả HTTP 400) |
| Publish báo `không map được job` | Frame của lỗi không thuộc job nào (task chưa chia job / job rỗng) |
| Mở *Quality control* nhưng **không thấy tab QA/QC** | Lớp lỗi "im lặng", kiểm tra theo thứ tự: (1) `cvat_ui` có dùng image đã build plugin (`docker ps --format '{{.Names}} {{.Image}}'`) và bundle có plugin (`docker exec cvat_ui grep -c qualityControlPage.tabs.items /usr/share/nginx/html/assets/plugin_1.*.min.js`); (2) plugin có `dispatch(actionCreators.addUIComponent(...))` chưa — **V31**, quên `dispatch` thì tab không hiện và console không báo gì; (3) hard-refresh (Ctrl+Shift+R) sau mỗi lần build; (4) đúng route `/tasks/<id>/quality-control`. Kiểm tra tự động: `node scripts/check_plugin_tab.mjs --task <id> --user dinhvt --password ***` |
| Plugin tab báo lỗi kết nối | QA service chưa chạy, hoặc URL sai: plugin tự dùng `http://127.0.0.1:8081` khi UI mở ở localhost; ép thủ công bằng `localStorage.setItem('qaqc.serviceUrl', 'http://127.0.0.1:8081')` trong browser của CVAT rồi tải lại |

## 10. Kiểm thử lại toàn bộ

```powershell
python -m pytest -q                 # 232 test, offline (không cần CVAT)
python -m pytest tests/test_levels.py -q
python -m ruff check . ; python -m ruff format --check .
python -m qaqc validate rules/level1_v1.yaml
node scripts/check_plugin_tab.mjs --task 9 --user dinhvt --password ***   # tab QA/QC trong CVAT UI (cần puppeteer-core)
```

