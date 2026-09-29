"""Test các tiện ích hình học trong :mod:`qaqc.geometry`."""

from __future__ import annotations

import pytest
from shapely.geometry import LineString, box

from qaqc.geometry import (
    bbox_area,
    bbox_center,
    bbox_is_degenerate,
    bbox_min_side,
    bbox_of_points,
    bboxes_overlap,
    clamp,
    compute_iou,
    contained_ratio,
    geometry_of,
    to_points,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ([1, 2], (1.0, 2.0)),
        ((0.5, -1), (0.5, -1.0)),
        (None, ()),
        ([], ()),
        (["a", "b"], ()),
        ([1.0, float("nan")], ()),
        ([1.0, float("inf")], ()),
    ],
)
def test_to_points_normalizes_and_rejects_invalid(raw, expected) -> None:
    """``to_points`` chỉ nhận dữ liệu số hữu hạn."""
    assert to_points(raw) == expected


def test_rectangle_bbox_keeps_original_order() -> None:
    """Rectangle lỗi (toạ độ đảo) KHÔNG được tự chuẩn hoá min/max."""
    assert bbox_of_points("rectangle", (60.0, 10.0, 60.0, 40.0)) == (60.0, 10.0, 60.0, 40.0)
    assert bbox_of_points("rectangle", (50.0, 45.0, 10.0, 5.0)) == (50.0, 45.0, 10.0, 5.0)


def test_rectangle_bbox_requires_four_points() -> None:
    """Rectangle phải có đúng 4 toạ độ."""
    assert bbox_of_points("rectangle", (1, 2, 3, 4, 5, 6)) is None


def test_ellipse_bbox_uses_center_and_radius() -> None:
    """CVAT lưu ellipse là ``[cx, cy, rx, ry]`` nên bbox phải suy ra từ đó."""
    assert bbox_of_points("ellipse", (50.0, 50.0, 10.0, 20.0)) == (40.0, 30.0, 60.0, 70.0)


def test_polygon_bbox_uses_extremes() -> None:
    """Polygon lấy bao ngoài của tất cả các điểm."""
    assert bbox_of_points("polygon", (10.0, 20.0, 30.0, 5.0, 20.0, 40.0)) == (
        10.0,
        5.0,
        30.0,
        40.0,
    )


@pytest.mark.parametrize("shape_type", ["mask", "skeleton", "tag", "unknown", ""])
def test_bbox_of_points_unknown_types(shape_type: str) -> None:
    """Các loại không có bbox ý nghĩa phải trả ``None``."""
    assert bbox_of_points(shape_type, (1, 2, 3, 4)) is None


def test_geometry_polygon_self_intersection_is_repaired() -> None:
    """Polygon tự cắt (annotation lỗi) phải vẫn dựng được hình học hợp lệ."""
    geometry = geometry_of("polygon", (0.0, 0.0, 10.0, 10.0, 10.0, 0.0, 0.0, 10.0))
    assert geometry is not None
    assert geometry.is_valid
    assert geometry.area > 0


def test_geometry_returns_none_for_degenerate_rectangle() -> None:
    """Rectangle suy biến không có hình học."""
    assert geometry_of("rectangle", (5.0, 5.0, 5.0, 20.0)) is None


def test_geometry_polyline_and_points_have_zero_area() -> None:
    """Polyline/points dựng được hình học nhưng không có diện tích."""
    line = geometry_of("polyline", (0.0, 0.0, 10.0, 10.0))
    points = geometry_of("points", (0.0, 0.0, 10.0, 10.0))
    assert isinstance(line, LineString)
    assert line.area == 0
    assert points is not None
    assert points.area == 0


def test_compute_iou_identical_and_partial() -> None:
    """IoU bằng 1 khi trùng khít, 0.5 khi chồng một nửa."""
    assert compute_iou(box(0, 0, 10, 10), box(0, 0, 10, 10)) == pytest.approx(1.0)
    assert compute_iou(box(0, 0, 10, 10), box(0, 0, 5, 10)) == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (None, box(0, 0, 1, 1)),
        (box(0, 0, 1, 1), None),
        (box(0, 0, 1, 1), box(5, 5, 6, 6)),
        (LineString([(0, 0), (1, 1)]), box(0, 0, 1, 1)),
    ],
)
def test_compute_iou_edge_cases_are_zero(a, b) -> None:
    """IoU trả 0 nếu thiếu hình, không giao nhau, hoặc hình không có diện tích."""
    assert compute_iou(a, b) == 0.0


def test_contained_ratio_full_partial_and_empty() -> None:
    """Tỉ lệ chứa nhau tính trên diện tích của hình trong."""
    assert contained_ratio(box(0, 0, 10, 10), box(0, 0, 20, 20)) == pytest.approx(1.0)
    assert contained_ratio(box(0, 0, 10, 10), box(0, 0, 5, 10)) == pytest.approx(0.5)
    assert contained_ratio(None, box(0, 0, 1, 1)) == 0.0


def test_bbox_helpers() -> None:
    """Các helper bbox: diện tích, tâm, cạnh ngắn, suy biến, giao nhau."""
    assert bbox_is_degenerate((0, 0, 0, 10)) is True
    assert bbox_is_degenerate((0, 0, 10, 10)) is False
    assert bbox_area((0, 0, 10, 5)) == 50
    assert bbox_area((0, 0, 0, 5)) == 0
    assert bbox_center((0, 0, 10, 20)) == (5.0, 10.0)
    assert bbox_min_side((0, 0, 10, 3)) == 3
    assert bboxes_overlap((0, 0, 10, 10), (10, 0, 20, 10)) is True
    assert bboxes_overlap((0, 0, 1, 1), (2, 2, 3, 3)) is False


def test_clamp_handles_reversed_bounds() -> None:
    """``clamp`` kẹp giá trị và tự đảo khi low > high."""
    assert clamp(5, 0, 10) == 5
    assert clamp(-1, 0, 10) == 0
    assert clamp(99, 0, 10) == 10
    assert clamp(5, 10, 0) == 5
