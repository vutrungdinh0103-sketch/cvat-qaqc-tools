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
| V6 | Shape attribute trong API dùng `spec_id` = **tên attribute** (không phải id số); `AttributeVal{spec_id, value}` | `models.AttributeVal.attribute_map` + payload thật của CVAT | `qaqc/normalize.py::normalize_attributes` |
| V7 | `points` của `ellipse` là `[cx, cy, rx, ry]`, **không phải** 4 góc như rectangle; `mask` là RLE; `cuboid` là 8 toạ độ 4 góc | quy ước của CVAT + `points` trong fixture thật | `qaqc/geometry.py::bbox_of_points` |
| V8 | `type` của shape là `ModelSimple` → phải so sánh qua `str()`/`.value` | code CLI cũ (`getattr(shape, "type")` + `str()`), đã chạy thật | `qaqc/normalize.py::_shape_type` |
| V9 | UI plugin của CVAT nạp ở **build-time** qua env `CLIENT_PLUGINS` (mặc định `plugins/sam`); mỗi plugin cần entry `plugins/<name>/src/ts/index.tsx` | `cvat-ui/webpack.config.js`, `Dockerfile.ui` (`ARG CLIENT_PLUGINS`) | `docker-compose.qaqc.yml`, `cvat-ui/plugins/qaqc/src/ts/index.tsx` |
| V10 | Plugin đăng ký component ở runtime bằng `window.cvatUI.registerComponent(builder)` sau sự kiện `plugins.ready`; builder trả `{name, destructor, globalStateDidUpdate}` | `cvat-ui/src/components/plugins-entrypoint.tsx`, mẫu `cvat-ui/plugins/sam/src/ts/index.tsx` | plugin skeleton |
| V11 | Điểm mở rộng UI có sẵn: `qualityControlPage.tabs.items`, `taskActions.items`, `jobActions.items`, `components.router`, `annotationPage.header.menu.beforeJobFinish`; có thể override `qualityControlPage.task.requirementsTab` | `cvat-ui/src/plugins/plugins-actions.ts`, `plugins-reducer.ts`, `index.tsx` | plugin skeleton |
| V12 | ⚠️ Đăng ký **plugin tab** vào `qualityControlPage.tabs.items` sẽ **ẩn tab "Requirements" gốc** (điều kiện render là `!pluginTabs.length && qualitySettings`) | `quality-requirements-tab.tsx` | quyết định: chấp nhận, hoặc dùng `updateUIComponent` để thêm lại |
| V13 | App QC sẵn có của CVAT (`cvat/apps/quality_control/`) là so sánh **GT/Consensus** (accuracy, confusion), **không phải** linter annotation → tool này bổ trợ, không trùng chức năng | source `cvat/apps/quality_control/` | định vị sản phẩm |
| V14 | `cvat-sdk` ném `MaxRetryError` (urllib3) khi không kết nối được; `ApiException` khi lỗi HTTP 4xx/5xx | chạy thật CLI khi server off | `qaqc/legacy.py::main`, `qaqc/cli.py::_with_api_error_handling` |

## Thay đổi hành vi khi nâng cấp

Khi bump `cvat-sdk`/CVAT, chạy lại:

```powershell
python -m pytest                      # 183 test, có 1 "golden test" khoá số lỗi theo rule
python -c "from cvat_sdk import models as m; print(list(m.IssueWriteRequest.attribute_map))"
python -m qaqc rules --json | Select-Object -First 5
```

Nếu `IssueWriteRequest`/`FrameMeta` đổi field, cập nhật V2/V5 trong bảng trên và
`qaqc/publishers/cvat_issues.py` + `qaqc/data_source.py` tương ứng.
