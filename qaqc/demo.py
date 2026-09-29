"""Dữ liệu mẫu (offline) để thử QA/QC trên localhost mà không cần CVAT server.

Được dùng bởi ``python -m qaqc serve --demo`` và test của service. Task mẫu có
8 frame (frame 100x50), mỗi lỗi được tạo **có chủ đích** để kích hoạt đúng một
rule trong ``rules/driving_v1.yaml``:

===========  ========  ======================================================
Frame        Object    Lỗi cố ý tạo
===========  ========  ======================================================
0            s1        (không lỗi - box tham chiếu)
0            s2        trùng khít s1 → ``duplicate_bbox``
1            s3        width = 0 → ``invalid_size``
2            s4        nằm hoàn toàn ngoài khung → ``out_of_frame``
2            s5        biển số không nằm trong xe → ``must_be_inside``
4            s6        box 1x1 px → ``tiny_box``
4            s7        nhãn ``forklift`` lạ → ``unexpected_label``
5            s8        thiếu attribute ``color`` → ``required_attributes``
6            tag1      thiếu attribute ``color`` → ``required_attributes``
0..5         t100      keyframe cách nhau 5 frame → ``track_gap``
6..7         t101/t102 track đổi nhãn ngay frame kế tiếp → ``track_class_change``
===========  ========  ======================================================

Nhờ bộ dữ liệu này, ``qaqc serve --demo`` cho ra kết quả **có lỗi để xem** mà
không cần cài CVAT - tiện cho demo và kiểm thử end-to-end của service.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .model import (
    JobInfo,
    LabelAttribute,
    LabelSchema,
    NormShape,
    NormTag,
    NormTrack,
    TaskData,
)

#: Task id của dữ liệu mẫu (dùng trong URL ``/tasks/1/report``).
DEMO_TASK_ID = 1

#: Tên task mẫu.
DEMO_TASK_NAME = "demo-drive"

#: Kích thước frame mẫu (theo CVAT: điểm ảnh).
FRAME_WIDTH = 100
FRAME_HEIGHT = 50

#: Số frame của task mẫu.
FRAME_COUNT = 8

#: Attribute hợp lệ của label ``vehicle``.
VEHICLE_ATTRS: dict[str, str] = {"vehicle_type": "car", "color": "white"}

#: Schema label của task mẫu (label ``forklift`` cố ý nằm ngoài danh sách cho phép).
DEMO_LABELS: tuple[LabelSchema, ...] = (
    LabelSchema(
        id=1,
        name="vehicle",
        attributes=(
            LabelAttribute(
                id=1,
                name="vehicle_type",
                values=("car", "truck", "bus"),
                default_value="car",
            ),
            LabelAttribute(
                id=2,
                name="color",
                values=("white", "black", "red"),
                default_value="white",
            ),
        ),
    ),
    LabelSchema(
        id=2,
        name="license_plate",
        attributes=(LabelAttribute(id=3, name="plate_number", input_type="text"),),
    ),
    LabelSchema(id=3, name="pedestrian"),
    LabelSchema(id=4, name="forklift"),
)

#: Job mẫu (dùng để map ``frame -> job_id`` khi tạo issue trên CVAT).
DEMO_JOBS: tuple[JobInfo, ...] = (
    JobInfo(id=7, start_frame=0, stop_frame=3, stage="annotation", state="new"),
    JobInfo(id=8, start_frame=4, stop_frame=7, stage="acceptance", state="completed"),
)


def _shape(
    object_key: str,
    frame: int,
    points: Sequence[float],
    *,
    label: str = "vehicle",
    label_id: int = 1,
    attributes: Mapping[str, str] | None = None,
    shape_id: int | None = None,
    track_id: int | None = None,
    shape_type: str = "rectangle",
) -> NormShape:
    """Tạo một shape của dữ liệu mẫu."""
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
    )


def _track_keyframe(
    frame: int,
    points: Sequence[float],
    *,
    track_id: int,
    label: str = "vehicle",
    label_id: int = 1,
    attributes: Mapping[str, str] | None = None,
) -> NormShape:
    """Tạo một keyframe thuộc track (``object_key = t<id>@f<frame>``)."""
    return _shape(
        f"t{track_id}@f{frame}",
        frame,
        points,
        label=label,
        label_id=label_id,
        attributes=attributes,
        track_id=track_id,
    )


def build_demo_task(task_id: int = DEMO_TASK_ID) -> TaskData:
    """Dựng :class:`TaskData` mẫu cho demo/kiểm thử (không cần CVAT server).

    Keyframe của track được đưa vào **cả** ``shapes`` lẫn ``tracks`` - đúng như
    dữ liệu do :func:`qaqc.normalize.build_task_data` tạo ra từ CVAT.
    """
    standalone = [
        # frame 0: box tham chiếu (không lỗi)
        _shape("s1", 0, (10, 5, 50, 45), attributes=VEHICLE_ATTRS, shape_id=1),
        # frame 0: trùng khít s1 -> duplicate_bbox
        _shape("s2", 0, (11, 6, 51, 46), attributes=VEHICLE_ATTRS, shape_id=2),
        # frame 1: width = 0 -> invalid_size
        _shape("s3", 1, (60, 10, 60, 40), attributes=VEHICLE_ATTRS, shape_id=3),
        # frame 2: nằm hoàn toàn ngoài khung -> out_of_frame
        _shape("s4", 2, (-40, 5, -10, 30), attributes=VEHICLE_ATTRS, shape_id=4),
        # frame 2: biển số không nằm trong xe nào -> must_be_inside
        _shape(
            "s5",
            2,
            (5, 5, 15, 15),
            label="license_plate",
            label_id=2,
            attributes={"plate_number": "51A-12345"},
            shape_id=5,
        ),
        # frame 4: box 1x1 px -> tiny_box
        _shape("s6", 4, (10, 10, 11, 11), attributes=VEHICLE_ATTRS, shape_id=6),
        # frame 4: nhãn lạ -> unexpected_label
        _shape("s7", 4, (30, 10, 50, 30), label="forklift", label_id=4, shape_id=7),
        # frame 5: thiếu attribute "color" -> required_attributes
        _shape("s8", 5, (20, 10, 40, 30), attributes={"vehicle_type": "car"}, shape_id=8),
    ]

    track_100_keyframes = [
        # 2 keyframe cách nhau 5 frame -> track_gap
        _track_keyframe(0, (5, 30, 25, 48), track_id=100, attributes=VEHICLE_ATTRS),
        _track_keyframe(5, (15, 30, 35, 48), track_id=100, attributes=VEHICLE_ATTRS),
    ]
    track_101_keyframes = [
        # track vehicle chỉ có 1 keyframe ở frame 6
        _track_keyframe(6, (60, 5, 90, 40), track_id=101, attributes=VEHICLE_ATTRS),
    ]
    track_102_keyframes = [
        # frame 7: cùng vị trí nhưng khác nhãn, nối tiếp t101 -> track_class_change
        _track_keyframe(
            7,
            (60, 5, 90, 40),
            track_id=102,
            label="pedestrian",
            label_id=3,
        ),
    ]

    tracks = (
        NormTrack(
            track_id=100,
            label_id=1,
            label="vehicle",
            attributes=dict(VEHICLE_ATTRS),
            shapes=tuple(track_100_keyframes),
        ),
        NormTrack(
            track_id=101,
            label_id=1,
            label="vehicle",
            attributes=dict(VEHICLE_ATTRS),
            shapes=tuple(track_101_keyframes),
        ),
        NormTrack(
            track_id=102,
            label_id=3,
            label="pedestrian",
            shapes=tuple(track_102_keyframes),
        ),
    )

    tags = (
        # frame 6: tag thiếu attribute "color" -> required_attributes (check_tags)
        NormTag(
            object_key="tag1",
            frame=6,
            label_id=1,
            label="vehicle",
            attributes={"vehicle_type": "truck"},
            shape_id=101,
        ),
    )

    shapes = (
        *standalone,
        *track_100_keyframes,
        *track_101_keyframes,
        *track_102_keyframes,
    )

    return TaskData(
        task_id=task_id,
        task_name=DEMO_TASK_NAME,
        size=FRAME_COUNT,
        dimension="2d",
        frame_width=FRAME_WIDTH,
        frame_height=FRAME_HEIGHT,
        labels=DEMO_LABELS,
        shapes=shapes,
        tracks=tracks,
        tags=tags,
        jobs=DEMO_JOBS,
        shapes_total=len(shapes),
        warnings=("Dữ liệu mẫu (--demo): không lấy từ CVAT server.",),
    )
