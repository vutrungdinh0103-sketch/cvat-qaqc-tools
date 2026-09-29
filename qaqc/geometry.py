"""Tiện ích hình học dùng chung cho các rule QA/QC.

Quy ước:

- Toạ độ là pixel, gốc ở góc trên-trái ảnh (đúng như CVAT lưu trong ``points``).
- ``rectangle`` giữ nguyên thứ tự ``[xtl, ytl, xbr, ybr]`` mà CVAT trả về, **không**
  tự chuẩn hoá ``min``/``max``; nhờ vậy phát hiện được cả trường hợp box bị đảo
  toạ độ (``width <= 0`` / ``height <= 0``).
- ``ellipse`` được CVAT lưu là ``[cx, cy, rx, ry]`` (khác hẳn rectangle).
- ``mask`` (RLE) và ``skeleton`` không có bounding box ý nghĩa nên trả ``None``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Final

import numpy as np
from shapely.geometry import LineString, MultiPoint, Polygon
from shapely.geometry import box as shapely_box
from shapely.geometry.base import BaseGeometry
from shapely.validation import make_valid

#: Bounding box theo thứ tự ``(xtl, ytl, xbr, ybr)``.
BBox = tuple[float, float, float, float]

#: Các loại shape có thể quy về bounding box từ ``points``.
BOX_LIKE_TYPES: Final[tuple[str, ...]] = (
    "rectangle",
    "polygon",
    "polyline",
    "points",
    "ellipse",
    "cuboid",
)

#: Các loại shape dựng được hình học shapely chính xác (không xấp xỉ).
EXACT_GEOMETRY_TYPES: Final[tuple[str, ...]] = ("rectangle", "polygon")

#: Các loại chỉ dùng được bounding box xấp xỉ.
APPROXIMATED_GEOMETRY_TYPES: Final[tuple[str, ...]] = ("ellipse", "cuboid")


def to_points(values: Iterable[float] | None) -> tuple[float, ...]:
    """Chuyển ``points`` của CVAT thành tuple ``float``.

    Trả về tuple rỗng nếu dữ liệu rỗng/không phải số/chứa ``NaN``/``inf``.
    """
    if not values:
        return ()

    try:
        array = np.asarray(list(values), dtype=float)
    except (TypeError, ValueError):
        return ()

    if array.ndim != 1 or not np.all(np.isfinite(array)):
        return ()

    return tuple(float(value) for value in array)


def bbox_of_points(shape_type: str, points: Sequence[float]) -> BBox | None:
    """Bounding box ``(xtl, ytl, xbr, ybr)`` của một shape.

    :param shape_type: ``rectangle``/``polygon``/``polyline``/``points``/
        ``ellipse``/``cuboid`` (các loại khác trả ``None``).
    :param points: danh sách toạ độ đã chuẩn hoá (xem :func:`to_points`).
    :return: bbox hoặc ``None`` nếu không tính được.
    """
    count = len(points)

    if shape_type == "rectangle":
        # Giữ nguyên thứ tự: box lỗi (xbr <= xtl) vẫn phải được báo cáo.
        return (points[0], points[1], points[2], points[3]) if count == 4 else None

    if shape_type == "ellipse":
        if count != 4:
            return None
        cx, cy, rx, ry = points
        return (cx - rx, cy - ry, cx + rx, cy + ry)

    if shape_type in ("polygon", "polyline", "points", "cuboid"):
        if count < 4 or count % 2 != 0:
            return None
        xs, ys = points[0::2], points[1::2]
        return (min(xs), min(ys), max(xs), max(ys))

    return None


def geometry_of(shape_type: str, points: Sequence[float]) -> BaseGeometry | None:
    """Dựng hình học shapely của một shape.

    ``rectangle``/``ellipse``/``cuboid`` được quy về bounding box (riêng 2 loại
    sau là xấp xỉ), ``polygon`` dựng đa giác thật (tự sửa nếu tự cắt),
    ``polyline``/``points`` dựng hình học 1 chiều/0 chiều.

    :return: hình học shapely hoặc ``None`` nếu shape suy biến/không hỗ trợ.
    """
    if shape_type in ("rectangle", "ellipse", "cuboid"):
        bbox = bbox_of_points(shape_type, points)
        if bbox is None or bbox_is_degenerate(bbox):
            return None
        return shapely_box(*bbox)

    if shape_type == "polygon":
        if len(points) < 6 or len(points) % 2 != 0:
            return None
        geometry: BaseGeometry = Polygon(list(zip(points[0::2], points[1::2], strict=True)))
        if not geometry.is_valid:
            # Polygon tự cắt (annotation lỗi) -> sửa để vẫn tính được IoU/diện tích.
            geometry = make_valid(geometry)
        return None if geometry.is_empty else geometry

    if shape_type == "polyline":
        if len(points) < 4 or len(points) % 2 != 0:
            return None
        return LineString(list(zip(points[0::2], points[1::2], strict=True)))

    if shape_type == "points":
        if len(points) < 2 or len(points) % 2 != 0:
            return None
        return MultiPoint(list(zip(points[0::2], points[1::2], strict=True)))

    return None


def compute_iou(a: BaseGeometry | None, b: BaseGeometry | None) -> float:
    """Tính IoU (Intersection over Union) của 2 hình học.

    Trả về ``0.0`` nếu một trong hai hình rỗng/suy biến/không có diện tích
    (ví dụ polyline hoặc points) hoặc không giao nhau.
    """
    if a is None or b is None or a.is_empty or b.is_empty:
        return 0.0

    try:
        intersection_area = float(a.intersection(b).area)
    except Exception:  # pragma: no cover - hình học hỏng (TopologyException...)
        return 0.0

    if intersection_area <= 0.0:
        return 0.0

    union_area = float(a.area) + float(b.area) - intersection_area
    if union_area <= 0.0:
        return 0.0

    return intersection_area / union_area


def contained_ratio(inner: BaseGeometry | None, outer: BaseGeometry | None) -> float:
    """Tỉ lệ diện tích của ``inner`` nằm trong ``outer`` (``0.0``..``1.0``)."""
    if inner is None or outer is None or inner.is_empty:
        return 0.0

    inner_area = float(inner.area)
    if inner_area <= 0.0:
        return 0.0

    try:
        overlap = float(inner.intersection(outer).area)
    except Exception:  # pragma: no cover - hình học hỏng
        return 0.0

    return max(0.0, min(1.0, overlap / inner_area))


def bbox_is_degenerate(bbox: BBox) -> bool:
    """``True`` nếu bbox có ``width <= 0`` hoặc ``height <= 0``."""
    return (bbox[2] - bbox[0]) <= 0 or (bbox[3] - bbox[1]) <= 0


def bbox_area(bbox: BBox) -> float:
    """Diện tích bbox (``0.0`` nếu bbox suy biến)."""
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def bbox_center(bbox: BBox) -> tuple[float, float]:
    """Tâm của bbox."""
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


def bbox_min_side(bbox: BBox) -> float:
    """Cạnh nhỏ nhất của bbox (số âm nếu bbox suy biến)."""
    return min(bbox[2] - bbox[0], bbox[3] - bbox[1])


def bboxes_overlap(a: BBox, b: BBox) -> bool:
    """``True`` nếu 2 bbox giao nhau (kể cả chỉ chạm cạnh)."""
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def clamp(value: float, low: float, high: float) -> float:
    """Kẹp ``value`` vào khoảng ``[low, high]`` (tự đảo nếu ``low > high``)."""
    if low > high:
        low, high = high, low
    return max(low, min(high, value))
