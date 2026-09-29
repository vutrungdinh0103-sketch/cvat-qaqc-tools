"""Test cấp độ QA: Level 1 (Overall/Completeness) và Level 2 (Detailed).

Bao gồm: metadata ``level`` của rule, helper :func:`qaqc.rules.rule_ids_for_level`,
file cấu hình ``rules/level1_v1.yaml``, cờ ``--level`` của CLI và thông tin cấp độ
trong báo cáo (JSON/CSV/summary).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import make_shape, make_task_data, run_rules
from qaqc.cli import _rule_config_from_args, build_parser
from qaqc.config import RuleConfig
from qaqc.rules import registered_rule_ids, rule_catalog, rule_ids_for_level, rule_level

#: Rule thuộc "Level 1 - Overall / Completeness Check" (proposal mục 4).
LEVEL1_RULES = {
    "duplicate_bbox",
    "empty_frame",
    "empty_frame_range",
    "missing_label",
    "object_count",
    "required_attributes",
    "unexpected_label",
}

#: Rule thuộc "Level 2 - Detailed Annotation Validation".
LEVEL2_RULES = {
    "invalid_size",
    "must_be_inside",
    "out_of_frame",
    "tiny_box",
    "track_class_change",
    "track_gap",
}


def test_rule_levels_cover_every_rule() -> None:
    """Hai cấp độ không giao nhau và phủ hết mọi rule đã đăng ký."""
    level_one = set(rule_ids_for_level(1))
    level_two = set(rule_ids_for_level(2))
    assert level_one == LEVEL1_RULES
    assert level_two == LEVEL2_RULES
    assert not level_one & level_two
    assert level_one | level_two == set(registered_rule_ids())


def test_rule_level_helper_and_invalid_level() -> None:
    """``rule_level`` đọc đúng metadata; cấp độ lạ bị từ chối rõ ràng."""
    assert rule_level("object_count") == 1
    assert rule_level("tiny_box") == 2
    with pytest.raises(ValueError, match="Cấp độ QA không hợp lệ"):
        rule_ids_for_level(3)
    with pytest.raises(KeyError):
        rule_level("khong_ton_tai")


def test_rule_catalog_exposes_level() -> None:
    """``python -m qaqc rules --json`` có trường ``level`` cho mọi rule."""
    catalog = rule_catalog()
    assert {item["rule_id"]: item["level"] for item in catalog}["duplicate_bbox"] == 1
    assert all(item["level"] in (1, 2) for item in catalog)


def test_level_one_config_matches_level_one_rules() -> None:
    """``rules/level1_v1.yaml`` bật đúng các rule Level 1, không bật rule Level 2."""
    config = RuleConfig.load(Path("rules") / "level1_v1.yaml")
    enabled = config.enabled_rule_ids()
    assert set(enabled) == LEVEL1_RULES
    assert all(rule_level(rule_id) == 1 for rule_id in enabled)


def test_cli_level_flag_selects_rules() -> None:
    """``--level 1``/``--level 2`` chọn đúng tập rule; kết hợp ``--only`` lấy phần giao."""
    args = build_parser().parse_args(
        ["run", "42", "--rules", "rules/driving_v1.yaml", "--level", "1"]
    )
    config = _rule_config_from_args(args)
    assert set(config.enabled_rule_ids()) == LEVEL1_RULES
    assert config.name.endswith("+cli")

    args = build_parser().parse_args(["run", "42", "--level", "2"])
    assert set(_rule_config_from_args(args).enabled_rule_ids()) == LEVEL2_RULES

    args = build_parser().parse_args(
        ["run", "42", "--level", "1", "--only", "duplicate_bbox,tiny_box"]
    )
    assert _rule_config_from_args(args).enabled_rule_ids() == ["duplicate_bbox"]


def test_cli_level_conflicting_with_only_raises() -> None:
    """``--only`` toàn rule Level 2 nhưng ``--level 1`` -> báo lỗi rõ ràng."""
    args = build_parser().parse_args(["run", "42", "--level", "1", "--only", "tiny_box"])
    with pytest.raises(ValueError, match="không khớp rule nào"):
        _rule_config_from_args(args)


def test_report_marks_issue_level_and_counts_by_level() -> None:
    """Mỗi lỗi có ``level`` của rule; báo cáo có ``counts_by_level``."""
    data = make_task_data(
        [
            make_shape("s1", frame=0, shape_id=1, points=(10, 5, 50, 45)),
            make_shape("s2", frame=0, shape_id=2, points=(11, 6, 51, 46)),  # trùng s1
            make_shape("s3", frame=1, shape_id=3, points=(60, 10, 60, 40)),  # width = 0
        ],
        size=3,
    )
    report = run_rules(data, {"duplicate_bbox": {}, "invalid_size": {}})
    assert {issue.rule_id: issue.level for issue in report.issues} == {
        "duplicate_bbox": 1,
        "invalid_size": 2,
    }
    assert report.counts_by_level == {"1": 1, "2": 1}
    assert report.to_json_dict()["counts_by_level"] == {"1": 1, "2": 1}
    assert "Level 1 (overall)=1" in "\n".join(report.summary_lines())


def test_report_csv_contains_level_column(tmp_path: Path) -> None:
    """CSV có cột ``level`` ngay sau ``severity`` và ghi đúng cấp độ của rule."""
    data = make_task_data(
        [
            make_shape("s1", frame=0, shape_id=1, points=(10, 5, 50, 45)),
            make_shape("s2", frame=0, shape_id=2, points=(11, 6, 51, 46)),
        ],
        size=1,
    )
    report = run_rules(data, {"duplicate_bbox": {}})
    path = report.write(tmp_path / "bao-cao.csv")
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    header = lines[0].split(",")
    assert header.index("level") == header.index("severity") + 1
    assert lines[1].split(",")[header.index("level")] == "1"
