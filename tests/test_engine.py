"""Test engine QA/QC: chạy rule, fingerprint, thống kê, cô lập lỗi."""

from __future__ import annotations

import pytest

from conftest import findings_for, make_config, make_shape, make_task_data, run_rules
from qaqc.engine import QAQCEngine
from qaqc.model import JobInfo, Severity


def test_engine_runs_configured_rules_only() -> None:
    """Chỉ rule được bật mới chạy."""
    data = make_task_data([make_shape("s1", points=(60, 10, 60, 40))])
    report = run_rules(data, {"invalid_size": {}, "tiny_box": {"enabled": False}})
    assert [issue.rule_id for issue in report.issues] == ["invalid_size"]


def test_engine_reports_statistics() -> None:
    """Thống kê shape/frame/object được điền vào báo cáo."""
    data = make_task_data(
        [make_shape("s1", frame=0), make_shape("s2", frame=3, shape_id=2)],
        shapes_total=5,
        shapes_skipped=3,
    )
    report = run_rules(data, {})
    assert report.shapes_total == 5
    assert report.shapes_skipped == 3
    assert report.objects_scanned == 2
    assert report.frames_scanned == 2
    assert report.frames_total == 10


def test_engine_sorts_issues_by_severity_then_frame() -> None:
    """Lỗi nghiêm trọng hơn đứng trước, cùng mức thì theo frame."""
    data = make_task_data(
        [
            make_shape("s1", frame=1, points=(60, 10, 60, 40)),  # invalid_size (error)
            make_shape("s2", frame=0, shape_id=2, points=(10, 10, 12, 12)),  # tiny_box (warning)
        ]
    )
    report = run_rules(data, {"invalid_size": {}, "tiny_box": {}})
    assert [issue.severity for issue in report.issues] == [Severity.ERROR, Severity.WARNING]


def test_engine_maps_job_id_from_frames() -> None:
    """``job_id`` được suy ra từ frame của lỗi."""
    data = make_task_data(
        [make_shape("s1", frame=6, points=(60, 10, 60, 40))],
        jobs=[
            JobInfo(id=7, start_frame=0, stop_frame=4),
            JobInfo(id=8, start_frame=5, stop_frame=9),
        ],
    )
    report = run_rules(data, {"invalid_size": {}})
    assert report.issues[0].job_id == 8


def test_engine_fingerprints_are_stable_and_config_sensitive() -> None:
    """Fingerprint ổn định giữa 2 lần chạy, đổi khi tham số rule đổi."""
    data = make_task_data([make_shape("s1", points=(60, 10, 60, 40))])
    first = run_rules(data, {"invalid_size": {}}).issues[0].fingerprint
    second = run_rules(data, {"invalid_size": {}}).issues[0].fingerprint
    other_rules = run_rules(data, {"invalid_size": {}, "tiny_box": {}})
    assert first == second
    invalid_issue = findings_for(other_rules, "invalid_size")[0]
    assert invalid_issue.fingerprint == first


def test_engine_fingerprint_changes_with_threshold() -> None:
    """Đổi ngưỡng IoU -> fingerprint của lỗi trùng lặp đổi theo."""
    data = make_task_data(
        [
            make_shape("s1", points=(10, 5, 50, 45)),
            make_shape("s2", shape_id=2, points=(11, 6, 51, 46)),
        ]
    )
    base = run_rules(data, {"duplicate_bbox": {"params": {"iou_threshold": 0.85}}})
    changed = run_rules(data, {"duplicate_bbox": {"params": {"iou_threshold": 0.8}}})
    assert base.issues[0].fingerprint != changed.issues[0].fingerprint


def test_engine_max_issues_truncates_and_warns() -> None:
    """Giới hạn số lỗi cắt bớt và ghi cảnh báo."""
    data = make_task_data(
        [
            make_shape(f"s{index}", frame=index, shape_id=index, points=(60, 10, 60, 40))
            for index in range(5)
        ]
    )
    engine = QAQCEngine(make_config({"invalid_size": {}}), max_issues=2)
    report = engine.run(data)
    assert len(report.issues) == 2
    assert any("vượt giới hạn" in warning for warning in report.warnings)


def test_engine_isolates_failing_rule(monkeypatch) -> None:
    """Rule bị lỗi không làm hỏng lần chạy, chỉ ghi cảnh báo."""
    from qaqc.rules.geometry_rules import InvalidSizeRule

    def _boom(self, ctx):
        raise RuntimeError("rule hỏng")

    monkeypatch.setattr(InvalidSizeRule, "check", _boom)
    data = make_task_data([make_shape("s1", points=(10, 10, 12, 12))])
    report = run_rules(data, {"invalid_size": {}, "tiny_box": {}})
    assert report.issues  # tiny_box vẫn chạy
    assert any("invalid_size" in warning and "rule hỏng" in warning for warning in report.warnings)


def test_engine_rejects_unknown_rule_id() -> None:
    """Cấu hình có rule không tồn tại -> ValueError ngay khi tạo engine."""
    with pytest.raises(ValueError, match="khong_ton_tai"):
        QAQCEngine(make_config({"khong_ton_tai": {}}))


def test_engine_propagates_data_warnings() -> None:
    """Cảnh báo từ dữ liệu được đưa vào báo cáo."""
    data = make_task_data([], warnings=["cảnh báo từ loader"])
    report = run_rules(data, {})
    assert "cảnh báo từ loader" in report.warnings


def test_engine_describe_rules() -> None:
    """``describe_rules`` liệt kê rule kèm mức độ hiệu lực."""
    engine = QAQCEngine(make_config({"invalid_size": {"severity": "warning"}}))
    lines = engine.describe_rules()
    assert len(lines) == 1
    assert "invalid_size (warning)" in lines[0]


def test_engine_counts_by_rule_and_severity() -> None:
    """Báo cáo tổng hợp số lỗi theo rule và theo mức độ."""
    data = make_task_data(
        [
            make_shape("s1", points=(60, 10, 60, 40)),
            make_shape("s2", shape_id=2, points=(10, 10, 12, 12)),
        ]
    )
    report = run_rules(data, {"invalid_size": {}, "tiny_box": {}})
    assert report.counts_by_rule == {"invalid_size": 1, "tiny_box": 1}
    assert report.counts_by_severity == {"error": 1, "warning": 1, "info": 0}
    assert report.max_severity is Severity.ERROR
