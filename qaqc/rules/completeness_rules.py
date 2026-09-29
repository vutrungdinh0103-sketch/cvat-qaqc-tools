"""Các rule về tính đầy đủ: thiếu nhãn, thiếu attribute, nhãn lạ, frame trống.

Đây là nơi cài đặt mục 3 của ``.clinerules``: "kiểm tra thiếu nhãn
(Missing labels/attributes) theo danh sách quy định":

- ``missing_label``: nhãn bắt buộc (theo cấu hình) không xuất hiện.
- ``required_attributes``: object thiếu/để trống attribute bắt buộc.
- ``unexpected_label``: nhãn không nằm trong danh sách cho phép.
- ``empty_frame``: frame không có annotation nào trong phạm vi yêu cầu.
- ``empty_frame_range``: dải frame liên tiếp không có annotation (Level 1).
- ``object_count``: số object trong frame bất thường (Level 1).

Cả 6 rule đều thuộc **Level 1 - Overall / Completeness Check** của proposal
(``level = 1``), xem ``rules/level1_v1.yaml`` và ``docs/level1-demo.md``.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from typing import ClassVar, Literal

from pydantic import Field, model_validator

from ..model import Severity
from .base import Finding, Rule, RuleContext, RuleParams
from .geometry_rules import shape_ref


class FrameSelectionParams(RuleParams):
    """Tham số dùng chung để chọn phạm vi frame cần kiểm tra."""

    start_frame: int | None = Field(default=None, ge=0)
    stop_frame: int | None = Field(default=None, ge=0)
    #: Kiểm tra cách nhau bao nhiêu frame (1 = mọi frame).
    frame_step: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _check_range(self) -> FrameSelectionParams:
        if (
            self.start_frame is not None
            and self.stop_frame is not None
            and self.stop_frame < self.start_frame
        ):
            raise ValueError("stop_frame phải >= start_frame")
        return self

    def frames(self, ctx: RuleContext) -> list[int]:
        """Danh sách frame cần kiểm tra (đã áp ``start/stop/step``)."""
        frames = list(ctx.frames)
        if self.start_frame is not None:
            frames = [frame for frame in frames if frame >= self.start_frame]
        if self.stop_frame is not None:
            frames = [frame for frame in frames if frame <= self.stop_frame]
        return frames[:: self.frame_step]


class EmptyFrameParams(FrameSelectionParams):
    """Tham số của rule ``empty_frame``."""

    min_annotations: int = Field(default=1, ge=1)
    #: Bỏ qua các nhãn này khi đếm (ví dụ label đánh dấu "không quan tâm").
    ignore_labels: tuple[str, ...] = ()


class EmptyFrameRule(Rule):
    """Phát hiện frame không có bất kỳ annotation nào trong phạm vi yêu cầu.

    Mặc định rule *tắt* trong ``rules/driving_v1.yaml`` vì khá ồn với video dài;
    bật khi cần kiểm tra độ phủ (ví dụ cứ 30 frame phải có ít nhất 1 object).
    """

    rule_id: ClassVar[str] = "empty_frame"
    description: ClassVar[str] = (
        "Frame không có object/tag nào trong phạm vi kiểm tra (kiểm tra độ phủ)."
    )
    default_severity: ClassVar[Severity] = Severity.WARNING
    params_model: ClassVar[type[EmptyFrameParams]] = EmptyFrameParams
    group: ClassVar[str] = "completeness"
    #: Level 1 - Overall/Completeness Check.
    level: ClassVar[int] = 1

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        params: EmptyFrameParams = self.params  # type: ignore[assignment]
        ignore_labels = set(params.ignore_labels)

        for frame in params.frames(ctx):
            objects = [
                shape
                for shape in ctx.objects_in_frame(frame)
                if not ignore_labels or shape.label not in ignore_labels
            ]
            tags = [
                tag
                for tag in ctx.data.tags_in_frame(frame)
                if not ignore_labels or tag.label not in ignore_labels
            ]
            present = len(objects) + len(tags)
            if present >= params.min_annotations:
                continue

            yield self.finding(
                frame=frame,
                message=(
                    f"[empty_frame] frame {frame}: chỉ có {present} annotation, "
                    f"yêu cầu >= {params.min_annotations}"
                ),
                details={
                    "objects": len(objects),
                    "tags": len(tags),
                    "min_annotations": params.min_annotations,
                    "frame_step": params.frame_step,
                },
                discriminator=f"frame{frame}",
            )


class MissingLabelParams(FrameSelectionParams):
    """Tham số của rule ``missing_label``."""

    #: Nhãn bắt buộc phải có (theo danh sách quy định của dự án).
    labels: tuple[str, ...] = ()
    #: ``frame``: kiểm tra trên từng frame; ``task``: kiểm tra trên toàn task.
    scope: Literal["frame", "task"] = "frame"
    min_count: int = Field(default=1, ge=1)


class MissingLabelRule(Rule):
    """Kiểm tra thiếu nhãn bắt buộc (``.clinerules`` mục 3).

    Rule không làm gì khi ``labels`` rỗng nên bật mặc định là an toàn.
    """

    rule_id: ClassVar[str] = "missing_label"
    description: ClassVar[str] = (
        "Nhãn bắt buộc (theo danh sách quy định) không xuất hiện trong frame/toàn task."
    )
    default_severity: ClassVar[Severity] = Severity.ERROR
    params_model: ClassVar[type[MissingLabelParams]] = MissingLabelParams
    group: ClassVar[str] = "completeness"
    #: Level 1 - Overall/Completeness Check.
    level: ClassVar[int] = 1

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        params: MissingLabelParams = self.params  # type: ignore[assignment]
        if not params.labels:
            self.logger.debug("Rule 'missing_label' chưa cấu hình 'labels' -> bỏ qua.")
            return

        required = list(params.labels)

        if params.scope == "task":
            yield from self._check_task_scope(ctx, params, required)
            return

        for frame in params.frames(ctx):
            present = self._count_by_label(ctx, frame)
            for label in required:
                count = present.get(label, 0)
                if count >= params.min_count:
                    continue
                yield self.finding(
                    frame=frame,
                    label=label,
                    message=(
                        f"[missing_label] frame {frame}: thiếu nhãn '{label}' "
                        f"(cần >= {params.min_count}, hiện có {count})"
                    ),
                    details={
                        "label": label,
                        "min_count": params.min_count,
                        "present": count,
                        "scope": "frame",
                    },
                    discriminator=f"{label}|frame{frame}",
                )

    def _check_task_scope(
        self,
        ctx: RuleContext,
        params: MissingLabelParams,
        required: list[str],
    ) -> Iterable[Finding]:
        totals: dict[str, int] = {}
        for shape in ctx.data.objects():
            totals[shape.label] = totals.get(shape.label, 0) + 1
        for tag in ctx.data.tags:
            totals[tag.label] = totals.get(tag.label, 0) + 1

        anchor_frame = params.start_frame if params.start_frame is not None else 0
        for label in required:
            count = totals.get(label, 0)
            if count >= params.min_count:
                continue
            yield self.finding(
                frame=anchor_frame,
                label=label,
                message=(
                    f"[missing_label] toàn task: thiếu nhãn '{label}' "
                    f"(cần >= {params.min_count}, hiện có {count})"
                ),
                details={
                    "label": label,
                    "min_count": params.min_count,
                    "present": count,
                    "scope": "task",
                },
                discriminator=f"{label}|task",
            )

    @staticmethod
    def _count_by_label(ctx: RuleContext, frame: int) -> dict[str, int]:
        counts: dict[str, int] = {}
        for shape in ctx.objects_in_frame(frame):
            counts[shape.label] = counts.get(shape.label, 0) + 1
        for tag in ctx.data.tags_in_frame(frame):
            counts[tag.label] = counts.get(tag.label, 0) + 1
        return counts


class RequiredAttributesParams(RuleParams):
    """Tham số của rule ``required_attributes``."""

    #: ``label -> danh sách attribute bắt buộc``.
    labels: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    #: Attribute bắt buộc cho mọi label không khai báo riêng ở trên.
    default_attributes: tuple[str, ...] = ()
    #: Coi giá trị rỗng (``""``) là thiếu.
    empty_counts_as_missing: bool = True
    check_shapes: bool = True
    check_tags: bool = True
    include_tracked: bool = True


class RequiredAttributesRule(Rule):
    """Kiểm tra object thiếu attribute bắt buộc (``.clinerules`` mục 3).

    Ví dụ cấu hình: mọi ``vehicle`` phải có ``vehicle_type``; mọi
    ``license_plate`` phải có ``plate_number``.
    """

    rule_id: ClassVar[str] = "required_attributes"
    description: ClassVar[str] = (
        "Object/tag thiếu hoặc để trống attribute bắt buộc của label tương ứng."
    )
    default_severity: ClassVar[Severity] = Severity.ERROR
    params_model: ClassVar[type[RequiredAttributesParams]] = RequiredAttributesParams
    group: ClassVar[str] = "completeness"
    #: Level 1 - Overall/Completeness Check.
    level: ClassVar[int] = 1

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        params: RequiredAttributesParams = self.params  # type: ignore[assignment]
        if not params.labels and not params.default_attributes:
            self.logger.debug("Rule 'required_attributes' chưa cấu hình -> bỏ qua.")
            return

        if params.check_shapes:
            for shape in ctx.data.objects(include_tracked=params.include_tracked):
                yield from self._check_object(
                    params,
                    frame=shape.frame,
                    label=shape.label,
                    label_id=shape.label_id,
                    attributes=shape.attributes,
                    object_keys=(shape.object_key,),
                    reference=shape.describe(),
                )

        if params.check_tags:
            for tag in ctx.data.tags:
                yield from self._check_object(
                    params,
                    frame=tag.frame,
                    label=tag.label,
                    label_id=tag.label_id,
                    attributes=tag.attributes,
                    object_keys=(tag.object_key,),
                    reference=f"tag {tag.object_key} (label='{tag.label}')",
                )

    def _check_object(
        self,
        params: RequiredAttributesParams,
        *,
        frame: int,
        label: str,
        label_id: int,
        attributes: dict[str, str],
        object_keys: tuple[str, ...],
        reference: str,
    ) -> Iterable[Finding]:
        required = params.labels.get(label, params.default_attributes)
        if not required:
            return

        for name in required:
            raw_value = attributes.get(name)
            filled = raw_value is not None and (
                not params.empty_counts_as_missing or str(raw_value).strip() != ""
            )
            if filled:
                continue

            yield self.finding(
                frame=frame,
                object_keys=object_keys,
                label=label,
                message=(
                    f"[required_attributes] frame {frame}, {reference}: thiếu attribute "
                    f"bắt buộc '{name}'" + (" (đang để trống)" if raw_value is not None else "")
                ),
                details={
                    "label": label,
                    "label_id": label_id,
                    "attribute": name,
                    "attribute_value": raw_value,
                    "empty_counts_as_missing": params.empty_counts_as_missing,
                    "required_by_config": list(required),
                },
                discriminator=name,
            )


class UnexpectedLabelParams(RuleParams):
    """Tham số của rule ``unexpected_label``."""

    #: Danh sách nhãn được phép. Rỗng = không kiểm tra.
    allowed_labels: tuple[str, ...] = ()
    check_shapes: bool = True
    check_tags: bool = True
    include_tracked: bool = True


class UnexpectedLabelRule(Rule):
    """Phát hiện nhãn nằm ngoài danh sách cho phép."""

    rule_id: ClassVar[str] = "unexpected_label"
    description: ClassVar[str] = (
        "Object/tag dùng nhãn không nằm trong danh sách nhãn cho phép của dự án."
    )
    default_severity: ClassVar[Severity] = Severity.WARNING
    params_model: ClassVar[type[UnexpectedLabelParams]] = UnexpectedLabelParams
    group: ClassVar[str] = "completeness"
    #: Level 1 - Overall/Completeness Check.
    level: ClassVar[int] = 1

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        params: UnexpectedLabelParams = self.params  # type: ignore[assignment]
        if not params.allowed_labels:
            self.logger.debug("Rule 'unexpected_label' chưa cấu hình -> bỏ qua.")
            return

        allowed = set(params.allowed_labels)

        if params.check_shapes:
            for shape in ctx.data.objects(include_tracked=params.include_tracked):
                if shape.label in allowed:
                    continue
                yield self.finding(
                    frame=shape.frame,
                    object_keys=(shape.object_key,),
                    label=shape.label,
                    message=(
                        f"[unexpected_label] frame {shape.frame}, {shape_ref(shape)}: "
                        f"nhãn '{shape.label}' không nằm trong danh sách cho phép "
                        f"[{', '.join(params.allowed_labels)}]"
                    ),
                    details={
                        "label": shape.label,
                        "label_id": shape.label_id,
                        "shape_type": shape.shape_type,
                        "allowed_labels": list(params.allowed_labels),
                    },
                    discriminator=f"label:{shape.label}",
                )

        if params.check_tags:
            for tag in ctx.data.tags:
                if tag.label in allowed:
                    continue
                yield self.finding(
                    frame=tag.frame,
                    object_keys=(tag.object_key,),
                    label=tag.label,
                    message=(
                        f"[unexpected_label] frame {tag.frame}, tag {tag.object_key}: "
                        f"nhãn '{tag.label}' không nằm trong danh sách cho phép "
                        f"[{', '.join(params.allowed_labels)}]"
                    ),
                    details={
                        "label": tag.label,
                        "label_id": tag.label_id,
                        "shape_type": "tag",
                        "allowed_labels": list(params.allowed_labels),
                    },
                    discriminator=f"tag|label:{tag.label}",
                )


# ---------------------------------------------------------------------------
# Level 1 - Overall / Completeness Check (proposal mục 4)
# ---------------------------------------------------------------------------
class EmptyFrameRangeParams(FrameSelectionParams):
    """Tham số của rule ``empty_frame_range``."""

    #: Số frame trống **liên tiếp** tối thiểu để báo (2 = báo khi có >= 2 frame liền nhau).
    min_run_frames: int = Field(default=3, ge=2)
    #: Bỏ qua các nhãn này khi đếm (giống ``empty_frame``).
    ignore_labels: tuple[str, ...] = ()


class EmptyFrameRangeRule(Rule):
    """Phát hiện dải frame liên tiếp không có annotation (unlabeled frame range).

    Khác ``empty_frame`` (báo từng frame trống riêng lẻ - rất ồn với video dài),
    rule này chỉ báo khi số frame trống **liền nhau** >= ``min_run_frames``: dấu hiệu
    annotator bỏ sót cả một đoạn chứ không phải chỉ vài frame không có object.

    Vì ý nghĩa nằm ở tính *liên tiếp*, rule luôn quét từng frame (bỏ qua
    ``frame_step``) và ghi chú lại trong ``report.warnings`` nếu người dùng đặt
    ``frame_step`` khác 1.
    """

    rule_id: ClassVar[str] = "empty_frame_range"
    description: ClassVar[str] = (
        "Dải frame liên tiếp không có annotation nào (unlabeled frame range)."
    )
    default_severity: ClassVar[Severity] = Severity.WARNING
    params_model: ClassVar[type[EmptyFrameRangeParams]] = EmptyFrameRangeParams
    group: ClassVar[str] = "completeness"
    #: Level 1 - Overall/Completeness Check.
    level: ClassVar[int] = 1

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        params: EmptyFrameRangeParams = self.params  # type: ignore[assignment]
        if params.frame_step != 1:
            self.warnings.append(
                "Rule 'empty_frame_range' luôn quét từng frame (đã bỏ qua "
                f'frame_step={params.frame_step}) để giữ đúng nghĩa "liên tiếp".'
            )

        ignore_labels = set(params.ignore_labels)
        run: list[int] = []
        for frame in self._frames(ctx, params):
            if self._is_empty(ctx, frame, ignore_labels):
                run.append(frame)
                continue
            if len(run) >= params.min_run_frames:
                yield self._finding_for_run(run, params)
            run = []

        if len(run) >= params.min_run_frames:
            yield self._finding_for_run(run, params)

    @staticmethod
    def _frames(ctx: RuleContext, params: EmptyFrameRangeParams) -> list[int]:
        """Danh sách frame cần quét (tôn trọng ``start_frame``/``stop_frame``)."""
        frames = list(ctx.frames)
        if params.start_frame is not None:
            frames = [frame for frame in frames if frame >= params.start_frame]
        if params.stop_frame is not None:
            frames = [frame for frame in frames if frame <= params.stop_frame]
        return frames

    @staticmethod
    def _is_empty(ctx: RuleContext, frame: int, ignore_labels: set[str]) -> bool:
        """``True`` nếu frame không còn annotation nào sau khi lọc ``ignore_labels``."""
        objects = [
            shape for shape in ctx.objects_in_frame(frame) if shape.label not in ignore_labels
        ]
        tags = [tag for tag in ctx.data.tags_in_frame(frame) if tag.label not in ignore_labels]
        return not objects and not tags

    def _finding_for_run(self, run: list[int], params: EmptyFrameRangeParams) -> Finding:
        """Một lỗi đại diện cho cả dải frame trống (neo ở frame đầu)."""
        first, last = run[0], run[-1]
        return self.finding(
            frame=first,
            message=(
                f"[empty_frame_range] frame {first}..{last}: {len(run)} frame liên tiếp "
                f"không có annotation nào (ngưỡng >= {params.min_run_frames})"
            ),
            details={
                "start_frame": first,
                "stop_frame": last,
                "frames": len(run),
                "min_run_frames": params.min_run_frames,
            },
            discriminator=f"run{first}-{last}",
        )


class ObjectCountParams(FrameSelectionParams):
    """Tham số của rule ``object_count`` (suspicious object count)."""

    #: Frame phải có ít nhất bao nhiêu object (``None`` = không kiểm tra cận dưới).
    min_objects: int | None = Field(default=None, ge=0)
    #: Frame không được vượt quá bao nhiêu object (``None`` = không kiểm tra cận trên).
    max_objects: int | None = Field(default=None, ge=1)
    #: Báo frame có số object > ``median * outlier_ratio``; ``None`` = tắt kiểm tra ngoại lai.
    outlier_ratio: float | None = Field(default=None, gt=1.0)
    #: Cần tối thiểu bao nhiêu frame có object để tính median (tránh ồn với task nhỏ).
    min_frames_for_outlier: int = Field(default=5, ge=2)
    #: Nhãn không tính vào số lượng (ví dụ nhãn ``crowd``/``ignore``).
    ignore_labels: tuple[str, ...] = ()


class ObjectCountRule(Rule):
    """Phát hiện số object trong frame bất thường (suspicious object count).

    Ba kiểu kiểm tra, bật/tắt độc lập bằng tham số - tương ứng 2 mục Level 1:

    - ``min_objects``: "Missing annotation" - frame có ít object hơn quy định.
    - ``max_objects``: "Suspicious object count" - frame có quá nhiều object
      (nghi ngờ dán nhầm/nhân bản annotation).
    - ``outlier_ratio``: "Suspicious object count" theo thống kê - số object vượt xa
      trung vị của task (median của các frame **có** object, cần tối thiểu
      ``min_frames_for_outlier`` frame để tránh ồn).

    Chỉ đếm shape (không đếm tag) vì tag không phải "object".
    """

    rule_id: ClassVar[str] = "object_count"
    description: ClassVar[str] = (
        "Số object trong frame bất thường: ít hơn min_objects, nhiều hơn max_objects "
        "hoặc vượt xa trung vị của task (nghi ngờ thiếu/nhân bản annotation)."
    )
    default_severity: ClassVar[Severity] = Severity.WARNING
    params_model: ClassVar[type[ObjectCountParams]] = ObjectCountParams
    group: ClassVar[str] = "completeness"
    #: Level 1 - Overall/Completeness Check.
    level: ClassVar[int] = 1

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        params: ObjectCountParams = self.params  # type: ignore[assignment]
        if (
            params.min_objects is None
            and params.max_objects is None
            and params.outlier_ratio is None
        ):
            self.logger.debug("Rule 'object_count' chưa cấu hình ngưỡng -> bỏ qua.")
            return

        ignore_labels = set(params.ignore_labels)
        frames = params.frames(ctx)
        counts = {
            frame: sum(
                1 for shape in ctx.objects_in_frame(frame) if shape.label not in ignore_labels
            )
            for frame in frames
        }
        median = self._median(counts)
        outlier_threshold = self._outlier_threshold(median, counts, params)

        for frame in frames:
            present = counts[frame]
            reasons = self._reasons(present, outlier_threshold, params)
            if not reasons:
                continue

            yield self.finding(
                frame=frame,
                message=(
                    f"[object_count] frame {frame}: {present} object "
                    f"({', '.join(reasons)}){self._threshold_hint(params, outlier_threshold)}"
                ),
                details={
                    "objects": present,
                    "min_objects": params.min_objects,
                    "max_objects": params.max_objects,
                    "outlier_ratio": params.outlier_ratio,
                    "outlier_threshold": outlier_threshold,
                    "median_objects": median,
                    "reasons": reasons,
                    "frame_step": params.frame_step,
                },
                discriminator="|".join(reasons),
            )

    @staticmethod
    def _median(counts: dict[int, int]) -> float | None:
        """Trung vị số object của các frame **có** object (``None`` nếu không có)."""
        populated = [count for count in counts.values() if count > 0]
        return statistics.median(populated) if populated else None

    @staticmethod
    def _outlier_threshold(
        median: float | None,
        counts: dict[int, int],
        params: ObjectCountParams,
    ) -> float | None:
        """Ngưỡng "quá nhiều" theo thống kê (``None`` nếu chưa đủ dữ liệu để kết luận)."""
        if params.outlier_ratio is None or median is None:
            return None
        populated_frames = sum(1 for count in counts.values() if count > 0)
        if populated_frames < params.min_frames_for_outlier:
            return None
        return median * params.outlier_ratio

    @staticmethod
    def _reasons(
        present: int,
        outlier_threshold: float | None,
        params: ObjectCountParams,
    ) -> list[str]:
        """Các lý do frame bị coi là bất thường."""
        reasons: list[str] = []
        if params.min_objects is not None and present < params.min_objects:
            reasons.append("quá ít")
        if params.max_objects is not None and present > params.max_objects:
            reasons.append("quá nhiều")
        if outlier_threshold is not None and present > outlier_threshold:
            reasons.append("bất thường so với trung vị")
        return reasons

    @staticmethod
    def _threshold_hint(params: ObjectCountParams, outlier_threshold: float | None) -> str:
        """Chuỗi mô tả ngưỡng đang áp dụng (để message tự giải thích được)."""
        parts: list[str] = []
        if params.min_objects is not None:
            parts.append(f"min={params.min_objects}")
        if params.max_objects is not None:
            parts.append(f"max={params.max_objects}")
        if outlier_threshold is not None:
            parts.append(f"ngưỡng bất thường>{outlier_threshold:g}")
        return f" (ngưỡng: {', '.join(parts)})" if parts else ""
