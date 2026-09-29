"""Model dữ liệu chuẩn hoá cho engine QA/QC (không phụ thuộc CVAT SDK).

Toàn bộ rule chỉ làm việc với :class:`TaskData`. Nhờ lớp chuẩn hoá này, engine
chạy được cả offline (test bằng fixture JSON) lẫn online (dữ liệu lấy từ
``cvat_sdk``).

Ghi chú quan trọng về **track**: trong CVAT, id thuộc về *track* chứ không thuộc
từng keyframe, nên khoá định danh một object theo frame được quy ước như sau:

- Shape độc lập (không thuộc track): ``object_key = "s<shape_id>"``.
- Keyframe của track: ``object_key = "t<track_id>@f<frame>"``.

Khoá này ổn định giữa các lần chạy nên dùng được để sinh fingerprint chống trùng
issue khi re-run.
"""

from __future__ import annotations

import functools
from collections.abc import Sequence
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .geometry import (
    BBox,
    bbox_area,
    bbox_center,
    bbox_is_degenerate,
    bbox_min_side,
    bbox_of_points,
    geometry_of,
)

#: Loại shape được coi là "có thể tính bounding box" (mask/skeleton/tag thì không).
BOX_LIKE_TYPES: tuple[str, ...] = (
    "rectangle",
    "polygon",
    "polyline",
    "points",
    "ellipse",
    "cuboid",
)

#: Loại shape "đóng" (có diện tích) - dùng cho IoU/containment.
AREA_TYPES: tuple[str, ...] = ("rectangle", "polygon", "ellipse", "cuboid")


class Severity(str, Enum):
    """Mức độ nghiêm trọng của một lỗi QA/QC."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    @property
    def rank(self) -> int:
        """Thứ tự so sánh: ``info=1 < warning=2 < error=3``."""
        return SEVERITY_RANK[self]

    @classmethod
    def parse(cls, value: Severity | str | None, default: Severity) -> Severity:
        """Chuyển chuỗi (không phân biệt hoa/thường) thành :class:`Severity`."""
        if value is None:
            return default
        if isinstance(value, Severity):
            return value
        try:
            return cls(str(value).strip().lower())
        except ValueError as exc:
            allowed = ", ".join(item.value for item in cls)
            raise ValueError(f"Mức độ không hợp lệ: {value!r} (chỉ nhận: {allowed})") from exc


#: Xếp hạng mức độ để sắp xếp/lọc.
SEVERITY_RANK: dict[Severity, int] = {
    Severity.INFO: 1,
    Severity.WARNING: 2,
    Severity.ERROR: 3,
}


class LabelAttribute(BaseModel):
    """Định nghĩa một attribute của label (theo schema của CVAT)."""

    model_config = ConfigDict(frozen=True)

    id: int | None = None
    name: str
    values: tuple[str, ...] = ()
    default_value: str = ""
    mutable: bool = True
    input_type: str = "select"


class LabelSchema(BaseModel):
    """Định nghĩa label + attributes trong task/project."""

    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    type: str = "any"
    attributes: tuple[LabelAttribute, ...] = ()

    def attribute_names(self) -> tuple[str, ...]:
        """Danh sách tên attribute của label."""
        return tuple(attribute.name for attribute in self.attributes)

    def attribute(self, name: str) -> LabelAttribute | None:
        """Lấy định nghĩa attribute theo tên (``None`` nếu label không có)."""
        for attribute in self.attributes:
            if attribute.name == name:
                return attribute
        return None


class NormShape(BaseModel):
    """Một shape đã chuẩn hoá (shape độc lập hoặc keyframe của track)."""

    model_config = ConfigDict(frozen=True)

    object_key: str
    frame: int
    shape_type: str
    label_id: int
    label: str
    points: tuple[float, ...] = ()
    attributes: dict[str, str] = Field(default_factory=dict)
    shape_id: int | None = None
    track_id: int | None = None
    group: int | None = None
    source: str | None = None
    occluded: bool = False
    outside: bool = False
    z_order: int = 0
    rotation: float | None = None
    score: float | None = None

    @property
    def is_tracked(self) -> bool:
        """``True`` nếu shape này thuộc một track."""
        return self.track_id is not None

    @property
    def bbox(self) -> BBox | None:
        """Bounding box ``(xtl, ytl, xbr, ybr)``; ``None`` nếu không tính được."""
        return bbox_of_points(self.shape_type, self.points)

    @property
    def is_degenerate(self) -> bool:
        """``True`` nếu có bbox nhưng ``width <= 0`` hoặc ``height <= 0``."""
        bbox = self.bbox
        return bbox is not None and bbox_is_degenerate(bbox)

    @property
    def width(self) -> float:
        """Chiều rộng bbox (``0.0`` nếu không có bbox)."""
        bbox = self.bbox
        return 0.0 if bbox is None else bbox[2] - bbox[0]

    @property
    def height(self) -> float:
        """Chiều cao bbox (``0.0`` nếu không có bbox)."""
        bbox = self.bbox
        return 0.0 if bbox is None else bbox[3] - bbox[1]

    @property
    def area(self) -> float:
        """Diện tích bbox (``0.0`` nếu bbox suy biến)."""
        bbox = self.bbox
        return 0.0 if bbox is None else bbox_area(bbox)

    @property
    def min_side(self) -> float:
        """Cạnh nhỏ nhất của bbox (số âm nếu bbox suy biến)."""
        bbox = self.bbox
        return 0.0 if bbox is None else bbox_min_side(bbox)

    @property
    def center(self) -> tuple[float, float] | None:
        """Tâm bbox (dùng để đặt ``position`` cho issue của CVAT)."""
        bbox = self.bbox
        return None if bbox is None else bbox_center(bbox)

    @property
    def has_area(self) -> bool:
        """``True`` nếu loại shape có diện tích (IoU/containment có ý nghĩa)."""
        return self.shape_type in AREA_TYPES and not self.is_degenerate

    def geometry(self) -> Any:
        """Hình học shapely của shape (``None`` nếu suy biến/không hỗ trợ)."""
        return geometry_of(self.shape_type, self.points)

    def attribute(self, name: str) -> str | None:
        """Giá trị attribute theo tên; ``None`` nếu không có hoặc rỗng."""
        value = self.attributes.get(name)
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    def describe(self) -> str:
        """Chuỗi mô tả object dùng cho message báo cáo."""
        parts = [f"shape #{self.shape_id}" if self.shape_id is not None else self.object_key]
        if self.track_id is not None:
            parts.append(f"track #{self.track_id}")
        parts.append(f"({self.shape_type}, label='{self.label}')")
        if self.bbox is not None:
            bbox = self.bbox
            parts.append(f"bbox=({bbox[0]:g}, {bbox[1]:g}, {bbox[2]:g}, {bbox[3]:g})")
        return " ".join(parts)


class NormTag(BaseModel):
    """Một tag (nhãn không có hình học) tại một frame."""

    model_config = ConfigDict(frozen=True)

    object_key: str
    frame: int
    label_id: int
    label: str
    attributes: dict[str, str] = Field(default_factory=dict)
    source: str | None = None
    shape_id: int | None = None


class NormTrack(BaseModel):
    """Một track đã chuẩn hoá (giữ đủ keyframe để kiểm tra logic theo thời gian)."""

    model_config = ConfigDict(frozen=True)

    track_id: int
    label_id: int
    label: str
    shapes: tuple[NormShape, ...] = ()
    attributes: dict[str, str] = Field(default_factory=dict)
    group: int | None = None
    source: str | None = None

    @property
    def start_frame(self) -> int | None:
        """Frame nhỏ nhất trong track."""
        frames = [shape.frame for shape in self.shapes]
        return min(frames) if frames else None

    @property
    def stop_frame(self) -> int | None:
        """Frame lớn nhất trong track."""
        frames = [shape.frame for shape in self.shapes]
        return max(frames) if frames else None

    @property
    def visible_shapes(self) -> tuple[NormShape, ...]:
        """Các keyframe đang hiển thị (``outside = False``)."""
        return tuple(shape for shape in self.shapes if not shape.outside)


class JobInfo(BaseModel):
    """Thông tin cần thiết của một job (map ``frame -> job_id`` khi tạo issue)."""

    model_config = ConfigDict(frozen=True)

    id: int
    start_frame: int = 0
    stop_frame: int = 0
    stage: str | None = None
    state: str | None = None
    assignee: int | None = None

    def contains(self, frame: int) -> bool:
        """``True`` nếu frame nằm trong khoảng ``[start_frame, stop_frame]``."""
        return self.start_frame <= frame <= self.stop_frame


class TaskData(BaseModel):
    """Toàn bộ dữ liệu annotation của một task/job đã chuẩn hoá."""

    task_id: int
    task_name: str | None = None
    size: int | None = None
    dimension: str | None = None
    frame_width: int | None = None
    frame_height: int | None = None
    frame_size_overrides: dict[int, tuple[int, int]] = Field(default_factory=dict)
    labels: tuple[LabelSchema, ...] = ()
    shapes: tuple[NormShape, ...] = ()
    tracks: tuple[NormTrack, ...] = ()
    tags: tuple[NormTag, ...] = ()
    jobs: tuple[JobInfo, ...] = ()
    shapes_total: int = 0
    shapes_skipped: int = 0
    skipped_reasons: dict[str, int] = Field(default_factory=dict)
    warnings: tuple[str, ...] = ()
    source_job_id: int | None = None

    # ------------------------------------------------------------------
    # Tra cứu label / kích thước frame
    # ------------------------------------------------------------------
    def label_names(self) -> dict[int, str]:
        """Map ``label_id -> tên label``."""
        return {label.id: label.name for label in self.labels}

    def label_schema(self, label_id: int) -> LabelSchema | None:
        """Schema của label theo id (``None`` nếu không tìm thấy)."""
        for label in self.labels:
            if label.id == label_id:
                return label
        return None

    def label_schema_by_name(self, name: str) -> LabelSchema | None:
        """Schema của label theo tên (``None`` nếu không tìm thấy)."""
        for label in self.labels:
            if label.name == name:
                return label
        return None

    def frame_size(self, frame: int) -> tuple[int, int] | None:
        """Kích thước ``(width, height)`` của frame (``None`` nếu chưa biết)."""
        override = self.frame_size_overrides.get(frame)
        if override is not None:
            return override
        if self.frame_width and self.frame_height:
            return (self.frame_width, self.frame_height)
        return None

    def describe_frames(self) -> str:
        """Mô tả phạm vi frame của task (dùng cho log/báo cáo)."""
        if self.size is not None:
            return f"0..{max(self.size - 1, 0)} ({self.size} frame)"
        frames = sorted(self.frames_with_objects())
        if not frames:
            return "không có frame nào có annotation"
        return f"{frames[0]}..{frames[-1]} ({len(frames)} frame có annotation)"

    # ------------------------------------------------------------------
    # Tra cứu object
    # ------------------------------------------------------------------
    def objects(
        self,
        *,
        shape_types: Sequence[str] | None = None,
        include_tracked: bool = True,
        include_standalone: bool = True,
    ) -> list[NormShape]:
        """Danh sách object đang hiển thị (``outside = False``), có thể lọc."""
        allowed = set(shape_types) if shape_types else None
        result: list[NormShape] = []

        for shape in self.shapes:
            if shape.outside:
                continue
            if allowed is not None and shape.shape_type not in allowed:
                continue
            if shape.is_tracked and not include_tracked:
                continue
            if not shape.is_tracked and not include_standalone:
                continue
            result.append(shape)

        return result

    def objects_in_frame(
        self,
        frame: int,
        *,
        shape_types: Sequence[str] | None = None,
    ) -> list[NormShape]:
        """Các object đang hiển thị trong một frame."""
        if shape_types is None:
            return list(self.objects_by_frame().get(frame, ()))
        allowed = set(shape_types)
        return [
            shape for shape in self.objects_by_frame().get(frame, ()) if shape.shape_type in allowed
        ]

    def objects_by_frame(self) -> dict[int, list[NormShape]]:
        """Index ``frame -> danh sách object`` (được cache theo instance)."""
        return self._objects_by_frame_cache

    @functools.cached_property
    def _objects_by_frame_cache(self) -> dict[int, list[NormShape]]:
        index: dict[int, list[NormShape]] = {}
        for shape in self.shapes:
            if shape.outside:
                continue
            index.setdefault(shape.frame, []).append(shape)
        return index

    def object_by_key(self, object_key: str) -> NormShape | None:
        """Tra object theo ``object_key``."""
        for shape in self.shapes:
            if shape.object_key == object_key:
                return shape
        return None

    def tags_in_frame(self, frame: int) -> list[NormTag]:
        """Các tag của một frame."""
        return [tag for tag in self.tags if tag.frame == frame]

    def frames_with_objects(self) -> set[int]:
        """Tập frame có ít nhất một object đang hiển thị."""
        return set(self.objects_by_frame().keys())

    def all_frames(self) -> range:
        """Toàn bộ frame của task (theo ``size``), hoặc suy ra từ dữ liệu."""
        if self.size:
            return range(self.size)

        last = -1
        for shape in self.shapes:
            last = max(last, shape.frame)
        for tag in self.tags:
            last = max(last, tag.frame)

        return range(last + 1)

    def job_for_frame(self, frame: int) -> int | None:
        """ID job chứa frame (``None`` nếu không xác định được)."""
        for job in self.jobs:
            if job.contains(frame):
                return job.id
        return self.source_job_id
