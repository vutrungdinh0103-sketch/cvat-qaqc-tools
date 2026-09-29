"""Tải dữ liệu task/job từ CVAT server về :class:`TaskData`.

Đây là module **duy nhất** trong lớp engine phụ thuộc ``cvat_sdk``; phần còn lại
(rule, engine, report) chạy thuần Python nên test được offline.

Mọi lời gọi API đều đi qua SDK chính thức ``cvat_sdk.Client`` (theo
``.clinerules``), không dùng REST cũ.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from cvat_sdk import Client

from .model import TaskData
from .normalize import build_task_data

LOGGER = logging.getLogger("cvat_qaqc.data_source")


class CVATDataSource:
    """Đọc annotations + metadata của task/job từ CVAT."""

    def __init__(self, client: Client, *, logger: logging.Logger | None = None) -> None:
        self.client = client
        self.logger = logger or LOGGER

    # ------------------------------------------------------------------
    # Tải dữ liệu
    # ------------------------------------------------------------------
    def fetch_task(
        self,
        task_id: int,
        *,
        frame_range: tuple[int, int] | None = None,
    ) -> TaskData:
        """Tải một task theo ID.

        :param frame_range: chỉ lấy annotation trong khoảng ``(start, stop)``.
        """
        self.logger.info("Tải task #%s từ CVAT...", task_id)
        task = self.client.tasks.retrieve(task_id)
        annotations = task.get_annotations()
        labels = self._safe_call(task, "get_labels")
        frames_info = self._safe_call(task, "get_frames_info")
        jobs = self._safe_call(task, "get_jobs")

        data = build_task_data(
            task_id=int(task.id),
            task_name=getattr(task, "name", None),
            size=_as_int(getattr(task, "size", None)),
            dimension=_as_text(getattr(task, "dimension", None)),
            frames_info=frames_info,
            labels=labels,
            annotations=annotations,
            jobs=jobs,
            frame_range=frame_range,
        )
        self.logger.info(
            "Task #%s: %s object, frame %s.",
            task_id,
            len(data.objects()),
            data.describe_frames(),
        )
        return data

    def fetch_job(self, job_id: int) -> TaskData:
        """Tải một job theo ID (chỉ annotation trong khoảng frame của job đó)."""
        self.logger.info("Tải job #%s từ CVAT...", job_id)
        job = self.client.jobs.retrieve(job_id)
        annotations = job.get_annotations()
        labels = self._safe_call(job, "get_labels")
        frames_info = self._safe_call(job, "get_frames_info")

        task_id = int(getattr(job, "task_id", job_id))
        task: Any = None
        jobs: Iterable[Any] = ()
        task_name = getattr(job, "task_name", None)
        size = _as_int(getattr(job, "frame_count", None))

        try:
            task = self.client.tasks.retrieve(task_id)
            task_name = getattr(task, "name", task_name)
            size = _as_int(getattr(task, "size", None)) or size
            jobs = self._safe_call(task, "get_jobs")
            if not frames_info:
                frames_info = self._safe_call(task, "get_frames_info")
        except Exception as exc:
            self.logger.warning(
                "Không tải được task #%s (job #%s): %s: %s",
                task_id,
                job_id,
                type(exc).__name__,
                exc,
            )

        start_frame = _as_int(getattr(job, "start_frame", None))
        stop_frame = _as_int(getattr(job, "stop_frame", None))
        frame_range = (
            (start_frame, stop_frame)
            if start_frame is not None and stop_frame is not None
            else None
        )

        return build_task_data(
            task_id=task_id,
            task_name=task_name,
            size=size,
            dimension=_as_text(getattr(task, "dimension", None)) if task is not None else None,
            frames_info=frames_info,
            labels=labels,
            annotations=annotations,
            jobs=jobs,
            source_job_id=int(job.id),
            frame_range=frame_range,
        )

    # ------------------------------------------------------------------
    # Nội bộ
    # ------------------------------------------------------------------
    def _safe_call(self, entity: Any, method_name: str) -> list[Any]:
        """Gọi một method "phụ" của SDK, trả ``[]`` nếu không hỗ trợ/lỗi.

        Các endpoint như ``get_frames_info``/``get_jobs`` có thể không khả dụng với
        một số loại task (3D, video cũ...) nên không được phép làm hỏng lần chạy.
        """
        method = getattr(entity, method_name, None)
        if not callable(method):
            return []
        try:
            return list(method())
        except Exception as exc:
            self.logger.warning("Không gọi được %s(): %s: %s", method_name, type(exc).__name__, exc)
            return []


def _as_int(value: Any) -> int | None:
    """Ép về ``int`` (``None`` nếu không được)."""
    if value is None:
        return None
    try:
        return int(getattr(value, "value", value))
    except (TypeError, ValueError):
        return None


def _as_text(value: Any) -> str | None:
    """Ép về ``str`` (``None`` nếu rỗng)."""
    if value is None:
        return None
    text = str(getattr(value, "value", value)).strip()
    return text or None
