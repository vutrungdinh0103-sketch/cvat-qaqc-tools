"""Test tích hợp: payload CVAT thô -> chuẩn hoá -> engine -> báo cáo.

Đây là "golden test" của cả luồng: nó kiểm tra đồng thời lớp chuẩn hoá, toàn bộ
11 rule và bộ cấu hình ``rules/driving_v1.yaml`` của repo.
"""

from __future__ import annotations

from qaqc.engine import QAQCEngine
from qaqc.model import Severity
from qaqc.normalize import SKIP_INVALID_POINTS, SKIP_OUTSIDE, SKIP_UNSUPPORTED_TYPE
from qaqc.report import ReportFormat


def test_fixture_normalization_counts(anomaly_task_data) -> None:
    """Fixture được chuẩn hoá đúng: số shape, lý do bỏ qua, track, tag, job."""
    data = anomaly_task_data

    assert data.task_id == 42
    assert data.task_name == "driving-test"
    assert data.size == 10
    assert (data.frame_width, data.frame_height) == (100, 50)
    assert data.frame_size_overrides == {}

    # 14 shape độc lập, trong đó bỏ qua skeleton + mask (không có bbox)
    assert data.shapes_total == 14
    assert data.shapes_skipped == 2
    assert data.skipped_reasons[SKIP_UNSUPPORTED_TYPE] == 2
    assert data.skipped_reasons[SKIP_OUTSIDE] == 1
    assert SKIP_INVALID_POINTS not in data.skipped_reasons

    # 12 shape độc lập + 6 keyframe của 3 track
    assert len(data.shapes) == 18
    assert len(data.tracks) == 3
    assert len(data.tags) == 1
    assert [job.id for job in data.jobs] == [7, 8]

    # 17 object đang hiển thị (bỏ shape outside=True)
    assert len(data.objects()) == 17
    assert data.object_by_key("s14").outside is True

    # attribute cấp track được áp xuống keyframe
    assert data.object_by_key("t100@f6").attribute("vehicle_type") == "car"
    assert data.object_by_key("t100@f6").attribute("color") == "white"

    # label + attribute schema
    assert data.label_names()[1] == "vehicle"
    assert data.label_schema_by_name("vehicle").attribute_names() == ("vehicle_type", "color")


def test_fixture_engine_finding_per_rule(anomaly_task_data, driving_config) -> None:
    """Mỗi rule phát hiện đúng số lỗi trên fixture (golden test chống hồi quy)."""
    report = QAQCEngine(driving_config).run(anomaly_task_data)
    assert report.counts_by_rule == {
        "duplicate_bbox": 1,
        "invalid_size": 1,
        "must_be_inside": 1,
        "out_of_frame": 1,
        "required_attributes": 4,
        "tiny_box": 1,
        "track_class_change": 1,
        "track_gap": 1,
        "unexpected_label": 1,
    }
    assert report.max_severity is Severity.ERROR
    assert report.frames_scanned == 7  # các frame 0..6 có annotation


def test_fixture_engine_issue_details(anomaly_task_data, driving_config) -> None:
    """Một số chi tiết quan trọng của lỗi được kiểm tra kỹ."""
    report = QAQCEngine(driving_config).run(anomaly_task_data)
    by_rule = {issue.rule_id: issue for issue in report.issues}

    assert by_rule["invalid_size"].details["width"] == 0
    assert by_rule["out_of_frame"].details["overflow_x_px"] == 20
    assert by_rule["duplicate_bbox"].details["iou"] > 0.85
    assert by_rule["must_be_inside"].details["outer_label"] == "vehicle"
    assert by_rule["track_gap"].details["gap_frames"] == 4
    assert by_rule["track_class_change"].details["label_after"] == "vehicle"

    # job được suy ra đúng từ frame (job 7: 0-4, job 8: 5-9)
    assert by_rule["duplicate_bbox"].job_id == 7  # frame 0
    assert by_rule["track_class_change"].job_id == 8  # frame 5
    assert by_rule["track_gap"].job_id == 8  # frame 6


def test_fixture_report_is_reproducible(anomaly_task_data, driving_config, tmp_path) -> None:
    """Chạy 2 lần cho cùng fingerprint và ghi được cả 3 định dạng báo cáo."""
    first = QAQCEngine(driving_config).run(anomaly_task_data)
    second = QAQCEngine(driving_config).run(anomaly_task_data)
    assert [issue.fingerprint for issue in first.issues] == [
        issue.fingerprint for issue in second.issues
    ]

    for fmt in ReportFormat:
        path = first.write(tmp_path / f"report.{fmt.value}", fmt)
        assert path.exists() and path.stat().st_size > 0


def test_fixture_only_rule_selection(anomaly_task_data, driving_config) -> None:
    """``--only`` chỉ chạy rule được chọn."""
    config = driving_config.with_overrides(only=["invalid_size", "duplicate_bbox"])
    report = QAQCEngine(config).run(anomaly_task_data)
    assert set(report.counts_by_rule) == {"invalid_size", "duplicate_bbox"}


def test_fixture_job_scope(anomaly_payload) -> None:
    """Chạy theo phạm vi job chỉ lấy annotation trong job đó."""
    from qaqc.normalize import build_task_data

    task = anomaly_payload["task"]
    data = build_task_data(
        task_id=task["id"],
        size=task["size"],
        frames_info=anomaly_payload["frames"],
        labels=anomaly_payload["labels"],
        annotations=anomaly_payload["annotations"],
        jobs=anomaly_payload["jobs"],
        source_job_id=8,
        frame_range=(5, 9),
    )
    assert [shape.frame for shape in data.objects()] == [6, 5]
    assert data.job_for_frame(6) == 8
    assert [tag.frame for tag in data.tags] == [5]
