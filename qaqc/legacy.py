"""Lớp tương thích ngược cho CLI cũ ``python checker.py <task_id>``.

Mục tiêu: **không phá vỡ CI đã có**. Module này giữ nguyên:

- 2 loại lỗi với tên cũ: ``invalid_size`` và ``duplicate``,
- định dạng message, schema JSON, cột CSV của báo cáo,
- mã thoát ``0`` (thành công) / ``1`` (có lỗi khi ``--fail-on-issues``) /
  ``2`` (lỗi cấu hình) / ``3`` (lỗi kết nối-API),
- API công khai cũ: ``CVATConfig``, ``CVATChecker``, ``Box``, ``Issue``,
  ``IssueKind``, ``QAReport``, ``compute_iou``, ``build_arg_parser``, ``main``.

Khác biệt duy nhất so với bản cũ: logic dùng chung ``qaqc.geometry`` và
``qaqc.normalize`` để chỉ có một nguồn sự thật cho việc đọc/chuẩn hoá dữ liệu;
phần kiểm tra (IoU trên bbox, message, details) giữ nguyên từng chi tiết.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Final

import urllib3
from pydantic import BaseModel, ConfigDict, Field

from .config import CVATConfig
from .geometry import BBox, compute_iou
from .normalize import normalize_shape
from .report import EXIT_API_ERROR, EXIT_CONFIG_ERROR, EXIT_ISSUES_FOUND, EXIT_OK

LOGGER = logging.getLogger("cvat_qaqc")

#: Ngưỡng IoU mặc định để coi 2 box là trùng lặp (theo .clinerules).
DEFAULT_IOU_THRESHOLD: Final[float] = 0.85

#: Các loại shape được quy về bounding box để kiểm tra (giống CLI cũ).
SUPPORTED_SHAPE_TYPES: Final[tuple[str, ...]] = ("rectangle", "polygon")

#: Tên cột của báo cáo CSV (giữ nguyên như bản cũ).
CSV_FIELDS: Final[tuple[str, ...]] = (
    "kind",
    "task_id",
    "frame",
    "shape_id",
    "label",
    "message",
    "details",
)


class IssueKind(str, Enum):
    """Loại lỗi QA/QC phát hiện được (tên cũ)."""

    INVALID_SIZE = "invalid_size"
    DUPLICATE = "duplicate"


class Box(BaseModel):
    """Một box (hoặc bounding box của polygon) trong 1 frame của task."""

    model_config = ConfigDict(frozen=True)

    shape_id: int | None = None
    frame: int
    shape_type: str = "rectangle"
    label_id: int
    label: str
    xtl: float
    ytl: float
    xbr: float
    ybr: float

    @property
    def width(self) -> float:
        """Chiều rộng box (có thể <= 0 nếu annotation lỗi)."""
        return self.xbr - self.xtl

    @property
    def height(self) -> float:
        """Chiều cao box (có thể <= 0 nếu annotation lỗi)."""
        return self.ybr - self.ytl

    @property
    def is_degenerate(self) -> bool:
        """``True`` nếu box có kích thước không hợp lệ (w <= 0 hoặc h <= 0)."""
        return self.width <= 0 or self.height <= 0

    def bbox_str(self) -> str:
        """Chuỗi mô tả toạ độ box, dùng cho message báo cáo."""
        return f"({self.xtl:g}, {self.ytl:g}, {self.xbr:g}, {self.ybr:g})"

    def to_bbox(self) -> BBox:
        """Trả bbox dạng tuple."""
        return (self.xtl, self.ytl, self.xbr, self.ybr)

    def to_geometry(self) -> Any:
        """Chuyển box thành hình học shapely; ``None`` nếu box suy biến."""
        if self.is_degenerate:
            return None
        from shapely.geometry import box as shapely_box

        return shapely_box(self.xtl, self.ytl, self.xbr, self.ybr)


class Issue(BaseModel):
    """Một lỗi QA/QC phát hiện trong task (schema cũ)."""

    kind: IssueKind
    task_id: int
    frame: int
    shape_id: int | None = None
    label: str | None = None
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class QAReport(BaseModel):
    """Báo cáo kết quả kiểm tra QA/QC của một task (schema cũ)."""

    task_id: int
    task_name: str | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    iou_threshold: float = DEFAULT_IOU_THRESHOLD
    shapes_total: int = 0
    shapes_skipped: int = 0
    boxes_scanned: int = 0
    frames_scanned: int = 0
    issues: list[Issue] = Field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        """``True`` nếu phát hiện ít nhất một lỗi."""
        return bool(self.issues)

    @property
    def counts_by_kind(self) -> dict[str, int]:
        """Số lượng lỗi theo từng loại."""
        counts = {kind.value: 0 for kind in IssueKind}
        for issue in self.issues:
            counts[issue.kind.value] += 1
        return counts

    def summary_lines(self) -> list[str]:
        """Các dòng tóm tắt báo cáo để in ra màn hình (giữ nguyên định dạng cũ)."""
        counts = self.counts_by_kind
        task_label = f"Task {self.task_id}"
        if self.task_name:
            task_label += f" ({self.task_name})"
        lines = [
            task_label,
            f"Thời điểm kiểm tra: {self.generated_at.isoformat()}",
            f"Ngưỡng IoU trùng lặp: {self.iou_threshold}",
            f"Shape đọc được: {self.shapes_total} "
            f"(kiểm tra {self.boxes_scanned}, bỏ qua {self.shapes_skipped})",
            f"Số frame có box: {self.frames_scanned}",
            f"Tổng số lỗi: {len(self.issues)} "
            f"(invalid_size={counts[IssueKind.INVALID_SIZE.value]}, "
            f"duplicate={counts[IssueKind.DUPLICATE.value]})",
        ]
        lines.extend(f"  - {issue.message}" for issue in self.issues)
        return lines

    def to_json_dict(self) -> dict[str, Any]:
        """Dict đã ở dạng JSON-serializable (enum -> str, datetime -> ISO string)."""
        data = self.model_dump(mode="json")
        data["counts_by_kind"] = self.counts_by_kind
        return data

    def to_csv_rows(self) -> list[dict[str, str]]:
        """Các dòng CSV (list of dict) tương ứng với ``issues``."""
        return [
            {
                "kind": issue.kind.value,
                "task_id": str(issue.task_id),
                "frame": str(issue.frame),
                "shape_id": "" if issue.shape_id is None else str(issue.shape_id),
                "label": issue.label or "",
                "message": issue.message,
                "details": json.dumps(issue.details, ensure_ascii=False),
            }
            for issue in self.issues
        ]


class CVATChecker:
    """Kiểm tra QA/QC annotations của một CVAT task (API cũ).

    Ví dụ::

        config = CVATConfig.from_env()
        with CVATChecker(config, iou_threshold=0.85) as checker:
            report = checker.run(task_id=42)
            checker.save_report(report, "reports/task_42.json")
    """

    def __init__(
        self,
        config: CVATConfig,
        *,
        iou_threshold: float = DEFAULT_IOU_THRESHOLD,
        logger: logging.Logger | None = None,
    ) -> None:
        if not 0.0 <= iou_threshold <= 1.0:
            raise ValueError(f"iou_threshold phải nằm trong [0, 1], nhận được {iou_threshold}")
        self.config = config
        self.iou_threshold = float(iou_threshold)
        self.logger = logger or LOGGER
        self._client: Any = None

    # ------------------------------------------------------------------
    # Kết nối
    # ------------------------------------------------------------------
    def connect(self) -> Any:
        """Tạo client chính thức của CVAT và đăng nhập (idempotent)."""
        if self._client is None:
            self.logger.info(
                "Kết nối CVAT server %s (user='%s')...", self.config.host, self.config.user
            )
            self._client = self.config.create_client()
        return self._client

    @property
    def client(self) -> Any:
        """Client đã đăng nhập (tự tạo nếu chưa có)."""
        return self.connect()

    def close(self) -> None:
        """Đóng kết nối tới CVAT server."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> CVATChecker:
        self.connect()
        return self

    def __exit__(self, exc_type: object, exc: object, exc_tb: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Tải dữ liệu từ CVAT
    # ------------------------------------------------------------------
    def load_task(self, task_id: int) -> Any:
        """Lấy task theo ID.

        :raises cvat_sdk.exceptions.ApiException: task không tồn tại hoặc
            tài khoản không có quyền truy cập.
        """
        self.logger.info("Tải task #%s...", task_id)
        return self.client.tasks.retrieve(task_id)

    def load_label_names(self, task: Any) -> dict[int, str]:
        """Trả về map ``label_id -> tên label`` của task."""
        names: dict[int, str] = {}
        for label in task.get_labels():
            label_id = int(getattr(label, "id", -1))
            names[label_id] = str(getattr(label, "name", None) or f"label_id={label_id}")
        return names

    def load_annotations(self, task: Any) -> Any:
        """Tải toàn bộ annotations (shapes/tracks/tags) của task."""
        annotations = task.get_annotations()
        shapes = getattr(annotations, "shapes", None) or []
        self.logger.info("Task #%s: đã tải %s shape.", task.id, len(shapes))
        return annotations

    # ------------------------------------------------------------------
    # Chuẩn hoá annotations -> Box
    # ------------------------------------------------------------------
    def extract_boxes(
        self,
        shapes: Iterable[Any],
        label_names: Mapping[int, str],
    ) -> tuple[list[Box], int]:
        """Chuyển shapes của CVAT thành danh sách :class:`Box`.

        Chỉ nhận ``rectangle``/``polygon`` đang hiển thị (``outside = False``);
        polygon được quy về bounding box.

        :return: tuple ``(danh sách box, số shape bị bỏ qua)``.
        """
        boxes: list[Box] = []
        skipped = 0

        for index, raw in enumerate(shapes or ()):
            shape, _reason = normalize_shape(raw, label_names=label_names, index=index)
            if shape is None or shape.shape_type not in SUPPORTED_SHAPE_TYPES:
                skipped += 1
                continue

            if shape.outside:
                self.logger.debug(
                    "Bỏ qua shape #%s: outside=True (không hiển thị).", shape.shape_id
                )
                skipped += 1
                continue

            bbox = shape.bbox
            if bbox is None:
                self.logger.warning(
                    "Bỏ qua shape #%s: dữ liệu points không hợp lệ (%s giá trị).",
                    shape.shape_id,
                    len(shape.points),
                )
                skipped += 1
                continue

            boxes.append(
                Box(
                    shape_id=shape.shape_id,
                    frame=shape.frame,
                    shape_type=shape.shape_type,
                    label_id=shape.label_id,
                    label=shape.label,
                    xtl=bbox[0],
                    ytl=bbox[1],
                    xbr=bbox[2],
                    ybr=bbox[3],
                )
            )

        self.logger.info("Chuẩn hoá được %s box (bỏ qua %s shape).", len(boxes), skipped)
        return boxes, skipped

    # ------------------------------------------------------------------
    # Các kiểm tra QA/QC (giữ nguyên thuật toán bản cũ)
    # ------------------------------------------------------------------
    def check_invalid_size(self, boxes: Iterable[Box], task_id: int) -> list[Issue]:
        """Kiểm tra box có kích thước không hợp lệ (``width <= 0`` hoặc ``height <= 0``)."""
        issues: list[Issue] = []
        for box in boxes:
            if not box.is_degenerate:
                continue

            issues.append(
                Issue(
                    kind=IssueKind.INVALID_SIZE,
                    task_id=task_id,
                    frame=box.frame,
                    shape_id=box.shape_id,
                    label=box.label,
                    message=(
                        f"[invalid_size] frame {box.frame}, shape #{box.shape_id} "
                        f"({box.shape_type}, label='{box.label}'): width={box.width:g}, "
                        f"height={box.height:g}, bbox={box.bbox_str()}"
                    ),
                    details={
                        "shape_type": box.shape_type,
                        "label_id": box.label_id,
                        "width": box.width,
                        "height": box.height,
                        "xtl": box.xtl,
                        "ytl": box.ytl,
                        "xbr": box.xbr,
                        "ybr": box.ybr,
                    },
                )
            )
        return issues

    def check_duplicates(
        self,
        boxes: Iterable[Box],
        task_id: int,
        *,
        iou_threshold: float | None = None,
    ) -> list[Issue]:
        """Tìm các cặp box trùng lặp (cùng frame, cùng label, ``IoU > ngưỡng``).

        Box có kích thước không hợp lệ bị bỏ qua (không tính được IoU).
        """
        threshold = self.iou_threshold if iou_threshold is None else float(iou_threshold)

        grouped: dict[tuple[int, str], list[Box]] = defaultdict(list)
        for box in boxes:
            if box.is_degenerate:
                continue
            grouped[(box.frame, box.label)].append(box)

        issues: list[Issue] = []
        for (frame, label), group in sorted(grouped.items()):
            for index, first in enumerate(group):
                first_geometry = first.to_geometry()
                if first_geometry is None:
                    continue

                for second in group[index + 1 :]:
                    second_geometry = second.to_geometry()
                    if second_geometry is None:
                        continue

                    iou = compute_iou(first_geometry, second_geometry)
                    if iou <= threshold:
                        continue

                    issues.append(
                        Issue(
                            kind=IssueKind.DUPLICATE,
                            task_id=task_id,
                            frame=frame,
                            shape_id=first.shape_id,
                            label=label,
                            message=(
                                f"[duplicate] frame {frame}, label '{label}': shape "
                                f"#{first.shape_id} {first.bbox_str()} trùng với shape "
                                f"#{second.shape_id} {second.bbox_str()} "
                                f"(IoU={iou:.4f} > {threshold})"
                            ),
                            details={
                                "iou": round(iou, 6),
                                "iou_threshold": threshold,
                                "label_id": first.label_id,
                                "shape_id_a": first.shape_id,
                                "shape_id_b": second.shape_id,
                                "shape_type_a": first.shape_type,
                                "shape_type_b": second.shape_type,
                                "bbox_a": list(first_geometry.bounds),
                                "bbox_b": list(second_geometry.bounds),
                            },
                        )
                    )
        return issues

    # ------------------------------------------------------------------
    # Chạy kiểm tra & xuất báo cáo
    # ------------------------------------------------------------------
    def run(self, task_id: int) -> QAReport:
        """Tải annotations của task và chạy toàn bộ kiểm tra QA/QC."""
        task = self.load_task(task_id)
        label_names = self.load_label_names(task)
        annotations = self.load_annotations(task)
        shapes = list(getattr(annotations, "shapes", None) or [])

        return self.run_on_shapes(
            shapes,
            task_id=task_id,
            task_name=getattr(task, "name", None),
            label_names=label_names,
        )

    def run_on_shapes(
        self,
        shapes: Sequence[Any],
        *,
        task_id: int,
        label_names: Mapping[int, str],
        task_name: str | None = None,
    ) -> QAReport:
        """Chạy kiểm tra trên danh sách shape có sẵn (không cần kết nối CVAT).

        Hữu ích cho test offline và khi dữ liệu đã được tải ở nơi khác.
        """
        raw_shapes = list(shapes or [])
        boxes, skipped = self.extract_boxes(raw_shapes, label_names)

        issues = self.check_invalid_size(boxes, task_id)
        issues.extend(self.check_duplicates(boxes, task_id))

        report = QAReport(
            task_id=task_id,
            task_name=task_name,
            iou_threshold=self.iou_threshold,
            shapes_total=len(raw_shapes),
            shapes_skipped=skipped,
            boxes_scanned=len(boxes),
            frames_scanned=len({box.frame for box in boxes}),
            issues=issues,
        )
        self.logger.info("Task #%s: phát hiện %s lỗi QA/QC.", task_id, len(issues))
        return report

    def save_report(
        self,
        report: QAReport,
        output_path: str | os.PathLike[str],
        fmt: str | None = None,
    ) -> Path:
        """Ghi báo cáo ra file JSON hoặc CSV.

        :param output_path: đường dẫn file kết quả (thư mục cha sẽ được tạo).
        :param fmt: ``"json"`` hoặc ``"csv"``; mặc định suy ra từ phần mở rộng file.
        :raises ValueError: nếu định dạng không được hỗ trợ.
        """
        path = Path(output_path)
        report_format = (fmt or path.suffix.lstrip(".") or "json").lower()
        path.parent.mkdir(parents=True, exist_ok=True)

        if report_format == "json":
            with path.open("w", encoding="utf-8") as file_obj:
                json.dump(report.to_json_dict(), file_obj, ensure_ascii=False, indent=2)
                file_obj.write("\n")
        elif report_format == "csv":
            # utf-8-sig để Excel hiển thị đúng tiếng Việt
            with path.open("w", encoding="utf-8-sig", newline="") as file_obj:
                writer = csv.DictWriter(file_obj, fieldnames=CSV_FIELDS)
                writer.writeheader()
                writer.writerows(report.to_csv_rows())
        else:
            raise ValueError(
                f"Định dạng báo cáo không được hỗ trợ: {report_format!r} "
                "(chỉ hỗ trợ 'json' hoặc 'csv')."
            )

        self.logger.info("Đã ghi báo cáo %s: %s", report_format.upper(), path)
        return path


# ---------------------------------------------------------------------------
# CLI cũ: python checker.py <task_id> [...]
# ---------------------------------------------------------------------------
def configure_logging(verbosity: int = 0) -> None:
    """Cấu hình logging: mặc định WARNING, ``-v`` -> INFO, ``-vv`` -> DEBUG."""
    if verbosity >= 2:
        level = logging.DEBUG
    elif verbosity == 1:
        level = logging.INFO
    else:
        level = logging.WARNING

    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    """Tạo parser cho CLI ``python checker.py <task_id> [...]``."""
    parser = argparse.ArgumentParser(
        prog="checker.py",
        description=(
            "Kiểm tra QA/QC annotations của một CVAT task: "
            "box lỗi kích thước (w <= 0 hoặc h <= 0) và box trùng lặp (IoU > ngưỡng)."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("task_id", type=int, help="ID của CVAT task cần kiểm tra")
    parser.add_argument("--host", help="URL CVAT server (mặc định lấy từ CVAT_HOST)")
    parser.add_argument("--user", help="Tài khoản CVAT (mặc định lấy từ CVAT_USER)")
    parser.add_argument("--password", help="Mật khẩu CVAT (mặc định lấy từ CVAT_PASS)")
    parser.add_argument("--env-file", default=".env", help="File cấu hình .env cần nạp")
    parser.add_argument(
        "--iou",
        type=float,
        default=DEFAULT_IOU_THRESHOLD,
        help="Ngưỡng IoU để coi 2 box cùng label/frame là trùng lặp",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="File báo cáo (.json hoặc .csv). Không truyền thì chỉ in ra màn hình.",
    )
    parser.add_argument(
        "--format",
        choices=("json", "csv"),
        help="Định dạng báo cáo (mặc định suy ra từ phần mở rộng của --output)",
    )
    parser.add_argument(
        "--fail-on-issues",
        action="store_true",
        help="Trả mã thoát 1 nếu phát hiện lỗi (hữu ích khi chạy trong CI)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Tăng mức độ log (-v: INFO, -vv: DEBUG)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point CLI cũ: kiểm tra 1 task và in/xuất báo cáo.

    :return: mã thoát -- ``0`` thành công, ``1`` có lỗi khi bật
        ``--fail-on-issues``, ``2`` lỗi cấu hình, ``3`` lỗi kết nối/API.
    """
    from cvat_sdk import exceptions  # import muộn để CLI --help không cần SDK

    args = build_arg_parser().parse_args(argv)
    configure_logging(args.verbose)

    try:
        config = CVATConfig.from_env(
            env_file=args.env_file,
            host=args.host,
            user=args.user,
            password=args.password,
        )
        checker = CVATChecker(config, iou_threshold=args.iou)
    except ValueError as exc:
        print(f"Lỗi cấu hình: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    try:
        with checker:
            report = checker.run(args.task_id)
            if args.output:
                checker.save_report(report, args.output, args.format)
    except exceptions.ApiException as exc:
        # Lỗi HTTP từ CVAT (401/403/404...) hoặc sai thông tin đăng nhập
        print(
            f"Lỗi gọi API CVAT (status={getattr(exc, 'status', '?')}): "
            f"{getattr(exc, 'reason', exc)}",
            file=sys.stderr,
        )
        return EXIT_API_ERROR
    except (exceptions.CvatSdkException, urllib3.exceptions.HTTPError, OSError) as exc:
        # Sai host / server không chạy / timeout / lỗi mạng (urllib3)
        print(
            f"Không kết nối được CVAT server {config.host}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return EXIT_API_ERROR

    print("===== Kết quả QA/QC =====")
    print("\n".join(report.summary_lines()))

    return EXIT_ISSUES_FOUND if args.fail_on_issues and report.has_issues else EXIT_OK
