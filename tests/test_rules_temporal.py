"""Test các rule theo thời gian (track_gap, track_class_change)."""

from __future__ import annotations

from conftest import findings_for, make_shape, make_task_data, make_track, run_rules


def _track_with_gap() -> list:
    """Track có keyframe tại frame 0 và frame 5 (khoảng trống 4 frame)."""
    return [
        make_track(
            track_id=100,
            keyframes=[
                make_shape("t100@f0", frame=0, track_id=100, points=(10, 10, 30, 30)),
                make_shape("t100@f5", frame=5, track_id=100, points=(20, 10, 40, 30)),
            ],
        )
    ]


def test_track_gap_flags_large_gap() -> None:
    """Khoảng trống vượt ngưỡng bị báo (nội suy giữa 2 keyframe có thể sai)."""
    data = make_task_data([], track_list=_track_with_gap())
    issues = findings_for(run_rules(data, {"track_gap": {}}), "track_gap")
    assert len(issues) == 1
    assert issues[0].details["gap_frames"] == 4
    assert issues[0].details["track_id"] == 100
    assert issues[0].frame == 5


def test_track_gap_ok_within_threshold() -> None:
    """Khoảng trống trong ngưỡng thì không báo."""
    track = make_track(
        keyframes=[
            make_shape("t100@f0", frame=0, track_id=100),
            make_shape("t100@f2", frame=2, track_id=100),
        ]
    )
    data = make_task_data([], track_list=[track])
    assert run_rules(data, {"track_gap": {"params": {"max_gap_frames": 2}}}).issues == []


def test_track_gap_ignores_outside_keyframes() -> None:
    """Keyframe ``outside=True`` không tính là mốc của khoảng trống."""
    track = make_track(
        keyframes=[
            make_shape("t100@f0", frame=0, track_id=100),
            make_shape("t100@f3", frame=3, track_id=100, outside=True),
            make_shape("t100@f4", frame=4, track_id=100),
        ]
    )
    data = make_task_data([], track_list=[track])
    issues = findings_for(run_rules(data, {"track_gap": {}}), "track_gap")
    assert [issue.details["gap_frames"] for issue in issues] == [3]


def _class_change_tracks() -> list:
    """Track pedestrian kết thúc ở frame 4, track vehicle bắt đầu ở frame 5 cùng vị trí."""
    return [
        make_track(
            track_id=200,
            label="pedestrian",
            label_id=3,
            keyframes=[
                make_shape("t200@f4", frame=4, label="pedestrian", label_id=3, track_id=200)
            ],
        ),
        make_track(
            track_id=201,
            label="vehicle",
            label_id=1,
            keyframes=[make_shape("t201@f5", frame=5, label="vehicle", label_id=1, track_id=201)],
        ),
    ]


def test_track_class_change_flags_id_switch() -> None:
    """Hai track khác nhãn chồng nhau ngay sau khi track trước kết thúc."""
    data = make_task_data([], track_list=_class_change_tracks())
    issues = findings_for(run_rules(data, {"track_class_change": {}}), "track_class_change")
    assert len(issues) == 1
    assert issues[0].details["label_before"] == "pedestrian"
    assert issues[0].details["label_after"] == "vehicle"
    assert issues[0].details["iou"] == 1.0


def test_track_class_change_ignores_same_label() -> None:
    """Cùng nhãn thì không phải đổi class."""
    tracks = _class_change_tracks()
    same_label_track = tracks[1].model_copy(update={"label": "pedestrian", "label_id": 3})
    data = make_task_data([], track_list=[tracks[0], same_label_track])
    assert run_rules(data, {"track_class_change": {}}).issues == []


def test_track_class_change_ignores_far_apart_tracks() -> None:
    """Hai track cách xa nhau về thời gian thì bỏ qua."""
    tracks = _class_change_tracks()
    shifted = make_track(
        track_id=201,
        label="vehicle",
        label_id=1,
        keyframes=[make_shape("t201@f9", frame=9, label="vehicle", label_id=1, track_id=201)],
    )
    data = make_task_data([], track_list=[tracks[0], shifted], size=10)
    assert run_rules(data, {"track_class_change": {}}).issues == []
