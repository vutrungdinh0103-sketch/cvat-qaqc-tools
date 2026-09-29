"""CVAT QA/QC toolkit - kiểm tra chất lượng annotation trước khi review.

Package được tổ chức thành 3 lớp, tách biệt rõ ràng:

1. **Engine** (``qaqc.model``, ``qaqc.geometry``, ``qaqc.rules``, ``qaqc.engine``)
   - thuần Python + pydantic/shapely/numpy, **không phụ thuộc CVAT**, chạy được
   offline và test được bằng fixture JSON. Nhận vào :class:`qaqc.model.TaskData`
   đã được chuẩn hoá và trả ra :class:`qaqc.report.QAReport`.
2. **Cầu nối CVAT** (``qaqc.data_source``, ``qaqc.publishers``) - dùng SDK chính
   thức ``cvat_sdk`` để tải annotations và đẩy các lỗi phát hiện được thành
   *issue* trên job tương ứng (idempotent theo fingerprint).
3. **Giao diện sử dụng** (``qaqc.cli``, ``qaqc.service``) - CLI cho CI và HTTP
   service cho CVAT UI plugin.

Ví dụ nhanh::

    python -m qaqc run 42 --rules rules/driving_v1.yaml -o reports/task_42.json
    python -m qaqc publish 42 --rules rules/driving_v1.yaml --publish-severity error
    python checker.py 42 --output reports/task_42.json --fail-on-issues   # CLI cũ
"""

from __future__ import annotations

__version__ = "0.2.0"

#: Phiên bản schema báo cáo JSON (tăng khi thay đổi cấu trúc, có phá vỡ tương thích).
SCHEMA_VERSION = "qaqc/1"

#: Tiền tố đánh dấu issue do tool này tạo ra trên CVAT (dùng để nhận diện khi re-run).
ISSUE_TAG = "[QA]"

__all__ = ["ISSUE_TAG", "SCHEMA_VERSION", "__version__"]
