"""Đăng ký và tra cứu các rule QA/QC.

Thêm rule mới = thêm lớp con của :class:`~qaqc.rules.base.Rule` rồi khai báo vào
``_RULE_CLASSES`` ở cuối file này. Registry được dùng để:

- validate file cấu hình YAML (phát hiện ``rule_id`` không tồn tại),
- liệt kê rule cho CLI (``python -m qaqc rules``),
- khởi tạo rule kèm tham số + mức độ đã resolve từ cấu hình.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from ..model import Severity
from . import completeness_rules, geometry_rules, temporal_rules
from .base import Finding, Rule, RuleContext, RuleParams, ShapeSelectionParams

_RULE_REGISTRY: dict[str, type[Rule]] = {}


def register_rule(rule_cls: type[Rule]) -> type[Rule]:
    """Đăng ký một rule vào registry.

    :raises ValueError: nếu thiếu ``rule_id``/``description`` hoặc trùng id.
    """
    if not rule_cls.rule_id:
        raise ValueError(f"Rule {rule_cls.__name__} thiếu thuộc tính 'rule_id'.")
    if not rule_cls.description:
        raise ValueError(f"Rule '{rule_cls.rule_id}' thiếu 'description'.")

    existing = _RULE_REGISTRY.get(rule_cls.rule_id)
    if existing is not None and existing is not rule_cls:
        raise ValueError(
            f"Trùng rule_id '{rule_cls.rule_id}': {existing.__name__} và {rule_cls.__name__}."
        )

    _RULE_REGISTRY[rule_cls.rule_id] = rule_cls
    return rule_cls


def registered_rule_ids() -> list[str]:
    """Danh sách id của tất cả rule đã đăng ký (đã sắp xếp)."""
    return sorted(_RULE_REGISTRY)


def get_rule_class(rule_id: str) -> type[Rule]:
    """Lấy lớp rule theo id.

    :raises KeyError: nếu rule chưa được đăng ký.
    """
    try:
        return _RULE_REGISTRY[rule_id]
    except KeyError as exc:
        raise KeyError(
            f"Rule '{rule_id}' không tồn tại. Danh sách hợp lệ: " + ", ".join(registered_rule_ids())
        ) from exc


def build_rule(
    rule_id: str,
    *,
    params: Mapping[str, Any] | None = None,
    severity: Severity | None = None,
    logger: logging.Logger | None = None,
) -> Rule:
    """Khởi tạo rule với tham số/mức độ đã resolve từ cấu hình."""
    rule_cls = get_rule_class(rule_id)
    return rule_cls(params, severity=severity, logger=logger)


def rule_catalog() -> list[dict[str, Any]]:
    """Thông tin mô tả tất cả rule (cho CLI/tài liệu/API)."""
    catalog: list[dict[str, Any]] = []
    for rule_id in registered_rule_ids():
        rule_cls = _RULE_REGISTRY[rule_id]
        catalog.append(
            {
                "rule_id": rule_id,
                "group": rule_cls.group,
                "description": rule_cls.description,
                "default_severity": rule_cls.default_severity.value,
                "params_schema": rule_cls.params_model.model_json_schema(),
            }
        )
    return catalog


#: Tất cả rule của tool (thứ tự ở đây cũng là thứ tự chạy mặc định).
_RULE_CLASSES: tuple[type[Rule], ...] = (
    # geometry
    geometry_rules.InvalidSizeRule,
    geometry_rules.OutOfFrameRule,
    geometry_rules.TinyBoxRule,
    geometry_rules.DuplicateBBoxRule,
    geometry_rules.MustBeInsideRule,
    # completeness
    completeness_rules.MissingLabelRule,
    completeness_rules.RequiredAttributesRule,
    completeness_rules.UnexpectedLabelRule,
    completeness_rules.EmptyFrameRule,
    # temporal
    temporal_rules.TrackGapRule,
    temporal_rules.TrackClassChangeRule,
)

for _rule_cls in _RULE_CLASSES:
    register_rule(_rule_cls)

__all__ = [
    "Finding",
    "Rule",
    "RuleContext",
    "RuleParams",
    "ShapeSelectionParams",
    "build_rule",
    "get_rule_class",
    "register_rule",
    "registered_rule_ids",
    "rule_catalog",
]
