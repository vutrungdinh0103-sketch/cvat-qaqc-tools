"""Khung sườn cho các rule QA/QC.

Một rule là một lớp con của :class:`Rule`:

- Khai báo ``rule_id`` (khoá dùng trong file cấu hình YAML), ``description``,
  ``default_severity`` và ``params_model`` (pydantic - tự validate tham số).
- Cài đặt :meth:`Rule.check`, nhận :class:`RuleContext` và trả về các
  :class:`Finding` (lỗi thô, chưa có fingerprint/job_id - engine sẽ bù).

Nhờ ``extra="forbid"`` trong :class:`RuleParams`, gõ sai tên tham số trong YAML
sẽ báo lỗi ngay thay vì âm thầm dùng giá trị mặc định.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..model import BOX_LIKE_TYPES, NormShape, Severity, TaskData

LOGGER = logging.getLogger("cvat_qaqc.rules")


class RuleParams(BaseModel):
    """Lớp cơ sở cho tham số rule (chặn khoá lạ)."""

    model_config = ConfigDict(extra="forbid")


class ShapeSelectionParams(RuleParams):
    """Tham số dùng chung cho rule hình học: lọc object theo loại/nguồn."""

    shape_types: tuple[str, ...] = BOX_LIKE_TYPES
    include_tracked: bool = True
    include_standalone: bool = True

    def selected(self, ctx: RuleContext) -> list[NormShape]:
        """Danh sách object đang hiển thị thoả điều kiện lọc."""
        return ctx.data.objects(
            shape_types=self.shape_types,
            include_tracked=self.include_tracked,
            include_standalone=self.include_standalone,
        )


class Finding(BaseModel):
    """Một lỗi thô do rule phát hiện (chưa có fingerprint/job_id)."""

    model_config = ConfigDict(frozen=True)

    rule_id: str
    severity: Severity
    frame: int
    message: str
    object_keys: tuple[str, ...] = ()
    label: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    #: Phần phân biệt thêm khi cùng rule/object/frame có nhiều lỗi khác nhau.
    discriminator: str | None = None

    @property
    def primary_key(self) -> str | None:
        """``object_key`` chính của lỗi (dùng để chọn lỗi đại diện)."""
        return self.object_keys[0] if self.object_keys else None


class RuleContext:
    """Bối cảnh một lần chạy: dữ liệu task + chỉ mục dùng chung cho mọi rule."""

    def __init__(self, data: TaskData, *, logger: logging.Logger | None = None) -> None:
        self.data = data
        self.logger = logger or LOGGER
        self._by_frame: dict[int, list[NormShape]] | None = None

    @property
    def frames(self) -> range:
        """Toàn bộ frame của task."""
        return self.data.all_frames()

    def objects_by_frame_index(self) -> dict[int, list[NormShape]]:
        """Index ``frame -> objects`` (chỉ xây một lần cho cả lần chạy)."""
        if self._by_frame is None:
            self._by_frame = self.data.objects_by_frame()
        return self._by_frame

    def objects_in_frame(self, frame: int) -> list[NormShape]:
        """Object đang hiển thị trong frame."""
        return self.objects_by_frame_index().get(frame, [])

    def frames_with_objects(self) -> list[int]:
        """Danh sách frame có ít nhất một object (đã sắp xếp)."""
        return sorted(self.objects_by_frame_index())

    def iter_frames(self) -> Iterator[tuple[int, list[NormShape]]]:
        """Duyệt ``(frame, objects)`` theo thứ tự frame tăng dần."""
        index = self.objects_by_frame_index()
        for frame in sorted(index):
            yield frame, index[frame]

    def frame_size(self, frame: int) -> tuple[int, int] | None:
        """Kích thước frame ``(width, height)`` nếu biết."""
        return self.data.frame_size(frame)


class Rule(ABC):
    """Lớp cơ sở của mọi rule QA/QC."""

    #: Khoá định danh rule (dùng trong file cấu hình YAML).
    rule_id: ClassVar[str] = ""
    #: Mô tả ngắn gọn (hiển thị ở ``python -m qaqc rules``).
    description: ClassVar[str] = ""
    #: Mức độ mặc định khi cấu hình không ghi đè.
    default_severity: ClassVar[Severity] = Severity.WARNING
    #: Model pydantic validate tham số của rule.
    params_model: ClassVar[type[RuleParams]] = RuleParams
    #: Nhóm rule (``geometry``/``completeness``/``temporal``) - dùng cho tài liệu.
    group: ClassVar[str] = "general"

    def __init__(
        self,
        params: Mapping[str, Any] | None = None,
        *,
        severity: Severity | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        try:
            self.params: RuleParams = self.params_model.model_validate(dict(params or {}))
        except ValidationError as exc:
            raise ValueError(f"Tham số không hợp lệ cho rule '{self.rule_id}':\n{exc}") from exc
        self.severity = severity or self.default_severity
        self.logger = logger or LOGGER
        #: Cảnh báo thu thập được trong lúc chạy (engine sẽ đưa vào báo cáo).
        self.warnings: list[str] = []

    @abstractmethod
    def check(self, ctx: RuleContext) -> Iterable[Finding]:
        """Chạy rule và trả về danh sách lỗi phát hiện được."""

    # ------------------------------------------------------------------
    # Tiện ích cho lớp con
    # ------------------------------------------------------------------
    def finding(
        self,
        *,
        frame: int,
        message: str,
        object_keys: Sequence[str] = (),
        label: str | None = None,
        details: Mapping[str, Any] | None = None,
        discriminator: str | None = None,
        severity: Severity | None = None,
    ) -> Finding:
        """Tạo :class:`Finding` với ``rule_id``/``severity`` của rule hiện tại."""
        return Finding(
            rule_id=self.rule_id,
            severity=severity or self.severity,
            frame=frame,
            message=message,
            object_keys=tuple(object_keys),
            label=label,
            details=dict(details or {}),
            discriminator=discriminator,
        )

    def describe(self) -> str:
        """Mô tả rule cho CLI/tài liệu."""
        return f"{self.rule_id} [{self.group}/{self.default_severity.value}]: {self.description}"
