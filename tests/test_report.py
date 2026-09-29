"""Test báo cáo QA/QC: writer JSON/CSV/JUnit, thống kê, mã thoát."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from xml.etree import ElementTree

import pytest

from conftest import make_shape, make_task_data, run_rules
from qaqc.model import Severity
from qaqc.report import (
    CSV_FIELDS,
    EXIT_ISSUES_FOUND,
    EXIT_OK,
    Issue,
    QAReport,
    ReportFormat,
)


@pytest.fixture
def report_with_issue() -> QAReport:
    """Báo cáo có 1 lỗi error (invalid_size) và 1 lỗi warning (tiny_box)."""
    data = make_task_data(
        [
            make_shape("s1", frame=1, points=(60, 10, 60, 40)),
            make_shape("s2", frame=0, shape_id=2, points=(10, 10, 12, 12)),
        ]
    )
    return run_rules(data, {"invalid_size": {}, "tiny_box": {}})


def test_report_json_roundtrip(tmp_path: Path, report_with_issue: QAReport) -> None:
    """File JSON chứa đủ thông tin và là JSON hợp lệ."""
    path = report_with_issue.write(tmp_path / "bao-cao.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "qaqc/1"
    assert payload["task_id"] == 42
    assert payload["counts_by_rule"] == {"invalid_size": 1, "tiny_box": 1}
    assert payload["counts_by_severity"]["error"] == 1
    assert payload["issues"][0]["fingerprint"]
    assert payload["issues"][0]["details"]


def test_report_csv_has_bom_and_fields(tmp_path: Path, report_with_issue: QAReport) -> None:
    """CSV dùng utf-8-sig và đúng bộ cột (để Excel hiển thị tiếng Việt)."""
    path = report_with_issue.write(tmp_path / "bao-cao.csv")
    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")

    with path.open(encoding="utf-8-sig", newline="") as file_obj:
        rows = list(csv.DictReader(file_obj))
    assert list(rows[0].keys()) == list(CSV_FIELDS)
    assert rows[0]["rule_id"] == "invalid_size"
    assert json.loads(rows[0]["details"])["width"] == 0


def test_report_junit_xml(tmp_path: Path, report_with_issue: QAReport) -> None:
    """JUnit XML đọc lại được bằng ElementTree và có số failure đúng."""
    path = report_with_issue.write(tmp_path / "bao-cao.xml")
    suite = ElementTree.fromstring(path.read_text(encoding="utf-8"))
    assert suite.tag == "testsuite"
    assert suite.get("failures") == "2"
    failures = suite.findall("testcase/failure")
    assert len(failures) == 2
    assert {failure.get("type") for failure in failures} == {"invalid_size", "tiny_box"}


def test_report_empty_junit_has_passing_case(tmp_path: Path) -> None:
    """Báo cáo sạch vẫn tạo 1 testcase pass (CI hiển thị xanh)."""
    report = QAReport(task_id=1)
    suite = ElementTree.fromstring(report.to_junit_xml())
    assert suite.get("failures") == "0"
    assert suite.find("testcase").get("name") == "no-issues"


def test_report_format_inference(tmp_path: Path, report_with_issue: QAReport) -> None:
    """Định dạng suy ra từ phần mở rộng file; tham số tường minh được ưu tiên."""
    assert ReportFormat.parse(None, path="x.csv") is ReportFormat.CSV
    assert ReportFormat.parse(None, path="x.xml") is ReportFormat.JUNIT
    # file không có phần mở rộng -> mặc định JSON
    assert ReportFormat.parse(None, path="bao-cao") is ReportFormat.JSON
    # phần mở rộng lạ -> báo lỗi rõ ràng thay vì âm thầm ghi JSON
    with pytest.raises(ValueError, match="không được hỗ trợ"):
        ReportFormat.parse(None, path="x.unknownext")
    assert ReportFormat.parse("junit", path="x.txt") is ReportFormat.JUNIT
    with pytest.raises(ValueError, match="không được hỗ trợ"):
        ReportFormat.parse("pdf", path="x.pdf")

    path = report_with_issue.write(tmp_path / "report.data", "csv")
    assert path.read_bytes().startswith(b"\xef\xbb\xbf")


def test_report_exit_code(report_with_issue: QAReport) -> None:
    """Mã thoát phụ thuộc ngưỡng ``--fail-on``."""
    assert report_with_issue.exit_code(None) == EXIT_OK
    assert report_with_issue.exit_code(Severity.ERROR) == EXIT_ISSUES_FOUND
    assert report_with_issue.exit_code(Severity.INFO) == EXIT_ISSUES_FOUND
    clean = QAReport(task_id=1)
    assert clean.exit_code(Severity.ERROR) == EXIT_OK


def test_report_issues_at_or_above(report_with_issue: QAReport) -> None:
    """Lọc lỗi theo mức độ."""
    assert len(report_with_issue.issues_at_or_above(Severity.ERROR)) == 1
    assert len(report_with_issue.issues_at_or_above(Severity.WARNING)) == 2


def test_report_summary_lines(report_with_issue: QAReport) -> None:
    """Tóm tắt có số liệu và tự cắt bớt danh sách lỗi."""
    text = "\n".join(report_with_issue.summary_lines())
    assert "Tổng số lỗi: 2" in text
    assert "Theo rule: invalid_size=1, tiny_box=1" in text

    truncated = "\n".join(report_with_issue.summary_lines(max_issues=1))
    assert "và 1 lỗi khác" in truncated


def test_report_has_issues_and_max_severity() -> None:
    """``has_issues``/``max_severity`` đúng với báo cáo rỗng."""
    clean = QAReport(task_id=7)
    assert clean.has_issues is False
    assert clean.max_severity is None
    assert clean.counts_by_severity == {"error": 0, "warning": 0, "info": 0}


def test_issue_csv_row_and_kind_alias() -> None:
    """``Issue.to_csv_row`` dùng ``rule_id`` làm ``kind``."""
    issue = Issue(
        rule_id="duplicate_bbox",
        severity=Severity.ERROR,
        task_id=42,
        frame=3,
        message="msg",
        fingerprint="deadbeef",
        details={"iou": 0.9},
    )
    row = issue.to_csv_row()
    assert issue.kind == "duplicate_bbox"
    assert row["severity"] == "error"
    assert json.loads(row["details"]) == {"iou": 0.9}
