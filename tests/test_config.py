"""Test cấu hình kết nối CVAT và cấu hình rule."""

from __future__ import annotations

from pathlib import Path

import pytest

from qaqc.config import CVATConfig, RuleConfig, load_rule_config
from qaqc.model import Severity
from qaqc.rules import registered_rule_ids


# ---------------------------------------------------------------------------
# CVATConfig
# ---------------------------------------------------------------------------
class _FakeConfiguration:
    """``api_client.configuration`` giả (chỉ cần ``api_key``)."""

    def __init__(self) -> None:
        self.api_key: dict[str, str] = {}


class _FakeApiClient:
    """``api_client`` giả."""

    def __init__(self) -> None:
        self.configuration = _FakeConfiguration()


class _FakeClient:
    """Client giả để test ``CVATConfig.create_client`` mà không cần CVAT server."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.api_client = _FakeApiClient()
        self.credentials: object = None
        self.closed = False
        self.calls: list[object] = []

    def login(self, credentials: object) -> None:
        self.credentials = credentials
        self.calls.append(credentials)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_client(monkeypatch) -> type[_FakeClient]:
    """Thay ``cvat_sdk.Client`` bằng client giả và trả về lớp giả đó."""
    monkeypatch.setattr("cvat_sdk.Client", _FakeClient)
    return _FakeClient


def test_create_client_uses_access_token(fake_client) -> None:
    """Token được gửi qua ``api_key['tokenAuth']`` -> header ``Authorization: Token ...``."""
    config = CVATConfig(host="http://cvat.test", token="tok-123")
    client = config.create_client()
    assert client.url == "http://cvat.test"
    assert client.api_client.configuration.api_key["tokenAuth"] == "tok-123"
    assert client.calls == []  # không gọi API login


def test_create_client_uses_password_credentials(fake_client) -> None:
    """Khi không có token thì đăng nhập bằng ``(user, password)``."""
    config = CVATConfig(host="http://cvat.test", user="admin", password="secret")
    client = config.create_client()
    assert client.credentials == ("admin", "secret")
    assert client.api_client.configuration.api_key == {}


def test_create_client_without_credentials_raises(fake_client) -> None:
    """Thiếu cả token lẫn user/password -> ValueError (không gọi CVAT)."""
    config = CVATConfig(host="http://cvat.test")
    with pytest.raises(ValueError, match="xác thực"):
        config.create_client()


def test_config_from_env_values(monkeypatch) -> None:
    """Đọc đủ host/user/password từ biến môi trường."""
    monkeypatch.setenv("CVAT_HOST", "cvat.example.com/")
    monkeypatch.setenv("CVAT_USER", "admin")
    monkeypatch.setenv("CVAT_PASS", "secret")
    monkeypatch.delenv("CVAT_TOKEN", raising=False)

    config = CVATConfig.from_env(env_file=None)
    assert config.host == "http://cvat.example.com"
    assert config.credentials == ("admin", "secret")


def test_config_requires_host(monkeypatch) -> None:
    """Thiếu host -> ValueError nêu rõ tên biến."""
    monkeypatch.delenv("CVAT_HOST", raising=False)
    monkeypatch.delenv("CVAT_TOKEN", raising=False)
    with pytest.raises(ValueError, match="CVAT_HOST"):
        CVATConfig.from_env(env_file=None, user="a", password="b")


def test_config_requires_credentials(monkeypatch) -> None:
    """Thiếu cả token lẫn user/password -> ValueError."""
    monkeypatch.delenv("CVAT_TOKEN", raising=False)
    with pytest.raises(ValueError, match="xác thực"):
        CVATConfig.from_env(env_file=None, host="http://localhost:8080")


def test_config_token_only(monkeypatch) -> None:
    """Chỉ có token cũng hợp lệ và không dùng credentials."""
    monkeypatch.setenv("CVAT_TOKEN", "token-abc")
    monkeypatch.delenv("CVAT_USER", raising=False)
    monkeypatch.delenv("CVAT_PASS", raising=False)
    config = CVATConfig.from_env(env_file=None, host="localhost:8080")
    assert config.host == "http://localhost:8080"
    assert config.credentials is None
    assert config.token.get_secret_value() == "token-abc"


def test_config_host_normalization() -> None:
    """Host giữ scheme https và bỏ dấu / cuối."""
    assert CVATConfig(host="https://cvat.local/").host == "https://cvat.local"


def test_config_reads_env_file(tmp_path: Path, monkeypatch) -> None:
    """Nạp được thông tin từ file .env."""
    for name in ("CVAT_HOST", "CVAT_USER", "CVAT_PASS", "CVAT_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "CVAT_HOST=http://localhost:9999\nCVAT_USER=u\nCVAT_PASS=p\n", encoding="utf-8"
    )
    config = CVATConfig.from_env(env_file=env_file)
    assert config.host == "http://localhost:9999"
    assert config.credentials == ("u", "p")


# ---------------------------------------------------------------------------
# RuleConfig
# ---------------------------------------------------------------------------
def test_rule_config_load_from_dict() -> None:
    """Nạp cấu hình từ dict và resolve mức độ hiệu lực."""
    config = RuleConfig.load({"rules": {"invalid_size": {"severity": "warning"}}})
    assert config.name == "default"
    assert config.enabled_rule_ids() == ["invalid_size"]
    assert config.severity_for("invalid_size", Severity.ERROR) == Severity.WARNING


def test_rule_config_severity_falls_back_to_rule_default() -> None:
    """Không khai báo severity -> dùng mặc định của rule."""
    config = RuleConfig.load({"rules": {"invalid_size": {}}})
    assert config.severity_for("invalid_size", Severity.ERROR) == Severity.ERROR


def test_rule_config_defaults_severity_applies() -> None:
    """``defaults.severity`` áp cho mọi rule không khai báo riêng."""
    config = RuleConfig.load(
        {"defaults": {"severity": "info"}, "rules": {"invalid_size": {}, "tiny_box": {}}}
    )
    assert config.severity_for("invalid_size", Severity.ERROR) == Severity.INFO


def test_rule_config_load_from_yaml(tmp_path: Path) -> None:
    """Nạp cấu hình từ file YAML và ghi lại đường dẫn nguồn."""
    path = tmp_path / "rules.yaml"
    path.write_text(
        "version: 1\nname: custom\nrules:\n  invalid_size:\n    enabled: false\n",
        encoding="utf-8",
    )
    config = RuleConfig.load(path)
    assert config.name == "custom"
    assert config.source_path == str(path)
    assert config.enabled_rule_ids() == []


def test_rule_config_missing_file() -> None:
    """File không tồn tại -> ValueError."""
    with pytest.raises(ValueError, match="Không tìm thấy file"):
        RuleConfig.load("rules/khong-ton-tai.yaml")


def test_rule_config_invalid_yaml(tmp_path: Path) -> None:
    """YAML sai -> ValueError nêu rõ file."""
    path = tmp_path / "bad.yaml"
    path.write_text("rules: [1, 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML"):
        RuleConfig.load(path)


def test_rule_config_rejects_unknown_keys(tmp_path: Path) -> None:
    """Khoá lạ trong YAML bị chặn (extra=forbid) để phát hiện gõ sai."""
    path = tmp_path / "typo.yaml"
    path.write_text("rules:\n  invalid_size:\n    severty: error\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Cấu hình rule không hợp lệ"):
        RuleConfig.load(path)


def test_rule_config_validate_rule_ids() -> None:
    """Rule id không tồn tại -> ValueError kèm danh sách hợp lệ."""
    config = RuleConfig.load({"rules": {"khong_ton_tai": {}}})
    with pytest.raises(ValueError, match="khong_ton_tai"):
        config.validate_rule_ids(registered_rule_ids())


def test_rule_config_with_overrides_only_and_disable() -> None:
    """``--only`` và ``--disable`` áp đúng."""
    config = RuleConfig.load({"rules": {"invalid_size": {}, "tiny_box": {}, "duplicate_bbox": {}}})
    only = config.with_overrides(only=["invalid_size", "tiny_box"])
    assert only.enabled_rule_ids() == ["invalid_size", "tiny_box"]

    disabled = config.with_overrides(disabled=["tiny_box"])
    assert disabled.enabled_rule_ids() == ["duplicate_bbox", "invalid_size"]


def test_rule_config_with_overrides_params_and_severity() -> None:
    """``--param`` gộp tham số và ghi đè mức độ."""
    config = RuleConfig.load({"rules": {"duplicate_bbox": {"params": {"iou_threshold": 0.85}}}})
    overridden = config.with_overrides(
        extra_params={"duplicate_bbox": {"iou_threshold": 0.9, "same_label_only": False}},
        severities={"duplicate_bbox": "warning"},
        name_suffix="cli",
    )
    spec = overridden.spec_of("duplicate_bbox")
    assert spec.params["iou_threshold"] == 0.9
    assert spec.params["same_label_only"] is False
    assert spec.severity == Severity.WARNING
    assert overridden.name.endswith("+cli")


def test_rule_config_hash_reflects_params() -> None:
    """Hash đổi khi tham số đổi, và đổi khi rule bị tắt."""
    base = RuleConfig.load({"rules": {"duplicate_bbox": {"params": {"iou_threshold": 0.85}}}})
    changed = RuleConfig.load({"rules": {"duplicate_bbox": {"params": {"iou_threshold": 0.9}}}})
    disabled = RuleConfig.load(
        {"rules": {"duplicate_bbox": {"params": {"iou_threshold": 0.85}, "enabled": False}}}
    )
    assert base.config_hash != changed.config_hash
    assert base.config_hash != disabled.config_hash


def test_rule_config_default_enables_everything() -> None:
    """Cấu hình mặc định bật mọi rule đã đăng ký."""
    assert RuleConfig.default().enabled_rule_ids() == registered_rule_ids()


def test_load_rule_config_uses_repo_yaml() -> None:
    """``load_rule_config(None)`` dùng ``rules/driving_v1.yaml`` của repo."""
    config = load_rule_config(None)
    assert config.name == "driving_v1"
    assert "invalid_size" in config.enabled_rule_ids()


def test_rule_config_summary_lines() -> None:
    """``summary_lines`` in tên/hash/số rule bật."""
    config = RuleConfig.load({"rules": {"invalid_size": {}, "tiny_box": {"enabled": False}}})
    text = "\n".join(config.summary_lines())
    assert "invalid_size" in text
    assert "tiny_box" in text
    assert "hash=" in text
