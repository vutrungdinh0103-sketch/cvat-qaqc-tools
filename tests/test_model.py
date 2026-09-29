"""Test model chuẩn hoá :mod:`qaqc.model`."""

from __future__ import annotations

import pytest

from conftest import TASK_ID, make_shape, make_task_data, make_track
from qaqc.model import (
    SEVERITY_RANK,
    JobInfo,
    LabelAttribute,
    LabelSchema,
    NormShape,
    NormTag,
    Severity,
    TaskData,
)


@pytest.mark.parametrize(
    ("value", "rank"),
    [("info", 1), ("warning", 2), ("error", 3), (Severity.ERROR, 3)],
)
def test_severity_parse_and_rank(value, rank) -> None:
    """``Severity.parse`` nhận chuỗi/enum và xếp hạng đúng."""
    assert Severity.parse(value, Severity.WARNING).rank == rank


def test_severity_parse_invalid_message() -> None:
    """Giá trị sai phải nêu rõ danh sách hợp lệ."""
    with pytest.raises(ValueError, match="Mức độ không hợp lệ"):
        Severity.parse("critical", Severity.ERROR)


def test_severity_rank_is_ordered() -> None:
    """Xếp hạng info < warning < error."""
    assert SEVERITY_RANK[Severity.INFO] < SEVERITY_RANK[Severity.WARNING]
    assert SEVERITY_RANK[Severity.WARNING] < SEVERITY_RANK[Severity.ERROR]


def test_shape_geometry_properties() -> None:
    """Các thuộc tính bbox/width/height/area/center của shape."""
    shape = make_shape("s1", points=(10, 20, 30, 60))
    assert shape.bbox == (10.0, 20.0, 30.0, 60.0)
    assert shape.width == 20
    assert shape.height == 40
    assert shape.area == 800
    assert shape.min_side == 20
    assert shape.center == (20.0, 40.0)
    assert shape.is_degenerate is False


@pytest.mark.parametrize("points", [(60, 10, 60, 40), (10, 40, 50, 40)])
def test_shape_is_degenerate(points) -> None:
    """Shape có width/height <= 0 là suy biến."""
    assert make_shape("s1", points=points).is_degenerate is True


def test_shape_without_bbox_is_not_degenerate() -> None:
    """Mask/points không có bbox thì không coi là suy biến."""
    mask = make_shape("s1", shape_type="mask", points=(1, 2, 3, 4))
    assert mask.bbox is None
    assert mask.is_degenerate is False
    assert mask.has_area is False


def test_shape_attribute_returns_none_for_empty() -> None:
    """Attribute rỗng/không tồn tại trả ``None``."""
    shape = make_shape("s1", attributes={"vehicle_type": "", "color": " white "})
    assert shape.attribute("vehicle_type") is None
    assert shape.attribute("color") == "white"
    assert shape.attribute("missing") is None


def test_task_data_objects_filters() -> None:
    """``objects()`` lọc theo loại shape, nguồn và bỏ object ẩn."""
    data = make_task_data(
        [
            make_shape("s1", frame=0),
            make_shape("s2", frame=0, shape_type="polygon"),
            make_shape("s3", frame=1, outside=True),
            make_shape("t100@f0", frame=2, track_id=100),
        ]
    )
    assert {shape.object_key for shape in data.objects()} == {"s1", "s2", "t100@f0"}
    assert {shape.object_key for shape in data.objects(shape_types=["polygon"])} == {"s2"}
    assert {shape.object_key for shape in data.objects(include_tracked=False)} == {"s1", "s2"}
    assert {shape.object_key for shape in data.objects(include_standalone=False)} == {"t100@f0"}


def test_task_data_frame_index_and_lookups() -> None:
    """Index theo frame, tra theo khoá, tag theo frame và tập frame có object."""
    tag = NormTag(object_key="g1", frame=0, label_id=3, label="pedestrian")
    data = make_task_data(
        [make_shape("s1", frame=0), make_shape("s2", frame=5), make_shape("s3", frame=5)],
        tags=[tag],
    )
    assert len(data.objects_in_frame(5)) == 2
    assert data.objects_in_frame(0, shape_types=["polygon"]) == []
    assert data.object_by_key("s2") is not None
    assert data.object_by_key("khong-ton-tai") is None
    assert data.tags_in_frame(0) == [tag]
    assert data.frames_with_objects() == {0, 5}


def test_task_data_frame_size_and_overrides() -> None:
    """Kích thước frame lấy theo overrides rồi tới giá trị chung."""
    data = make_task_data([make_shape("s1", frame=3)], frame_size=(100, 50))
    data.frame_size_overrides[3] = (200, 100)
    assert data.frame_size(3) == (200, 100)
    assert data.frame_size(4) == (100, 50)
    assert make_task_data([], frame_size=None).frame_size(0) is None


def test_task_data_all_frames_falls_back_to_data() -> None:
    """Không biết ``size`` thì suy phạm vi frame từ annotations."""
    data = make_task_data([make_shape("s1", frame=7)], size=0)
    assert list(data.all_frames()) == list(range(8))


def test_task_data_job_for_frame_and_labels() -> None:
    """Map frame -> job và tra schema label."""
    data = make_task_data(
        [],
        jobs=[
            JobInfo(id=7, start_frame=0, stop_frame=4),
            JobInfo(id=8, start_frame=5, stop_frame=9),
        ],
        labels=[LabelSchema(id=1, name="vehicle", attributes=(LabelAttribute(name="color"),))],
    )
    assert data.job_for_frame(3) == 7
    assert data.job_for_frame(9) == 8
    assert data.job_for_frame(99) is None
    assert data.label_names() == {1: "vehicle"}
    assert data.label_schema(1).attribute_names() == ("color",)
    assert data.label_schema_by_name("vehicle") is not None
    assert data.label_schema(2) is None


def test_task_data_describe_frames() -> None:
    """Mô tả phạm vi frame cho log."""
    assert "10 frame" in make_task_data([], size=10).describe_frames()
    assert "không có frame" in make_task_data([], size=None).describe_frames()


def test_track_properties() -> None:
    """Track: start/stop frame và keyframe đang hiển thị."""
    track = make_track(
        keyframes=[
            make_shape("t100@f0", frame=0, track_id=100),
            make_shape("t100@f5", frame=5, track_id=100, outside=True),
        ]
    )
    assert track.start_frame == 0
    assert track.stop_frame == 5
    assert [shape.frame for shape in track.visible_shapes] == [0]
    assert make_track(keyframes=[]).start_frame is None


def test_job_info_contains() -> None:
    """``JobInfo.contains`` bao gồm cả 2 đầu mút."""
    job = JobInfo(id=1, start_frame=10, stop_frame=20)
    assert job.contains(10) and job.contains(20)
    assert not job.contains(21)


def test_task_data_source_job_fallback() -> None:
    """Nếu chỉ biết job nguồn thì dùng nó khi không có danh sách job."""
    data = TaskData(task_id=TASK_ID, source_job_id=9)
    assert data.job_for_frame(0) == 9


def test_shape_describe_mentions_ids_and_bbox() -> None:
    """Chuỗi mô tả phải chứa id/loại/label/bbox (dùng cho message issue)."""
    shape = NormShape(
        object_key="t100@f3",
        frame=3,
        shape_type="rectangle",
        label_id=1,
        label="vehicle",
        points=(1, 2, 3, 4),
        track_id=100,
    )
    text = shape.describe()
    assert "track #100" in text
    assert "label='vehicle'" in text
    assert "bbox=(1, 2, 3, 4)" in text


def test_shape_geometry_returns_shapely_object() -> None:
    """``geometry()`` trả hình học shapely (dùng cho IoU/containment)."""
    shape = make_shape("s1", shape_type="polygon", points=(0, 0, 10, 0, 10, 10, 0, 10))
    assert shape.geometry().area == pytest.approx(100.0)
