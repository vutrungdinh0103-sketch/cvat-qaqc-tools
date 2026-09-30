"""Test QA/QC service localhost (``python -m qaqc serve``).

Test chạy **offline hoàn toàn**: dùng nguồn dữ liệu mẫu (:mod:`qaqc.demo`) hoặc
file JSON dạng payload annotations của CVAT, không cần CVAT server. HTTP server
được khởi động trên cổng ngẫu nhiên (``port=0``) trong một luồng riêng.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http import HTTPStatus
from pathlib import Path
from typing import Any

import pytest
from test_publisher import FakeClient

from qaqc import SCHEMA_VERSION, __version__
from qaqc.cli import main as cli_main
from qaqc.report import CSV_FIELDS, EXIT_CONFIG_ERROR
from qaqc.rules import rule_ids_for_level
from qaqc.service import (
    QAQCHandler,
    QAQCServer,
    QAQCService,
    ServiceError,
    ServiceOptions,
    load_task_data_from_file,
    parse_bool,
    split_csv,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"

#: Task id của file fixture ``tests/fixtures/task_anomalies.json``.
FIXTURE_TASK_ID = 42


# ---------------------------------------------------------------------------
# Tiện ích HTTP
# ---------------------------------------------------------------------------
def _request(
    url: str,
    method: str = "GET",
) -> tuple[int, dict[str, str], bytes]:
    """Gọi một URL, trả ``(status, headers, body)`` - kể cả khi lỗi 4xx/5xx."""
    request = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def _get_json(url: str, method: str = "GET") -> dict[str, Any]:
    """Gọi URL và parse body JSON."""
    status, _, body = _request(url, method)
    assert status == HTTPStatus.OK, f"{url} trả về {status}: {body[:200]!r}"
    return json.loads(body.decode("utf-8"))


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------
@pytest.fixture
def demo_service() -> QAQCService:
    """Service dùng dữ liệu mẫu (không gọi CVAT)."""
    return QAQCService(ServiceOptions(demo=True))


@pytest.fixture
def http_base_url(demo_service: QAQCService) -> Iterator[str]:
    """Khởi động service trên cổng ngẫu nhiên, trả về URL gốc."""
    server = QAQCServer(("127.0.0.1", 0), demo_service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)


# ---------------------------------------------------------------------------
# Hàm tiện ích
# ---------------------------------------------------------------------------
def test_split_csv() -> None:
    """Tách danh sách rule từ query string."""
    assert split_csv(None) is None
    assert split_csv("") is None
    assert split_csv(" , ") is None
    assert split_csv("a, b ,c") == ["a", "b", "c"]
    assert split_csv(["a", " b "]) == ["a", "b"]


def test_parse_bool() -> None:
    """Nhận diện ``refresh=true/1/yes/on``."""
    assert parse_bool("true") is True
    assert parse_bool("1") is True
    assert parse_bool("YES") is True
    assert parse_bool("no") is False
    assert parse_bool(None) is False


def test_load_task_data_from_file_missing() -> None:
    """File không tồn tại → ``404`` (kèm thông báo rõ ràng)."""
    with pytest.raises(ServiceError) as excinfo:
        load_task_data_from_file(FIXTURES_DIR / "khong-ton-tai.json", 1)
    assert excinfo.value.status == HTTPStatus.NOT_FOUND


def test_load_task_data_from_file_invalid_json(tmp_path: Path) -> None:
    """File không phải JSON → lỗi ``400``."""
    broken = tmp_path / "broken.json"
    broken.write_text("{ khong-phai-json", encoding="utf-8")
    with pytest.raises(ServiceError) as excinfo:
        load_task_data_from_file(broken, 1)
    assert excinfo.value.status == HTTPStatus.BAD_REQUEST
    assert "JSON" in str(excinfo.value)


def test_resolve_rules_file() -> None:
    """Chấp nhận đường dẫn file và tên ngắn trong ``rules/``; chặn ``..``."""
    assert QAQCService.resolve_rules_file(None) is None
    assert Path(QAQCService.resolve_rules_file("rules/driving_v1.yaml")).name == "driving_v1.yaml"
    assert Path(QAQCService.resolve_rules_file("driving_v1")).name == "driving_v1.yaml"
    with pytest.raises(ServiceError):
        QAQCService.resolve_rules_file("../secrets.yaml")
    with pytest.raises(ServiceError):
        QAQCService.resolve_rules_file("khong-co-file-nay")


# ---------------------------------------------------------------------------
# QAQCService (không qua HTTP)
# ---------------------------------------------------------------------------
def test_demo_data_triggers_every_enabled_rule(demo_service: QAQCService) -> None:
    """Dữ liệu mẫu kích hoạt đủ mọi rule đang bật (nền tảng của demo/verify)."""
    payload = demo_service.report_payload(1)
    enabled = set(demo_service.rule_config().enabled_rule_ids())
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["task_id"] == 1
    assert enabled, "cấu hình rule mặc định phải bật ít nhất một rule"
    assert set(payload["counts_by_rule"]) == enabled
    assert len(payload["issues"]) >= len(enabled)
    assert any("Dữ liệu mẫu" in warning for warning in payload["warnings"])


def test_report_from_source_file() -> None:
    """Đọc dữ liệu task từ file JSON (offline) và báo cáo đúng task trong file."""
    service = QAQCService(ServiceOptions(source_file=str(FIXTURES_DIR / "task_anomalies.json")))
    payload = service.report_payload(FIXTURE_TASK_ID)
    assert payload["task_id"] == FIXTURE_TASK_ID
    assert payload["issues"]
    assert payload["objects_scanned"] > 0


def test_only_and_disable_filters(demo_service: QAQCService) -> None:
    """``only`` chỉ giữ rule được chọn, ``disable`` bỏ rule được chọn."""
    only = demo_service.report_payload(1, only="invalid_size,duplicate_bbox")
    assert {issue["rule_id"] for issue in only["issues"]} == {"invalid_size", "duplicate_bbox"}

    disabled = demo_service.report_payload(1, disable="duplicate_bbox")
    assert "duplicate_bbox" not in disabled["counts_by_rule"]
    assert "invalid_size" in disabled["counts_by_rule"]


def test_unknown_rule_in_only_raises_value_error(demo_service: QAQCService) -> None:
    """Rule không tồn tại → ``ValueError`` (HTTP map thành 400)."""
    with pytest.raises(ValueError):
        demo_service.report_payload(1, only="khong_co_rule_nay")


def test_report_is_cached_until_refresh(demo_service: QAQCService) -> None:
    """Báo cáo được cache; ``refresh=True`` buộc đọc lại dữ liệu task."""
    calls: list[int] = []
    original = demo_service.load_task_data

    def counting_load(task_id: int) -> Any:
        calls.append(task_id)
        return original(task_id)

    demo_service.load_task_data = counting_load  # type: ignore[method-assign]

    first = demo_service.report_payload(1)
    second = demo_service.report_payload(1)
    assert calls == [1], "lần gọi thứ hai phải lấy từ cache"
    assert first == second

    demo_service.report_payload(1, refresh=True)
    assert calls == [1, 1], "refresh=True phải đọc lại dữ liệu"


def test_cache_ttl_zero_disables_cache(demo_service: QAQCService) -> None:
    """``cache_ttl=0`` → mỗi lần gọi đều đọc lại dữ liệu."""
    demo_service.options.cache_ttl = 0.0
    calls: list[int] = []
    original = demo_service.load_task_data

    def counting_load(task_id: int) -> Any:
        calls.append(task_id)
        return original(task_id)

    demo_service.load_task_data = counting_load  # type: ignore[method-assign]

    demo_service.report_payload(1)
    demo_service.report_payload(1)
    assert calls == [1, 1]


def test_health_reports_source_and_rules(demo_service: QAQCService) -> None:
    """``health()`` cho UI biết đang chạy nguồn dữ liệu nào và bộ rule nào."""
    payload = demo_service.health()
    assert payload["status"] == "ok"
    assert payload["version"] == __version__
    assert payload["data_source"].startswith("dữ liệu mẫu")
    assert payload["rules"] == "driving_v1"
    assert "invalid_size" in payload["rules_enabled"]


def test_unexpected_data_error_maps_to_bad_gateway(demo_service: QAQCService) -> None:
    """Lỗi khi lấy dữ liệu (CVAT sập...) → ``502`` thay vì ``500``."""

    def broken_load(task_id: int) -> Any:
        raise RuntimeError("CVAT offline")

    demo_service.load_task_data = broken_load  # type: ignore[method-assign]
    with pytest.raises(ServiceError) as excinfo:
        demo_service.report_payload(1)
    assert excinfo.value.status == HTTPStatus.BAD_GATEWAY
    assert "CVAT offline" in str(excinfo.value)


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------
def test_index_page_serves_html(http_base_url: str) -> None:
    """``GET /`` trả trang web QA/QC (nhúng trong ``qaqc.webui``)."""
    status, headers, body = _request(f"{http_base_url}/")
    assert status == HTTPStatus.OK
    assert headers["Content-Type"].startswith("text/html")
    assert "QA/QC" in body.decode("utf-8")


def test_health_endpoint(http_base_url: str) -> None:
    """``GET /health`` trả JSON + CORS (để plugin CVAT UI gọi được)."""
    status, headers, body = _request(f"{http_base_url}/health")
    assert status == HTTPStatus.OK
    assert headers["Access-Control-Allow-Origin"] == "*"
    payload = json.loads(body.decode("utf-8"))
    assert payload["status"] == "ok"
    assert payload["data_source"].startswith("dữ liệu mẫu")


def test_report_endpoint(http_base_url: str) -> None:
    """``GET /tasks/{id}/report`` trả báo cáo JSON đủ trường cho plugin."""
    payload = _get_json(f"{http_base_url}/tasks/1/report")
    assert payload["task_id"] == 1
    assert payload["issues"]
    assert payload["counts_by_severity"]["error"] > 0
    assert {"rule_id", "severity", "frame", "message", "fingerprint"} <= set(payload["issues"][0])


def test_report_endpoint_query_params(http_base_url: str) -> None:
    """Query ``only``/``disable``/``rules`` được áp dụng cho báo cáo."""
    only = _get_json(f"{http_base_url}/tasks/1/report?only=invalid_size")
    assert {issue["rule_id"] for issue in only["issues"]} == {"invalid_size"}

    disabled = _get_json(f"{http_base_url}/tasks/1/report?disable=duplicate_bbox")
    assert "duplicate_bbox" not in disabled["counts_by_rule"]

    named = _get_json(f"{http_base_url}/tasks/1/report?rules=driving_v1")
    assert named["rules_name"] == "driving_v1"


def test_csv_endpoint(http_base_url: str) -> None:
    """``GET /tasks/{id}/report.csv`` trả CSV (BOM utf-8-sig để mở bằng Excel)."""
    status, headers, body = _request(f"{http_base_url}/tasks/1/report.csv")
    assert status == HTTPStatus.OK
    assert headers["Content-Type"].startswith("text/csv")
    text = body.decode("utf-8-sig")
    assert text.splitlines()[0] == ",".join(CSV_FIELDS)
    assert "invalid_size" in text


def test_report_endpoint_level_filter(http_base_url: str) -> None:
    """Query ``level=1`` chỉ chạy rule Level 1 (Overall/Completeness)."""
    payload = _get_json(f"{http_base_url}/tasks/1/report?level=1")
    level_one_rules = set(rule_ids_for_level(1))
    assert {issue["rule_id"] for issue in payload["issues"]} <= level_one_rules
    assert all(issue["level"] == 1 for issue in payload["issues"])
    assert payload["counts_by_level"] == {"1": len(payload["issues"])}


def test_report_endpoint_rejects_bad_level(http_base_url: str) -> None:
    """``level`` sai (không phải 1/2) -> HTTP 400 kèm thông báo rõ ràng."""
    status, _, body = _request(f"{http_base_url}/tasks/1/report?level=3")
    assert status == HTTPStatus.BAD_REQUEST
    assert "level" in body.decode("utf-8")


def test_post_run_endpoint(http_base_url: str) -> None:
    """``POST /tasks/{id}/run`` chạy QA/QC và trả báo cáo."""
    payload = _get_json(f"{http_base_url}/tasks/1/run", method="POST")
    assert payload["issues"]


def test_get_run_is_method_not_allowed(http_base_url: str) -> None:
    """``GET`` trên endpoint chạy QA/QC → ``405`` kèm gợi ý dùng POST."""
    status, _, body = _request(f"{http_base_url}/tasks/1/run")
    assert status == HTTPStatus.METHOD_NOT_ALLOWED
    assert "POST" in json.loads(body.decode("utf-8"))["error"]


def test_unknown_endpoints_return_404(http_base_url: str) -> None:
    """Đường dẫn/task id không hợp lệ → ``404`` (không làm sập service)."""
    assert _request(f"{http_base_url}/khong-ton-tai")[0] == HTTPStatus.NOT_FOUND
    assert _request(f"{http_base_url}/tasks/1/khong-co-action")[0] == HTTPStatus.NOT_FOUND
    assert _request(f"{http_base_url}/tasks/abc/report")[0] == HTTPStatus.NOT_FOUND


def test_options_preflight_has_cors_headers(http_base_url: str) -> None:
    """CORS preflight đủ header cho CVAT UI gọi service từ origin khác."""
    status, headers, _ = _request(f"{http_base_url}/tasks/1/report", method="OPTIONS")
    assert status == HTTPStatus.NO_CONTENT
    assert headers["Access-Control-Allow-Origin"] == "*"
    assert "POST" in headers["Access-Control-Allow-Methods"]
    assert "Content-Type" in headers["Access-Control-Allow-Headers"]


def test_bad_rules_param_returns_400(http_base_url: str) -> None:
    """``?rules=`` trỏ tới file không có → ``400`` kèm thông báo."""
    status, _, body = _request(f"{http_base_url}/tasks/1/report?rules=khong-co-file-nay")
    assert status == HTTPStatus.BAD_REQUEST
    assert "rule" in json.loads(body.decode("utf-8"))["error"]


# ---------------------------------------------------------------------------
# CLI: `python -m qaqc serve` báo lỗi cấu hình sớm và chỉ cách sửa
# ---------------------------------------------------------------------------
def _clear_cvat_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Xoá biến môi trường ``CVAT_*`` để test không phụ thuộc máy đang chạy."""
    for name in ("CVAT_HOST", "CVAT_USER", "CVAT_PASS", "CVAT_TOKEN"):
        monkeypatch.delenv(name, raising=False)


def test_cli_serve_reports_missing_env_file(monkeypatch, tmp_path, capsys) -> None:
    """Thiếu ``.env`` → exit 2, in đường dẫn file thiếu và 4 gợi ý sửa (có ``--demo``)."""
    _clear_cvat_env(monkeypatch)
    missing = tmp_path / "khong-ton-tai.env"

    code = cli_main(["serve", "--env-file", str(missing)])

    assert code == EXIT_CONFIG_ERROR == 2
    err = capsys.readouterr().err
    assert "Thiếu CVAT_HOST" in err
    assert "Không tìm thấy file cấu hình" in err
    assert "khong-ton-tai.env" in err
    assert "serve --demo --open" in err
    assert "Copy-Item .env.example" in err
    assert "--env-file <đường-dẫn>" in err


def test_cli_serve_reports_missing_credentials(monkeypatch, tmp_path, capsys) -> None:
    """Có host nhưng thiếu token lẫn user/password → vẫn exit 2 kèm hướng dẫn."""
    _clear_cvat_env(monkeypatch)

    code = cli_main(
        [
            "serve",
            "--host",
            "http://localhost:8080",
            "--env-file",
            str(tmp_path / "khong-ton-tai.env"),
        ]
    )

    assert code == EXIT_CONFIG_ERROR
    err = capsys.readouterr().err
    assert "Thiếu thông tin xác thực CVAT" in err
    assert "Cách sửa:" in err


# ---------------------------------------------------------------------------
# POST /tasks/{id}/publish - đẩy issue lên CVAT (nút "Đẩy issue" trong tab QA/QC)
# ---------------------------------------------------------------------------
def _post_publish(base_url: str, query: str = "", task_id: int = 1) -> dict[str, Any]:
    """Gọi ``POST /tasks/{id}/publish`` và trả payload JSON (assert ``200``)."""
    return _get_json(f"{base_url}/tasks/{task_id}/publish{query}", "POST")


def _fake_cvat_client(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    """Thay client CVAT thật bằng client giả để test không gọi mạng."""
    client = FakeClient()
    monkeypatch.setattr(QAQCService, "_create_cvat_client", lambda self: client)
    return client


def test_publish_params_defaults() -> None:
    """``_publish_params`` có mặc định khớp cờ của CLI ``qaqc publish``."""
    params = QAQCHandler._publish_params({})

    assert params["severity"] == "error"
    assert params["dry_run"] is False
    assert params["reopen_resolved"] is True
    assert params["resolve_stale"] is False
    assert params["post_details"] is True
    assert params["max_issues_per_job"] == 50
    assert params["max_issues_total"] == 0


def test_publish_params_from_query_string() -> None:
    """``_publish_params`` đọc tham số từ query (``true``/``false``/``1``/``0``)."""
    params = QAQCHandler._publish_params(
        {
            "severity": ["warning"],
            "dry_run": ["true"],
            "reopen_resolved": ["false"],
            "resolve_stale": ["1"],
            "post_details": ["0"],
            "max_issues_per_job": ["3"],
            "max_issues_total": ["7"],
        }
    )

    assert params["severity"] == "warning"
    assert params["dry_run"] is True
    assert params["reopen_resolved"] is False
    assert params["resolve_stale"] is True
    assert params["post_details"] is False
    assert params["max_issues_per_job"] == 3
    assert params["max_issues_total"] == 7


def test_publish_params_rejects_bad_integer() -> None:
    """``?max_issues_per_job=abc`` → ``400`` nêu rõ tên tham số sai."""
    with pytest.raises(ServiceError) as excinfo:
        QAQCHandler._publish_params({"max_issues_per_job": ["abc"]})

    assert "max_issues_per_job" in str(excinfo.value)
    assert excinfo.value.status == HTTPStatus.BAD_REQUEST


def test_parse_publish_severity() -> None:
    """``severity=`` hợp lệ (kể cả hoa/thường) được parse, giá trị lạ bị từ chối."""
    assert QAQCService.parse_publish_severity("WARNING").value == "warning"
    assert QAQCService.parse_publish_severity("info").value == "info"

    for bad in ("", "nghiem-trong"):
        with pytest.raises(ServiceError) as excinfo:
            QAQCService.parse_publish_severity(bad)
        assert "severity" in str(excinfo.value)
        assert "error, warning, info" in str(excinfo.value)


def test_get_publish_is_method_not_allowed(http_base_url: str) -> None:
    """``GET /tasks/{id}/publish`` → ``405`` kèm gợi ý dùng ``POST``."""
    status, _, body = _request(f"{http_base_url}/tasks/1/publish")

    assert status == HTTPStatus.METHOD_NOT_ALLOWED
    assert "POST" in json.loads(body.decode("utf-8"))["error"]


def test_publish_in_demo_mode_returns_conflict(http_base_url: str) -> None:
    """Chế độ ``--demo`` không có CVAT để ghi → ``409`` kèm cách sửa."""
    status, _, body = _request(f"{http_base_url}/tasks/1/publish", "POST")

    assert status == HTTPStatus.CONFLICT
    message = json.loads(body.decode("utf-8"))["error"]
    assert "--demo" in message
    assert "python -m qaqc serve" in message


def test_publish_creates_issues(http_base_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """``POST /publish`` tạo issue trên CVAT rồi trả đủ số liệu cho UI hiển thị."""
    client = _fake_cvat_client(monkeypatch)

    payload = _post_publish(http_base_url, "?severity=info")

    assert payload["dry_run"] is False
    assert payload["min_severity"] == "info"
    assert payload["created"] > 0
    assert payload["published"] >= payload["created"]
    assert len(client.store) == payload["created"]
    assert payload["issues_total"] >= payload["created"]
    assert payload["items"]
    assert payload["summary"]
    assert client.closed is True


def test_publish_second_run_is_idempotent(
    http_base_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bấm "Đẩy issue" lần 2: không tạo trùng, chỉ báo ``skipped_existing``."""
    client = _fake_cvat_client(monkeypatch)

    first = _post_publish(http_base_url, "?severity=warning")
    second = _post_publish(http_base_url, "?severity=warning")

    assert first["created"] > 0
    assert second["created"] == 0
    assert second["skipped_existing"] == first["created"]
    assert len(client.store) == first["created"]


def test_publish_dry_run_writes_nothing(
    http_base_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``?dry_run=true`` chỉ mô phỏng: báo số issue sẽ tạo nhưng không ghi gì."""
    client = _fake_cvat_client(monkeypatch)

    payload = _post_publish(http_base_url, "?severity=info&dry_run=true")

    assert payload["dry_run"] is True
    assert payload["created"] > 0
    assert client.store == []


def test_publish_without_details_posts_no_comment(
    http_base_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``?post_details=false`` không ghi comment JSON chi tiết."""
    client = _fake_cvat_client(monkeypatch)

    _post_publish(http_base_url, "?severity=error&post_details=false")

    assert client.comments.comments == []


def test_publish_uses_only_filter_of_report(
    http_base_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``?only=`` tác động cả lần chạy publish (dùng chung tham số với ``report``)."""
    _fake_cvat_client(monkeypatch)

    payload = _post_publish(http_base_url, "?severity=info&only=invalid_size")
    report = _get_json(f"{http_base_url}/tasks/1/report?only=invalid_size&refresh=true")

    assert payload["issues_total"] == len(report["issues"])


def test_publish_rejects_unknown_severity(
    http_base_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``?severity=`` sai → ``400`` và liệt kê các giá trị hợp lệ."""
    _fake_cvat_client(monkeypatch)

    status, _, body = _request(f"{http_base_url}/tasks/1/publish?severity=nghiem-trong", "POST")

    assert status == HTTPStatus.BAD_REQUEST
    message = json.loads(body.decode("utf-8"))["error"]
    assert "severity" in message
    assert "error, warning, info" in message


def test_publish_bad_integer_returns_400(http_base_url: str) -> None:
    """``?max_issues_per_job=abc`` → ``400`` (không chạy QA/QC)."""
    status, _, body = _request(f"{http_base_url}/tasks/1/publish?max_issues_per_job=abc", "POST")

    assert status == HTTPStatus.BAD_REQUEST
    assert "max_issues_per_job" in json.loads(body.decode("utf-8"))["error"]


def test_health_advertises_publish_support() -> None:
    """``/health`` liệt kê endpoint publish + cho biết có ghi được lên CVAT không."""
    demo = QAQCService(ServiceOptions(demo=True)).health()
    assert "/tasks/{id}/publish" in demo["data_endpoints"]
    assert demo["publish_supported"] is False

    real = QAQCService(ServiceOptions(host="http://localhost:8080", token="x")).health()
    assert real["publish_supported"] is True
