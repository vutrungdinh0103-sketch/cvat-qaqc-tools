"""Các rule hình học: kích thước, tràn khung, trùng lặp, quan hệ chứa nhau.

Đây là nơi cài đặt 2 kiểm tra bắt buộc theo ``.clinerules``:

- ``invalid_size``: box/polygon có ``w <= 0`` hoặc ``h <= 0`` (kể cả tràn khung
  theo nghĩa toạ độ đảo - xem thêm ``out_of_frame`` cho trường hợp vượt biên ảnh).
- ``duplicate_bbox``: 2 object cùng frame (và cùng label nếu cấu hình yêu cầu)
  trùng nhau với ``IoU > 0.85``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import ClassVar

from pydantic import Field

from ..geometry import compute_iou, contained_ratio
from ..model import NormShape, Severity
from .base import Finding, Rule, RuleContext, RuleParams, ShapeSelectionParams


def bbox_str(bbox: tuple[float, float, float, float]) -> str:
    """Chuỗi mô tả bbox theo định dạng dùng trong báo cáo."""
    return f"({bbox[0]:g}, {bbox[1]:g}, {bbox[2]:g}, {bbox[3]:g})"


def shape_ref(shape: NormShape) -> str:
    """Chuỗi tham chiếu object trong message (giữ định dạng của CLI cũ)."""
    if shape.shape_id is not None:
        return f"shape #{shape.shape_id}"
    if shape.track_id is not None:
        return f"track #{shape.track_id}@frame{shape.frame}"
    return shape.object_key


class InvalidSizeRule(Rule):
    """Kiểm tra box/polygon có kích thước không hợp lệ.

    ``rectangle`` bị đảo toạ độ (``xbr <= xtl``) cũng bị báo vì rule dùng
    ``points`` gốc của CVAT, không tự chuẩn hoá ``min``/``max``.
    """

    rule_id: ClassVar[str] = "invalid_size"
    description: ClassVar[str] = "Box/polygon có width <= 0 hoặc height <= 0 (kể cả toạ độ bị đảo)."
    default_severity: ClassVar[Severity] = Severity.ERROR
    params_model: ClassVar[type[ShapeSelectionParams]] = ShapeSelectionParams
    group: ClassVar[str] = "geometry"

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        params: ShapeSelectionParams = self.params  # type: ignore[assignment]

        for shape in params.selected(ctx):
            bbox = shape.bbox
            if bbox is None or not shape.is_degenerate:
                continue

            yield self.finding(
                frame=shape.frame,
                object_keys=(shape.object_key,),
                label=shape.label,
                message=(
                    f"[invalid_size] frame {shape.frame}, {shape_ref(shape)} "
                    f"({shape.shape_type}, label='{shape.label}'): "
                    f"width={shape.width:g}, height={shape.height:g}, bbox={bbox_str(bbox)}"
                ),
                details={
                    "shape_type": shape.shape_type,
                    "label_id": shape.label_id,
                    "width": shape.width,
                    "height": shape.height,
                    "xtl": bbox[0],
                    "ytl": bbox[1],
                    "xbr": bbox[2],
                    "ybr": bbox[3],
                },
                discriminator="invalid_size",
            )


class OutOfFrameParams(ShapeSelectionParams):
    """Tham số của rule ``out_of_frame``."""

    #: Cho phép vượt khung bao nhiêu pixel (làm tròn sai số khi kéo box).
    tolerance_px: float = Field(default=0.5, ge=0.0)
    #: Nếu > 0: báo thêm cả trường hợp phần nhìn thấy nhỏ hơn tỉ lệ này.
    min_visible_ratio: float = Field(default=0.0, ge=0.0, le=1.0)


class OutOfFrameRule(Rule):
    """Kiểm tra box vượt ra ngoài khung ảnh (``.clinerules`` mục 1).

    Cần biết kích thước frame; nếu CVAT không trả về kích thước (một số task
    ``video``/``3D``), rule tự bỏ qua và ghi cảnh báo vào báo cáo.
    """

    rule_id: ClassVar[str] = "out_of_frame"
    description: ClassVar[str] = "Box vượt ra ngoài khung ảnh quá dung sai cho phép."
    default_severity: ClassVar[Severity] = Severity.ERROR
    params_model: ClassVar[type[OutOfFrameParams]] = OutOfFrameParams
    group: ClassVar[str] = "geometry"

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        params: OutOfFrameParams = self.params  # type: ignore[assignment]
        missing_size = 0

        for shape in params.selected(ctx):
            bbox = shape.bbox
            if bbox is None or shape.is_degenerate:
                continue

            frame_size = ctx.frame_size(shape.frame)
            if frame_size is None:
                missing_size += 1
                continue

            width, height = frame_size
            overflow_x = max(-bbox[0], bbox[2] - width, 0.0)
            overflow_y = max(-bbox[1], bbox[3] - height, 0.0)
            outside = overflow_x > params.tolerance_px or overflow_y > params.tolerance_px

            visible_ratio = 1.0
            if shape.area > 0:
                visible_w = max(0.0, min(bbox[2], width) - max(bbox[0], 0.0))
                visible_h = max(0.0, min(bbox[3], height) - max(bbox[1], 0.0))
                visible_ratio = (visible_w * visible_h) / shape.area

            low_visibility = (
                params.min_visible_ratio > 0.0 and visible_ratio < params.min_visible_ratio
            )
            if not outside and not low_visibility:
                continue

            reason = (
                f"tràn khung {overflow_x:g}px ngang / {overflow_y:g}px dọc"
                if outside
                else f"chỉ nhìn thấy {visible_ratio:.0%} diện tích"
            )
            yield self.finding(
                frame=shape.frame,
                object_keys=(shape.object_key,),
                label=shape.label,
                message=(
                    f"[out_of_frame] frame {shape.frame}, {shape_ref(shape)} "
                    f"({shape.shape_type}, label='{shape.label}'): {reason} "
                    f"(khung ảnh {width}x{height}, bbox={bbox_str(bbox)})"
                ),
                details={
                    "shape_type": shape.shape_type,
                    "label_id": shape.label_id,
                    "frame_width": width,
                    "frame_height": height,
                    "overflow_x_px": overflow_x,
                    "overflow_y_px": overflow_y,
                    "visible_ratio": round(visible_ratio, 6),
                    "bbox": list(bbox),
                },
                discriminator="out_of_frame",
            )

        if missing_size:
            self.warnings.append(
                f"Rule 'out_of_frame': bỏ qua {missing_size} object vì không xác định được "
                "kích thước frame (CVAT không trả về meta)."
            )


class TinyBoxParams(ShapeSelectionParams):
    """Tham số của rule ``tiny_box``."""

    min_area_px: float = Field(default=16.0, ge=0.0)
    min_side_px: float = Field(default=2.0, ge=0.0)


class TinyBoxRule(Rule):
    """Phát hiện box quá nhỏ - thường là click nhầm khi annotate."""

    rule_id: ClassVar[str] = "tiny_box"
    description: ClassVar[str] = (
        "Box có diện tích hoặc cạnh ngắn nhỏ hơn ngưỡng (thường do click nhầm)."
    )
    default_severity: ClassVar[Severity] = Severity.WARNING
    params_model: ClassVar[type[TinyBoxParams]] = TinyBoxParams
    group: ClassVar[str] = "geometry"

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        params: TinyBoxParams = self.params  # type: ignore[assignment]

        for shape in params.selected(ctx):
            bbox = shape.bbox
            if bbox is None or shape.is_degenerate:
                continue

            too_small_area = shape.area < params.min_area_px
            too_small_side = shape.min_side < params.min_side_px
            if not (too_small_area or too_small_side):
                continue

            reasons = []
            if too_small_area:
                reasons.append(f"diện tích {shape.area:g}px² < {params.min_area_px:g}px²")
            if too_small_side:
                reasons.append(f"cạnh ngắn {shape.min_side:g}px < {params.min_side_px:g}px")

            yield self.finding(
                frame=shape.frame,
                object_keys=(shape.object_key,),
                label=shape.label,
                message=(
                    f"[tiny_box] frame {shape.frame}, {shape_ref(shape)} "
                    f"({shape.shape_type}, label='{shape.label}'): "
                    + ", ".join(reasons)
                    + f", bbox={bbox_str(bbox)}"
                ),
                details={
                    "shape_type": shape.shape_type,
                    "label_id": shape.label_id,
                    "area_px": round(shape.area, 6),
                    "min_side_px": round(shape.min_side, 6),
                    "min_area_px": params.min_area_px,
                    "min_side_threshold_px": params.min_side_px,
                },
                discriminator="tiny_box",
            )


class DuplicateParams(ShapeSelectionParams):
    """Tham số của rule ``duplicate_bbox``."""

    #: Ngưỡng IoU để coi 2 object là trùng lặp (``.clinerules`` mục 2: > 0.85).
    iou_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    #: Chỉ coi là trùng lặp khi 2 object cùng label.
    same_label_only: bool = True
    #: Mặc định rule so các object "đóng" (có diện tích) để IoU có ý nghĩa.
    shape_types: tuple[str, ...] = ("rectangle", "polygon", "ellipse", "cuboid")


class DuplicateBBoxRule(Rule):
    """Phát hiện 2 object trùng nhau trong cùng frame với ``IoU > ngưỡng``.

    Cách nhóm giống CLI cũ: theo ``(frame, label)`` nếu ``same_label_only``, ngược
    lại theo ``frame``. Object suy biến (không tính được IoU) bị bỏ qua.
    """

    rule_id: ClassVar[str] = "duplicate_bbox"
    description: ClassVar[str] = (
        "Hai box/polygon cùng frame (và cùng label nếu bật) trùng nhau với IoU > ngưỡng."
    )
    default_severity: ClassVar[Severity] = Severity.ERROR
    params_model: ClassVar[type[DuplicateParams]] = DuplicateParams
    group: ClassVar[str] = "geometry"

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        params: DuplicateParams = self.params  # type: ignore[assignment]

        groups: dict[tuple[int, str], list[NormShape]] = {}
        for shape in params.selected(ctx):
            if shape.bbox is None or shape.is_degenerate:
                continue
            key = (shape.frame, shape.label if params.same_label_only else "")
            groups.setdefault(key, []).append(shape)

        for (frame, label), group in sorted(groups.items()):
            for index, first in enumerate(group):
                first_geometry = first.geometry()
                if first_geometry is None:
                    continue

                for second in group[index + 1 :]:
                    second_geometry = second.geometry()
                    if second_geometry is None:
                        continue

                    iou = compute_iou(first_geometry, second_geometry)
                    if iou <= params.iou_threshold:
                        continue

                    yield self.finding(
                        frame=frame,
                        object_keys=(first.object_key, second.object_key),
                        label=label or first.label,
                        message=(
                            f"[duplicate_bbox] frame {frame}, label '{label or first.label}': "
                            f"{shape_ref(first)} ({bbox_str(first.bbox)}) trùng với "
                            f"{shape_ref(second)} ({bbox_str(second.bbox)}) "
                            f"(IoU={iou:.4f} > {params.iou_threshold})"
                        ),
                        details={
                            "iou": round(iou, 6),
                            "iou_threshold": params.iou_threshold,
                            "label_id": first.label_id,
                            "shape_id_a": first.shape_id,
                            "shape_id_b": second.shape_id,
                            "shape_type_a": first.shape_type,
                            "shape_type_b": second.shape_type,
                            "object_key_a": first.object_key,
                            "object_key_b": second.object_key,
                            "bbox_a": list(first_geometry.bounds),
                            "bbox_b": list(second_geometry.bounds),
                        },
                        discriminator=f"{first.object_key}|{second.object_key}",
                    )


class ContainsPair(RuleParams):
    """Một cặp quan hệ "inner phải nằm trong outer"."""

    inner_labels: tuple[str, ...]
    #: Rỗng nghĩa là "nằm trong bất kỳ object nào khác".
    outer_labels: tuple[str, ...] = ()
    min_ratio: float = Field(default=0.9, ge=0.0, le=1.0)


class MustBeInsideParams(ShapeSelectionParams):
    """Tham số của rule ``must_be_inside``."""

    pairs: tuple[ContainsPair, ...] = ()
    #: Nếu > 0: chỉ xét các ``outer`` có tâm cách tâm ``inner`` không quá giá trị này.
    max_center_distance_px: float | None = Field(default=None, gt=0.0)


class MustBeInsideRule(Rule):
    """Kiểm tra quan hệ chứa nhau giữa các label (biển số trong xe, ...).

    Rule không làm gì nếu ``pairs`` rỗng, nên mặc định an toàn khi bật.
    """

    rule_id: ClassVar[str] = "must_be_inside"
    description: ClassVar[str] = (
        "Object thuộc nhóm 'inner' phải nằm trong object nhóm 'outer' với tỉ lệ diện "
        "tích tối thiểu (ví dụ license_plate phải nằm trong vehicle)."
    )
    default_severity: ClassVar[Severity] = Severity.ERROR
    params_model: ClassVar[type[MustBeInsideParams]] = MustBeInsideParams
    group: ClassVar[str] = "geometry"

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        params: MustBeInsideParams = self.params  # type: ignore[assignment]
        if not params.pairs:
            return

        candidates = params.selected(ctx)
        by_frame: dict[int, list[NormShape]] = {}
        for shape in candidates:
            if shape.bbox is None or shape.is_degenerate:
                continue
            by_frame.setdefault(shape.frame, []).append(shape)

        for frame in sorted(by_frame):
            objects = by_frame[frame]
            for pair in params.pairs:
                inners = [obj for obj in objects if obj.label in pair.inner_labels]
                inner_keys = {obj.object_key for obj in inners}
                outers = [
                    obj
                    for obj in objects
                    if obj.object_key not in inner_keys
                    and (not pair.outer_labels or obj.label in pair.outer_labels)
                ]
                for inner in inners:
                    outer_list = outers
                    if params.max_center_distance_px is not None:
                        outer_list = [
                            obj
                            for obj in outer_list
                            if self._center_distance(inner, obj) <= params.max_center_distance_px
                        ]
                    if not outer_list:
                        continue

                    best_outer, best_ratio = None, 0.0
                    inner_geometry = inner.geometry()
                    for outer in outer_list:
                        ratio = contained_ratio(inner_geometry, outer.geometry())
                        if ratio > best_ratio:
                            best_outer, best_ratio = outer, ratio

                    if best_ratio >= pair.min_ratio:
                        continue

                    outer_label = best_outer.label if best_outer else "(không có)"
                    object_keys = [inner.object_key]
                    if best_outer is not None:
                        object_keys.append(best_outer.object_key)

                    yield self.finding(
                        frame=frame,
                        object_keys=tuple(object_keys),
                        label=inner.label,
                        message=(
                            f"[must_be_inside] frame {frame}, {shape_ref(inner)} "
                            f"(label='{inner.label}'): chỉ {best_ratio:.0%} diện tích nằm trong "
                            f"'{outer_label}' (yêu cầu >= {pair.min_ratio:.0%})"
                        ),
                        details={
                            "inner_label": inner.label,
                            "inner_label_id": inner.label_id,
                            "outer_label": best_outer.label if best_outer else None,
                            "outer_key": best_outer.object_key if best_outer else None,
                            "contained_ratio": round(best_ratio, 6),
                            "min_ratio": pair.min_ratio,
                            "inner_bbox": list(inner.bbox) if inner.bbox else None,
                            "outer_bbox": (
                                list(best_outer.bbox) if best_outer and best_outer.bbox else None
                            ),
                        },
                        discriminator=f"{inner.object_key}|{outer_label}",
                    )

    @staticmethod
    def _center_distance(a: NormShape, b: NormShape) -> float:
        """Khoảng cách Euclid giữa tâm 2 bbox (``inf`` nếu thiếu bbox)."""
        if a.center is None or b.center is None:
            return float("inf")
        return ((a.center[0] - b.center[0]) ** 2 + (a.center[1] - b.center[1]) ** 2) ** 0.5
