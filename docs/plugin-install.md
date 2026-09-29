# Cài plugin QA/QC vào CVAT (không cần fork `cvat-ui`)

CVAT có **registry plugin UI chính thức**, nạp ở build-time qua biến `CLIENT_PLUGINS`
(đã kiểm chứng: `cvat-ui/webpack.config.js`, `Dockerfile.ui`). Vì vậy việc tích hợp
chỉ cần copy thư mục plugin + build lại image `cvat_ui` - không patch `cvat-ui/src`.

## 1. Chuẩn bị

- CVAT self-hosted đang chạy (đã kiểm chứng trên **v2.74.1**; source ở `C:\cvat-day2`).
  Bản `2.76` cũng dùng cùng API plugin (V1, V25 trong [`verified-behaviors.md`](verified-behaviors.md)).
- Một checkout CVAT **có source `cvat-ui`** (thư mục `Dockerfile.ui`, `cvat-ui/`), ví dụ
  `git clone --depth 1 --branch v2.74.1 https://github.com/cvat-ai/cvat C:\cvat-day2`.
- QA service: `python -m qaqc serve --rules rules/level1_v1.yaml` (xem
  [`docs/localhost.md`](localhost.md)) - chạy **trước** khi mở tab QA/QC.
- Docker Desktop đang chạy (build UI cần tải `node:lts-slim` + `yarn install`, lần đầu
  khá lâu và tốn dung lượng).

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
cd C:\cvat-day2          # checkout CVAT có source cvat-ui + Dockerfile.ui

# (a) Build image MỚI, KHÔNG ghi đè cvat/ui:v2.74.1 đang chạy (an toàn để rollback)
docker build -f Dockerfile.ui -t cvat/ui:qaqc-2.74.1 --build-arg CLIENT_PLUGINS=plugins/qaqc .
```

⚠️ `CLIENT_PLUGINS` là **đường dẫn tương đối từ `cvat-ui/`** (ví dụ `plugins/qaqc`),
không phải tên trần `qaqc`: webpack kiểm tra `<path>/src/ts/index.tsx` và in
`Not found entrypoint ... The plugin skipped.` nếu sai (xem V28 trong
[`verified-behaviors.md`](verified-behaviors.md)). Nhiều plugin: `plugins/qaqc:plugins/khac`.

Hoặc dùng overlay compose của repo này (build rồi chạy trực tiếp):

```powershell
Copy-Item <repo>\docker-compose.qaqc.yml C:\cvat-day2\      # bản mẫu: build arg CLIENT_PLUGINS=plugins/qaqc
docker compose -f docker-compose.yml -f docker-compose.qaqc.yml build cvat_ui
docker compose -f docker-compose.yml -f docker-compose.qaqc.yml up -d
```

Nếu đã build theo cách (a), chỉ cần một overlay nhỏ để `cvat_ui` dùng image mới:

```powershell
@"
services:
  cvat_ui:
    image: cvat/ui:qaqc-2.74.1
"@ | Set-Content C:\cvat-day2\docker-compose.qaqc.overlay.yml
docker compose -f docker-compose.yml -f docker-compose.qaqc.overlay.yml up -d cvat_ui
```

Trong `docker-compose.qaqc.yml` (bản mẫu ở repo này):

- `CLIENT_PLUGINS: qaqc` → build arg của `Dockerfile.ui` → webpack thêm entry
  `plugins/qaqc` (plugin `sam` luôn được thêm mặc định). Plugin **không cần**
  `tsconfig.json` riêng (giống `plugins/sam`).
- `QAQC_SERVICE_URL` → biến môi trường cho UI biết địa chỉ QA service.

Kiểm tra build thành công: log build in ra danh sách plugin, trong đó có
`.../cvat-ui/plugins/qaqc/src/ts/index.tsx`.

> Rollback về UI gốc: `docker compose up -d cvat_ui` (dùng lại `cvat/ui:v2.74.1`).

## 4. Cấu hình địa chỉ QA service

Plugin xác định địa chỉ service **ngay trên browser** (`service-client.ts::serviceBaseUrl`),
theo thứ tự:

1. `localStorage['qaqc.serviceUrl']` - đổi nhanh khi debug trong browser:
   ```js
   localStorage.setItem('qaqc.serviceUrl', 'http://127.0.0.1:8081')
   ```
2. Nếu UI đang mở ở `localhost`/`127.0.0.1` → mặc định `http://127.0.0.1:8081`
   (service chạy trên máy host, đã bật CORS) → tab hoạt động ngay, không cần cấu hình.
3. Mặc định `/qaqc` - cần proxy qua nginx (phần dưới) khi triển khai thật.

> ⚠️ Không dùng `process.env.QAQC_SERVICE_URL`: `cvat-ui` dùng `dotenv-webpack` (chỉ nạp
> biến từ file `.env` trong build context) mà `Dockerfile.ui` không copy `.env` → biến
> này luôn `undefined` khi chạy (V27 trong [`verified-behaviors.md`](verified-behaviors.md)).

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

> ✅ **Đã kiểm chứng đầu-cuối trên CVAT 2.74.1** (29/09/2026): image
> `cvat/ui:qaqc-2.74.1`, tab **QA/QC** hiện trong trang Quality control của task #9 và
> đọc được **15 lỗi** từ QA service (ảnh bằng chứng:
> [`img/qaqc-tab-live.png`](img/qaqc-tab-live.png)).
> ⚠️ Bản build **đầu tiên không hiện tab** vì plugin thiếu `dispatch(...)`; lỗi này im
> lặng (không có cảnh báo trong console) - xem V31 trong
> [`verified-behaviors.md`](verified-behaviors.md).
> Chuyển sang image mới (giữ image gốc để rollback):
> ```powershell
> @"
> services:
>   cvat_ui:
>     image: cvat/ui:qaqc-2.74.1
> "@ | Set-Content C:\cvat-day2\docker-compose.qaqc.overlay.yml
> cd C:\cvat-day2
> docker compose -f docker-compose.yml -f docker-compose.qaqc.overlay.yml up -d cvat_ui
> ```

1. Mở một task → tab **Quality control** (route `http://localhost:8080/tasks/<id>/quality-control`).
2. Tab **QA/QC** xuất hiện (đăng ký qua `qualityControlPage.tabs.items`,
   `weight = 100`).
3. Bảng lỗi hiển thị kèm:
   - bộ lọc **Level 1 – Overall/Completeness · Level 2 – Chi tiết · Tất cả cấp độ**
     (mặc định Level 1; gửi `?level=` xuống QA service),
   - bộ lọc mức độ (`error`/`warning`/`info`), cột **Cấp độ** (L1/L2) và dòng tổng hợp
     `Level 1: n, Level 2: m (bộ rule: …)`,
   - nút **Chạy lại QA/QC** (POST `/tasks/{id}/run`) và **Tải lại kết quả** (đọc cache).

Kiểm tra tự động bằng Chrome headless (khuyến nghị - bắt được cả lớp lỗi V31 mà test
Python không thấy được):

```powershell
npm install puppeteer-core        # 1 lần, không tải browser (dùng Chrome/Edge sẵn có)
node scripts/check_plugin_tab.mjs --task 9 --user dinhvt --password *** `
     --run --screenshot docs\img\qaqc-tab-live.png
```

Script in ra JSON (`tabs`, `summary`, `issuesFound`, `serviceRequests`, `pageErrors`) và
exit code `0`/`1` - tiện gắn vào CI sau khi build image. Không có mật khẩu thì truyền
`--session <sessionid>` (cách lấy sessionid ghi ở đầu file script), hoặc
`--puppeteer <đường-dẫn-tới-module-puppeteer-core>` nếu đã cài ở nơi khác.

### Nếu **không thấy tab QA/QC**

| Hiện tượng | Nguyên nhân & cách sửa |
| --- | --- |
| Trang Quality control chỉ có tab **Requirements** | Plugin chưa vào store. Kiểm tra theo thứ tự: **(1)** `cvat_ui` dùng đúng image: `docker ps --format '{{.Names}} {{.Image}}'` → `cvat/ui:qaqc-2.74.1`; bundle có plugin: `docker exec cvat_ui grep -c qualityControlPage.tabs.items /usr/share/nginx/html/assets/plugin_1.*.min.js` (phải ≥ 1). **(2)** Plugin có `dispatch(actionCreators.addUIComponent(...))` chưa (V31) - chỉ gọi action creator thì tab **im lặng** không hiện, không có lỗi nào trong console. **(3)** Hard-refresh trình duyệt (Ctrl+Shift+R): `index.html` trỏ tới hash bundle mới sau mỗi lần build. **(4)** Đúng route chưa: tab nằm ở `/tasks/<id>/quality-control`, **không** có ở danh sách Tasks hay trang chi tiết task (V26). |
| Tab hiện nhưng báo "Không lấy được kết quả QA/QC" | QA service chưa chạy (`python -m qaqc serve --rules rules/level1_v1.yaml`) hoặc sai URL: plugin tự dùng `http://127.0.0.1:8081` khi UI mở ở localhost; ép thủ công bằng `localStorage.setItem('qaqc.serviceUrl', 'http://127.0.0.1:8081')` rồi tải lại (V27) |
| `http://localhost:8080/tasks/9/quality-control` bị "connection closed" | Port-forward của Docker Desktop bị kẹt sau khi restart engine: `docker restart traefik cvat_ui` (V30) |

## ⚠️ Lưu ý đã kiểm chứng

- **API plugin của CVAT v2.74.1 giống v2.76**: `window.cvatUI.registerComponent`,
  `plugins.ready`, `actionCreators.addUIComponent('qualityControlPage.tabs.items', …)`
  đều tồn tại (V25 trong [`verified-behaviors.md`](verified-behaviors.md)) → plugin
  trong repo **không cần patch source `cvat-ui`**.
- **Plugin nạp ở build-time**: sửa plugin xong phải build lại image `cvat_ui`. Trong
  workspace repo này plugin **chưa từng được compile** (không có `node_modules`); lần
  build đầu tiên là bước kiểm chứng cuối cùng.
- **Tab "Requirements" gốc sẽ bị ẩn** khi có plugin tab: điều kiện render trong
  `quality-control-page.tsx` là `if (!pluginTabs.length && qualitySettings)`.
  Muốn giữ, dùng `actionCreators.updateUIComponent('qualityControlPage.task.requirementsTab', ...)`
  để tự render lại nội dung đó (xem V12 trong `verified-behaviors.md`).
- **QA service phải chạy trước**: `python -m qaqc serve --rules rules/level1_v1.yaml`
  (mặc định `http://127.0.0.1:8081`). Service không có xác thực và chỉ bind localhost.
- Service **không ghi** gì lên CVAT; muốn đẩy lỗi thành issue (idempotent) thì dùng
  CLI `python -m qaqc publish` - issue hiện trong tab *Issues* kèm jump-to-frame
  (xem [`docs/level1-demo.md`](level1-demo.md)).

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
