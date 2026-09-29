"""Cấu hình cho tool QA/QC.

Gồm 2 phần độc lập:

1. :class:`CVATConfig` - thông tin kết nối CVAT server (đọc từ ``.env``/biến
   môi trường ``CVAT_HOST``, ``CVAT_USER``, ``CVAT_PASS``).
2. :class:`RuleConfig` - bộ rule + tham số, nạp từ file YAML (xem
   ``rules/driving_v1.yaml``). ``extra="forbid"`` giúp phát hiện ngay lỗi gõ sai
   khoá trong file cấu hình thay vì âm thầm bỏ qua.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

from .model import Severity

#: File cấu hình rule mặc định (đường dẫn tương đối từ thư mục chạy lệnh).
DEFAULT_RULES_FILE: Final[str] = "rules/driving_v1.yaml"


# ---------------------------------------------------------------------------
# Kết nối CVAT
# ---------------------------------------------------------------------------
class CVATConfig(BaseModel):
    """Cấu hình kết nối CVAT server.

    Giá trị được đọc từ file ``.env`` (xem ``.env.example``) hoặc biến môi
    trường. Ngoài ``CVAT_USER``/``CVAT_PASS``, có thể dùng **Access Token** của
    CVAT (``CVAT_TOKEN``) - khuyến nghị cho tài khoản bot/CI.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    host: str
    user: str | None = None
    password: SecretStr | None = None
    token: SecretStr | None = None

    @field_validator("host")
    @classmethod
    def _normalize_host(cls, value: str) -> str:
        """Bỏ dấu ``/`` cuối và tự thêm scheme nếu người dùng nhập thiếu."""
        value = value.rstrip("/")
        if not value.startswith(("http://", "https://")):
            value = f"http://{value}"
        return value

    @property
    def credentials(self) -> tuple[str, str] | None:
        """Tuple ``(user, password)`` cho ``Client.login()`` hoặc ``None`` nếu dùng token."""
        if self.token is not None and not self.user:
            return None
        if self.user is None or self.password is None:
            return None
        return (self.user, self.password.get_secret_value())

    def create_client(self) -> Any:
        """Tạo ``cvat_sdk.Client`` đã đăng nhập.

        Dùng Access Token nếu có, ngược lại dùng user/password.

        :raises ValueError: nếu thiếu cả token lẫn user/password.
        """
        from cvat_sdk import Client  # import muộn: engine không cần phụ thuộc SDK

        client = Client(url=self.host)
        try:
            if self.token is not None:
                client.login(access_token=self.token.get_secret_value())
            elif self.credentials is not None:
                client.login(credentials=self.credentials)
            else:
                raise ValueError(
                    "Thiếu thông tin xác thực CVAT: cần CVAT_TOKEN hoặc CVAT_USER/CVAT_PASS."
                )
        except Exception:
            client.close()
            raise
        return client

    @classmethod
    def from_env(
        cls,
        env_file: str | os.PathLike[str] | None = ".env",
        *,
        host: str | None = None,
        user: str | None = None,
        password: str | None = None,
        token: str | None = None,
    ) -> CVATConfig:
        """Nạp cấu hình từ ``env_file``/biến môi trường, tham số truyền vào được ưu tiên.

        :raises ValueError: nếu thiếu host, hoặc thiếu cả token lẫn user/password.
        """
        if env_file is not None:
            # load_dotenv bỏ qua (không raise) nếu file không tồn tại
            load_dotenv(Path(env_file), override=False, encoding="utf-8")

        values: dict[str, Any] = {
            "host": host or os.getenv("CVAT_HOST"),
            "user": user or os.getenv("CVAT_USER"),
            "password": password or os.getenv("CVAT_PASS"),
            "token": token or os.getenv("CVAT_TOKEN"),
        }
        if not values["host"]:
            raise ValueError(
                "Thiếu CVAT_HOST. Hãy khai báo trong file .env (xem .env.example) "
                "hoặc truyền qua tham số --host."
            )
        if not values["token"] and not (values["user"] and values["password"]):
            raise ValueError(
                "Thiếu thông tin xác thực CVAT: cần CVAT_TOKEN hoặc cặp CVAT_USER/CVAT_PASS."
            )

        try:
            return cls(**values)
        except ValidationError as exc:  # pragma: no cover - pydantic báo lỗi rõ ràng
            raise ValueError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Cấu hình rule
# ---------------------------------------------------------------------------
class RuleDefaults(BaseModel):
    """Giá trị mặc định áp dụng cho mọi rule chưa khai báo rõ.

    ``severity=None`` nghĩa là "dùng mức độ mặc định của chính rule đó".
    """

    model_config = ConfigDict(extra="forbid")

    severity: Severity | None = None
    enabled: bool = True


class RuleSpec(BaseModel):
    """Cấu hình của một rule trong file YAML."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    severity: Severity | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class RuleConfig(BaseModel):
    """Bộ rule + tham số dùng cho một lần chạy QA/QC."""

    model_config = ConfigDict(extra="forbid")

    version: int = 1
    name: str = "default"
    description: str | None = None
    defaults: RuleDefaults = Field(default_factory=RuleDefaults)
    rules: dict[str, RuleSpec] = Field(default_factory=dict)
    #: Đường dẫn file cấu hình đã nạp (không tham gia vào ``config_hash``).
    source_path: str | None = None

    # ------------------------------------------------------------------
    # Nạp / tạo cấu hình
    # ------------------------------------------------------------------
    @classmethod
    def default(cls) -> RuleConfig:
        """Cấu hình mặc định: bật **tất cả** rule đã đăng ký với tham số gốc."""
        from .rules import registered_rule_ids  # import muộn để tránh vòng lặp

        return cls(
            name="default",
            description="Tất cả rule đã đăng ký, dùng tham số mặc định của từng rule.",
            rules={rule_id: RuleSpec() for rule_id in registered_rule_ids()},
        )

    @classmethod
    def load(cls, source: str | os.PathLike[str] | Mapping[str, Any] | None) -> RuleConfig:
        """Nạp cấu hình rule từ file YAML, dict, hoặc ``None`` (mặc định).

        :raises ValueError: file không tồn tại / YAML sai / cấu trúc không hợp lệ.
        """
        if source is None:
            return cls.default()

        if isinstance(source, Mapping):
            return cls.model_validate(dict(source))

        path = Path(source)
        if not path.exists():
            raise ValueError(f"Không tìm thấy file cấu hình rule: {path}")

        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ValueError(f"File cấu hình rule không phải YAML hợp lệ ({path}): {exc}") from exc

        if not isinstance(raw, Mapping):
            raise ValueError(f"File cấu hình rule phải là một mapping YAML ở cấp cao nhất: {path}")

        try:
            config = cls.model_validate(dict(raw))
        except ValidationError as exc:
            raise ValueError(f"Cấu hình rule không hợp lệ ({path}):\n{exc}") from exc

        config.source_path = str(path)
        return config

    # ------------------------------------------------------------------
    # Truy vấn
    # ------------------------------------------------------------------
    def validate_rule_ids(self, known_ids: Iterable[str]) -> None:
        """Kiểm tra mọi rule trong cấu hình đều tồn tại.

        :raises ValueError: nếu có rule id không được đăng ký.
        """
        known = set(known_ids)
        unknown = sorted(rule_id for rule_id in self.rules if rule_id not in known)
        if unknown:
            raise ValueError(
                "Rule không tồn tại trong tool: "
                + ", ".join(unknown)
                + f". Danh sách rule hợp lệ: {', '.join(sorted(known))}"
            )

    def spec_of(self, rule_id: str) -> RuleSpec:
        """Cấu hình của một rule (mặc định bật, không tham số nếu chưa khai báo)."""
        return self.rules.get(rule_id, RuleSpec())

    def severity_for(self, rule_id: str, rule_default: Severity) -> Severity:
        """Mức độ hiệu lực của rule: khai báo riêng > ``defaults.severity`` > rule."""
        spec = self.rules.get(rule_id)
        if spec is not None and spec.severity is not None:
            return spec.severity
        if self.defaults.severity is not None:
            return self.defaults.severity
        return rule_default

    def enabled_rule_ids(self) -> list[str]:
        """Danh sách rule đang bật (đã sắp xếp theo id)."""
        return sorted(rule_id for rule_id, spec in self.rules.items() if spec.enabled)

    @property
    def config_hash(self) -> str:
        """Hash SHA-256 của toàn bộ cấu hình đang bật (ghi vào báo cáo)."""
        payload = {
            "version": self.version,
            "defaults": {
                "severity": self.defaults.severity.value if self.defaults.severity else None
            },
            "rules": {
                rule_id: self._rule_payload(rule_id)
                for rule_id in sorted(self.rules)
                if self.rules[rule_id].enabled
            },
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def rule_hash(self, rule_id: str) -> str:
        """Hash SHA-256 của riêng một rule (dùng cho fingerprint của issue).

        Chỉ phụ thuộc rule đó nên **thêm/bớt rule khác không làm đổi
        fingerprint** của issue đã tạo trước đó (tránh tạo trùng khi bật rule mới).
        """
        payload = {"rule_id": rule_id, **self._rule_payload(rule_id)}
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def _rule_payload(self, rule_id: str) -> dict[str, Any]:
        """Phần dữ liệu của một rule tham gia vào hash."""
        spec = self.spec_of(rule_id)
        return {
            "severity": spec.severity.value if spec.severity else None,
            "params": spec.params,
        }

    def with_overrides(
        self,
        *,
        only: Sequence[str] | None = None,
        disabled: Sequence[str] | None = None,
        extra_params: Mapping[str, Mapping[str, Any]] | None = None,
        severities: Mapping[str, Severity | str] | None = None,
        name_suffix: str | None = None,
    ) -> RuleConfig:
        """Trả về bản sao cấu hình đã áp các ghi đè từ CLI/API.

        :param only: chỉ chạy các rule này (các rule khác bị tắt).
        :param disabled: tắt thêm các rule này.
        :param extra_params: gộp thêm tham số cho rule (rule được bật nếu đang tắt).
        :param severities: ghi đè mức độ của rule.
        :param name_suffix: hậu tố thêm vào tên cấu hình (dùng cho log/report).
        """
        updated: dict[str, RuleSpec] = {
            rule_id: spec.model_copy(deep=True) for rule_id, spec in self.rules.items()
        }

        def _spec(rule_id: str) -> RuleSpec:
            if rule_id not in updated:
                updated[rule_id] = RuleSpec()
            return updated[rule_id]

        if only is not None:
            only_set = set(only)
            for rule_id in set(updated) | only_set:
                _spec(rule_id).enabled = rule_id in only_set

        for rule_id in disabled or ():
            _spec(rule_id).enabled = False

        for rule_id, params in (extra_params or {}).items():
            spec = _spec(rule_id)
            spec.params = {**spec.params, **params}
            spec.enabled = True

        for rule_id, severity in (severities or {}).items():
            default = self.defaults.severity or Severity.WARNING
            _spec(rule_id).severity = Severity.parse(severity, default)

        clone = self.model_copy(deep=True)
        clone.rules = updated
        if name_suffix:
            clone.name = f"{self.name}+{name_suffix}"
        return clone

    def summary_lines(self) -> list[str]:
        """Các dòng mô tả cấu hình rule (in ra log/CLI)."""
        identifier = f"{self.name} (version={self.version}, hash={self.config_hash[:12]})"
        lines = [f"Cấu hình rule: {identifier}"]
        if self.source_path:
            lines.append(f"  - File: {self.source_path}")
        enabled = self.enabled_rule_ids()
        listed = ", ".join(enabled) if enabled else "(không có)"
        lines.append(f"  - Rule bật ({len(enabled)}): {listed}")
        disabled = sorted(set(self.rules) - set(enabled))
        if disabled:
            lines.append(f"  - Rule tắt: {', '.join(disabled)}")
        return lines


def load_rule_config(
    source: str | os.PathLike[str] | Mapping[str, Any] | None = None,
) -> RuleConfig:
    """Tiện ích nạp cấu hình rule (mặc định dùng ``rules/driving_v1.yaml`` nếu tồn tại).

    :param source: đường dẫn YAML, dict, hoặc ``None`` để dùng file mặc định.
    """
    if source is None:
        default_path = Path(DEFAULT_RULES_FILE)
        if default_path.exists():
            return RuleConfig.load(default_path)
        return RuleConfig.default()
    return RuleConfig.load(source)
