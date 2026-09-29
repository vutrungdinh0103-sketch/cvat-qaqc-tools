"""Test các rule nhóm hình học."""

from __future__ import annotations

import pytest

from conftest import findings_for, make_shape, make_task_data, run_rules
from qaqc.model import Severity


# ---------------------------------------------------------------------------
# invalid_size
# ---------------------------------------------------------------------------
def test_invalid_size_flags_zero_and_flipped_boxes() -> None:
    """Bắt cả box width = 0 và box bị đảo toạ độ."""
    data = make_task_data(
        [
            make_shape("s1", points=(60, 10, 60, 40)),
            make_shape("s2", shape_id=2, points=(50, 45, 10, 5)),
        ]
    )
    issues = findings_for(run_rules(data, {"invalid_size": {}}), "invalid_size")
    assert len(issues) == 2
    assert issues[0].severity is Severity.ERROR
    assert issues[0].details["width"] == 0
    assert issues[1].details["height"] == -40
    assert "bbox=(50, 45, 10, 5)" in issues[1].message


def test_invalid_size_ignores_healthy_boxes() -> None:
    """Box bình thường không bị báo."""
    data = make_task_data([make_shape("s1", points=(10, 10, 30, 30))])
    assert run_rules(data, {"invalid_size": {}}).issues == []


def test_invalid_size_respects_shape_types_filter() -> None:
    """``shape_types`` lọc loại shape được kiểm tra."""
    data = make_task_data(
        [make_shape("s1", shape_type="polygon", points=(0, 0, 0, 0, 0, 10, 0, 10))]
    )
    assert findings_for(run_rules(data, {"invalid_size": {}}), "invalid_size")
    filtered = run_rules(data, {"invalid_size": {"params": {"shape_types": ["rectangle"]}}})
    assert filtered.issues == []


# ---------------------------------------------------------------------------
# out_of_frame
# ---------------------------------------------------------------------------
def test_out_of_frame_flags_overflow() -> None:
    """Box vượt biên ảnh quá dung sai bị báo kèm số pixel tràn."""
    data = make_task_data([make_shape("s1", frame=2, points=(95, 10, 120, 40))])
    issues = findings_for(run_rules(data, {"out_of_frame": {}}), "out_of_frame")
    assert len(issues) == 1
    assert issues[0].details["overflow_x_px"] == 20
    assert issues[0].details["frame_width"] == 100


def test_out_of_frame_allows_tolerance() -> None:
    """Lệch trong dung sai thì không báo."""
    data = make_task_data([make_shape("s1", points=(99.4, 10, 100.4, 40))])
    assert run_rules(data, {"out_of_frame": {"params": {"tolerance_px": 1.0}}}).issues == []


def test_out_of_frame_warns_when_frame_size_unknown() -> None:
    """Thiếu kích thước frame -> bỏ qua và ghi cảnh báo vào báo cáo."""
    data = make_task_data([make_shape("s1", points=(95, 10, 120, 40))], frame_size=None)
    report = run_rules(data, {"out_of_frame": {}})
    assert report.issues == []
    assert any("kích thước frame" in warning for warning in report.warnings)


def test_out_of_frame_min_visible_ratio() -> None:
    """Box nhìn thấy quá ít (dù tràn trong dung sai) cũng bị báo."""
    data = make_task_data([make_shape("s1", points=(95, 0, 105, 50))])
    issues = findings_for(
        run_rules(
            data,
            {"out_of_frame": {"params": {"tolerance_px": 10.0, "min_visible_ratio": 0.9}}},
        ),
        "out_of_frame",
    )
    assert len(issues) == 1
    assert issues[0].details["visible_ratio"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# tiny_box
# ---------------------------------------------------------------------------
def test_tiny_box_flags_small_box() -> None:
    """Box nhỏ hơn ngưỡng diện tích/cạnh bị báo là warning."""
    data = make_task_data([make_shape("s1", points=(10, 10, 12, 12))])
    issues = findings_for(run_rules(data, {"tiny_box": {}}), "tiny_box")
    assert len(issues) == 1
    assert issues[0].severity is Severity.WARNING
    assert issues[0].details["area_px"] == 4


def test_tiny_box_ignores_normal_and_degenerate() -> None:
    """Box bình thường không báo; box suy biến do rule invalid_size lo."""
    data = make_task_data(
        [
            make_shape("s1", points=(10, 10, 30, 30)),
            make_shape("s2", shape_id=2, points=(10, 40, 10, 45)),
        ]
    )
    assert run_rules(data, {"tiny_box": {}}).issues == []


# ---------------------------------------------------------------------------
# duplicate_bbox
# ---------------------------------------------------------------------------
def _duplicate_pair() -> list:
    """2 box gần như trùng nhau (IoU ~0.906 > 0.85)."""
    return [
        make_shape("s1", points=(10, 5, 50, 45), attributes={"vehicle_type": "car"}),
        make_shape("s2", shape_id=2, points=(11, 6, 51, 46), attributes={"vehicle_type": "car"}),
    ]


def test_duplicate_bbox_flags_overlapping_same_label() -> None:
    """Cùng frame + cùng label + IoU > 0.85 -> 1 lỗi (mục 2 của .clinerules)."""
    issues = findings_for(
        run_rules(make_task_data(_duplicate_pair()), {"duplicate_bbox": {}}), "duplicate_bbox"
    )
    assert len(issues) == 1
    assert issues[0].details["iou"] > 0.85
    assert issues[0].details["iou_threshold"] == 0.85
    assert "IoU=" in issues[0].message
    assert set(issues[0].object_keys) == {"s1", "s2"}


def test_duplicate_bbox_respects_custom_threshold() -> None:
    """Ngưỡng IoU cao hơn -> không còn là trùng lặp."""
    config = {"duplicate_bbox": {"params": {"iou_threshold": 0.99}}}
    assert run_rules(make_task_data(_duplicate_pair()), config).issues == []


def test_duplicate_bbox_same_label_only_toggle() -> None:
    """``same_label_only=false`` cho phép bắt trùng lặp khác label."""
    shapes = [
        make_shape("s1", label="vehicle", label_id=1, points=(10, 5, 50, 45)),
        make_shape("s2", shape_id=2, label="trailer", label_id=4, points=(10, 5, 50, 45)),
    ]
    same = run_rules(make_task_data(shapes), {"duplicate_bbox": {}})
    assert same.issues == []

    loose = run_rules(
        make_task_data(shapes), {"duplicate_bbox": {"params": {"same_label_only": False}}}
    )
    assert len(findings_for(loose, "duplicate_bbox")) == 1


def test_duplicate_bbox_skips_degenerate_and_other_frames() -> None:
    """Box suy biến và khác frame không bị coi là trùng lặp."""
    shapes = [
        make_shape("s1", frame=0, points=(10, 5, 50, 45)),
        make_shape("s2", frame=1, shape_id=2, points=(10, 5, 50, 45)),
        make_shape("s3", frame=0, shape_id=3, points=(10, 5, 10, 45)),
    ]
    assert run_rules(make_task_data(shapes), {"duplicate_bbox": {}}).issues == []


# ---------------------------------------------------------------------------
# must_be_inside
# ---------------------------------------------------------------------------
def _containment_config() -> dict:
    """Cấu hình yêu cầu license_plate nằm trong vehicle."""
    return {
        "must_be_inside": {
            "params": {
                "pairs": [
                    {
                        "inner_labels": ["license_plate"],
                        "outer_labels": ["vehicle"],
                        "min_ratio": 0.9,
                    }
                ]
            }
        }
    }


def test_must_be_inside_flags_plate_outside_vehicle() -> None:
    """Biển số nằm phần lớn ngoài xe -> lỗi."""
    shapes = [
        make_shape("s1", label="vehicle", label_id=1, points=(60, 5, 85, 25)),
        make_shape("s2", label="license_plate", label_id=2, points=(80, 15, 95, 30)),
    ]
    issues = findings_for(
        run_rules(make_task_data(shapes), _containment_config()), "must_be_inside"
    )
    assert len(issues) == 1
    assert issues[0].details["contained_ratio"] < 0.9
    assert issues[0].details["outer_label"] == "vehicle"


def test_must_be_inside_ok_when_contained() -> None:
    """Biển số nằm trong xe -> không lỗi."""
    shapes = [
        make_shape("s1", label="vehicle", label_id=1, points=(60, 5, 85, 25)),
        make_shape("s2", label="license_plate", label_id=2, points=(65, 10, 75, 20)),
    ]
    assert run_rules(make_task_data(shapes), _containment_config()).issues == []


def test_must_be_inside_without_pairs_does_nothing() -> None:
    """Chưa cấu hình ``pairs`` thì rule không báo gì (bật mặc định là an toàn)."""
    shapes = [make_shape("s1", label="license_plate", label_id=2, points=(0, 0, 5, 5))]
    assert run_rules(make_task_data(shapes), {"must_be_inside": {}}).issues == []


def test_must_be_inside_max_center_distance() -> None:
    """Giới hạn khoảng cách tâm giúp bỏ qua các ``outer`` ở xa."""
    shapes = [
        make_shape("s1", label="vehicle", label_id=1, points=(0, 0, 10, 10)),
        make_shape("s2", label="license_plate", label_id=2, points=(80, 30, 90, 40)),
    ]
    config = _containment_config()
    config["must_be_inside"]["params"]["max_center_distance_px"] = 20
    assert run_rules(make_task_data(shapes), config).issues == []
