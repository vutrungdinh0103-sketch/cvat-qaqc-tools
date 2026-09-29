"""Fingerprint - khoá định danh ổn định cho mỗi lỗi QA/QC.

Mục đích: khi chạy lại QA/QC trên cùng task, tool phải **nhận ra** lỗi đã tạo
issue trước đó và không tạo trùng. Vì vậy fingerprint phải:

- Ổn định giữa các lần chạy: chỉ phụ thuộc ``rule_id``, ``task_id``, ``frame``,
  tập ``object_keys`` và hash cấu hình rule.
- Ngắn để nhét vào ``message`` của issue (không cần metadata riêng), dạng
  ``fp=1a2b3c4d``.

Đổi tham số rule (ví dụ ``iou_threshold``) làm ``config_hash`` đổi -> fingerprint
đổi -> lỗi được báo lại như một issue mới (đúng ý đồ: tiêu chuẩn đã thay đổi).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from typing import Final

#: Biểu thức nhận diện fingerprint trong message của issue.
FINGERPRINT_PATTERN: Final[re.Pattern[str]] = re.compile(r"\bfp=([0-9a-f]{8,32})\b")

#: Tiền tố hiển thị trong message.
FINGERPRINT_PREFIX: Final[str] = "fp"

#: Số byte digest (4 byte -> 8 ký tự hex).
DIGEST_BYTES: Final[int] = 4


def fingerprint(
    *,
    rule_id: str,
    task_id: int,
    frame: int,
    object_keys: Iterable[str],
    config_hash: str,
    discriminator: str | None = None,
) -> str:
    """Sinh fingerprint cho một lỗi.

    :param rule_id: id rule đã sinh ra lỗi.
    :param task_id: id task (hoặc job nếu dữ liệu lấy theo job).
    :param frame: frame chứa lỗi.
    :param object_keys: các ``object_key`` liên quan (tự sắp xếp để ổn định).
    :param config_hash: hash cấu hình rule đang dùng (:attr:`RuleConfig.config_hash`).
    :param discriminator: phần phân biệt thêm, dùng khi một rule có thể sinh
        nhiều lỗi trên cùng một tập object trong cùng frame (ví dụ nhiều attribute
        bắt buộc bị thiếu).
    """
    keys = ",".join(sorted(str(key) for key in object_keys))
    raw = "|".join(
        [
            rule_id,
            str(task_id),
            str(frame),
            keys,
            config_hash,
            discriminator or "",
        ]
    )
    digest = hashlib.blake2s(raw.encode("utf-8"), digest_size=DIGEST_BYTES).hexdigest()
    return digest


def format_fingerprint(value: str) -> str:
    """Định dạng fingerprint để nhúng vào message: ``fp=1a2b3c4d``."""
    return f"{FINGERPRINT_PREFIX}={value}"


def extract_fingerprints(text: str | None) -> set[str]:
    """Trích tất cả fingerprint có trong một đoạn text (message của issue)."""
    if not text:
        return set()
    return {match.group(1) for match in FINGERPRINT_PATTERN.finditer(text)}


def extract_fingerprint(text: str | None) -> str | None:
    """Trích fingerprint đầu tiên trong text (``None`` nếu không có)."""
    if not text:
        return None
    match = FINGERPRINT_PATTERN.search(text)
    return match.group(1) if match else None
