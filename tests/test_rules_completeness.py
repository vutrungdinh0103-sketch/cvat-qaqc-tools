"""Test các rule nhóm đầy đủ (thiếu nhãn, thiếu attribute, nhãn lạ, frame trống)."""

from __future__ import annotations

from conftest import findings_for, make_shape, make_task_data, run_rules
from qaqc.model import NormTag


# ---------------------------------------------------------------------------
# empty_frame
# ---------------------------------------------------------------------------
def test_empty_frame_flags_frames_without_annotations() -> None:
    """Các frame không có object/tag bị báo."""
    data = make_task_data([make_shape("s1", frame=0)], size=3)
    issues = findings_for(
        run_rules(data, {"empty_frame": {"params": {"frame_step": 1}}}), "empty_frame"
    )
    assert [issue.frame for issue in issues] == [1, 2]


def test_empty_frame_respects_step_tags_and_min_annotations() -> None:
    """Bước nhảy frame và tag đều được tôn trọng."""
    tag = NormTag(object_key="g1", frame=2, label_id=3, label="pedestrian")
    data = make_task_data([make_shape("s1", frame=0)], tags=[tag], size=5)
    report = run_rules(data, {"empty_frame": {"params": {"frame_step": 2, "min_annotations": 1}}})
    # kiểm tra frame 0, 2, 4 -> chỉ frame 4 trống
    assert [issue.frame for issue in findings_for(report, "empty_frame")] == [4]


def test_empty_frame_ignore_labels() -> None:
    """Nhãn nằm trong ``ignore_labels`` không tính là annotation."""
    data = make_task_data([make_shape("s1", frame=1, label="ignored", label_id=9)], size=2)
    issues = findings_for(
        run_rules(data, {"empty_frame": {"params": {"ignore_labels": ["ignored"]}}}),
        "empty_frame",
    )
    # frame 0 không có gì, frame 1 chỉ có shape bị ignore -> cả 2 đều trống
    assert [issue.frame for issue in issues] == [0, 1]


# ---------------------------------------------------------------------------
# missing_label
# ---------------------------------------------------------------------------
def test_missing_label_frame_scope() -> None:
    """Nhãn bắt buộc thiếu ở từng frame -> 1 lỗi mỗi frame thiếu."""
    data = make_task_data(
        [
            make_shape("s1", frame=0, label="vehicle", label_id=1),
            make_shape("s2", frame=1, label="pedestrian", label_id=3),
        ],
        size=2,
    )
    config = {"missing_label": {"params": {"labels": ["vehicle"], "scope": "frame"}}}
    issues = findings_for(run_rules(data, config), "missing_label")
    assert [issue.frame for issue in issues] == [1]
    assert "thiếu nhãn 'vehicle'" in issues[0].message
    assert issues[0].details["present"] == 0


def test_missing_label_task_scope() -> None:
    """``scope: task`` chỉ báo 1 lỗi cho cả task."""
    data = make_task_data([make_shape("s1", frame=0, label="vehicle", label_id=1)], size=5)
    config = {"missing_label": {"params": {"labels": ["license_plate"], "scope": "task"}}}
    issues = findings_for(run_rules(data, config), "missing_label")
    assert len(issues) == 1
    assert issues[0].details["scope"] == "task"
    assert "toàn task" in issues[0].message


def test_missing_label_without_configuration_is_skipped() -> None:
    """Chưa cấu hình danh sách nhãn -> rule im lặng."""
    assert (
        run_rules(make_task_data([make_shape("s1", frame=0)], size=3), {"missing_label": {}}).issues
        == []
    )


# ---------------------------------------------------------------------------
# required_attributes
# ---------------------------------------------------------------------------
def test_required_attributes_missing_and_empty_values() -> None:
    """Thiếu hẳn attribute hoặc để trống đều bị báo (mục 3 của .clinerules)."""
    data = make_task_data(
        [
            make_shape("s1", attributes={"color": "white"}),  # thiếu vehicle_type
            make_shape("s2", shape_id=2, attributes={"vehicle_type": "  "}),  # để trống
            make_shape("s3", shape_id=3, attributes={"vehicle_type": "car"}),
        ]
    )
    config = {"required_attributes": {"params": {"labels": {"vehicle": ["vehicle_type"]}}}}
    issues = findings_for(run_rules(data, config), "required_attributes")
    assert len(issues) == 2
    assert "đang để trống" in issues[1].message
    assert issues[0].details["attribute"] == "vehicle_type"
    assert issues[0].details["required_by_config"] == ["vehicle_type"]


def test_required_attributes_default_attributes_for_all_labels() -> None:
    """``default_attributes`` áp cho mọi label không khai báo riêng."""
    data = make_task_data([make_shape("s1", label="trailer", label_id=4, attributes={})])
    config = {"required_attributes": {"params": {"default_attributes": ["quality"]}}}
    issues = findings_for(run_rules(data, config), "required_attributes")
    assert len(issues) == 1
    assert issues[0].label == "trailer"


def test_required_attributes_on_tags() -> None:
    """Tag cũng được kiểm tra attribute bắt buộc."""
    tag = NormTag(object_key="g1", frame=4, label_id=2, label="license_plate")
    data = make_task_data([], tags=[tag])
    config = {"required_attributes": {"params": {"labels": {"license_plate": ["plate_number"]}}}}
    issues = findings_for(run_rules(data, config), "required_attributes")
    assert len(issues) == 1
    assert "tag g1" in issues[0].message


def test_required_attributes_toggle_shape_and_tag_checks() -> None:
    """Có thể tắt kiểm tra trên shape hoặc tag."""
    tag = NormTag(object_key="g1", frame=0, label_id=2, label="license_plate")
    data = make_task_data([make_shape("s1", attributes={})], tags=[tag])
    config = {
        "required_attributes": {
            "params": {
                "labels": {"vehicle": ["color"], "license_plate": ["plate_number"]},
                "check_shapes": False,
            }
        }
    }
    issues = findings_for(run_rules(data, config), "required_attributes")
    assert [issue.label for issue in issues] == ["license_plate"]


def test_required_attributes_without_configuration_is_skipped() -> None:
    """Không cấu hình -> không báo gì."""
    data = make_task_data([make_shape("s1", attributes={})])
    assert run_rules(data, {"required_attributes": {}}).issues == []


# ---------------------------------------------------------------------------
# unexpected_label
# ---------------------------------------------------------------------------
def test_unexpected_label_flags_shapes_and_tags() -> None:
    """Nhãn ngoài danh sách cho phép bị báo cho cả shape và tag."""
    tag = NormTag(object_key="g1", frame=1, label_id=3, label="pedestrian")
    data = make_task_data(
        [
            make_shape("s1", label="vehicle", label_id=1),
            make_shape("s2", shape_id=2, label="trailer", label_id=4),
        ],
        tags=[tag],
    )
    config = {"unexpected_label": {"params": {"allowed_labels": ["vehicle"]}}}
    issues = findings_for(run_rules(data, config), "unexpected_label")
    assert {issue.label for issue in issues} == {"trailer", "pedestrian"}


def test_unexpected_label_without_configuration_is_skipped() -> None:
    """Chưa cấu hình danh sách cho phép -> rule im lặng."""
    data = make_task_data([make_shape("s1", label="trailer", label_id=4)])
    assert run_rules(data, {"unexpected_label": {}}).issues == []
