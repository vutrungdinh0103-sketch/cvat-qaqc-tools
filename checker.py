"""CLI QA/QC cũ (giữ tương thích ngược cho CI đã có).

Từ phiên bản 0.2.0, toàn bộ logic được chuyển vào package :mod:`qaqc`:

- Engine + rule + báo cáo: ``qaqc.engine``, ``qaqc.rules``, ``qaqc.report``.
- Lớp tương thích cho CLI/API cũ: :mod:`qaqc.legacy`.

Module này chỉ re-export các tên công khai cũ nên:

- ``from checker import CVATChecker, CVATConfig, QAReport`` vẫn chạy,
- ``python checker.py 42 -o reports/task_42.json --fail-on-issues`` giữ nguyên
  hành vi, schema JSON/CSV và mã thoát (0/1/2/3).

Các tính năng mới (nhiều rule, phát issue lên CVAT, JUnit...) nằm ở CLI mới::

    python -m qaqc run 42 --rules rules/driving_v1.yaml -o reports/task_42.json
    python -m qaqc publish 42 --rules rules/driving_v1.yaml --publish-severity error
"""

from __future__ import annotations

from qaqc.geometry import compute_iou
from qaqc.legacy import (
    CSV_FIELDS,
    DEFAULT_IOU_THRESHOLD,
    SUPPORTED_SHAPE_TYPES,
    Box,
    CVATChecker,
    CVATConfig,
    Issue,
    IssueKind,
    QAReport,
    build_arg_parser,
    configure_logging,
    main,
)

__all__ = [
    "CSV_FIELDS",
    "DEFAULT_IOU_THRESHOLD",
    "SUPPORTED_SHAPE_TYPES",
    "Box",
    "CVATChecker",
    "CVATConfig",
    "Issue",
    "IssueKind",
    "QAReport",
    "build_arg_parser",
    "compute_iou",
    "configure_logging",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
