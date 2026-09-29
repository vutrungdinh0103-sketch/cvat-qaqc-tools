"""Các rule về tính đầy đủ: thiếu nhãn, thiếu attribute, nhãn lạ, frame trống.

Đây là nơi cài đặt mục 3 của ``.clinerules``: "kiểm tra thiếu nhãn
(Missing labels/attributes) theo danh sách quy định":

- ``missing_label``: nhãn bắt buộc (theo cấu hình) không xuất hiện.
- ``required_attributes``: object thiếu/để trống attribute bắt buộc.
- ``unexpected_label``: nhãn không nằm trong danh sách cho phép.
- ``empty_frame``: frame không có annotation nào trong phạm vi yêu cầu.
"""

from __future__ import annotations

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
