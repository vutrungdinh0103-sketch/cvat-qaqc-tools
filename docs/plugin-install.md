# Cài plugin QA/QC vào CVAT (không cần fork `cvat-ui`)

CVAT có **registry plugin UI chính thức**, nạp ở build-time qua biến `CLIENT_PLUGINS`
(đã kiểm chứng: `cvat-ui/webpack.config.js`, `Dockerfile.ui`). Vì vậy việc tích hợp
chỉ cần copy thư mục plugin + build lại image `cvat_ui` - không patch `cvat-ui/src`.

## 1. Chuẩn bị

- CVAT **v2.76.0** (khớp `cvat-sdk` trong `requirements.txt`; xem V1 trong
  [`verified-behaviors.md`](verified-behaviors.md)).
- Một checkout CVAT: `git clone --depth 1 --branch v2.76.0 https://github.com/cvat-ai/cvat`.
- QA service (Phase 3) hoặc dùng tạm CLI để kiểm tra trước.

## 2. Copy plugin

```powershell
# Từ repo này
Copy-Item -Recurse cvat-qaqc-tools\cvat-ui\plugins\qaqc <cvat>\cvat-ui\plugins\qaqc
```

Cấu trúc plugin (webpack tìm entry `plugins/<name>/src/ts/index.tsx`):

```
cvat-ui/plugins/qaqc/
  README.md
  src/ts/index.tsx          # đăng ký tab vào qualityControlPage.tabs.items
  src/ts/qaqc-tab.tsx       # component hiển thị kết quả QA/QC
  src/ts/service-client.ts  # gọi QA service (fetch)
```

## 3. Build UI với plugin

```powershell
cd <cvat>
docker compose -f docker-compose.yml -f docker-compose.qaqc.yml build cvat_ui
docker compose -f docker-compose.yml -f docker-compose.qaqc.yml up -d
```

Trong `docker-compose.qaqc.yml` (bản mẫu ở repo này):

- `CLIENT_PLUGINS: qaqc` → build arg của `Dockerfile.ui` → webpack thêm entry
  `plugins/qaqc` (plugin `sam` luôn được thêm mặc định).
- `QAQC_SERVICE_URL` → biến môi trường cho UI biết địa chỉ QA service.

Kiểm tra build thành công: log build in ra danh sách plugin, trong đó có
`.../cvat-ui/plugins/qaqc/src/ts/index.tsx`.

## 4. Cấu hình địa chỉ QA service

Thứ tự ưu tiên (xem `service-client.ts`):

1. `localStorage['qaqc.serviceUrl']` - đổi nhanh khi debug trong browser:
   ```js
   localStorage.setItem('qaqc.serviceUrl', 'http://localhost:8081/qaqc')
   ```
2. Biến môi trường `QAQC_SERVICE_URL` khi build UI (hoặc lúc chạy container).
3. Mặc định `/qaqc` - cần proxy qua nginx (3 dòng dưới đây).

### Nginx: proxy `/qaqc` sang QA service

Thêm vào `location /api/` block trong `cvat-ui/react_nginx.conf` (hoặc file
`react_nginx.conf` của bản CVAT đang dùng):

```nginx
location /qaqc/ {
    proxy_pass http://qaqc_service:8081/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

Sau đó restart `cvat_ui`: `docker compose restart cvat_ui`.

## 5. Kiểm tra trong CVAT

1. Mở một task → tab **Quality control**.
2. Tab **QA/QC** xuất hiện (đăng ký qua `qualityControlPage.tabs.items`,
   `weight = 100`).
3. Bấm **Chạy lại QA/QC** để yêu cầu service chạy lại và hiển thị bảng lỗi
   (mức độ, rule, frame, nhãn, mô tả) + cảnh báo.

## ⚠️ Lưu ý đã kiểm chứng

- **Tab "Requirements" gốc sẽ bị ẩn** khi có plugin tab: điều kiện render trong
  `quality-control-page.tsx` là `if (!pluginTabs.length && qualitySettings)`.
  Muốn giữ, dùng `actionCreators.updateUIComponent('qualityControlPage.task.requirementsTab', ...)`
  để tự render lại nội dung đó (xem V12 trong `verified-behaviors.md`).
- **Phải build lại image `cvat_ui`** mỗi khi sửa plugin (plugin nạp ở build-time,
  không phải runtime).
- **Plugin skeleton trong repo chưa được compile trong workspace này** (không có
  checkout `cvat-ui` + `node_modules`). Trước khi dùng thật, hãy build theo mục 3
  và sửa type import nếu bản CVAT nội bộ đã đổi cấu trúc `components/plugins-entrypoint`.
- QA service HTTP (`/qaqc/tasks/{id}/report`) là **Phase 3** - chưa có trong repo.
  Trước khi service sẵn sàng, dùng CLI (`python -m qaqc run|publish`) cho CI/hàng đợi.

## Điểm mở rộng khác có thể dùng

Các "điểm mở rộng" đã có sẵn trong `plugins-reducer.ts` (đường dẫn hợp lệ cho
`addUIComponent`):

| Đường dẫn | Vị trí |
| --- | --- |
| `qualityControlPage.tabs.items` | Tab trong trang Quality control (đang dùng) |
| `taskActions.items` / `projectActions.items` / `jobActions.items` | Nút hành động trên task/project/job |
| `header.userMenu.items` | Menu tài khoản |
| `annotationPage.menuActions.items`, `annotationPage.player.slider` | Trang annotation |
| `taskPage.details.topBar.extras`, `projectPage.details.topBar.extras` | Thanh chi tiết task/project |
| `modelsPage.topBar.items`, `modelsPage.modelItem.*` | Trang model |
| `router` | Route mới (trang riêng) |
| `settings.player`, `about.links.items`, `aiTools.interactors.extras` | Khác |

Có thể **override** (thay component gốc) bằng `updateUIComponent` với các đường dẫn
trong `overridableComponents`: `qualityControlPage.task.requirementsTab`,
`qualityControlPage.task.allocationTable`, `qualityControlPage.project.requirementsTab`,
`app.serverUnavailable`, `analyticsReportPage.content`.
