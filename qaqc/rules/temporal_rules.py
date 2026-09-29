"""Các rule theo thời gian (chỉ áp dụng cho track).

Lưu ý về dữ liệu CVAT: mỗi track có một ``label_id`` cố định, nên "đổi class"
thực chất là hiện tượng **ID switch kèm đổi nhãn**: một track kết thúc rồi một
track khác (khác nhãn) bắt đầu ngay sau đó tại cùng vị trí. Rule
``track_class_change`` phát hiện đúng tình huống này.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterable
from typing import ClassVar

from pydantic import Field

from ..geometry import compute_iou
from ..model import NormShape, NormTrack, Severity
from .base import Finding, Rule, RuleContext, RuleParams


class TrackGapParams(RuleParams):
    """Tham số của rule ``track_gap``."""

    #: Khoảng trống tối đa cho phép giữa 2 keyframe liên tiếp (đơn vị: frame).
    max_gap_frames: int = Field(default=2, ge=0)
    #: Diện tích tối thiểu của bbox để xét (bỏ qua object quá nhỏ/nhiễu).
    min_area_px: float = Field(default=1.0, ge=0.0)


class TrackGapRule(Rule):
    """Phát hiện track bị "nhảy" keyframe (khoảng trống lớn giữa 2 keyframe).

    Trong CVAT, giữa 2 keyframe object được nội suy tuyến tính; khoảng cách quá
    lớn làm nội suy sai (object trượt qua vật thể khác hoặc sai vị trí).
    """

    rule_id: ClassVar[str] = "track_gap"
    description: ClassVar[str] = (
        "Track có khoảng trống keyframe lớn hơn ngưỡng (nội suy có thể sai)."
    )
    default_severity: ClassVar[Severity] = Severity.WARNING
    params_model: ClassVar[type[TrackGapParams]] = TrackGapParams
    group: ClassVar[str] = "temporal"

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        params: TrackGapParams = self.params  # type: ignore[assignment]

        for track in ctx.data.tracks:
            visible = list(track.visible_shapes)
            if len(visible) < 2:
                continue

            for previous, current in itertools.pairwise(visible):
                gap = current.frame - previous.frame - 1
                if gap <= params.max_gap_frames:
                    continue
                if previous.area < params.min_area_px or current.area < params.min_area_px:
                    continue

                yield self.finding(
                    frame=current.frame,
                    object_keys=(previous.object_key, current.object_key),
                    label=track.label,
                    message=(
                        f"[track_gap] track #{track.track_id} (label='{track.label}'): "
                        f"khoảng trống {gap} frame giữa keyframe {previous.frame} và "
                        f"{current.frame} (ngưỡng {params.max_gap_frames})"
                    ),
                    details={
                        "track_id": track.track_id,
                        "label": track.label,
                        "label_id": track.label_id,
                        "gap_frames": gap,
                        "max_gap_frames": params.max_gap_frames,
                        "frame_before": previous.frame,
                        "frame_after": current.frame,
                    },
                    discriminator=f"gap@{previous.frame}->{current.frame}",
                )


class TrackClassChangeParams(RuleParams):
    """Tham số của rule ``track_class_change``."""

    #: IoU tối thiểu giữa keyframe cuối của track trước và keyframe đầu track sau.
    min_iou: float = Field(default=0.5, ge=0.0, le=1.0)
    #: Khoảng cách frame tối đa giữa keyframe cuối track trước và keyframe đầu track sau.
    max_frame_distance: int = Field(default=1, ge=0)


class TrackClassChangeRule(Rule):
    """Phát hiện ID switch kèm đổi nhãn giữa 2 track liên tiếp."""

    rule_id: ClassVar[str] = "track_class_change"
    description: ClassVar[str] = (
        "Hai track liên tiếp (khác nhãn) chồng lên nhau tại cùng vị trí - dấu hiệu "
        "ID switch/đổi class giữa video."
    )
    default_severity: ClassVar[Severity] = Severity.ERROR
    params_model: ClassVar[type[TrackClassChangeParams]] = TrackClassChangeParams
    group: ClassVar[str] = "temporal"

    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        params: TrackClassChangeParams = self.params  # type: ignore[assignment]

        tracks = [track for track in ctx.data.tracks if track.visible_shapes]
        for before in tracks:
            before_last = self._last_visible(before)
            if before_last is None or before_last.geometry() is None:
                continue

            for after in tracks:
                if after.track_id == before.track_id or after.label_id == before.label_id:
                    continue

                after_first = self._first_visible(after)
                if after_first is None or after_first.geometry() is None:
                    continue

                distance = after_first.frame - before_last.frame
                if distance < 0 or distance > params.max_frame_distance:
                    continue

                iou = compute_iou(before_last.geometry(), after_first.geometry())
                if iou < params.min_iou:
                    continue

                yield self.finding(
                    frame=after_first.frame,
                    object_keys=(before_last.object_key, after_first.object_key),
                    label=after.label,
                    message=(
                        f"[track_class_change] frame {after_first.frame}: track "
                        f"#{before.track_id} (label='{before.label}') kết thúc tại frame "
                        f"{before_last.frame}, track #{after.track_id} "
                        f"(label='{after.label}') bắt đầu ngay đó (IoU={iou:.4f}) "
                        f"- nghi đổi class/ID switch"
                    ),
                    details={
                        "track_id_before": before.track_id,
                        "track_id_after": after.track_id,
                        "label_before": before.label,
                        "label_after": after.label,
                        "iou": round(iou, 6),
                        "min_iou": params.min_iou,
                        "frame_before": before_last.frame,
                        "frame_after": after_first.frame,
                        "object_key_before": before_last.object_key,
                        "object_key_after": after_first.object_key,
                    },
                    discriminator=f"t{before.track_id}->t{after.track_id}",
                )

    @staticmethod
    def _last_visible(track: NormTrack) -> NormShape | None:
        """Keyframe hiển thị cuối cùng của track."""
        visible = track.visible_shapes
        return visible[-1] if visible else None

    @staticmethod
    def _first_visible(track: NormTrack) -> NormShape | None:
        """Keyframe hiển thị đầu tiên của track."""
        visible = track.visible_shapes
        return visible[0] if visible else None
