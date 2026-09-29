"""CLI của tool QA/QC (``python -m qaqc ...``).

Lệnh con:

- ``run``      : chạy QA/QC và in/kết xuất báo cáo (JSON/CSV/JUnit).
- ``publish``  : chạy QA/QC rồi đẩy lỗi thành issue trên CVAT (idempotent).
- ``rules``    : liệt kê rule + tham số (dạng text hoặc JSON).
- ``validate`` : kiểm tra tính hợp lệ của file cấu hình rule YAML.
- ``serve``    : mở QA/QC service trên localhost (web UI + API cho plugin CVAT UI).
- ``legacy``   : chạy CLI cũ (giống ``python checker.py``) qua module mới.

Mã thoát (giống CLI cũ): ``0`` thành công, ``1`` có lỗi >= ngưỡng ``--fail-on``,
``2`` lỗi cấu hình, ``3`` lỗi kết nối/API CVAT.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import yaml

from . import __version__
from .config import DEFAULT_RULES_FILE, CVATConfig, RuleConfig, load_rule_config
from .engine import QAQCEngine
from .legacy import configure_logging
from .legacy import main as legacy_main
from .model import Severity, TaskData
from .report import (
    EXIT_API_ERROR,
    EXIT_CONFIG_ERROR,
    EXIT_OK,
    QAReport,
)
from .rules import registered_rule_ids, rule_catalog, rule_ids_for_level
from .service import DEFAULT_PORT, ServiceOptions, serve

LOGGER = logging.getLogger("cvat_qaqc.cli")


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    """Thêm các tuỳ chọn dùng chung cho ``run``/``publish``."""
    parser.add_argument("task_id", type=int, help="ID của CVAT task")
    parser.add_argument(
        "--job",
        type=int,
        help="Chạy QA/QC cho một job cụ thể (chỉ annotation trong job đó)",
    )
    parser.add_argument(
        "-r",
        "--rules",
        default=None,
        help=f"File cấu hình rule YAML (mặc định: {DEFAULT_RULES_FILE} nếu tồn tại)",
    )
    parser.add_argument(
        "--only",
        help="Chỉ chạy các rule này (phân tách bằng dấu phẩy)",
    )
    parser.add_argument(
        "--level",
        type=int,
        choices=(1, 2),
        help="Chỉ chạy rule thuộc cấp độ này (1 = Overall/Completeness, 2 = Detailed)",
    )
    parser.add_argument(
        "--disable",
        help="Tắt các rule này (phân tách bằng dấu phẩy)",
    )
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="RULE.KEY=VALUE",
        help="Ghi đè tham số rule, ví dụ --param duplicate_bbox.iou_threshold=0.9 "
        "(lặp lại được nhiều lần)",
    )
    parser.add_argument(
        "--iou",
        type=float,
        help="Tiện ích: ghi đè duplicate_bbox.iou_threshold",
    )
    parser.add_argument(
        "--fail-on",
        choices=("error", "warning", "info", "none"),
        default="error",
        help="Ngưỡng mức độ làm CLI trả mã thoát 1 (mặc định: error)",
    )
    parser.add_argument(
        "--max-issues",
        type=int,
        default=0,
        help="Giới hạn số lỗi trong báo cáo (0 = không giới hạn)",
    )
    parser.add_argument("--env-file", default=".env", help="File .env chứa thông tin kết nối CVAT")
    parser.add_argument("--host", help="URL CVAT server (mặc định: CVAT_HOST)")
    parser.add_argument("--user", help="Tài khoản CVAT (mặc định: CVAT_USER)")
    parser.add_argument("--password", help="Mật khẩu CVAT (mặc định: CVAT_PASS)")
    parser.add_argument(
        "--token", help="Access token CVAT (mặc định: CVAT_TOKEN) - khuyến nghị cho CI"
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Tăng mức độ log (-v: INFO, -vv: DEBUG)",
    )


def build_parser() -> argparse.ArgumentParser:
    """Tạo parser cho ``python -m qaqc``."""
    parser = argparse.ArgumentParser(
        prog="python -m qaqc",
        description=(
            "QA/QC annotation CVAT: kiểm tra kích thước/tràn khung/trùng lặp/thiếu "
            "nhãn - attribute, kết xuất báo cáo và (tuỳ chọn) tạo issue trên CVAT."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"qaqc {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run", help="Chạy QA/QC và kết xuất báo cáo", aliases=["check"]
    )
    _add_common_options(run_parser)
    run_parser.add_argument(
        "-o",
        "--output",
        help="File báo cáo (.json/.csv/.xml). Không truyền thì chỉ in ra màn hình.",
    )
    run_parser.add_argument(
        "--format",
        choices=("json", "csv", "junit"),
        help="Định dạng báo cáo (mặc định suy ra từ phần mở rộng file)",
    )
    run_parser.add_argument(
        "--print-json",
        action="store_true",
        help="In toàn bộ báo cáo JSON ra stdout (tiện cho pipeline/CI)",
    )
    run_parser.set_defaults(func=cmd_run)

    publish_parser = subparsers.add_parser("publish", help="Chạy QA/QC rồi tạo issue trên CVAT")
    _add_common_options(publish_parser)
    publish_parser.add_argument("-o", "--output", help="File báo cáo kèm theo (.json/.csv/.xml)")
    publish_parser.add_argument(
        "--format", choices=("json", "csv", "junit"), help="Định dạng file báo cáo"
    )
    publish_parser.add_argument(
        "--publish-severity",
        choices=("error", "warning", "info"),
        default="error",
        help="Chỉ tạo issue cho lỗi có mức độ >= giá trị này",
    )
    publish_parser.add_argument(
        "--max-issues-per-job",
        type=int,
        default=50,
        help="Giới hạn số issue tạo mới cho mỗi job (0 = không giới hạn)",
    )
    publish_parser.add_argument(
        "--max-issues-total",
        type=int,
        default=0,
        help="Giới hạn tổng số issue tạo mới trong lần chạy (0 = không giới hạn)",
    )
    publish_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Chỉ mô phỏng, không gọi API ghi lên CVAT",
    )
    publish_parser.add_argument(
        "--reopen-resolved",
        action="store_true",
        help="Mở lại issue đã resolved nếu lỗi vẫn còn",
    )
    publish_parser.add_argument(
        "--resolve-stale",
        action="store_true",
        help="Tự đánh dấu resolved cho issue cũ không còn lỗi",
    )
    publish_parser.add_argument(
        "--no-details", action="store_true", help="Không ghi comment JSON chi tiết"
    )
    publish_parser.set_defaults(func=cmd_publish)

    rules_parser = subparsers.add_parser("rules", help="Liệt kê các rule có sẵn")
    rules_parser.add_argument("--json", action="store_true", help="In ra JSON")
    rules_parser.add_argument(
        "--level",
        type=int,
        choices=(1, 2),
        help="Chỉ liệt kê rule thuộc cấp độ này (1 = Overall/Completeness, 2 = Detailed)",
    )
    rules_parser.set_defaults(func=cmd_rules)

    validate_parser = subparsers.add_parser("validate", help="Kiểm tra file cấu hình rule YAML")
    validate_parser.add_argument("path", help="Đường dẫn file YAML cần kiểm tra")
    validate_parser.set_defaults(func=cmd_validate)

    legacy_parser = subparsers.add_parser(
        "legacy",
        help="Chạy CLI cũ (tương đương 'python checker.py') qua module mới",
    )
    serve_parser = subparsers.add_parser(
        "serve",
        help="Chạy QA/QC service trên localhost (web UI + API cho plugin CVAT UI)",
    )
    serve_parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="Cổng lắng nghe của service"
    )
    serve_parser.add_argument(
        "--bind-host",
        default="127.0.0.1",
        help="Địa chỉ bind (127.0.0.1 = chỉ truy cập được từ máy đang chạy)",
    )
    serve_parser.add_argument("--open", action="store_true", help="Tự mở trình duyệt")
    serve_parser.add_argument(
        "--demo", action="store_true", help="Dùng dữ liệu mẫu dựng sẵn (không cần CVAT)"
    )
    serve_parser.add_argument(
        "--source-file", help="Đọc dữ liệu task từ file JSON (dạng annotations của CVAT)"
    )
    serve_parser.add_argument(
        "-r",
        "--rules",
        default=None,
        help=f"File cấu hình rule YAML (mặc định: {DEFAULT_RULES_FILE} nếu tồn tại)",
    )
    serve_parser.add_argument("--only", help="Chỉ chạy các rule này (phân tách bằng dấu phẩy)")
    serve_parser.add_argument(
        "--level",
        type=int,
        choices=(1, 2),
        help="Chỉ chạy rule thuộc cấp độ này (1 = Overall/Completeness, 2 = Detailed)",
    )
    serve_parser.add_argument("--disable", help="Tắt các rule này (phân tách bằng dấu phẩy)")
    serve_parser.add_argument(
        "--max-issues", type=int, default=0, help="Giới hạn số lỗi mỗi báo cáo (0 = vô hạn)"
    )
    serve_parser.add_argument(
        "--cache-ttl", type=float, default=30.0, help="Cache báo cáo trong bao nhiêu giây (0 = tắt)"
    )
    serve_parser.add_argument(
        "--env-file", default=".env", help="File .env chứa thông tin kết nối CVAT"
    )
    serve_parser.add_argument("--host", help="URL CVAT server (mặc định: CVAT_HOST)")
    serve_parser.add_argument("--user", help="Tài khoản CVAT (mặc định: CVAT_USER)")
    serve_parser.add_argument("--password", help="Mật khẩu CVAT (mặc định: CVAT_PASS)")
    serve_parser.add_argument("--token", help="Access token CVAT (mặc định: CVAT_TOKEN)")
    serve_parser.add_argument(
        "-v", "--verbose", action="count", default=0, help="Tăng mức độ log (-v: INFO, -vv: DEBUG)"
    )
    serve_parser.set_defaults(func=cmd_serve)

    legacy_parser.set_defaults(func=cmd_legacy)

    return parser


# ---------------------------------------------------------------------------
# Tiện ích dùng chung
# ---------------------------------------------------------------------------
def _split_csv(value: str | None) -> list[str] | None:
    """Tách chuỗi phân tách bằng dấu phẩy thành list (``None`` nếu rỗng)."""
    if not value:
        return None
    items = [item.strip() for item in value.split(",")]
    return [item for item in items if item] or None


def _only_rule_ids(args: argparse.Namespace) -> list[str] | None:
    """Danh sách rule sẽ chạy theo ``--only`` và ``--level`` (``None`` = theo file YAML).

    ``--level`` (1 = Overall/Completeness, 2 = Detailed) lọc theo cấp độ QA của rule;
    nếu dùng kèm ``--only`` thì lấy phần giao của hai điều kiện.

    :raises ValueError: nếu ``--level`` không khớp rule nào trong ``--only``.
    """
    explicit = _split_csv(getattr(args, "only", None))
    level = getattr(args, "level", None)
    if level is None:
        return explicit

    level_ids = rule_ids_for_level(int(level))
    if explicit is None:
        return level_ids

    selected = [rule_id for rule_id in explicit if rule_id in set(level_ids)]
    if not selected:
        raise ValueError(
            f"--level {level} không khớp rule nào trong --only "
            f"({', '.join(explicit)}). Rule cấp độ {level}: {', '.join(level_ids)}"
        )
    return selected


def _parse_param_overrides(entries: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Phân tích các ``--param RULE.KEY=VALUE`` thành dict lồng nhau.

    Giá trị được parse bằng YAML nên dùng được số/bool/list, ví dụ::

        --param duplicate_bbox.iou_threshold=0.9
        --param required_attributes.labels.vehicle=[vehicle_type, color]
    """
    params: dict[str, dict[str, Any]] = {}
    for entry in entries:
        target, separator, raw_value = str(entry).partition("=")
        rule_id, dot, key = target.partition(".")
        if not separator or not dot or not rule_id.strip() or not key.strip():
            raise ValueError(
                f"--param phải có dạng RULE.KEY=VALUE (ví dụ "
                f"duplicate_bbox.iou_threshold=0.9), nhận được: {entry!r}"
            )
        try:
            value: Any = yaml.safe_load(raw_value)
        except yaml.YAMLError:
            value = raw_value
        params.setdefault(rule_id.strip(), {})[key.strip()] = value
    return params


def _rule_config_from_args(args: argparse.Namespace) -> RuleConfig:
    """Nạp file YAML + áp các ghi đè từ CLI thành cấu hình rule hiệu lực."""
    config = RuleConfig.load(args.rules) if args.rules else load_rule_config(None)
    config.validate_rule_ids(registered_rule_ids())

    # `--level` (và `--only`) chọn tập rule sẽ chạy trước khi áp các ghi đè khác.
    only_ids = _only_rule_ids(args)

    overrides = _parse_param_overrides(args.param)
    if args.iou is not None:
        overrides.setdefault("duplicate_bbox", {})["iou_threshold"] = args.iou

    if overrides:
        # Ghi đè có thể trỏ tới rule không tồn tại -> báo lỗi rõ ràng ngay.
        _unknown = sorted(set(overrides) - set(registered_rule_ids()))
        if _unknown:
            raise ValueError(
                "Rule trong --param không tồn tại: "
                + ", ".join(_unknown)
                + f". Danh sách rule hợp lệ: {', '.join(registered_rule_ids())}"
            )

    return config.with_overrides(
        only=only_ids,
        disabled=_split_csv(args.disable),
        extra_params=overrides,
        name_suffix="cli" if (only_ids or args.disable or overrides) else None,
    )


def _fail_on_severity(args: argparse.Namespace) -> Severity | None:
    """Chuyển ``--fail-on`` thành :class:`Severity` (``none`` -> ``None``)."""
    if args.fail_on == "none":
        return None
    return Severity.parse(args.fail_on, Severity.ERROR)


def _connection_config(args: argparse.Namespace) -> CVATConfig:
    """Tạo :class:`CVATConfig` từ tham số CLI/.env."""
    return CVATConfig.from_env(
        env_file=args.env_file,
        host=args.host,
        user=args.user,
        password=args.password,
        token=args.token,
    )


def _fetch_task_data(client: Any, args: argparse.Namespace) -> TaskData:
    """Tải dữ liệu task (hoặc job) từ CVAT."""
    from .data_source import CVATDataSource  # import muộn: cần cvat_sdk

    source = CVATDataSource(client)
    if args.job:
        data = source.fetch_job(args.job)
        if args.task_id and data.task_id != args.task_id:
            LOGGER.warning(
                "Job #%s thuộc task #%s (khác task_id #%s đã truyền).",
                args.job,
                data.task_id,
                args.task_id,
            )
        return data
    return source.fetch_task(args.task_id)


def _write_report_if_requested(report: QAReport, args: argparse.Namespace) -> None:
    """Ghi báo cáo ra file nếu người dùng truyền ``-o/--output``."""
    if getattr(args, "output", None):
        path = report.write(args.output, getattr(args, "format", None))
        print(f"Đã ghi báo cáo: {path}")


# ---------------------------------------------------------------------------
# Lệnh con
# ---------------------------------------------------------------------------
def cmd_run(args: argparse.Namespace) -> int:
    """``qaqc run``: chạy QA/QC và kết xuất báo cáo."""
    rules = _rule_config_from_args(args)
    LOGGER.info("\n".join(rules.summary_lines()))

    client = _connection_config(args).create_client()
    try:
        data = _fetch_task_data(client, args)
        report = QAQCEngine(rules, max_issues=args.max_issues).run(data)
    finally:
        client.close()

    print("===== Kết quả QA/QC =====")
    print("\n".join(report.summary_lines()))

    _write_report_if_requested(report, args)
    if args.print_json:
        print(json.dumps(report.to_json_dict(), ensure_ascii=False, indent=2))

    return report.exit_code(_fail_on_severity(args))


def cmd_publish(args: argparse.Namespace) -> int:
    """``qaqc publish``: chạy QA/QC rồi tạo issue trên CVAT."""
    from .publishers import CvatIssuePublisher  # import muộn: cần cvat_sdk

    rules = _rule_config_from_args(args)
    LOGGER.info("\n".join(rules.summary_lines()))

    client = _connection_config(args).create_client()
    try:
        data = _fetch_task_data(client, args)
        report = QAQCEngine(rules, max_issues=args.max_issues).run(data)
        _write_report_if_requested(report, args)

        publisher = CvatIssuePublisher(
            client,
            dry_run=args.dry_run,
            min_severity=Severity.parse(args.publish_severity, Severity.ERROR),
            max_issues_per_job=args.max_issues_per_job,
            max_issues_total=args.max_issues_total,
            reopen_resolved=args.reopen_resolved,
            resolve_stale=args.resolve_stale,
            post_details=not args.no_details,
        )
        result = publisher.publish(report, data=data)
    finally:
        client.close()

    print("===== Kết quả QA/QC =====")
    print("\n".join(report.summary_lines()))
    print("===== Kết quả publish =====")
    print("\n".join(result.summary_lines()))

    return report.exit_code(_fail_on_severity(args))


def cmd_rules(args: argparse.Namespace) -> int:
    """``qaqc rules``: liệt kê rule và tham số."""
    level = getattr(args, "level", None)
    catalog = rule_catalog()
    if level is not None:
        catalog = [item for item in catalog if item["level"] == level]

    if args.json:
        print(json.dumps(catalog, ensure_ascii=False, indent=2))
        return EXIT_OK

    scope = f" (cấp độ {level})" if level is not None else ""
    print(f"Tổng số rule{scope}: {len(catalog)}")
    current_group = None
    for item in catalog:
        if item["group"] != current_group:
            current_group = item["group"]
            print(f"\n== Nhóm {current_group} ==")
        print(f"- {item['rule_id']} (mặc định: {item['default_severity']}, level {item['level']})")
        print(f"    {item['description']}")
    print(
        "\nDùng 'python -m qaqc rules --json' để xem schema tham số chi tiết, "
        "'python -m qaqc rules --level 1' để xem riêng bộ rule Level 1, "
        "hoặc xem rules/driving_v1.yaml + rules/level1_v1.yaml để biết ví dụ cấu hình."
    )
    return EXIT_OK


def cmd_validate(args: argparse.Namespace) -> int:
    """``qaqc validate``: kiểm tra file cấu hình rule YAML."""
    from .rules import build_rule, get_rule_class

    config = RuleConfig.load(args.path)
    config.validate_rule_ids(registered_rule_ids())

    errors: list[str] = []
    for rule_id in config.enabled_rule_ids():
        spec = config.spec_of(rule_id)
        try:
            rule_cls = get_rule_class(rule_id)
            build_rule(
                rule_id,
                params=spec.params,
                severity=config.severity_for(rule_id, rule_cls.default_severity),
            )
        except ValueError as exc:
            errors.append(f"- {rule_id}: {exc}")

    print(f"Cấu hình hợp lệ: {args.path}")
    print("\n".join(config.summary_lines()))
    if errors:
        print("\nLỗi tham số rule:", file=sys.stderr)
        print("\n".join(errors), file=sys.stderr)
        return EXIT_CONFIG_ERROR
    return EXIT_OK


def cmd_legacy(args: argparse.Namespace) -> int:
    """``qaqc legacy``: chạy CLI cũ (được xử lý sớm trong :func:`main`)."""
    return legacy_main([])


def cmd_serve(args: argparse.Namespace) -> int:
    """``qaqc serve``: mở QA/QC service trên localhost (web UI + API JSON/CSV)."""
    options = ServiceOptions(
        rules=args.rules,
        only=args.only,
        level=args.level,
        disable=args.disable,
        max_issues=args.max_issues,
        source_file=args.source_file,
        demo=args.demo,
        env_file=args.env_file,
        host=args.host,
        user=args.user,
        password=args.password,
        token=args.token,
        cache_ttl=args.cache_ttl,
    )

    if not options.demo and not options.source_file:
        # Kiểm tra cấu hình kết nối ngay khi khởi động để báo lỗi sớm (exit code 2)
        # thay vì để người dùng thấy lỗi trên web UI.
        try:
            config = CVATConfig.from_env(
                env_file=args.env_file,
                host=args.host,
                user=args.user,
                password=args.password,
                token=args.token,
            )
        except ValueError as exc:
            # In kèm cách sửa cụ thể: người dùng thường chạy `serve` khi chưa có .env.
            _print_serve_config_hint(exc, args)
            return EXIT_CONFIG_ERROR
        account = config.user or "access token"
        print(f"Kết nối CVAT: {config.host} (tài khoản: {account})", flush=True)

    serve(options, bind_host=args.bind_host, port=args.port, open_browser=args.open)
    return EXIT_OK


def _print_serve_config_hint(exc: ValueError, args: argparse.Namespace) -> None:
    """In lỗi cấu hình của ``qaqc serve`` kèm cách sửa cụ thể (thay vì một dòng trơ)."""
    env_path = Path(args.env_file)
    print(f"Lỗi cấu hình: {exc}", file=sys.stderr)
    if not env_path.exists():
        print(f"Không tìm thấy file cấu hình: {env_path.resolve()}", file=sys.stderr)
    print("Cách sửa:", file=sys.stderr)
    print(
        "  1. Xem thử ngay (không cần CVAT) : python -m qaqc serve --demo --open",
        file=sys.stderr,
    )
    print(
        f"  2. Tạo file .env từ mẫu         : Copy-Item .env.example {args.env_file}",
        file=sys.stderr,
    )
    print(
        "     rồi điền CVAT_HOST + (CVAT_USER/CVAT_PASS hoặc CVAT_TOKEN)",
        file=sys.stderr,
    )
    print(
        "  3. Truyền tham số trực tiếp     : python -m qaqc serve --host <url> "
        "--user <u> --password <p>",
        file=sys.stderr,
    )
    print("     (hoặc --token <token> thay cho --user/--password)", file=sys.stderr)
    print(
        "  4. Dùng file .env ở nơi khác    : python -m qaqc serve --env-file <đường-dẫn>",
        file=sys.stderr,
    )


def _with_api_error_handling(func: Callable[[], int]) -> int:
    """Chuẩn hoá lỗi cấu hình/kết nối thành mã thoát ``2``/``3``."""
    from cvat_sdk import exceptions

    try:
        return func()
    except ValueError as exc:
        print(f"Lỗi cấu hình: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except exceptions.ApiException as exc:
        print(
            f"Lỗi gọi API CVAT (status={getattr(exc, 'status', '?')}): "
            f"{getattr(exc, 'reason', exc)}",
            file=sys.stderr,
        )
        return EXIT_API_ERROR
    except (exceptions.CvatSdkException, OSError) as exc:
        print(
            f"Không kết nối được CVAT: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return EXIT_API_ERROR
    except Exception as exc:
        if type(exc).__module__.startswith("urllib3"):
            print(
                f"Không kết nối được CVAT: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )
            return EXIT_API_ERROR
        raise


def _configure_stdout_encoding() -> None:
    """Đảm bảo in được tiếng Việt trên console Windows dùng codepage cũ (cp1252/cp437).

    Một số console Windows mặc định không encode được ký tự tiếng Việt, khiến mọi
    lệnh ``python -m qaqc ...`` chết với ``UnicodeEncodeError``. Hàm này chỉ đổi
    encoding của ``stdout``/``stderr`` sang UTF-8 **khi cần** (nếu encoding hiện tại
    đã in được tiếng Việt thì giữ nguyên, tránh làm hỏng màu/ký tự của terminal).
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # pytest capture / stream không hỗ trợ
            continue
        try:
            "ổ".encode(stream.encoding or "utf-8")
        except (UnicodeEncodeError, LookupError):
            pass
        else:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - phụ thuộc terminal
            LOGGER.debug("Không đổi được encoding của %s sang UTF-8.", stream)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point ``python -m qaqc``."""
    _configure_stdout_encoding()
    arguments = list(sys.argv[1:] if argv is None else argv)

    # `legacy` được xử lý trước khi argparse phân tích để chuyển nguyên tham số
    # của CLI cũ (positional task_id + các tuỳ chọn cũ) cho nó.
    if arguments and arguments[0] == "legacy":
        return legacy_main(arguments[1:])

    parser = build_parser()
    args = parser.parse_args(arguments)
    if getattr(args, "verbose", None):
        configure_logging(args.verbose)

    return _with_api_error_handling(lambda: args.func(args))
