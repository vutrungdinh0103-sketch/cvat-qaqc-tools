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
3. `builder` dùng `actionCreators.addUIComponent('qualityControlPage.tabs.items', component, { weight })`.
4. CVAT **gọi hàm** tab với props `{ key, targetProps }` và cần nhận về
   `{ key, label, children }` (hoặc `null`); `targetProps.instance` là task/project đang mở.

⚠️ Khi plugin đăng ký tab, tab **Requirements gốc của CVAT sẽ bị ẩn**
(`if (!pluginTabs.length && qualitySettings)` trong `quality-control-page.tsx`).
Muốn giữ lại, dùng `actionCreators.updateUIComponent('qualityControlPage.task.requirementsTab', ...)`.

## Trạng thái

- Skeleton đã viết theo đúng API của CVAT v2.76, **chưa compile trong workspace này**
  (không có checkout `cvat-ui` + `node_modules`).
- QA service HTTP là Phase 3 - chưa có trong repo; khi chưa có service, tab sẽ hiển thị
  lỗi kết nối kèm gợi ý kiểm tra `QAQC_SERVICE_URL`.
