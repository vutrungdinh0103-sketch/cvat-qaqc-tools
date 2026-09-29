"""Báo cáo kết quả QA/QC và các writer (JSON / CSV / JUnit XML).

Schema JSON của báo cáo mới (``qaqc/1``) **khác** schema cũ của ``checker.py``
(được giữ nguyên trong :mod:`qaqc.legacy` để không phá CI đã có).
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Final
from xml.etree import ElementTree

from pydantic import BaseModel, Field

from . import SCHEMA_VERSION
from .model import SEVERITY_RANK, Severity

#: Mã thoát của CLI (thống nhất cho cả CLI mới và CLI cũ của ``checker.py``).
EXIT_OK: Final[int] = 0
EXIT_ISSUES_FOUND: Final[int] = 1
EXIT_CONFIG_ERROR: Final[int] = 2
EXIT_API_ERROR: Final[int] = 3

#: Các cột của báo cáo CSV.
CSV_FIELDS: Final[tuple[str, ...]] = (
    "rule_id",
    "severity",
    "level",
    "task_id",
    "job_id",
    "frame",
    "object_key",
    "shape_id",
    "track_id",
    "label",
    "message",
    "fingerprint",
    "details",
)


class ReportFormat(str, Enum):
    """Định dạng báo cáo được hỗ trợ."""

    JSON = "json"
    CSV = "csv"
    JUNIT = "junit"

    @classmethod
    def parse(cls, value: ReportFormat | str | None, *, path: str | None = None) -> ReportFormat:
        """Xác định định dạng: ưu tiên tham số, sau đó suy từ phần mở rộng file."""
        if isinstance(value, ReportFormat):
            return value
        if value:
            normalized = str(value).strip().lower()
            aliases = {"xml": "junit", "junit.xml": "junit"}
            normalized = aliases.get(normalized, normalized)
            try:
                return cls(normalized)
            except ValueError as exc:
                allowed = ", ".join(item.value for item in cls)
                raise ValueError(
                    f"Định dạng báo cáo không được hỗ trợ: {value!r} (chỉ nhận: {allowed})"
                ) from exc

        suffix = Path(path).suffix.lstrip(".").lower() if path else "json"
        return cls.parse(suffix or "json")


class Issue(BaseModel):
    """Một lỗi QA/QC ở dạng "đã sẵn sàng xuất báo cáo/tạo issue"."""

    rule_id: str
    severity: Severity
    task_id: int
    frame: int
    message: str
    fingerprint: str
    #: Cấp độ QA của rule đã sinh ra lỗi (``1`` = Overall/Completeness, ``2`` = Detailed).
    level: int = 2
    job_id: int | None = None
    object_key: str | None = None
    object_keys: tuple[str, ...] = ()
    shape_id: int | None = None
    track_id: int | None = None
    label: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)

    @property
    def kind(self) -> str:
        """Bí danh của ``rule_id`` (tương thích ngữ nghĩa "loại lỗi")."""
        return self.rule_id

    def to_csv_row(self) -> dict[str, str]:
        """Một dòng CSV của issue."""
        return {
            "rule_id": self.rule_id,
            "severity": self.severity.value,
            "level": str(self.level),
            "task_id": str(self.task_id),
            "job_id": "" if self.job_id is None else str(self.job_id),
            "frame": str(self.frame),
            "object_key": self.object_key or "",
            "shape_id": "" if self.shape_id is None else str(self.shape_id),
            "track_id": "" if self.track_id is None else str(self.track_id),
            "label": self.label or "",
            "message": self.message,
            "fingerprint": self.fingerprint,
            "details": json.dumps(self.details, ensure_ascii=False),
        }


class QAReport(BaseModel):
    """Báo cáo kết quả kiểm tra QA/QC của một task/job."""

    schema_version: str = SCHEMA_VERSION
    task_id: int
    task_name: str | None = None
    source_job_id: int | None = None
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    rules_name: str = "default"
    rules_version: int = 1
    rules_hash: str = ""
    rules_path: str | None = None
    shapes_total: int = 0
    shapes_skipped: int = 0
    objects_scanned: int = 0
    frames_scanned: int = 0
    frames_total: int | None = None
    issues: list[Issue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    duration_seconds: float | None = None

    # ------------------------------------------------------------------
    # Thống kê
    # ------------------------------------------------------------------
    @property
    def has_issues(self) -> bool:
        """``True`` nếu phát hiện ít nhất một lỗi."""
        return bool(self.issues)

    @property
    def counts_by_rule(self) -> dict[str, int]:
        """Số lỗi theo từng rule."""
        counts: dict[str, int] = {}
        for issue in self.issues:
            counts[issue.rule_id] = counts.get(issue.rule_id, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def counts_by_severity(self) -> dict[str, int]:
        """Số lỗi theo từng mức độ (luôn có đủ 3 khoá)."""
        counts = {severity.value: 0 for severity in Severity}
        for issue in self.issues:
            counts[issue.severity.value] += 1
        return counts

    @property
    def counts_by_level(self) -> dict[str, int]:
        """Số lỗi theo cấp độ QA (``"1"`` = Overall/Completeness, ``"2"`` = Detailed).

        Chỉ chứa các cấp độ thực sự có lỗi để báo cáo gọn (khác ``counts_by_severity``).
        """
        counts: dict[str, int] = {}
        for issue in self.issues:
            key = str(issue.level)
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    @property
    def max_severity(self) -> Severity | None:
        """Mức độ nghiêm trọng nhất trong báo cáo (``None`` nếu không có lỗi)."""
        if not self.issues:
            return None
        return max((issue.severity for issue in self.issues), key=lambda item: item.rank)

    def issues_at_or_above(self, severity: Severity) -> list[Issue]:
        """Các lỗi có mức độ >= ``severity``."""
        threshold = severity.rank
        return [issue for issue in self.issues if SEVERITY_RANK[issue.severity] >= threshold]

    def exit_code(self, fail_on: Severity | None) -> int:
        """Mã thoát CLI: ``1`` nếu có lỗi >= ``fail_on``, ngược lại ``0``."""
        if fail_on is None:
            return EXIT_OK
        return EXIT_ISSUES_FOUND if self.issues_at_or_above(fail_on) else EXIT_OK

    # ------------------------------------------------------------------
    # Kết xuất
    # ------------------------------------------------------------------
    def summary_lines(self, *, max_issues: int = 50) -> list[str]:
        """Các dòng tóm tắt báo cáo để in ra màn hình."""
        task_label = f"Task {self.task_id}"
        if self.task_name:
            task_label += f" ({self.task_name})"
        if self.source_job_id is not None:
            task_label += f" [job {self.source_job_id}]"

        counts = self.counts_by_severity
        lines = [
            task_label,
            f"Thời điểm kiểm tra: {self.generated_at.isoformat()}",
            f"Cấu hình rule: {self.rules_name} (hash={self.rules_hash[:12] or 'n/a'})",
            f"Object đã kiểm tra: {self.objects_scanned} "
            f"(shape đọc được {self.shapes_total}, bỏ qua {self.shapes_skipped})",
            f"Frame có annotation: {self.frames_scanned}"
            + (f"/{self.frames_total}" if self.frames_total is not None else ""),
            f"Tổng số lỗi: {len(self.issues)} "
            f"(error={counts[Severity.ERROR.value]}, warning={counts[Severity.WARNING.value]}, "
            f"info={counts[Severity.INFO.value]})",
        ]

        by_rule = self.counts_by_rule
        if by_rule:
            lines.append(
                "Theo rule: " + ", ".join(f"{rule}={count}" for rule, count in by_rule.items())
            )

        by_level = self.counts_by_level
        if by_level:
            labels = {"1": "Level 1 (overall)", "2": "Level 2 (detailed)"}
            lines.append(
                "Theo cấp độ: "
                + ", ".join(
                    f"{labels.get(level, level)}={count}" for level, count in by_level.items()
                )
            )

        for issue in self.issues[:max_issues]:
            lines.append(f"  - {issue.message}")
        if len(self.issues) > max_issues:
            remaining = len(self.issues) - max_issues
            lines.append(f"  ... và {remaining} lỗi khác (xem file báo cáo).")

        for warning in self.warnings:
            lines.append(f"  ! {warning}")
        return lines

    def to_json_dict(self) -> dict[str, Any]:
        """Dict JSON-serializable (enum -> str, datetime -> ISO string)."""
        data = self.model_dump(mode="json")
        data["counts_by_rule"] = self.counts_by_rule
        data["counts_by_severity"] = self.counts_by_severity
        data["counts_by_level"] = self.counts_by_level
        return data

    def to_csv_rows(self) -> list[dict[str, str]]:
        """Các dòng CSV tương ứng với ``issues``."""
        return [issue.to_csv_row() for issue in self.issues]

    def to_junit_xml(self) -> str:
        """Kết xuất báo cáo dạng JUnit XML (hiển thị trực tiếp trên CI)."""
        suite = ElementTree.Element(
            "testsuite",
            {
                "name": f"qaqc-task-{self.task_id}",
                "tests": str(max(len(self.issues), 1)),
                "failures": str(len(self.issues)),
                "errors": "0",
                "skipped": "0",
                "timestamp": self.generated_at.isoformat(),
            },
        )

        if not self.issues:
            ElementTree.SubElement(
                suite, "testcase", {"classname": f"task.{self.task_id}", "name": "no-issues"}
            )

        for issue in self.issues:
            case = ElementTree.SubElement(
                suite,
                "testcase",
                {
                    "classname": f"task.{self.task_id}.{issue.rule_id}",
                    "name": f"{issue.rule_id}@frame{issue.frame}",
                    "file": f"frame-{issue.frame}",
                },
            )
            failure = ElementTree.SubElement(
                case,
                "failure",
                {"type": issue.rule_id, "message": f"{issue.severity.value}: {issue.message}"},
            )
            failure.text = json.dumps(issue.model_dump(mode="json"), ensure_ascii=False, indent=2)

        return ElementTree.tostring(suite, encoding="unicode")

    def write(
        self,
        output_path: str | Path,
        fmt: ReportFormat | str | None = None,
    ) -> Path:
        """Ghi báo cáo ra file JSON/CSV/JUnit XML.

        :param output_path: đường dẫn file kết quả (thư mục cha được tạo tự động).
        :param fmt: định dạng; mặc định suy ra từ phần mở rộng file.
        :raises ValueError: nếu định dạng không được hỗ trợ.
        """
        path = Path(output_path)
        report_format = ReportFormat.parse(fmt, path=str(path))
        path.parent.mkdir(parents=True, exist_ok=True)

        if report_format is ReportFormat.JSON:
            with path.open("w", encoding="utf-8") as file_obj:
                json.dump(self.to_json_dict(), file_obj, ensure_ascii=False, indent=2)
                file_obj.write("\n")
        elif report_format is ReportFormat.CSV:
            # utf-8-sig để Excel hiển thị đúng tiếng Việt
            with path.open("w", encoding="utf-8-sig", newline="") as file_obj:
                writer = csv.DictWriter(file_obj, fieldnames=CSV_FIELDS)
                writer.writeheader()
                writer.writerows(self.to_csv_rows())
        else:
            path.write_text(self.to_junit_xml() + "\n", encoding="utf-8")

        return path
