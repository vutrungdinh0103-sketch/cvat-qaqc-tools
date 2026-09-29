# Plugin QA/QC cho CVAT UI

Plugin này thêm tab **QA/QC** vào trang *Quality control* của CVAT để xem kết quả
kiểm tra chất lượng annotation do tool QA/QC (`qaqc/`) tạo ra.

Chi tiết cài đặt & build: [`docs/plugin-install.md`](../../../docs/plugin-install.md).

## Cấu trúc

| File | Vai trò |
| --- | --- |
| `src/ts/index.tsx` | Entry point (webpack yêu cầu). Đăng ký tab qua `window.cvatUI.registerComponent` |
| `src/ts/qaqc-tab.tsx` | Component hiển thị bảng lỗi (lọc theo mức độ, nút chạy lại) |
| `src/ts/service-client.ts` | Gọi QA service (`GET/POST /tasks/{id}/report`), cấu hình URL |

## Cơ chế (đã kiểm chứng trên CVAT v2.76)

1. Webpack nạp file entry khi build với `CLIENT_PLUGINS=qaqc`; `sam` luôn được thêm mặc định.
2. Sau sự kiện `plugins.ready`, plugin gọi `window.cvatUI.registerComponent(builder)`.
3. `builder` dùng `dispatch(actionCreators.addUIComponent('qualityControlPage.tabs.items', component, { weight }))`.
   ⚠️ **Phải `dispatch`**: chỉ gọi action creator thì action bị bỏ đi, store không đổi và
   tab không hiện (lỗi này **im lặng**, không có cảnh báo trong console) - xem V31 trong
   [`docs/verified-behaviors.md`](../../../docs/verified-behaviors.md).
4. CVAT **gọi hàm** tab với props `{ key, targetProps }` và cần nhận về
   `{ key, label, children }` (hoặc `null`); `targetProps.instance` là task/project đang mở.

⚠️ Khi plugin đăng ký tab, tab **Requirements gốc của CVAT sẽ bị ẩn**
(`if (!pluginTabs.length && qualitySettings)` trong `quality-control-page.tsx`).
Muốn giữ lại, dùng `actionCreators.updateUIComponent('qualityControlPage.task.requirementsTab', ...)`.

## Trạng thái

- Skeleton viết theo đúng API mà **CVAT v2.74.1** cung cấp (đã kiểm chứng bằng source
  tại `C:\cvat-day2`: `plugins-entrypoint.tsx`, `plugins-actions.ts`,
  `quality-control-page.tsx`) - xem V25 trong
  [`docs/verified-behaviors.md`](../../../docs/verified-behaviors.md).
- Plugin vẫn **chưa được compile trong workspace repo này** (không có `node_modules`):
  cần build image `cvat_ui` theo [`docs/plugin-install.md`](../../../docs/plugin-install.md).
- Tab có bộ lọc **Level 1 / Level 2 / tất cả** và cột `Cấp độ` (dữ liệu `level` +
  `counts_by_level` do QA service trả về).
- Tab chỉ render cho **task** (trả `null` cho project/job: QA service chỉ có endpoint
  theo task id). Bộ lọc cấp độ và bảng lỗi nằm trong `qaqc-tab.tsx`.
- QA service HTTP **đã có sẵn trong repo**: `python -m qaqc serve` (mặc định
  `http://127.0.0.1:8081`) - có web UI để xem kết quả và đúng các endpoint mà plugin
  gọi. Khi chưa chạy service, tab hiển thị lỗi kết nối kèm gợi ý kiểm tra
  `QAQC_SERVICE_URL`. Hướng dẫn + xử lý sự cố:
  [`docs/localhost.md`](../../../docs/localhost.md).
