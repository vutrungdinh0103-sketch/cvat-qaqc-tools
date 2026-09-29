"""Test chuẩn hoá dữ liệu CVAT -> :class:`TaskData`."""

from __future__ import annotations

from typing import Any

from qaqc.normalize import (
    SKIP_INVALID_POINTS,
    SKIP_OUTSIDE,
    SKIP_UNSUPPORTED_TYPE,
    attribute_name_index,
    build_task_data,
    extract_frame_sizes,
    normalize_attributes,
    normalize_label,
    normalize_shapes,
    normalize_tag,
    normalize_track,
    shape_object_key,
)


def test_shape_object_key_conventions() -> None:
    """Khoá object: shape độc lập dùng ``s<id>``, track dùng ``t<id>@f<frame>``."""
    assert shape_object_key(5, 10) == "s5"
    assert shape_object_key(None, 10, index=2) == "s@f10#2"
    assert shape_object_key(None, 10, track_id=100) == "t100@f10"


def test_normalize_attributes_both_shapes() -> None:
    """Hỗ trợ dạng list ``spec_id/value`` của API và dạng dict."""
    raw = [{"spec_id": "vehicle_type", "value": "car"}, {"spec_id": "color", "value": None}]
    assert normalize_attributes(raw) == {"vehicle_type": "car", "color": ""}
    assert normalize_attributes({"a": 1}) == {"a": "1"}
    assert normalize_attributes(None) == {}
    assert normalize_attributes([{"value": "x"}]) == {}


def test_normalize_attributes_resolves_numeric_spec_id() -> None:
    """API CVAT trả ``spec_id`` là id số -> đổi sang tên attribute qua schema label."""
    raw = [{"spec_id": 45, "value": "car"}, {"spec_id": 46, "value": "white"}]
    attribute_names = {45: "vehicle_type", 46: "color"}
    assert normalize_attributes(raw, attribute_names=attribute_names) == {
        "vehicle_type": "car",
        "color": "white",
    }
    # Không có bảng tra -> giữ id dạng chuỗi thay vì âm thầm bỏ attribute.
    assert normalize_attributes(raw) == {"45": "car", "46": "white"}


def test_attribute_name_index_from_label_schema() -> None:
    """``attribute_name_index`` đọc id + tên attribute của mọi label."""
    labels = [
        {
            "id": 1,
            "name": "vehicle",
            "attributes": [{"id": 45, "name": "vehicle_type"}, {"id": 46, "name": "color"}],
        },
        {"id": 2, "name": "license_plate", "attributes": [{"id": 47, "name": "plate_number"}]},
    ]
    assert attribute_name_index(labels) == {
        45: "vehicle_type",
        46: "color",
        47: "plate_number",
    }
    assert attribute_name_index(None) == {}


def test_build_task_data_maps_numeric_attribute_ids() -> None:
    """``build_task_data`` tự dựng bảng tra -> attribute theo id vẫn ra đúng tên."""
    data = build_task_data(
        task_id=1,
        size=2,
        labels=[{"id": 1, "name": "vehicle", "attributes": [{"id": 45, "name": "color"}]}],
        annotations={
            "shapes": [
                {
                    "id": 1,
                    "type": "rectangle",
                    "frame": 0,
                    "label_id": 1,
                    "points": [0, 0, 10, 10],
                    "attributes": [{"spec_id": 45, "value": "white"}],
                }
            ]
        },
        jobs=[],
    )
    assert data.shapes[0].attributes == {"color": "white"}


def test_normalize_label_and_attribute_schema() -> None:
    """Label + attribute được chuẩn hoá đúng (kèm default_value/values)."""
    label = normalize_label(
        {
            "id": 1,
            "name": "vehicle",
            "type": "any",
            "attributes": [
                {"name": "vehicle_type", "values": ["car", "truck"], "default_value": "car"},
                {"name": "", "values": []},
            ],
        }
    )
    assert label is not None
    assert label.attribute_names() == ("vehicle_type",)
    assert label.attribute("vehicle_type").values == ("car", "truck")
    assert label.attribute("vehicle_type").default_value == "car"
    assert normalize_label({"name": "no-id"}) is None


def test_normalize_shapes_counts_skip_reasons() -> None:
    """Shape mask/skeleton bị bỏ qua, ``outside`` được giữ và đếm riêng."""
    shapes, reasons, total = normalize_shapes(
        [
            {"id": 1, "type": "rectangle", "frame": 0, "label_id": 1, "points": [0, 0, 10, 10]},
            {"id": 2, "type": "mask", "frame": 0, "label_id": 1, "points": [1, 2]},
            {"id": 3, "type": "skeleton", "frame": 0, "label_id": 1, "points": []},
            {"id": 4, "type": "polygon", "frame": 1, "label_id": 1, "points": [0, 0, 1]},
            {
                "id": 5,
                "type": "rectangle",
                "frame": 1,
                "label_id": 1,
                "points": [0, 0, 5, 5],
                "outside": True,
            },
        ],
        label_names={1: "vehicle"},
    )
    assert total == 5
    assert [shape.object_key for shape in shapes] == ["s1", "s5"]
    assert reasons[SKIP_UNSUPPORTED_TYPE] == 2
    assert reasons[SKIP_INVALID_POINTS] == 1
    assert reasons[SKIP_OUTSIDE] == 1


def test_normalize_shapes_respects_frame_range() -> None:
    """Chỉ lấy annotation trong khoảng frame yêu cầu (dùng cho job)."""
    shapes, _reasons, total = normalize_shapes(
        [
            {"id": 1, "type": "rectangle", "frame": 0, "label_id": 1, "points": [0, 0, 5, 5]},
            {"id": 2, "type": "rectangle", "frame": 8, "label_id": 1, "points": [0, 0, 5, 5]},
        ],
        label_names={1: "vehicle"},
        frame_range=(5, 9),
    )
    assert total == 1
    assert [shape.object_key for shape in shapes] == ["s2"]


def test_normalize_track_flattens_keyframes_and_inherits_attributes() -> None:
    """Track: keyframe lấy attribute cấp track nếu chưa có."""
    track, keyframes, reasons = normalize_track(
        {
            "id": 100,
            "label_id": 1,
            "attributes": [{"spec_id": "vehicle_type", "value": "car"}],
            "shapes": [
                {"type": "rectangle", "frame": 5, "points": [0, 0, 5, 5], "attributes": []},
                {
                    "type": "rectangle",
                    "frame": 1,
                    "points": [0, 0, 5, 5],
                    "attributes": [{"spec_id": "vehicle_type", "value": "truck"}],
                },
            ],
        },
        label_names={1: "vehicle"},
    )
    assert track is not None
    assert reasons == {}
    assert [shape.frame for shape in track.shapes] == [1, 5]
    assert track.shapes[0].attribute("vehicle_type") == "truck"
    assert track.shapes[1].attribute("vehicle_type") == "car"
    assert {shape.object_key for shape in keyframes} == {"t100@f1", "t100@f5"}


def test_normalize_track_requires_id() -> None:
    """Track không có id bị bỏ qua."""
    track, keyframes, _reasons = normalize_track({"label_id": 1, "shapes": []}, label_names={})
    assert track is None
    assert keyframes == []


def test_normalize_tag() -> None:
    """Tag được chuẩn hoá (có hoặc không có id)."""
    tag = normalize_tag(
        {"id": 9, "frame": 4, "label_id": 3, "attributes": []}, label_names={3: "car"}
    )
    assert tag is not None
    assert tag.object_key == "g9"
    assert tag.label == "car"

    anonymous = normalize_tag({"frame": 2, "label_id": 3}, label_names={}, index=1)
    assert anonymous is not None
    assert anonymous.object_key == "tag@f2#1"
    assert anonymous.label == "label_id=3"


def test_extract_frame_sizes_common_and_overrides() -> None:
    """Kích thước phổ biến nhất làm mặc định, frame khác đưa vào overrides."""
    frames = [
        {"width": 100, "height": 50},
        {"width": 100, "height": 50},
        {"width": 200, "height": 100},
    ]
    width, height, overrides = extract_frame_sizes(frames)
    assert (width, height) == (100, 50)
    assert overrides == {2: (200, 100)}
    assert extract_frame_sizes([]) == (None, None, {})
    assert extract_frame_sizes([{"width": None, "height": None}]) == (None, None, {})


# ---------------------------------------------------------------------------
# build_task_data
# ---------------------------------------------------------------------------
def _raw_payload() -> dict[str, Any]:
    """Payload thô tối giản (1 shape + 1 track + 1 tag)."""
    return {
        "labels": [{"id": 1, "name": "vehicle", "attributes": [{"name": "color"}]}],
        "annotations": {
            "tags": [
                {
                    "frame": 1,
                    "label_id": 1,
                    "attributes": [{"spec_id": "color", "value": "white"}],
                }
            ],
            "shapes": [
                {
                    "id": 1,
                    "type": "rectangle",
                    "frame": 0,
                    "label_id": 1,
                    "points": [0, 0, 10, 10],
                    "attributes": [],
                }
            ],
            "tracks": [
                {
                    "id": 5,
                    "label_id": 1,
                    "attributes": [{"spec_id": "color", "value": "black"}],
                    "shapes": [
                        {
                            "type": "rectangle",
                            "frame": 2,
                            "points": [1, 1, 11, 11],
                            "attributes": [],
                        }
                    ],
                }
            ],
        },
    }


def test_build_task_data_normalizes_everything() -> None:
    """Hàm tổng hợp gom shape/track/tag/job/label/frame size vào ``TaskData``."""
    payload = _raw_payload()
    data = build_task_data(
        task_id=7,
        task_name="offline",
        size=10,
        dimension="2d",
        frames_info=[{"width": 100, "height": 50}] * 10,
        labels=payload["labels"],
        annotations=payload["annotations"],
        jobs=[{"id": 3, "start_frame": 0, "stop_frame": 9}],
    )
    assert (data.task_id, data.task_name, data.size) == (7, "offline", 10)
    assert (data.frame_width, data.frame_height) == (100, 50)
    assert data.shapes_total == 1
    assert {shape.object_key for shape in data.shapes} == {"s1", "t5@f2"}
    assert data.object_by_key("t5@f2").attribute("color") == "black"
    assert data.object_by_key("g5") is None  # tag không nằm trong shapes
    assert data.label_names() == {1: "vehicle"}
    assert len(data.tags) == 1
    assert data.job_for_frame(0) == 3
    assert data.warnings == ()


def test_build_task_data_frame_range_filters_all_annotations() -> None:
    """``frame_range`` lọc cả shape, track keyframe và tag (dùng cho job)."""
    payload = _raw_payload()
    data = build_task_data(
        task_id=7,
        size=10,
        frames_info=[{"width": 100, "height": 50}] * 10,
        labels=payload["labels"],
        annotations=payload["annotations"],
        frame_range=(1, 2),
        source_job_id=9,
    )
    assert data.shapes_total == 0
    assert {shape.object_key for shape in data.shapes} == {"t5@f2"}
    assert len(data.tags) == 1
    assert data.source_job_id == 9


def test_build_task_data_frame_size_parameter_wins() -> None:
    """``frame_size`` truyền tay ghi đè thông tin từ ``frames_info``."""
    payload = _raw_payload()
    data = build_task_data(
        task_id=7,
        labels=payload["labels"],
        annotations=payload["annotations"],
        frames_info=[{"width": 100, "height": 50}],
        frame_size=(640, 480),
    )
    assert data.frame_size(0) == (640, 480)
    assert data.frame_size_overrides == {}


def test_build_task_data_warns_when_labels_missing() -> None:
    """Thiếu schema label -> ghi cảnh báo để người dùng biết rule theo tên nhãn có thể lệch."""
    payload = _raw_payload()
    data = build_task_data(task_id=7, annotations=payload["annotations"])
    assert any("schema label" in warning for warning in data.warnings)
    assert data.objects()[0].label == "label_id=1"
