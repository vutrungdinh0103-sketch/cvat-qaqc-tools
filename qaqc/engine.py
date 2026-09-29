"""Engine QA/QC: chạy các rule đã cấu hình trên :class:`TaskData` -> :class:`QAReport`."""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence

from .config import RuleConfig
from .fingerprint import fingerprint
from .model import SEVERITY_RANK, TaskData
from .report import Issue, QAReport
from .rules import build_rule, get_rule_class, registered_rule_ids
from .rules.base import Finding, Rule, RuleContext

LOGGER = logging.getLogger("cvat_qaqc.engine")


class QAQCEngine:
    """Chạy rule và tổng hợp kết quả thành báo cáo.

    Ví dụ::

        config = RuleConfig.load("rules/driving_v1.yaml")
        engine = QAQCEngine(config)
        report = engine.run(task_data)
    """

    def __init__(
        self,
        rules: RuleConfig | None = None,
        *,
        logger: logging.Logger | None = None,
        max_issues: int = 0,
    ) -> None:
        """
        :param rules: cấu hình rule (mặc định: toàn bộ rule với tham số gốc).
        :param logger: logger dùng chung.
        :param max_issues: giới hạn số lỗi đưa vào báo cáo (0 = không giới hạn).
        :raises ValueError: nếu cấu hình chứa ``rule_id`` không tồn tại.
        """
        self.rules = rules or RuleConfig.default()
        self.logger = logger or LOGGER
        self.max_issues = int(max_issues)
        self.rules.validate_rule_ids(registered_rule_ids())
        self._config_hash = self.rules.config_hash
        #: Hash riêng từng rule (dùng cho fingerprint) - cache để không tính lại.
        self._rule_hashes: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Khởi tạo rule
    # ------------------------------------------------------------------
    def build_rules(self) -> list[Rule]:
        """Khởi tạo các rule đang bật theo cấu hình (đã resolve severity)."""
        built: list[Rule] = []
        for rule_id in self.rules.enabled_rule_ids():
            rule_cls = get_rule_class(rule_id)
            severity = self.rules.severity_for(rule_id, rule_cls.default_severity)
            built.append(
                build_rule(
                    rule_id,
                    params=self.rules.spec_of(rule_id).params,
                    severity=severity,
                    logger=self.logger,
                )
            )
            self.logger.debug("Bật rule '%s' (severity=%s).", rule_id, severity.value)
        return built

    def describe_rules(self) -> list[str]:
        """Mô tả các rule sẽ chạy (kèm mức độ hiệu lực) - dùng cho CLI/log."""
        return [
            f"{rule.rule_id} ({rule.severity.value}) [{rule.group}] - {rule.description}"
            for rule in self.build_rules()
        ]

    # ------------------------------------------------------------------
    # Chạy
    # ------------------------------------------------------------------
    def run(self, data: TaskData, *, max_issues: int | None = None) -> QAReport:
        """Chạy toàn bộ rule trên dữ liệu đã chuẩn hoá.

        Rule gặp lỗi không làm hỏng cả lần chạy: lỗi được ghi vào
        ``report.warnings`` để người dùng biết kết quả chỉ là *một phần*.

        :param data: dữ liệu task/job đã chuẩn hoá.
        :param max_issues: giới hạn số lỗi (ghi đè giá trị khởi tạo nếu truyền).
        """
        started = time.perf_counter()
        ctx = RuleContext(data, logger=self.logger)

        findings: list[Finding] = []
        warnings: list[str] = list(data.warnings)

        for rule in self.build_rules():
            self.logger.info("Chạy rule '%s'...", rule.rule_id)
            try:
                findings.extend(rule.check(ctx))
            except Exception as exc:
                message = f"Rule '{rule.rule_id}' lỗi và bị bỏ qua: {type(exc).__name__}: {exc}"
                self.logger.exception(message)
                warnings.append(message)
                continue
            warnings.extend(rule.warnings)

        issues = self._build_issues(findings, data, warnings)

        limit = self.max_issues if max_issues is None else int(max_issues)
        if limit > 0 and len(issues) > limit:
            warnings.append(
                f"Số lỗi vượt giới hạn --max-issues={limit}: đã bỏ {len(issues) - limit} "
                "lỗi khỏi báo cáo (giữ theo thứ tự nghiêm trọng giảm dần)."
            )
            issues = issues[:limit]

        report = QAReport(
            task_id=data.task_id,
            task_name=data.task_name,
            source_job_id=data.source_job_id,
            rules_name=self.rules.name,
            rules_version=self.rules.version,
            rules_hash=self._config_hash,
            rules_path=self.rules.source_path,
            shapes_total=data.shapes_total,
            shapes_skipped=data.shapes_skipped,
            objects_scanned=len(data.objects()),
            frames_scanned=len(data.frames_with_objects()),
            frames_total=data.size,
            issues=issues,
            warnings=warnings,
            duration_seconds=round(time.perf_counter() - started, 4),
        )
        self.logger.info(
            "QA/QC task #%s: %s lỗi trong %ss.",
            data.task_id,
            len(issues),
            report.duration_seconds,
        )
        return report

    # ------------------------------------------------------------------
    # Nội bộ
    # ------------------------------------------------------------------
    def _build_issues(
        self,
        findings: Sequence[Finding],
        data: TaskData,
        warnings: list[str],
    ) -> list[Issue]:
        """Chuyển Finding -> Issue (gắn fingerprint/job_id), sắp xếp, khử trùng."""
        ordered = sorted(
            findings,
            key=lambda item: (
                -SEVERITY_RANK[item.severity],
                item.frame,
                item.rule_id,
                item.primary_key or "",
                item.discriminator or "",
            ),
        )

        issues: list[Issue] = []
        seen: set[str] = set()
        for finding in ordered:
            value = fingerprint(
                rule_id=finding.rule_id,
                task_id=data.task_id,
                frame=finding.frame,
                object_keys=finding.object_keys,
                config_hash=self._rule_hash(finding.rule_id),
                discriminator=finding.discriminator,
            )
            if value in seen:
                self.logger.debug(
                    "Bỏ qua lỗi trùng fingerprint %s (rule=%s, frame=%s).",
                    value,
                    finding.rule_id,
                    finding.frame,
                )
                continue
            seen.add(value)
            issues.append(self._to_issue(finding, data, value))

        return issues

    def _rule_hash(self, rule_id: str) -> str:
        """Hash cấu hình của riêng một rule (có cache)."""
        if rule_id not in self._rule_hashes:
            self._rule_hashes[rule_id] = self.rules.rule_hash(rule_id)
        return self._rule_hashes[rule_id]

    @staticmethod
    def _to_issue(finding: Finding, data: TaskData, value: str) -> Issue:
        """Gắn thêm thông tin object/job vào một :class:`Finding`."""
        shape = data.object_by_key(finding.primary_key) if finding.primary_key else None
        return Issue(
            rule_id=finding.rule_id,
            severity=finding.severity,
            level=get_rule_class(finding.rule_id).level,
            task_id=data.task_id,
            job_id=data.job_for_frame(finding.frame),
            frame=finding.frame,
            message=finding.message,
            fingerprint=value,
            object_key=finding.primary_key,
            object_keys=finding.object_keys,
            shape_id=shape.shape_id if shape is not None else None,
            track_id=shape.track_id if shape is not None else None,
            label=finding.label,
            details=finding.details,
        )
