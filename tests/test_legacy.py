"""Test lớp tương thích ngược: ``qaqc.legacy`` và ``checker.py``.

Mục tiêu: chắc chắn CLI/schema/exit code cũ không bị thay đổi, để các CI đang
chạy ``python checker.py`` không bị phá.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

import checker
from qaqc import legacy
from qaqc.legacy import (
    CSV_FIELDS,
    Box,
    CVATChecker,
    CVATConfig,
    Issue,
    IssueKind,
    QAReport,
    compute_iou,
)

RECT = {"id": 1, "type": "rectangle", "frame": 0, "label_id": 1, "points": [10.0, 5.0, 50.0, 45.0]}
RECT_CLOSE = {
    "id": 2,
    "type": "rectangle",
    "frame": 0,
    "label_id": 1,
    "points": [11.0, 6.0, 51.0, 46.0],
}
RECT_BROKEN = {
    "id": 3,
    "type": "rectangle",
    "frame": 1,
    "label_id": 1,
    "points": [60.0, 10.0, 60.0, 40.0],
}


def _checker(**kwargs) -> CVATChecker:
    """Checker với cấu hình giả (không cần CVAT server)."""
    config = CVATConfig(host="http://localhost:8080", user="u", password="p")
    return CVATChecker(config, **kwargs)


def test_checker_module_reexports_public_api() -> None:
    """``checker.py`` vẫn export đúng các tên cũ."""
    assert checker.CVATChecker is legacy.CVATChecker
    assert checker.CVATConfig is CVATConfig
    assert checker.IssueKind is IssueKind
    assert checker.compute_iou is compute_iou
    assert callable(checker.main)
    assert callable(checker.build_arg_parser)


def test_extract_boxes_skips_unsupported_outside_and_bad_points() -> None:
    """Chỉ nhận rectangle/polygon đang hiển thị (giống bản cũ)."""
    shapes = [
        RECT,
        {"id": 4, "type": "ellipse", "frame": 0, "label_id": 1, "points": [5, 5, 2, 2]},
        {"id": 5, "type": "mask", "frame": 0, "label_id": 1, "points": [1, 2]},
        {"id": 6, "type": "polygon", "frame": 1, "label_id": 1, "points": [0, 0, 0, 10, 10, 10]},
        {
            "id": 7,
            "type": "rectangle",
            "frame": 1,
            "label_id": 1,
            "points": [0, 0, 5, 5],
            "outside": True,
        },
        {"id": 8, "type": "polygon", "frame": 2, "label_id": 1, "points": [0, 0, 1]},
    ]
    boxes, skipped = _checker().extract_boxes(shapes, {1: "vehicle"})

    assert [box.shape_id for box in boxes] == [1, 6]
    assert skipped == 4
    assert boxes[1].shape_type == "polygon"
    assert boxes[0].label == "vehicle"


def test_check_invalid_size_message_and_details() -> None:
    """Message/details của lỗi kích thước giữ nguyên định dạng cũ."""
    checker_ = _checker()
    boxes, _ = checker_.extract_boxes([RECT_BROKEN], {1: "vehicle"})
    issues = checker_.check_invalid_size(boxes, task_id=42)

    assert len(issues) == 1
    assert issues[0].kind is IssueKind.INVALID_SIZE
    assert issues[0].message == (
        "[invalid_size] frame 1, shape #3 (rectangle, label='vehicle'): "
        "width=0, height=30, bbox=(60, 10, 60, 40)"
    )
    assert issues[0].shape_id == 3
    assert issues[0].details["width"] == 0
    assert issues[0].details["height"] == 30


def test_check_duplicates_message_and_details() -> None:
    """Message/details của lỗi trùng lặp giữ nguyên định dạng cũ."""
    checker_ = _checker()
    boxes, _ = checker_.extract_boxes([RECT, RECT_CLOSE], {1: "vehicle"})
    issues = checker_.check_duplicates(boxes, task_id=42)

    assert len(issues) == 1
    assert issues[0].kind is IssueKind.DUPLICATE
    assert issues[0].message.startswith("[duplicate] frame 0, label 'vehicle': shape #1")
    assert "IoU=0.9059 > 0.85" in issues[0].message
    assert issues[0].details["shape_id_a"] == 1
    assert issues[0].details["shape_id_b"] == 2


def test_check_duplicates_respects_custom_threshold() -> None:
    """``--iou`` vẫn hoạt động."""
    checker_ = _checker(iou_threshold=0.99)
    boxes, _ = checker_.extract_boxes([RECT, RECT_CLOSE], {1: "vehicle"})
    assert checker_.check_duplicates(boxes, task_id=42) == []


def test_run_on_shapes_report_schema() -> None:
    """Schema JSON của báo cáo cũ không đổi (kể cả tên khoá)."""
    report = _checker().run_on_shapes(
        [RECT, RECT_CLOSE, RECT_BROKEN],
        task_id=42,
        label_names={1: "vehicle"},
        task_name="t",
    )
    payload = report.to_json_dict()

    assert set(payload) == {
        "task_id",
        "task_name",
        "generated_at",
        "iou_threshold",
        "shapes_total",
        "shapes_skipped",
        "boxes_scanned",
        "frames_scanned",
        "issues",
        "counts_by_kind",
    }
    assert payload["counts_by_kind"] == {"invalid_size": 1, "duplicate": 1}
    assert payload["shapes_total"] == 3
    assert payload["boxes_scanned"] == 3
    assert payload["frames_scanned"] == 2
    assert {issue["kind"] for issue in payload["issues"]} == {"invalid_size", "duplicate"}


def test_run_on_shapes_summary_lines() -> None:
    """Tóm tắt in ra màn hình giữ nguyên câu chữ cũ."""
    report = _checker().run_on_shapes([RECT_BROKEN], task_id=42, label_names={1: "vehicle"})
    text = "\n".join(report.summary_lines())
    assert "Shape đọc được: 1 (kiểm tra 1, bỏ qua 0)" in text
    assert "Tổng số lỗi: 1 (invalid_size=1, duplicate=0)" in text


def test_save_report_json_and_csv(tmp_path: Path) -> None:
    """Ghi báo cáo JSON/CSV đúng định dạng cũ."""
    checker_ = _checker()
    report = checker_.run_on_shapes([RECT_BROKEN], task_id=42, label_names={1: "vehicle"})

    json_path = checker_.save_report(report, tmp_path / "r.json")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["issues"][0]["kind"] == "invalid_size"

    csv_path = checker_.save_report(report, tmp_path / "r.csv", "csv")
    assert csv_path.read_bytes().startswith(b"\xef\xbb\xbf")
    with csv_path.open(encoding="utf-8-sig", newline="") as file_obj:
        rows = list(csv.DictReader(file_obj))
    assert list(rows[0].keys()) == list(CSV_FIELDS)
    assert rows[0]["kind"] == "invalid_size"


def test_save_report_rejects_unknown_format(tmp_path: Path) -> None:
    """Định dạng lạ vẫn raise ValueError như cũ."""
    with pytest.raises(ValueError, match="không được hỗ trợ"):
        _checker().save_report(QAReport(task_id=1), tmp_path / "r.xlsx", "xlsx")


def test_box_helpers() -> None:
    """``Box`` giữ nguyên API (width/height/is_degenerate/bbox_str/to_geometry)."""
    box = Box(frame=0, label_id=1, label="vehicle", xtl=0, ytl=0, xbr=10, ybr=20)
    assert (box.width, box.height) == (10, 20)
    assert box.is_degenerate is False
    assert box.bbox_str() == "(0, 0, 10, 20)"
    assert box.to_geometry() is not None
    assert Box(frame=0, label_id=1, label="v", xtl=5, ytl=5, xbr=5, ybr=9).to_geometry() is None


def test_main_returns_config_error(monkeypatch, tmp_path: Path) -> None:
    """Thiếu cấu hình -> mã thoát 2 (như cũ)."""
    for name in ("CVAT_HOST", "CVAT_USER", "CVAT_PASS", "CVAT_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    assert legacy.main(["42", "--env-file", str(tmp_path / "khong-ton-tai.env")]) == 2


def _patch_checker(monkeypatch, report_factory) -> None:
    """Thay các lời gọi mạng của ``CVATChecker`` bằng hàm giả."""
    monkeypatch.setattr(CVATChecker, "connect", lambda self: self._client)
    monkeypatch.setattr(CVATChecker, "close", lambda self: None)
    monkeypatch.setattr(CVATChecker, "run", lambda self, task_id: report_factory(task_id))


def _set_credentials(monkeypatch) -> None:
    """Đặt biến môi trường đủ để tạo ``CVATConfig``."""
    monkeypatch.setenv("CVAT_HOST", "http://localhost:8080")
    monkeypatch.setenv("CVAT_USER", "u")
    monkeypatch.setenv("CVAT_PASS", "p")
    monkeypatch.delenv("CVAT_TOKEN", raising=False)


def test_main_returns_one_when_issues_found(monkeypatch, tmp_path: Path) -> None:
    """``--fail-on-issues`` -> mã thoát 1 và ghi được báo cáo."""
    _set_credentials(monkeypatch)

    def factory(task_id: int) -> QAReport:
        return QAReport(
            task_id=task_id,
            issues=[
                Issue(
                    kind=IssueKind.INVALID_SIZE,
                    task_id=task_id,
                    frame=0,
                    shape_id=1,
                    label="vehicle",
                    message="[invalid_size] ...",
                )
            ],
        )

    _patch_checker(monkeypatch, factory)
    output = tmp_path / "reports" / "r.json"
    assert legacy.main(["42", "--fail-on-issues", "-o", str(output)]) == 1
    assert output.exists()


def test_main_returns_zero_without_issues(monkeypatch) -> None:
    """Không có lỗi -> mã thoát 0 dù bật ``--fail-on-issues``."""
    _set_credentials(monkeypatch)
    _patch_checker(monkeypatch, lambda task_id: QAReport(task_id=task_id))
    assert legacy.main(["42", "--fail-on-issues"]) == 0
    assert legacy.main(["42"]) == 0


def test_main_returns_api_error(monkeypatch) -> None:
    """Lỗi kết nối -> mã thoát 3 (như cũ)."""
    _set_credentials(monkeypatch)

    def boom(self):
        raise OSError("connection refused")

    monkeypatch.setattr(CVATChecker, "connect", boom)
    monkeypatch.setattr(CVATChecker, "close", lambda self: None)
    assert legacy.main(["42"]) == 3
