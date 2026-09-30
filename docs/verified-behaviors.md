# Hành vi đã kiểm chứng (CVAT / cvat-sdk)

Tài liệu này ghi lại các hành vi **đã được kiểm chứng bằng thực nghiệm** (đọc
source của CVAT/cvat-sdk hoặc chạy thử trên môi trường thật) và cách code trong
repo dựa vào chúng. Mục đích: tránh giả định sai khi nâng cấp CVAT/SDK.

Ký hiệu: ✅ đã kiểm chứng (có bằng chứng) · ⚠️ cần kiểm chứng lại khi nâng version.

| # | Hành vi | Bằng chứng | Code phụ thuộc |
| --- | --- | --- | --- |
| V1 | cvat-sdk 2.75 chỉ hỗ trợ server `2.75`/`2.76` (`SUPPORTED_SERVER_VERSIONS`), nên target CVAT là **v2.76.0** | `cvat_sdk/version.py`, `cvat_sdk/core/client.py` (`VersionIsNotSupportedError`) | `requirements.txt` (`cvat-sdk>=2.75,<3`) |
| V2 | `IssueWriteRequest` yêu cầu `frame:int`, `position:[float]`, `job:int`, `message:str`; `IssueRead` có `comments`; `PatchedIssueWriteRequest` chỉ có `position/assignee/resolved` | `python -c "from cvat_sdk import models; print(models.IssueWriteRequest.attribute_map)"` | `qaqc/publishers/cvat_issues.py` |
| V3 | `Client.issues.create(spec)` nhận **dict hoặc model**; `Issue.update(values)` gọi `partial_update(patched_issue_write_request=...)`; `Issue.get_comments()` tồn tại; `client.issues.list(job_id=...)` lọc theo job | source `cvat_sdk/core/proxies/issues.py`, `model_proxy.py` | `_create`, `_reopen`, `_resolve_stale`, `_load_existing` |
| V4 | **Track không có `label_id` ở từng keyframe** - nhãn nằm ở cấp track; attribute cấp track áp cho cả track, keyframe chỉ chứa attribute thay đổi | `normalize_shape` nhận `label_id` dự phòng; bug "nhãn `label_id=-1`" bị golden test phát hiện (`tests/test_integration.py`) | `qaqc/normalize.py::normalize_track` |
| V5 | `Task.get_frames_info()` = `task.get_meta().frames`; `FrameMeta` có `width`/`height`; `TaskRead` có `size`, `dimension`, `labels`; `Job` có `get_annotations()`, `get_labels()`, `get_frames_info()`, `get_issues()` | source `cvat_sdk/core/proxies/tasks.py`, `jobs.py`; attribute_map của `FrameMeta`/`TaskRead` | `qaqc/data_source.py` |
| V6 | ⚠️ **ĐÃ SỬA**: API CVAT 2.74.1 trả `spec_id` trong `attributes` là **id số** của attribute (ví dụ `45`), **không phải tên** - phải tra qua schema label (`label.attributes[].id` → `name`). Fixture trong `tests/fixtures` dùng tên nên code phải chấp nhận **cả hai** dạng | `GET /api/tasks/9/annotations` trên CVAT thật: `{"spec_id": 45, "value": "car"}`; `tests/test_normalize.py::test_normalize_attributes_resolves_numeric_spec_id` | `qaqc/normalize.py::normalize_attributes`, `attribute_name_index` |
| V7 | `points` của `ellipse` là `[cx, cy, rx, ry]`, **không phải** 4 góc như rectangle; `mask` là RLE; `cuboid` là 8 toạ độ 4 góc | quy ước của CVAT + `points` trong fixture thật | `qaqc/geometry.py::bbox_of_points` |
| V8 | `type` của shape là `ModelSimple` → phải so sánh qua `str()`/`.value` | code CLI cũ (`getattr(shape, "type")` + `str()`), đã chạy thật | `qaqc/normalize.py::_shape_type` |
| V9 | UI plugin của CVAT nạp ở **build-time** qua env `CLIENT_PLUGINS` (mặc định `plugins/sam`); mỗi plugin cần entry `plugins/<name>/src/ts/index.tsx` | `cvat-ui/webpack.config.js`, `Dockerfile.ui` (`ARG CLIENT_PLUGINS`) | `docker-compose.qaqc.yml`, `cvat-ui/plugins/qaqc/src/ts/index.tsx` |
| V10 | Plugin đăng ký component ở runtime bằng `window.cvatUI.registerComponent(builder)` sau sự kiện `plugins.ready`; builder trả `{name, destructor, globalStateDidUpdate}` | `cvat-ui/src/components/plugins-entrypoint.tsx`, mẫu `cvat-ui/plugins/sam/src/ts/index.tsx` | plugin skeleton |
| V11 | Điểm mở rộng UI có sẵn: `qualityControlPage.tabs.items`, `taskActions.items`, `jobActions.items`, `components.router`, `annotationPage.header.menu.beforeJobFinish`; có thể override `qualityControlPage.task.requirementsTab` | `cvat-ui/src/plugins/plugins-actions.ts`, `plugins-reducer.ts`, `index.tsx` | plugin skeleton |
| V12 | ⚠️ Đăng ký **plugin tab** vào `qualityControlPage.tabs.items` sẽ **ẩn tab "Requirements" gốc** (điều kiện render là `!pluginTabs.length && qualitySettings`) | `quality-requirements-tab.tsx` | quyết định: chấp nhận, hoặc dùng `updateUIComponent` để thêm lại |
| V13 | App QC sẵn có của CVAT (`cvat/apps/quality_control/`) là so sánh **GT/Consensus** (accuracy, confusion), **không phải** linter annotation → tool này bổ trợ, không trùng chức năng | source `cvat/apps/quality_control/` | định vị sản phẩm |
| V14 | `cvat-sdk` ném `MaxRetryError` (urllib3) khi không kết nối được; `ApiException` khi lỗi HTTP 4xx/5xx | chạy thật CLI khi server off | `qaqc/legacy.py::main`, `qaqc/cli.py::_with_api_error_handling` |
| V15 | `ThreadingHTTPServer` + `protocol_version = "HTTP/1.1"` **bắt buộc** phải gửi `Content-Length` (hoặc chunked); thiếu thì client treo chờ body | chạy thật `qaqc serve` rồi `Invoke-WebRequest`; mọi response trong `tests/test_service.py` đều đọc được | `qaqc/service.py::QAQCHandler._send_body` |
| V16 | Khác origin thì trình duyệt chặn cả request đọc kết quả, nên service phải trả `Access-Control-Allow-Origin` **và** xử lý preflight `OPTIONS` (204 + `Allow-Methods/Headers`) | `test_options_preflight_has_cors_headers`, `test_health_endpoint` | `QAQCHandler.do_OPTIONS`, `_send_cors_headers` |
| V17 | CSV tiếng Việt cần BOM `utf-8-sig` mới hiển thị đúng trong Excel (Windows) | so byte đầu của response (`b"\xef\xbb\xbf"`), `test_csv_endpoint` | `qaqc/report.py::QAReport.write`, `QAQCService.report_csv` |
| V18 | `BaseHTTPRequestHandler.log_message` in ra stderr cho **mọi** request → phải override để CLI/banner không bị rối | chạy `qaqc serve -v` và quan sát output | `QAQCHandler.log_message` |
| V19 | Thiếu cấu hình mà chỉ in một dòng lỗi thì người mới dễ tưởng tool hỏng → `serve` phải in **đường dẫn `.env` bị thiếu + 4 cách sửa** (có `--demo`) và thoát mã 2 | `test_cli_serve_reports_missing_env_file`, `test_cli_serve_reports_missing_credentials`; chạy thật `python -m qaqc serve` khi chưa có `.env` | `qaqc/cli.py::_print_serve_config_hint` |
| V20 | ⚠️ `Client.login()` của cvat-sdk 2.75 **chỉ nhận `credentials`**; xác thực bằng Access Token phải đặt `api_client.configuration.api_key["tokenAuth"]` (header `Authorization: Token …`). Dùng `AccessTokenCredentials`/`configuration.access_token` khiến SDK gửi `Bearer …` → CVAT trả **401** | thử thật trên CVAT 2.74.1: header `Token` → 200, `Bearer` → 401; `python -c "import inspect; from cvat_sdk import Client; print(inspect.signature(Client.login))"` | `qaqc/config.py::CVATConfig.create_client` |
| V21 | `TasksRepo.create_from_data(spec, resources, data_params=…, annotation_path=…, annotation_format=…)` tạo task **và** import annotation trong một lời gọi; tên format import phải là `CVAT 1.1` (`GET /api/server/annotation/formats` không có tên `CVAT XML 1.1`, dù đó là default trong signature) | chạy thật `python scripts/create_demo_task.py` → tạo task 12 frame kèm annotation | `scripts/create_demo_task.py::create_demo_task` |
| V22 | CVAT **tự điền `default_value`** cho attribute bị thiếu khi import pre-annotation → muốn demo lỗi "thiếu attribute" thì attribute bắt buộc **không được** khai `default_value` | task demo #8 (có `default_value` → 0 lỗi `required_attributes`) vs task #9 (bỏ → đúng 1 lỗi) | `scripts/create_demo_task.py::DEMO_LABELS`, `rules/level1_v1.yaml` |
| V23 | `AttributeRequest` của SDK 2.75 **bắt buộc** có `values` (kể cả `input_type=text` → `[]`) và `values` phải là **`list`** (truyền tuple → `ApiTypeError`) | lỗi thật khi chạy script: `TypeError: AttributeRequest._from_openapi_data() missing 1 required positional argument: 'values'` / `Invalid type for variable 'values'` | `scripts/create_demo_task.py::_label_spec` |
| V24 | Console Windows dùng codepage cũ (cp1252) không encode được tiếng Việt → mọi lệnh `python -m qaqc …` chết với `UnicodeEncodeError` (bị `_with_api_error_handling` hiểu nhầm thành "lỗi cấu hình") → CLI phải tự chuyển stdout/stderr sang UTF-8 khi cần | chạy `python -m qaqc rules --level 1` trong PowerShell mặc định | `qaqc/cli.py::_configure_stdout_encoding` |
| V25 | Plugin API của CVAT **v2.74.1** giống v2.76: `window.cvatUI.registerComponent`, sự kiện `plugins.ready`/`plugins.registered`, `actionCreators.addUIComponent('qualityControlPage.tabs.items', …)` đều tồn tại → plugin trong repo không cần patch source CVAT. ⚠️ `addUIComponent` là *action creator*: phải `dispatch(...)` mới có tác dụng - xem V31 | source `C:\cvat-day2\cvat-ui\src\components\plugins-entrypoint.tsx`, `actions\plugins-actions.ts`, `components\quality-control\quality-control-page.tsx:227` | `cvat-ui/plugins/qaqc/src/ts/index.tsx` |
| V26 | Trang Quality control nằm ở route `/tasks/:tid/quality-control` (và `/projects/:pid/quality-control`) - dùng để mở thẳng tab QA/QC sau khi chạy QA | `cvat-ui/src/components/cvat-app.tsx:569` | `scripts/create_demo_task.py` (in link), `docs/level1-demo.md` |
| V28 | ⚠️ `CLIENT_PLUGINS` là danh sách **đường dẫn tương đối từ `cvat-ui/`** (mặc định `plugins/sam`), **không phải tên trần**: webpack nối `path.join(__dirname, pluginPath)<...>/src/ts/index.tsx`; sai thì chỉ in `Not found entrypoint ... The plugin skipped.` và build vẫn "thành công" nhưng **không có plugin** | `cvat-ui/webpack.config.js:18-36`; build thật với `CLIENT_PLUGINS=qaqc` → `Not found entrypoint /tmp/cvat-ui/qaqc/src/ts/index.tsx`; đúng: `plugins/qaqc` | `docker-compose.qaqc.yml`, `docs/plugin-install.md` |
| V29 | CVAT compose 2.74.1: `cvat_ui` nằm sau **traefik** (`Host('localhost')`, loadbalancer trỏ cổng **8000**); nginx trong image `cvat/ui` listen **8000** (không phải 8080) → muốn kiểm tra nginx phải dùng `docker exec cvat_ui netstat -tlnp` hoặc `wget http://127.0.0.1:8000/` | `docker inspect cvat_ui` (labels traefik), `docker exec cvat_ui netstat -tlnp` | `docs/plugin-install.md` |
| V30 | Sau khi engine Docker Desktop bị restart, **port-forward host→8080 có thể kẹt** (listener vẫn LISTENING nhưng request bị "connection closed"/status 000) dù trong network Docker traefik vẫn phục vụ bình thường → `docker restart traefik cvat_ui` là đủ để khôi phục (không cần restart Docker Desktop) | gặp thật sau khi `wsl --terminate docker-desktop`; `curl http://localhost:8080/` = 000 → sau `docker restart traefik cvat_ui` = 200 | `docs/localhost.md` (xử lý sự cố) |
| V27 | ⚠️ `cvat-ui` dùng `dotenv-webpack` (chỉ nạp biến từ file `.env` **tại build context**), và `Dockerfile.ui` **không** copy `.env` → `process.env.X` trong plugin luôn là `undefined` khi chạy. Vì vậy cấu hình runtime (địa chỉ QA service) phải làm ở browser (localStorage), **không** dựa vào `process.env` | `cvat-ui/webpack.config.js:9,203` (`new Dotenv(...)`), `cvat-ui/package.json` (`dotenv-webpack`); `Dockerfile.ui` không COPY `.env` | `cvat-ui/plugins/qaqc/src/ts/service-client.ts::serviceBaseUrl` |
| V31 | ⚠️ **ĐÃ SỬA - lỗi làm tab QA/QC "mất tích"**: `actionCreators.addUIComponent(path, component, {weight})` chỉ **TẠO** action object; **bắt buộc** `dispatch(actionCreators.addUIComponent(...))` thì component mới vào store. Quên `dispatch` thì: bundle vẫn build, `window.cvatUI.registerComponent` vẫn chạy, store vẫn có tên plugin (vì CVAT tự dispatch `addPlugin`), **không có lỗi/cảnh báo nào** trong console - nhưng `state.plugins.components.qualityControlPage.tabs.items` rỗng → tab không hiện (chỉ còn tab `Requirements` gốc) | Đọc thẳng Redux store trong Chrome headless trên CVAT 2.74.1: `tabsItems: []` nhưng `plugins.current = ["Segment Anything", "QA/QC checker"]`; đăng ký thử một tab từ console: không `dispatch` → không có tab, có `dispatch` → tab hiện. Sau khi sửa: tab `QA/QC` hiện, `GET 200 /tasks/9/report?level=1`, nút "Chạy lại QA/QC" → `POST 200 /tasks/9/run?level=1` | `cvat-ui/plugins/qaqc/src/ts/index.tsx`, `scripts/check_plugin_tab.mjs` |
| V32 | Trang Quality control chỉ hiện tab plugin **sau khi** `plugins.ready` được phát (`setTimeout` trong `useEffect` của `plugins-entrypoint.tsx`, ~50 ms sau `DOMContentLoaded`); plugin nạp ở build-time (`defer`, trước `DOMContentLoaded`) nên **không có race** giữa "đăng ký listener" và "event được phát" | Đo trong Chrome headless: `addEventListener('plugins.ready')` @1587 ms < `DOMContentLoaded` @1590 ms < `dispatchEvent('plugins.ready')` @1646 ms | `cvat-ui/plugins/qaqc/src/ts/index.tsx` |
| V33 | `POST /tasks/{id}/publish` chạy **đúng publisher của CLI** (`CvatIssuePublisher`) nên idempotent theo fingerprint nhúng trong `message`; service **luôn chạy lại QA/QC** (bỏ cache) trước khi publish, trả `409` ở `--demo`/`--source-file`, `500` nếu thiếu `cvat-sdk`, `502` khi không kết nối được CVAT | `tests/test_service.py::test_publish_creates_issues`, `::test_publish_second_run_is_idempotent`, `::test_publish_in_demo_mode_returns_conflict`, `::test_get_publish_is_method_not_allowed` | `qaqc/service.py::QAQCService.publish_payload`, `cvat-ui/plugins/qaqc/src/ts/qaqc-tab.tsx` |

## Thay đổi hành vi khi nâng cấp

Khi bump `cvat-sdk`/CVAT, chạy lại:

```powershell
python -m pytest                             # 246 test (offline), có "golden test" khoá số lỗi theo rule
python -m pytest tests/test_service.py -q    # service localhost (offline)
python -m pytest tests/test_levels.py -q     # cấp độ QA (Level 1/2) + config level1_v1.yaml
python -c "from cvat_sdk import models as m; print(list(m.IssueWriteRequest.attribute_map))"
python -c "import inspect; from cvat_sdk import Client; print(inspect.signature(Client.login))"
python -m qaqc rules --level 1
python -m qaqc validate rules/level1_v1.yaml
```

Nếu `IssueWriteRequest`/`FrameMeta` đổi field, cập nhật V2/V5 trong bảng trên và
`qaqc/publishers/cvat_issues.py` + `qaqc/data_source.py` tương ứng.

Hai chỗ **dễ vỡ nhất khi đổi version CVAT/SDK** (đều đã từng gây lỗi thật, xem V6 và
V20-V23): cách xác thực Access Token và cách giải mã `spec_id` trong `attributes`.
Kiểm tra nhanh bằng dữ liệu thật:

```powershell
# 1) token: phải trả 200 (Bearer -> 401)
python -c "import urllib.request; T=open('.env').read(); print(T)"   # xem token trong .env
python -c "import json,urllib.request; req=urllib.request.Request('http://localhost:8080/api/tasks?page_size=1', headers={'Authorization': 'Token <token>'}); print(json.loads(urllib.request.urlopen(req).read())['count'])"

# 2) spec_id -> tên attribute: chạy lại script demo rồi xem báo cáo
python scripts/create_demo_task.py --keep-frames
python -m qaqc run <task_id> --rules rules/level1_v1.yaml
```
