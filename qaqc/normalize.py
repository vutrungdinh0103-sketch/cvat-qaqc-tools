"""Chuẩn hoá dữ liệu annotations của CVAT thành :class:`TaskData`.

Module này **thuần Python** (không import ``cvat_sdk``): nhận cả object của SDK
lẫn ``dict`` (dữ liệu JSON) nên test được offline bằng fixture, đồng thời giữ
được một nguồn sự thật duy nhất cho logic chuẩn hoá.

Quy ước đếm (dùng cho báo cáo):

- ``shapes_total``: số shape **độc lập** (``annotations.shapes``) trong phạm vi
  frame đang xét.
- ``shapes_skipped``: số shape độc lập không dùng được (loại không có bbox như
  ``mask``/``skeleton``, hoặc ``points`` sai định dạng).
- ``skipped_reasons``: chi tiết lý do, gồm cả ``outside`` (shape đang ẩn) để CLI
  cũ tính lại được đúng số liệu của mình.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from .geometry import to_points
from .model import (
    BOX_LIKE_TYPES,
    JobInfo,
    LabelAttribute,
    LabelSchema,
    NormShape,
    NormTag,
    NormTrack,
    TaskData,
)

#: Lý do bỏ qua shape (khoá trong ``skipped_reasons``).
SKIP_UNSUPPORTED_TYPE = "unsupported_type"
SKIP_INVALID_POINTS = "invalid_points"
SKIP_OUTSIDE = "outside"


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Đọc ``key`` từ object của SDK hoặc từ ``dict`` (trả ``default`` nếu thiếu)."""
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _as_plain(value: Any) -> Any:
    """Bỏ lớp ``ModelSimple`` của SDK (``.value``) nếu có."""
    return getattr(value, "value", value)


def _shape_type(raw: Any) -> str:
    """Chuẩn hoá ``type`` của shape (SDK dùng ``ModelSimple``)."""
    return str(_as_plain(_get(raw, "type", "")) or "").strip().lower()


def _as_int(value: Any, default: int | None = None) -> int | None:
    """Ép giá trị về ``int`` (``None`` nếu không được)."""
    if value is None:
        return default
    try:
        return int(_as_plain(value))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float | None = None) -> float | None:
    """Ép giá trị về ``float`` (``None`` nếu không được)."""
    if value is None:
        return default
    try:
        return float(_as_plain(value))
    except (TypeError, ValueError):
        return default


def _as_str(value: Any, default: str | None = None) -> str | None:
    """Ép giá trị về ``str`` (``None`` nếu rỗng)."""
    plain = _as_plain(value)
    if plain is None:
        return default
    text = str(plain).strip()
    return text or default


def _attribute_name(
    item: Any,
    attribute_names: Mapping[int, str] | None,
) -> str | None:
    """Tên attribute từ một ``AttributeVal``.

    API CVAT 2.x trả ``spec_id`` là **id** của attribute (ví dụ ``45``), nên cần
    ``attribute_names`` (lấy từ schema label) để đổi sang tên. Vẫn chấp nhận dữ liệu
    đã có sẵn tên trong ``spec_id`` (fixture cũ) hoặc trong ``name``.
    """
    raw_key = _get(item, "spec_id")
    if raw_key is None:
        raw_key = _get(item, "name")
    if raw_key is None:
        return None

    key_id = _as_int(raw_key)
    if key_id is not None and attribute_names and key_id in attribute_names:
        return attribute_names[key_id]
    return _as_str(raw_key)


def attribute_name_index(raw_labels: Iterable[Any] | None) -> dict[int, str]:
    """``{attribute_id: attribute_name}`` từ schema label của task/project.

    Dùng để giải mã ``spec_id`` (id) trong ``annotations`` - xem
    :func:`normalize_attributes`.
    """
    index: dict[int, str] = {}
    for raw_label in raw_labels or ():
        for raw_attribute in _get(raw_label, "attributes") or ():
            attribute_id = _as_int(_get(raw_attribute, "id"))
            name = _as_str(_get(raw_attribute, "name"))
            if attribute_id is not None and name:
                index[attribute_id] = name
    return index


def normalize_attributes(
    raw: Any,
    *,
    attribute_names: Mapping[int, str] | None = None,
) -> dict[str, str]:
    """Chuẩn hoá ``attributes`` của shape/track/tag thành ``dict[str, str]``.

    Hỗ trợ cả 3 dạng:

    - ``[{"spec_id": 45, "value": "car"}]`` - API CVAT 2.x (``spec_id`` là id, cần
      ``attribute_names`` để đổi sang tên ``vehicle_type``),
    - ``[{"spec_id": "vehicle_type", "value": "car"}]`` (dữ liệu đã có tên),
    - ``{"vehicle_type": "car"}`` (dữ liệu đã xử lý).
    """
    if not raw:
        return {}

    if isinstance(raw, Mapping):
        return {
            str(_as_plain(key)): "" if value is None else str(_as_plain(value))
            for key, value in raw.items()
        }

    result: dict[str, str] = {}
    for item in raw:
        name = _attribute_name(item, attribute_names)
        if not name:
            continue
        result[name] = _as_str(_get(item, "value"), default="") or ""
    return result


def normalize_attribute_definition(raw: Any) -> LabelAttribute | None:
    """Chuẩn hoá định nghĩa attribute trong schema label."""
    name = _as_str(_get(raw, "name"))
    if not name:
        return None

    values = _get(raw, "values", None) or ()
    return LabelAttribute(
        id=_as_int(_get(raw, "id")),
        name=name,
        values=tuple(str(_as_plain(value)) for value in values),
        default_value=_as_str(_get(raw, "default_value"), default="") or "",
        mutable=bool(_get(raw, "mutable", True)),
        input_type=_as_str(_get(raw, "input_type"), default="select") or "select",
    )


def normalize_label(raw: Any) -> LabelSchema | None:
    """Chuẩn hoá một label trong schema của task/project."""
    name = _as_str(_get(raw, "name"))
    label_id = _as_int(_get(raw, "id"))
    if name is None or label_id is None:
        return None

    raw_attributes = _get(raw, "attributes") or ()
    parsed = (normalize_attribute_definition(item) for item in raw_attributes)
    attributes = [attribute for attribute in parsed if attribute is not None]
    return LabelSchema(
        id=label_id,
        name=name,
        type=_as_str(_get(raw, "type"), default="any") or "any",
        attributes=tuple(attributes),
    )


def normalize_labels(raw_labels: Iterable[Any] | None) -> tuple[LabelSchema, ...]:
    """Chuẩn hoá danh sách label (bỏ qua phần tử thiếu ``id``/``name``)."""
    labels = [normalize_label(raw) for raw in (raw_labels or ())]
    return tuple(label for label in labels if label is not None)


def shape_object_key(
    shape_id: int | None,
    frame: int,
    *,
    track_id: int | None = None,
    index: int = 0,
) -> str:
    """Sinh ``object_key`` ổn định cho một object.

    - Track: ``t<track_id>@f<frame>`` (track không có id riêng cho từng keyframe).
    - Shape độc lập có id: ``s<shape_id>``.
    - Shape độc lập không có id: ``s@f<frame>#<index>``.
    """
    if track_id is not None:
        return f"t{track_id}@f{frame}"
    if shape_id is not None:
        return f"s{shape_id}"
    return f"s@f{frame}#{index}"


def normalize_shape(
    raw: Any,
    *,
    label_names: Mapping[int, str],
    attribute_names: Mapping[int, str] | None = None,
    track_id: int | None = None,
    label_id: int | None = None,
    index: int = 0,
) -> tuple[NormShape | None, str | None]:
    """Chuẩn hoá một shape.

    :param label_id: nhãn dự phòng của track - CVAT **không** lưu ``label_id``
        trên từng keyframe mà ở cấp track, nên keyframe phải kế thừa từ track.
    :return: ``(shape, None)`` hoặc ``(None, lý_do_bỏ_qua)``.
    """
    shape_type = _shape_type(raw)
    if shape_type not in BOX_LIKE_TYPES:
        return None, SKIP_UNSUPPORTED_TYPE

    shape_id = _as_int(_get(raw, "id"))
    frame = _as_int(_get(raw, "frame"), 0) or 0

    resolved_label_id = _as_int(_get(raw, "label_id"))
    if resolved_label_id is None:
        resolved_label_id = label_id
    resolved_label_id = -1 if resolved_label_id is None else resolved_label_id

    shape = NormShape(
        object_key=shape_object_key(shape_id, frame, track_id=track_id, index=index),
        frame=frame,
        shape_type=shape_type,
        label_id=resolved_label_id,
        label=label_names.get(resolved_label_id, f"label_id={resolved_label_id}"),
        points=to_points(_get(raw, "points")),
        attributes=normalize_attributes(_get(raw, "attributes"), attribute_names=attribute_names),
        shape_id=shape_id,
        track_id=track_id,
        group=_as_int(_get(raw, "group")),
        source=_as_str(_get(raw, "source")),
        occluded=bool(_get(raw, "occluded", False)),
        outside=bool(_get(raw, "outside", False)),
        z_order=_as_int(_get(raw, "z_order"), 0) or 0,
        rotation=_as_float(_get(raw, "rotation")),
        score=_as_float(_get(raw, "score")),
    )
    if shape.bbox is None:
        return None, SKIP_INVALID_POINTS
    return shape, None


def _in_range(frame: int, frame_range: tuple[int, int] | None) -> bool:
    """``True`` nếu frame nằm trong phạm vi (``None`` = nhận tất cả)."""
    if frame_range is None:
        return True
    return frame_range[0] <= frame <= frame_range[1]


def normalize_shapes(
    raw_shapes: Iterable[Any] | None,
    *,
    label_names: Mapping[int, str],
    attribute_names: Mapping[int, str] | None = None,
    frame_range: tuple[int, int] | None = None,
) -> tuple[list[NormShape], dict[str, int], int]:
    """Chuẩn hoá ``annotations.shapes``.

    :return: ``(danh sách shape, lý do bỏ qua, tổng số shape thô trong phạm vi)``.
    """
    shapes: list[NormShape] = []
    reasons: Counter[str] = Counter()
    total = 0

    for index, raw in enumerate(raw_shapes or ()):
        frame = _as_int(_get(raw, "frame"), 0) or 0
        if not _in_range(frame, frame_range):
            continue
        total += 1

        shape, reason = normalize_shape(
            raw,
            label_names=label_names,
            attribute_names=attribute_names,
            index=index,
        )
        if shape is None:
            reasons[reason or SKIP_INVALID_POINTS] += 1
            continue
        if shape.outside:
            reasons[SKIP_OUTSIDE] += 1
        shapes.append(shape)

    return shapes, dict(reasons), total


def normalize_track(
    raw: Any,
    *,
    label_names: Mapping[int, str],
    attribute_names: Mapping[int, str] | None = None,
    frame_range: tuple[int, int] | None = None,
) -> tuple[NormTrack | None, list[NormShape], dict[str, int]]:
    """Chuẩn hoá một track và toàn bộ keyframe của nó.

    :return: ``(track, danh sách keyframe, lý do bỏ qua keyframe)``.
    """
    track_id = _as_int(_get(raw, "id"))
    if track_id is None:
        return None, [], {}

    label_id = _as_int(_get(raw, "label_id"), -1)
    label_id = -1 if label_id is None else label_id
    reasons: Counter[str] = Counter()
    keyframes: list[NormShape] = []

    for index, raw_keyframe in enumerate(_get(raw, "shapes") or ()):
        frame = _as_int(_get(raw_keyframe, "frame"), 0) or 0
        if not _in_range(frame, frame_range):
            continue

        shape, reason = normalize_shape(
            raw_keyframe,
            label_names=label_names,
            attribute_names=attribute_names,
            track_id=track_id,
            label_id=label_id,
            index=index,
        )
        if shape is None:
            reasons[f"tracked_{reason or SKIP_INVALID_POINTS}"] += 1
            continue
        keyframes.append(shape)

    # CVAT lưu attribute của track ở cấp track; keyframe chỉ chứa attribute thay
    # đổi. Vì vậy attribute cấp track được áp xuống keyframe (keyframe ưu tiên).
    track_attributes = normalize_attributes(
        _get(raw, "attributes"), attribute_names=attribute_names
    )
    if track_attributes:
        keyframes = [
            shape.model_copy(update={"attributes": {**track_attributes, **shape.attributes}})
            if set(track_attributes) - set(shape.attributes)
            else shape
            for shape in keyframes
        ]

    track = NormTrack(
        track_id=track_id,
        label_id=label_id,
        label=label_names.get(label_id, f"label_id={label_id}"),
        shapes=tuple(sorted(keyframes, key=lambda shape: shape.frame)),
        attributes=track_attributes,
        group=_as_int(_get(raw, "group")),
        source=_as_str(_get(raw, "source")),
    )
    return track, keyframes, dict(reasons)


def normalize_tag(
    raw: Any,
    *,
    label_names: Mapping[int, str],
    attribute_names: Mapping[int, str] | None = None,
    index: int = 0,
    frame_range: tuple[int, int] | None = None,
) -> NormTag | None:
    """Chuẩn hoá một tag (``annotations.tags``)."""
    frame = _as_int(_get(raw, "frame"), 0) or 0
    if not _in_range(frame, frame_range):
        return None

    label_id = _as_int(_get(raw, "label_id"), -1)
    label_id = -1 if label_id is None else label_id
    tag_id = _as_int(_get(raw, "id"))
    object_key = f"g{tag_id}" if tag_id is not None else f"tag@f{frame}#{index}"

    return NormTag(
        object_key=object_key,
        frame=frame,
        label_id=label_id,
        label=label_names.get(label_id, f"label_id={label_id}"),
        attributes=normalize_attributes(_get(raw, "attributes"), attribute_names=attribute_names),
        source=_as_str(_get(raw, "source")),
        shape_id=tag_id,
    )


def normalize_jobs(raw_jobs: Iterable[Any] | None) -> tuple[JobInfo, ...]:
    """Chuẩn hoá danh sách job (dùng để map ``frame -> job_id``)."""
    jobs: list[JobInfo] = []
    for raw in raw_jobs or ():
        job_id = _as_int(_get(raw, "id"))
        if job_id is None:
            continue
        jobs.append(
            JobInfo(
                id=job_id,
                start_frame=_as_int(_get(raw, "start_frame"), 0) or 0,
                stop_frame=_as_int(_get(raw, "stop_frame"), 0) or 0,
                stage=_as_str(_get(raw, "stage")),
                state=_as_str(_get(raw, "state")),
                assignee=_as_int(_get(raw, "assignee")),
            )
        )
    return tuple(sorted(jobs, key=lambda job: job.id))


def extract_frame_sizes(
    frames_info: Iterable[Any] | None,
) -> tuple[int | None, int | None, dict[int, tuple[int, int]]]:
    """Rút kích thước frame từ ``get_frames_info()``.

    Trả về ``(width, height, overrides)``: kích thước phổ biến nhất dùng làm mặc
    định, các frame khác kích thước được ghi vào ``overrides`` (task video có thể
    đổi độ phân giải giữa chừng).
    """
    counter: Counter[tuple[int, int]] = Counter()
    per_frame: dict[int, tuple[int, int]] = {}

    for index, raw in enumerate(frames_info or ()):
        width = _as_int(_get(raw, "width"))
        height = _as_int(_get(raw, "height"))
        if not width or not height:
            continue
        size = (width, height)
        counter[size] += 1
        per_frame[index] = size

    if not counter:
        return None, None, {}

    common, _ = counter.most_common(1)[0]
    overrides = {frame: size for frame, size in per_frame.items() if size != common}
    return common[0], common[1], overrides


def build_task_data(
    *,
    task_id: int,
    task_name: str | None = None,
    size: int | None = None,
    dimension: str | None = None,
    frames_info: Iterable[Any] | None = None,
    labels: Iterable[Any] | None = None,
    annotations: Any = None,
    jobs: Iterable[Any] | None = None,
    source_job_id: int | None = None,
    frame_range: tuple[int, int] | None = None,
    frame_size: tuple[int, int] | None = None,
) -> TaskData:
    """Tổng hợp dữ liệu thô của CVAT thành :class:`TaskData`.

    Hàm này là điểm duy nhất biết cách "đọc" annotations, dùng chung cho cả
    luồng online (SDK) và offline (fixture JSON).

    :param annotations: object ``ILabeledData`` của SDK hoặc ``dict`` tương đương.
    :param frames_info: kết quả ``get_frames_info()`` (để biết kích thước frame).
    :param frame_range: chỉ lấy annotation trong khoảng ``(start, stop)`` - dùng
        khi chạy QA/QC cho một job.
    :param frame_size: kích thước frame thủ công (khi không có ``frames_info``).
    """
    label_schemas = normalize_labels(labels)
    label_names = {label.id: label.name for label in label_schemas}
    # API CVAT trả `spec_id` là id của attribute -> cần bảng tra id -> tên.
    attribute_names = attribute_name_index(labels)

    shapes, skip_reasons, shapes_total = normalize_shapes(
        _get(annotations, "shapes"),
        label_names=label_names,
        attribute_names=attribute_names,
        frame_range=frame_range,
    )

    tracks: list[NormTrack] = []
    for raw_track in _get(annotations, "tracks") or ():
        track, keyframes, reasons = normalize_track(
            raw_track,
            label_names=label_names,
            attribute_names=attribute_names,
            frame_range=frame_range,
        )
        if track is None:
            continue
        tracks.append(track)
        shapes.extend(keyframes)
        for reason, count in reasons.items():
            skip_reasons[reason] = skip_reasons.get(reason, 0) + count

    tags = []
    for index, raw_tag in enumerate(_get(annotations, "tags") or ()):
        tag = normalize_tag(
            raw_tag,
            label_names=label_names,
            attribute_names=attribute_names,
            index=index,
            frame_range=frame_range,
        )
        if tag is not None:
            tags.append(tag)

    width, height, overrides = extract_frame_sizes(frames_info)
    if frame_size is not None:
        width, height = frame_size
        overrides = {}

    warnings: list[str] = []
    if not label_schemas:
        warnings.append(
            "Không lấy được schema label của task: tên label sẽ hiển thị dạng "
            "'label_id=<id>' và các rule theo tên nhãn có thể không hoạt động."
        )

    shapes_skipped = skip_reasons.get(SKIP_UNSUPPORTED_TYPE, 0) + skip_reasons.get(
        SKIP_INVALID_POINTS, 0
    )

    return TaskData(
        task_id=task_id,
        task_name=task_name,
        size=size,
        dimension=dimension,
        frame_width=width,
        frame_height=height,
        frame_size_overrides=overrides,
        labels=label_schemas,
        shapes=tuple(shapes),
        tracks=tuple(tracks),
        tags=tuple(tags),
        jobs=normalize_jobs(jobs),
        shapes_total=shapes_total,
        shapes_skipped=shapes_skipped,
        skipped_reasons=skip_reasons,
        warnings=tuple(warnings),
        source_job_id=source_job_id,
    )
