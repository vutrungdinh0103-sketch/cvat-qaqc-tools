"""Test sinh/đọc fingerprint chống tạo trùng issue."""

from __future__ import annotations

from qaqc.fingerprint import (
    extract_fingerprint,
    extract_fingerprints,
    fingerprint,
    format_fingerprint,
)

BASE = {
    "rule_id": "duplicate_bbox",
    "task_id": 42,
    "frame": 10,
    "object_keys": ("s2", "s1"),
    "config_hash": "abc123",
}


def test_fingerprint_is_stable() -> None:
    """Cùng đầu vào -> cùng fingerprint (điều kiện để chống trùng khi re-run)."""
    assert fingerprint(**BASE) == fingerprint(**BASE)


def test_fingerprint_ignores_object_key_order() -> None:
    """Thứ tự object_keys không ảnh hưởng (đã sắp xếp nội bộ)."""
    other = {**BASE, "object_keys": ("s1", "s2")}
    assert fingerprint(**BASE) == fingerprint(**other)


def test_fingerprint_changes_with_inputs() -> None:
    """Đổi rule/task/frame/object/config/discriminator -> fingerprint khác."""
    base = fingerprint(**BASE)
    assert fingerprint(**{**BASE, "rule_id": "invalid_size"}) != base
    assert fingerprint(**{**BASE, "task_id": 43}) != base
    assert fingerprint(**{**BASE, "frame": 11}) != base
    assert fingerprint(**{**BASE, "object_keys": ("s1",)}) != base
    assert fingerprint(**{**BASE, "config_hash": "def456"}) != base
    assert fingerprint(**{**BASE, "discriminator": "vehicle_type"}) != base


def test_fingerprint_is_short_hex() -> None:
    """Fingerprint là 8 ký tự hex (nhét vừa message của issue)."""
    value = fingerprint(**BASE)
    assert len(value) == 8
    assert all(character in "0123456789abcdef" for character in value)


def test_format_and_extract_roundtrip() -> None:
    """``format_fingerprint`` và ``extract_fingerprint`` khớp nhau."""
    value = fingerprint(**BASE)
    text = f"[QA][fp={value}][v=1] {format_fingerprint(value)} frame 10"
    assert extract_fingerprint(text) == value
    assert extract_fingerprints(text) == {value}


def test_extract_fingerprints_multiple_and_none() -> None:
    """Trích nhiều fingerprint và trả rỗng khi không có."""
    assert extract_fingerprints("fp=aaaaaaaa và fp=bbbbbbbb") == {"aaaaaaaa", "bbbbbbbb"}
    assert extract_fingerprints("không có gì") == set()
    assert extract_fingerprint(None) is None
    assert extract_fingerprints(None) == set()
