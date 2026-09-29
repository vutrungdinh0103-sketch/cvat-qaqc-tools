"""QA/QC HTTP service chạy trên localhost (**không cần Docker, không cần FastAPI**).

Service dùng ``http.server`` của stdlib nên bật lên bằng đúng một lệnh::

    python -m qaqc serve --demo          # xem thử ngay với dữ liệu mẫu
    python -m qaqc serve                 # chạy thật với CVAT (đọc .env)

Endpoint **trùng khớp** với thứ mà plugin CVAT UI đang gọi
(``cvat-ui/plugins/qaqc/src/ts/service-client.ts``)::

    GET  /                              → trang HTML (xem :mod:`qaqc.webui`)
    GET  /health                        → trạng thái + nguồn dữ liệu
    GET  /tasks/{id}/report             → báo cáo JSON (có cache)
    GET  /tasks/{id}/report.csv         → báo cáo CSV (mở bằng Excel)
    POST /tasks/{id}/run                → chạy QA/QC mới rồi trả báo cáo JSON

Tham số query dùng chung: ``rules=`` (đường dẫn hoặc tên ngắn như ``driving_v1``),
``only=``/``disable=`` (danh sách rule, phân tách bằng dấu phẩy), ``refresh=true``
(bỏ qua cache).

CORS được bật (``Access-Control-Allow-Origin: *``) vì CVAT UI thường chạy ở
origin khác (``http://localhost:8080``); service mặc định chỉ bind ``127.0.0.1``.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import threading
import time
import webbrowser
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlparse

from . import SCHEMA_VERSION, __version__
from .config import CVATConfig, RuleConfig, load_rule_config
from .demo import build_demo_task
from .engine import QAQCEngine
from .model import TaskData
from .normalize import build_task_data
from .report import CSV_FIELDS, QAReport
from .rules import registered_rule_ids, rule_ids_for_level, rule_level
from .webui import INDEX_HTML

LOGGER = logging.getLogger("cvat_qaqc.service")

#: Route API dùng chung với plugin CVAT UI: ``/tasks/<id>/<action>``.
TASK_ROUTE = re.compile(r"^/tasks/(?P<task_id>\d+)/(?P<action>report|report\.csv|run)$")

#: Thư mục file cấu hình rule (cho phép truyền tên ngắn: ``?rules=driving_v1``).
RULES_DIR = Path("rules")

#: Phần mở rộng file cấu hình rule được chấp nhận qua API.
RULE_SUFFIXES = (".yaml", ".yml")

#: Cổng mặc định của service.
DEFAULT_PORT = 8081


class ServiceError(Exception):
    """Lỗi khi phục vụ request, kèm mã HTTP sẽ trả về client."""

    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class ServiceOptions:
    """Cấu hình service (do CLI ``python -m qaqc serve`` tạo ra)."""

    #: File YAML rule; ``None`` = dùng mặc định ``rules/driving_v1.yaml`` nếu có.
    rules: str | None = None
    #: Chỉ chạy các rule này (danh sách phân tách bằng dấu phẩy).
    only: str | None = None
    #: Tắt các rule này (danh sách phân tách bằng dấu phẩy).
    disable: str | None = None
    #: Chỉ chạy rule thuộc cấp độ QA này (``1`` = Overall/Completeness, ``2`` = Detailed).
    level: int | None = None
    #: Giới hạn số lỗi mỗi báo cáo (0 = không giới hạn).
    max_issues: int = 0
    #: Đọc dữ liệu task từ file JSON thay vì gọi CVAT (chạy offline).
    source_file: str | None = None
    #: Dùng dữ liệu mẫu dựng sẵn (:mod:`qaqc.demo`) - không cần CVAT.
    demo: bool = False
    #: File ``.env`` chứa thông tin kết nối CVAT.
    env_file: str = ".env"
    #: Ghi đè ``CVAT_HOST``.
    host: str | None = None
    #: Ghi đè ``CVAT_USER``.
    user: str | None = None
    #: Ghi đè ``CVAT_PASS``.
    password: str | None = None
    #: Ghi đè ``CVAT_TOKEN``.
    token: str | None = None
    #: Thời gian cache báo cáo (giây); ``0`` = không cache.
    cache_ttl: float = 30.0

    def data_source_label(self) -> str:
        """Mô tả ngắn nguồn dữ liệu (hiển thị trên UI và log)."""
        if self.demo:
            return "dữ liệu mẫu (--demo)"
        if self.source_file:
            return f"file {self.source_file}"
        location = self.host or f"{self.env_file} / biến môi trường CVAT_HOST"
        return f"CVAT - {location}"


def split_csv(value: str | Sequence[str] | None) -> list[str] | None:
    """Tách chuỗi/danh sách phân tách bằng dấu phẩy (``None`` nếu rỗng)."""
    if not value:
        return None
    items = value.split(",") if isinstance(value, str) else list(value)
    cleaned = [str(item).strip() for item in items]
    return [item for item in cleaned if item] or None


def load_task_data_from_file(path: str | Path, task_id: int) -> TaskData:
    """Nạp :class:`TaskData` từ file JSON (chạy offline, không cần CVAT server).

    File dùng đúng dạng payload của CVAT (``task``/``frames``/``labels``/
    ``annotations``/``jobs``) - xem ``tests/fixtures/task_anomalies.json``.

    :param task_id: ID task người dùng yêu cầu (chỉ dùng khi file không khai báo).
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise ServiceError(f"Không tìm thấy file dữ liệu: {file_path}", HTTPStatus.NOT_FOUND)

    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ServiceError(f"File dữ liệu không phải JSON hợp lệ: {file_path} ({exc})") from exc

    if not isinstance(payload, dict):
        raise ServiceError(f"File dữ liệu phải là JSON object: {file_path}")

    task = payload.get("task") or {}
    file_task_id = task.get("id")
    if file_task_id is not None and int(file_task_id) != task_id:
        LOGGER.info(
            "File %s chứa task #%s (yêu cầu #%s) - dùng dữ liệu trong file.",
            file_path,
            file_task_id,
            task_id,
        )

    return build_task_data(
        task_id=int(file_task_id or task_id),
        task_name=task.get("name"),
        size=task.get("size"),
        dimension=task.get("dimension"),
        frames_info=payload.get("frames") or [],
        labels=payload.get("labels") or [],
        annotations=payload.get("annotations") or {},
        jobs=payload.get("jobs") or [],
    )


class QAQCService:
    """Chạy QA/QC cho một task và cache báo cáo theo ``(task, bộ rule)``.

    Service là thread-safe (``ThreadingHTTPServer`` phục vụ mỗi request một luồng)
    nên mọi truy cập cache đều đi qua ``self._lock``.
    """

    def __init__(self, options: ServiceOptions) -> None:
        self.options = options
        self._lock = threading.Lock()
        self._cache: dict[tuple[int, str], tuple[float, QAReport]] = {}

    # ------------------------------------------------------------------
    # Dữ liệu đầu vào
    # ------------------------------------------------------------------
    def load_task_data(self, task_id: int) -> TaskData:
        """Lấy dữ liệu task theo nguồn đã cấu hình (demo → file → CVAT)."""
        options = self.options

        if options.demo:
            LOGGER.info("Dùng dữ liệu mẫu cho task #%s (không gọi CVAT).", task_id)
            return build_demo_task(task_id)

        if options.source_file:
            LOGGER.info("Đọc dữ liệu task #%s từ file %s.", task_id, options.source_file)
            return load_task_data_from_file(options.source_file, task_id)

        from .data_source import CVATDataSource  # import muộn: cần cvat_sdk

        config = CVATConfig.from_env(
            env_file=options.env_file,
            host=options.host,
            user=options.user,
            password=options.password,
            token=options.token,
        )
        client = config.create_client()
        try:
            return CVATDataSource(client).fetch_task(task_id)
        finally:
            client.close()

    # ------------------------------------------------------------------
    # Cấu hình rule
    # ------------------------------------------------------------------
    @staticmethod
    def resolve_rules_file(name: str | None) -> str | None:
        """Chuyển ``?rules=`` (đường dẫn hoặc tên ngắn) thành đường dẫn file YAML.

        :raises ServiceError: nếu file không tồn tại hoặc đường dẫn không hợp lệ.
        """
        if not name:
            return None

        candidate = Path(name)
        if ".." in candidate.parts:
            raise ServiceError(f"Đường dẫn file rule không hợp lệ: {name!r}")

        if candidate.is_file() and candidate.suffix.lower() in RULE_SUFFIXES:
            return str(candidate)

        stem = candidate.stem if candidate.suffix.lower() in RULE_SUFFIXES else name
        for suffix in RULE_SUFFIXES:
            short = RULES_DIR / f"{stem}{suffix}"
            if short.is_file():
                return str(short)

        raise ServiceError(
            f"Không tìm thấy file rule {name!r}. Truyền đường dẫn file .yaml/.yml "
            f"hoặc tên ngắn của file trong thư mục {RULES_DIR}/."
        )

    def rule_config(
        self,
        *,
        rules: str | None = None,
        only: str | None = None,
        disable: str | None = None,
        level: int | None = None,
    ) -> RuleConfig:
        """Nạp cấu hình rule hiệu lực (mặc định của service + ghi đè từ query).

        :param level: chỉ chạy rule thuộc cấp độ QA này (1 = Overall, 2 = Detailed);
            nếu truyền kèm ``only`` thì lấy phần giao của hai điều kiện.
        :raises ServiceError: nếu ``level`` không khớp rule nào trong ``only``.
        """
        resolved = self.resolve_rules_file(rules or self.options.rules)
        base = RuleConfig.load(resolved) if resolved else load_rule_config(None)
        base.validate_rule_ids(registered_rule_ids())

        only_ids = split_csv(only) if only is not None else split_csv(self.options.only)
        disabled_ids = (
            split_csv(disable) if disable is not None else split_csv(self.options.disable)
        )

        level_value = level if level is not None else self.options.level
        if level_value is not None:
            level_ids = rule_ids_for_level(int(level_value))
            if only_ids:
                selected = [rule_id for rule_id in only_ids if rule_id in set(level_ids)]
                if not selected:
                    raise ServiceError(
                        f"level={level_value} không khớp rule nào trong only="
                        f"{','.join(only_ids)}. Rule cấp độ {level_value}: "
                        f"{', '.join(level_ids)}"
                    )
                only_ids = selected
            else:
                only_ids = level_ids

        if not only_ids and not disabled_ids:
            return base
        return base.with_overrides(only=only_ids, disabled=disabled_ids, name_suffix="serve")

    # ------------------------------------------------------------------
    # Báo cáo
    # ------------------------------------------------------------------
    def build_report(
        self,
        task_id: int,
        *,
        rules: str | None = None,
        only: str | None = None,
        disable: str | None = None,
        level: int | None = None,
        refresh: bool = False,
    ) -> QAReport:
        """Chạy QA/QC cho ``task_id`` (dùng cache nếu còn hạn và ``refresh=False``)."""
        config = self.rule_config(rules=rules, only=only, disable=disable, level=level)
        key = (task_id, config.config_hash)

        if not refresh and self.options.cache_ttl > 0:
            cached = self._cached(key)
            if cached is not None:
                LOGGER.info("Trả báo cáo task #%s từ cache.", task_id)
                return cached

        data = self._load_task_data_or_error(task_id)
        report = QAQCEngine(config, max_issues=self.options.max_issues).run(data)

        if self.options.cache_ttl > 0:
            with self._lock:
                self._cache[key] = (time.monotonic(), report)
        return report

    def report_payload(self, task_id: int, **kwargs: Any) -> dict[str, Any]:
        """Báo cáo dạng dict JSON-serializable (schema ``qaqc/1``)."""
        return self.build_report(task_id, **kwargs).to_json_dict()

    def report_csv(self, task_id: int, **kwargs: Any) -> str:
        """Báo cáo dạng CSV (đúng các cột như ``qaqc run -o report.csv``)."""
        report = self.build_report(task_id, **kwargs)
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(report.to_csv_rows())
        return buffer.getvalue()

    def clear_cache(self) -> None:
        """Xoá cache báo cáo (dùng cho test)."""
        with self._lock:
            self._cache.clear()

    def health(self) -> dict[str, Any]:
        """Thông tin trạng thái service (dùng cho ``GET /health`` và UI)."""
        with self._lock:
            cached_tasks = sorted({key[0] for key in self._cache})
        config = self.rule_config()
        enabled = config.enabled_rule_ids()
        return {
            "status": "ok",
            "version": __version__,
            "schema_version": SCHEMA_VERSION,
            "data_source": self.options.data_source_label(),
            "rules": config.name,
            "rules_file": config.source_path,
            "rules_enabled": enabled,
            #: Cấp độ QA đang lọc (``None`` = chạy mọi cấp độ) + rule đang bật theo cấp độ.
            "level": self.options.level,
            "rules_by_level": {
                str(level): [rule_id for rule_id in enabled if rule_level(rule_id) == level]
                for level in (1, 2)
            },
            "cache_ttl": self.options.cache_ttl,
            "cached_tasks": cached_tasks,
            "data_endpoints": ["/tasks/{id}/report", "/tasks/{id}/report.csv", "/tasks/{id}/run"],
        }

    # ------------------------------------------------------------------
    # Nội bộ
    # ------------------------------------------------------------------
    def _cached(self, key: tuple[int, str]) -> QAReport | None:
        """Báo cáo còn hạn trong cache (``None`` nếu không có/hết hạn)."""
        with self._lock:
            entry = self._cache.get(key)
        if entry is None:
            return None
        created, report = entry
        if time.monotonic() - created >= self.options.cache_ttl:
            return None
        return report

    def _load_task_data_or_error(self, task_id: int) -> TaskData:
        """Bọc :meth:`load_task_data` để mọi lỗi đều thành :class:`ServiceError`."""
        try:
            return self.load_task_data(task_id)
        except ServiceError:
            raise
        except ImportError as exc:
            raise ServiceError(
                "Chưa cài 'cvat-sdk' nên không đọc được dữ liệu từ CVAT. Cài bằng "
                "'python -m pip install -r requirements.txt' hoặc chạy thử với "
                "'python -m qaqc serve --demo'.",
                HTTPStatus.INTERNAL_SERVER_ERROR,
            ) from exc
        except ValueError as exc:
            raise ServiceError(f"Lỗi cấu hình: {exc}") from exc
        except Exception as exc:
            raise ServiceError(
                f"Không lấy được dữ liệu task #{task_id}: {type(exc).__name__}: {exc}",
                HTTPStatus.BAD_GATEWAY,
            ) from exc


def parse_bool(value: str | None) -> bool:
    """``true/1/yes/on`` (không phân biệt hoa thường) → ``True``."""
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class QAQCServer(ThreadingHTTPServer):
    """HTTP server mang theo :class:`QAQCService` dùng chung cho mọi request."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], service: QAQCService) -> None:
        super().__init__(address, QAQCHandler)
        self.service = service


class QAQCHandler(BaseHTTPRequestHandler):
    """Xử lý request của service: trang HTML + API JSON/CSV."""

    server_version = f"qaqc/{__version__}"
    protocol_version = "HTTP/1.1"

    @property
    def service(self) -> QAQCService:
        """Service dùng chung (được gắn vào server khi khởi tạo)."""
        return cast("QAQCServer", self.server).service

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    def do_OPTIONS(self) -> None:
        """Trả lời CORS preflight (plugin CVAT UI chạy ở origin khác)."""
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        """Phục vụ trang chủ, ``/health`` và các endpoint báo cáo."""
        self._guard(self._route_get)

    def do_POST(self) -> None:
        """``POST /tasks/{id}/run`` - chạy QA/QC mới."""
        self._guard(self._route_post)

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------
    def _route_get(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        if path in {"/", "/index.html"}:
            self._send_body(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/health":
            self._send_json(self.service.health())
            return
        if path == "/favicon.ico":
            self._send_body(b"", "image/x-icon", HTTPStatus.NO_CONTENT)
            return

        match = TASK_ROUTE.match(path)
        if match is None:
            self._send_json({"error": f"Endpoint không tồn tại: {path}"}, HTTPStatus.NOT_FOUND)
            return

        task_id = int(match.group("task_id"))
        action = match.group("action")
        params = self._params(query)

        if action == "run":
            self._send_json(
                {"error": "Chạy QA/QC bằng POST /tasks/{id}/run."},
                HTTPStatus.METHOD_NOT_ALLOWED,
            )
            return
        if action == "report.csv":
            # utf-8-sig để Excel hiển thị đúng tiếng Việt
            content = self.service.report_csv(task_id, **params)
            self._send_body(content.encode("utf-8-sig"), "text/csv; charset=utf-8")
            return

        self._send_json(self.service.report_payload(task_id, **params))

    def _route_post(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = parse_qs(parsed.query)

        match = TASK_ROUTE.match(path)
        if match is None or match.group("action") != "run":
            self._send_json({"error": f"Endpoint không tồn tại: {path}"}, HTTPStatus.NOT_FOUND)
            return

        params = self._params(query)
        params["refresh"] = True  # POST = luôn chạy lại, không dùng cache
        self._send_json(self.service.report_payload(int(match.group("task_id")), **params))

    @staticmethod
    def _params(query: dict[str, list[str]]) -> dict[str, Any]:
        """Tham số ảnh hưởng tới kết quả QA/QC, lấy từ query string."""

        def first(name: str) -> str | None:
            values = query.get(name)
            return values[0] if values else None

        def level_param() -> int | None:
            """``level`` chỉ nhận 1 hoặc 2 (rỗng/0 = không lọc)."""
            raw = first("level")
            if raw is None or not str(raw).strip() or str(raw).strip() == "0":
                return None
            try:
                value = int(str(raw).strip())
            except ValueError as exc:
                raise ServiceError(f"Tham số 'level' phải là 1 hoặc 2, nhận được: {raw!r}") from exc
            if value not in (1, 2):
                raise ServiceError(f"Tham số 'level' phải là 1 hoặc 2, nhận được: {raw!r}")
            return value

        return {
            "rules": first("rules"),
            "only": first("only"),
            "disable": first("disable"),
            "level": level_param(),
            "refresh": parse_bool(first("refresh")),
        }

    # ------------------------------------------------------------------
    # Gửi response
    # ------------------------------------------------------------------
    def _guard(self, action: Callable[[], None]) -> None:
        """Bọc một lượt xử lý để mọi lỗi đều trả JSON + mã HTTP rõ ràng."""
        try:
            action()
        except ServiceError as exc:
            self._send_json({"error": str(exc)}, exc.status)
        except ValueError as exc:
            self._send_json({"error": f"Lỗi cấu hình: {exc}"}, HTTPStatus.BAD_REQUEST)
        except BrokenPipeError:
            LOGGER.debug("Client đóng kết nối sớm: %s", self.path)
        except Exception as exc:
            LOGGER.exception("Lỗi không mong đợi khi xử lý %s", self.path)
            self._send_json(
                {"error": f"{type(exc).__name__}: {exc}"},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        """Gửi JSON (UTF-8, giữ nguyên ký tự tiếng Việt)."""
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self._send_body(body, "application/json; charset=utf-8", status)

    def _send_body(
        self,
        body: bytes,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        """Gửi body kèm CORS và ``Content-Length`` (bắt buộc với HTTP/1.1)."""
        self.send_response(status)
        self._send_cors_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _send_cors_headers(self) -> None:
        """Cho phép CVAT UI (origin khác) đọc kết quả."""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def log_message(self, format: str, *args: Any) -> None:
        """Chuyển log truy cập sang ``logging`` (không in rối màn hình CLI)."""
        LOGGER.debug("%s - %s", self.address_string(), format % args)


def serve(
    options: ServiceOptions,
    *,
    bind_host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    open_browser: bool = False,
) -> None:
    """Chạy QA/QC service cho tới khi người dùng nhấn Ctrl+C.

    :param options: nguồn dữ liệu + cấu hình rule (xem :class:`ServiceOptions`).
    :param bind_host: địa chỉ bind; mặc định ``127.0.0.1`` (chỉ truy cập từ máy local).
    :param port: cổng lắng nghe (``0`` = tự chọn cổng trống, dùng cho test).
    :param open_browser: tự mở trình duyệt tại trang QA/QC.
    """
    service = QAQCService(options)

    with QAQCServer((bind_host, port), service) as httpd:
        host, actual_port = httpd.server_address[0], httpd.server_address[1]
        base_url = f"http://{host}:{actual_port}"

        print("===== QA/QC service =====")
        print(f"Giao diện    : {base_url}/")
        print(f"Trạng thái   : {base_url}/health")
        print(f"Báo cáo JSON : {base_url}/tasks/<task_id>/report")
        print(f"Báo cáo CSV  : {base_url}/tasks/<task_id>/report.csv")
        print(f"Chạy QA/QC   : POST {base_url}/tasks/<task_id>/run")
        print(f"Nguồn dữ liệu: {options.data_source_label()}")
        print(f"Cache báo cáo: {options.cache_ttl:g} giây")
        print("Nhấn Ctrl+C để dừng service.", flush=True)

        if open_browser:
            webbrowser.open(base_url)

        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nĐã dừng QA/QC service.")
