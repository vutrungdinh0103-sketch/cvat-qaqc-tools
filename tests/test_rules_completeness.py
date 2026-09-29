"""Test các rule nhóm đầy đủ (thiếu nhãn, thiếu attribute, nhãn lạ, frame trống)."""

from __future__ import annotations

from conftest import findings_for, make_shape, make_task_data, run_rules
from qaqc.model import NormShape, NormTag


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


# ---------------------------------------------------------------------------
# empty_frame_range (Level 1: unlabeled frame range)
# ---------------------------------------------------------------------------
def test_empty_frame_range_flags_long_runs_only() -> None:
    """Chỉ báo dải >= ``min_run_frames`` frame trống liên tiếp; dải ngắn bị bỏ qua."""
    data = make_task_data(
        [
            make_shape("s1", frame=0),
            make_shape("s2", frame=3, shape_id=2),  # frame 1-2 trống (dải 2)
            make_shape("s3", frame=6, shape_id=3),  # frame 4-5 trống (dải 2)
            make_shape("s4", frame=11, shape_id=4),  # frame 7-10 trống (dải 4)
        ],
        size=12,
    )
    issues = findings_for(
        run_rules(data, {"empty_frame_range": {"params": {"min_run_frames": 3}}}),
        "empty_frame_range",
    )
    assert len(issues) == 1
    assert issues[0].frame == 7
    assert issues[0].details["start_frame"] == 7
    assert issues[0].details["stop_frame"] == 10
    assert issues[0].details["frames"] == 4


def test_empty_frame_range_respects_ignore_labels_and_bounds() -> None:
    """``ignore_labels`` không tính là annotation; ``start/stop_frame`` giới hạn phạm vi."""
    data = make_task_data(
        [
            make_shape("s1", frame=0),
            make_shape("g1", frame=1, shape_id=2, label="ignored", label_id=9),
            make_shape("s2", frame=5, shape_id=3),
        ],
        size=6,
    )
    config = {
        "empty_frame_range": {
            "params": {"min_run_frames": 2, "ignore_labels": ["ignored"], "stop_frame": 3}
        }
    }
    issues = findings_for(run_rules(data, config), "empty_frame_range")
    # trong phạm vi 0..3: frame 0 có object, frame 1 chỉ có shape bị ignore -> dải 1..3
    assert len(issues) == 1
    assert issues[0].details["start_frame"] == 1
    assert issues[0].details["stop_frame"] == 3


def test_empty_frame_range_ignores_frame_step_and_warns() -> None:
    """``frame_step`` vô nghĩa với dải "liên tiếp" -> luôn quét từng frame và ghi cảnh báo."""
    data = make_task_data(
        [make_shape("s1", frame=0), make_shape("s2", frame=5, shape_id=2)], size=6
    )
    report = run_rules(
        data,
        {"empty_frame_range": {"params": {"min_run_frames": 4, "frame_step": 5}}},
    )
    issues = findings_for(report, "empty_frame_range")
    assert len(issues) == 1
    assert issues[0].details["frames"] == 4  # frame 1..4 (nếu áp frame_step=5 sẽ không thấy)
    assert any("frame_step" in warning for warning in report.warnings)


# ---------------------------------------------------------------------------
# object_count (Level 1: missing annotation + suspicious object count)
# ---------------------------------------------------------------------------
def _vehicle(object_key: str, frame: int, shape_id: int, x: float) -> NormShape:
    """Shape ``vehicle`` nhỏ (tiện tạo nhiều object trong cùng frame)."""
    return make_shape(object_key, frame=frame, shape_id=shape_id, points=(x, 10, x + 8, 18))


def test_object_count_min_and_max() -> None:
    """``min_objects``/``max_objects`` báo frame quá ít / quá nhiều object."""
    data = make_task_data(
        [
            _vehicle("s1", frame=0, shape_id=1, x=10),
            _vehicle("s2", frame=1, shape_id=2, x=10),
            _vehicle("s3", frame=1, shape_id=3, x=30),
            _vehicle("s4", frame=2, shape_id=4, x=10),
            _vehicle("s5", frame=2, shape_id=5, x=30),
            _vehicle("s6", frame=2, shape_id=6, x=50),
        ],
        size=5,
    )
    config = {"object_count": {"params": {"min_objects": 1, "max_objects": 2}}}
    issues = findings_for(run_rules(data, config), "object_count")
    assert [issue.frame for issue in issues] == [2, 3, 4]
    assert issues[0].details["reasons"] == ["quá nhiều"]
    assert issues[1].details["reasons"] == ["quá ít"]
    assert "ngưỡng: min=1, max=2" in issues[0].message


def test_object_count_outlier_ratio() -> None:
    """``outlier_ratio`` báo frame vượt xa trung vị của task."""
    shapes = [_vehicle(f"s{frame}", frame=frame, shape_id=frame, x=10) for frame in range(5)]
    shapes += [
        _vehicle("spike1", frame=5, shape_id=51, x=10),
        _vehicle("spike2", frame=5, shape_id=52, x=30),
        _vehicle("spike3", frame=5, shape_id=53, x=50),
        _vehicle("spike4", frame=5, shape_id=54, x=70),
    ]
    data = make_task_data(shapes, size=6)
    issues = findings_for(
        run_rules(data, {"object_count": {"params": {"outlier_ratio": 3.0}}}),
        "object_count",
    )
    assert [issue.frame for issue in issues] == [5]
    assert issues[0].details["median_objects"] == 1
    assert issues[0].details["outlier_threshold"] == 3.0
    assert issues[0].details["reasons"] == ["bất thường so với trung vị"]


def test_object_count_outlier_ratio_needs_enough_frames() -> None:
    """Chưa đủ ``min_frames_for_outlier`` frame có object -> không kết luận ngoại lai."""
    shapes = [_vehicle(f"s{frame}", frame=frame, shape_id=frame, x=10) for frame in range(5)]
    shapes += [_vehicle("spike1", frame=5, shape_id=51, x=10)]
    data = make_task_data(shapes, size=6)
    config = {"object_count": {"params": {"outlier_ratio": 3.0, "min_frames_for_outlier": 10}}}
    assert findings_for(run_rules(data, config), "object_count") == []


def test_object_count_ignore_labels() -> None:
    """Nhãn trong ``ignore_labels`` không tính vào số lượng object."""
    data = make_task_data(
        [
            _vehicle("s1", frame=0, shape_id=1, x=10),
            make_shape(
                "s2", frame=0, shape_id=2, label="crowd", label_id=7, points=(30, 10, 38, 18)
            ),
        ]
    )
    config = {"object_count": {"params": {"max_objects": 1, "ignore_labels": ["crowd"]}}}
    assert findings_for(run_rules(data, config), "object_count") == []


def test_object_count_without_thresholds_is_skipped() -> None:
    """Chưa cấu hình ngưỡng nào -> rule im lặng (an toàn khi bật mặc định)."""
    data = make_task_data([_vehicle("s1", frame=0, shape_id=1, x=10)], size=3)
    assert run_rules(data, {"object_count": {}}).issues == []
