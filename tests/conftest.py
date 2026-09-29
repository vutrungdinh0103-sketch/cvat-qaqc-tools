"""Fixture dùng chung cho bộ test QA/QC.

Nguyên tắc: engine QA/QC là thuần Python nên test chạy **offline**, không cần
CVAT server. Dữ liệu vào được dựng trực tiếp bằng model chuẩn hoá, riêng luồng
chuẩn hoá từ API được test bằng fixture JSON trong ``tests/fixtures``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from qaqc.config import RuleConfig
from qaqc.engine import QAQCEngine
from qaqc.model import (
    JobInfo,
    LabelAttribute,
    LabelSchema,
    NormShape,
    NormTag,
    NormTrack,
    TaskData,
)
from qaqc.normalize import build_task_data
from qaqc.report import QAReport

FIXTURES_DIR = Path(__file__).parent / "fixtures"

#: Kích thước frame mặc định cho dữ liệu test.
FRAME_SIZE = (100, 50)

#: Task id mặc định.
TASK_ID = 42


@pytest.fixture(autouse=True)
def _quiet_logging() -> None:
    """Giảm nhiễu log trong lúc test."""
    logging.getLogger("cvat_qaqc").setLevel(logging.CRITICAL)
    logging.getLogger("cvat_qaqc.rules").setLevel(logging.CRITICAL)
    logging.getLogger("cvat_qaqc.engine").setLevel(logging.CRITICAL)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------
def make_shape(
    object_key: str = "s1",
    frame: int = 0,
    shape_type: str = "rectangle",
    label: str = "vehicle",
    label_id: int = 1,
    points: Sequence[float] = (10.0, 10.0, 30.0, 30.0),
    attributes: Mapping[str, str] | None = None,
    *,
    outside: bool = False,
    shape_id: int | None = None,
    track_id: int | None = None,
) -> NormShape:
    """Tạo một :class:`NormShape` cho test."""
    return NormShape(
        object_key=object_key,
        frame=frame,
        shape_type=shape_type,
        label_id=label_id,
        label=label,
        points=tuple(float(value) for value in points),
        attributes=dict(attributes or {}),
        shape_id=shape_id,
        track_id=track_id,
        outside=outside,
    )


def make_track(
    track_id: int = 100,
    label: str = "vehicle",
    label_id: int = 1,
    keyframes: Sequence[NormShape] = (),
    attributes: Mapping[str, str] | None = None,
) -> NormTrack:
    """Tạo một :class:`NormTrack` cho test."""
    return NormTrack(
        track_id=track_id,
        label_id=label_id,
        label=label,
        shapes=tuple(keyframes),
        attributes=dict(attributes or {}),
    )


def make_task_data(
    shapes: Sequence[NormShape] = (),
    *,
    task_id: int = TASK_ID,
    track_list: Sequence[NormTrack] = (),
    tags: Sequence[NormTag] = (),
    labels: Sequence[LabelSchema] = (),
    jobs: Sequence[JobInfo] = (),
    size: int = 10,
    frame_size: tuple[int, int] | None = FRAME_SIZE,
    shapes_total: int | None = None,
    shapes_skipped: int = 0,
    warnings: Sequence[str] = (),
    task_name: str | None = "task-test",
) -> TaskData:
    """Tạo :class:`TaskData` từ danh sách shape (tiện cho test rule)."""
    width, height = frame_size or (None, None)
    return TaskData(
        task_id=task_id,
        task_name=task_name,
        size=size,
        dimension="2d",
        frame_width=width,
        frame_height=height,
        labels=tuple(labels),
        shapes=tuple(shapes),
        tracks=tuple(track_list),
        tags=tuple(tags),
        jobs=tuple(jobs),
        shapes_total=len(shapes) if shapes_total is None else shapes_total,
        shapes_skipped=shapes_skipped,
        warnings=tuple(warnings),
    )


def make_config(rules: Mapping[str, Mapping[str, Any]], **kwargs: Any) -> RuleConfig:
    """Tạo :class:`RuleConfig` từ dict ``rule_id -> spec``."""
    payload: dict[str, Any] = {"version": 1, "name": "test", "rules": dict(rules)}
    payload.update(kwargs)
    return RuleConfig.load(payload)


def run_rules(
    data: TaskData,
    rules: Mapping[str, Mapping[str, Any]],
    **kwargs: Any,
) -> QAReport:
    """Chạy engine với cấu hình rule cho trước trên ``data``."""
    return QAQCEngine(make_config(rules, **kwargs)).run(data)


def findings_for(report: QAReport, rule_id: str) -> list[Any]:
    """Các issue thuộc một rule trong báo cáo."""
    return [issue for issue in report.issues if issue.rule_id == rule_id]


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------
@pytest.fixture
def vehicle_labels() -> tuple[LabelSchema, ...]:
    """Schema label mẫu: ``vehicle`` (có attribute) và ``license_plate``."""
    return (
        LabelSchema(
            id=1,
            name="vehicle",
            attributes=(
                LabelAttribute(name="vehicle_type", values=("car", "truck"), default_value="car"),
                LabelAttribute(name="color", values=("white", "black"), default_value="white"),
            ),
        ),
        LabelSchema(
            id=2,
            name="license_plate",
            attributes=(LabelAttribute(name="plate_number", input_type="text"),),
        ),
    )


@pytest.fixture
def sample_shapes() -> list[NormShape]:
    """Bộ shape mẫu có đủ các tình huống lỗi cơ bản."""
    return [
        make_shape("s1", frame=0, shape_id=1, points=(10, 5, 50, 45)),
        make_shape("s2", frame=0, shape_id=2, points=(11, 6, 51, 46)),  # trùng với s1
        make_shape("s3", frame=1, shape_id=3, points=(60, 10, 60, 40)),  # width = 0
    ]


@pytest.fixture
def anomaly_payload() -> dict[str, Any]:
    """Payload thô mô phỏng ``GET /api/tasks/<id>/annotations`` kèm metadata."""
    return json.loads((FIXTURES_DIR / "task_anomalies.json").read_text(encoding="utf-8"))


@pytest.fixture
def anomaly_task_data(anomaly_payload: dict[str, Any]) -> TaskData:
    """Chuẩn hoá payload mẫu thành :class:`TaskData` (offline, không cần CVAT)."""
    task = anomaly_payload["task"]
    return build_task_data(
        task_id=task["id"],
        task_name=task["name"],
        size=task["size"],
        dimension=task["dimension"],
        frames_info=anomaly_payload["frames"],
        labels=anomaly_payload["labels"],
        annotations=anomaly_payload["annotations"],
        jobs=anomaly_payload["jobs"],
    )


@pytest.fixture
def driving_config() -> RuleConfig:
    """Cấu hình rule ``rules/driving_v1.yaml`` của repo."""
    return RuleConfig.load(Path("rules") / "driving_v1.yaml")
