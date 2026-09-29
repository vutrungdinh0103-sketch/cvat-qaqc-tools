"""Đẩy các lỗi QA/QC thành *issue* trên CVAT (idempotent theo fingerprint).

Cách hoạt động
--------------
1. Mỗi lỗi có một ``fingerprint`` ổn định (:mod:`qaqc.fingerprint`).
2. Fingerprint được nhúng vào ``message`` của issue dạng ``fp=1a2b3c4d`` vì API
   CVAT không có trường metadata riêng cho issue.
3. Trước khi tạo, publisher đọc issue hiện có của job (``client.issues.list``)
   và bỏ qua các fingerprint đã tồn tại -> chạy lại nhiều lần không sinh trùng.
4. Chi tiết lỗi (IoU, bbox, attribute thiếu...) được ghi vào **comment** đầu tiên
   dạng JSON để vẫn tra cứu được từ UI.
5. Tuỳ chọn ``resolve_stale``: issue do tool tạo ở lần chạy trước nhưng không còn
   xuất hiện trong lần này sẽ được đánh dấu *resolved*.

Ví dụ::

    client = CVATConfig.from_env().create_client()
    publisher = CvatIssuePublisher(client, dry_run=False, min_severity=Severity.ERROR)
    result = publisher.publish(report, data=task_data)
    print("\\n".join(result.summary_lines()))
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from cvat_sdk import models

from .. import ISSUE_TAG
from ..fingerprint import extract_fingerprints, format_fingerprint
from ..geometry import bbox_center, clamp
from ..model import SEVERITY_RANK, Severity, TaskData
from ..report import Issue, QAReport

LOGGER = logging.getLogger("cvat_qaqc.publisher")

#: Phiên bản định dạng message của issue (đổi khi cần parse lại khác đi).
ISSUE_MESSAGE_VERSION = 1

#: Các khoá trong ``details`` có thể chứa bbox để đặt ``position`` của issue.
_BBOX_KEYS: tuple[str, ...] = ("bbox", "bbox_a", "inner_bbox", "outer_bbox")


@dataclass
class PublishResult:
    """Kết quả một lần publish (dùng cho log/CLI/CI)."""

    dry_run: bool = False
    created: int = 0
    reopened: int = 0
    resolved_stale: int = 0
    skipped_existing: int = 0
    skipped_severity: int = 0
    skipped_cap: int = 0
    skipped_unmapped: int = 0
    skipped_resolved: int = 0
    failed: int = 0
    created_items: list[tuple[int, int, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def published(self) -> int:
        """Số issue đã tạo/mở lại (không tính lần chạy dry-run)."""
        return self.created + self.reopened

    def summary_lines(self) -> list[str]:
        """Các dòng tóm tắt để in ra CLI."""
        prefix = "[DRY-RUN] " if self.dry_run else ""
        lines = [
            f"{prefix}Publisher CVAT: tạo mới {self.created}, mở lại {self.reopened}, "
            f"đánh dấu hết lỗi {self.resolved_stale}, lỗi khi gọi API {self.failed}",
            f"  - Bỏ qua: đã tồn tại {self.skipped_existing}, "
            f"dưới ngưỡng severity {self.skipped_severity}, "
            f"quá giới hạn/job {self.skipped_cap}, "
            f"đang resolved {self.skipped_resolved}, "
            f"không map được job {self.skipped_unmapped}",
        ]
        lines.extend(f"  ! {error}" for error in self.errors)
        return lines


def build_message(issue: Issue, *, tag: str = ISSUE_TAG) -> str:
    """Tạo message của issue: ``[QA][sev=...][fp=...][v=1] <message gốc>``."""
    return (
        f"{tag}[sev={issue.severity.value}][{format_fingerprint(issue.fingerprint)}]"
        f"[v={ISSUE_MESSAGE_VERSION}] {issue.message}"
    )


def build_comment(issue: Issue) -> str:
    """Nội dung comment JSON mô tả chi tiết lỗi (đính kèm khi tạo issue)."""
    payload = {
        "schema": "qaqc/issue/1",
        "fingerprint": issue.fingerprint,
        "rule_id": issue.rule_id,
        "severity": issue.severity.value,
        "task_id": issue.task_id,
        "job_id": issue.job_id,
        "frame": issue.frame,
        "label": issue.label,
        "object_key": issue.object_key,
        "object_keys": list(issue.object_keys),
        "shape_id": issue.shape_id,
        "track_id": issue.track_id,
        "details": issue.details,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def issue_position(issue: Issue, data: TaskData | None = None) -> list[float]:
    """Toạ độ ``position`` của issue = tâm object, kẹp trong khung ảnh.

    CVAT yêu cầu ``position`` là ``[x, y]``; nếu không xác định được thì dùng
    ``[0, 0]`` (vẫn đúng frame nên vẫn hữu ích trong UI).
    """
    center: tuple[float, float] | None = None
    for key in _BBOX_KEYS:
        bbox = issue.details.get(key)
        if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
            try:
                x1, y1, x2, y2 = (float(value) for value in bbox)
                center = bbox_center((x1, y1, x2, y2))
            except (TypeError, ValueError):
                center = None
            if center is not None:
                break
    if center is None:
        return [0.0, 0.0]

    if data is not None:
        size = data.frame_size(issue.frame)
        if size is not None:
            width, height = size
            return [
                float(clamp(center[0], 0.0, max(float(width - 1), 0.0))),
                float(clamp(center[1], 0.0, max(float(height - 1), 0.0))),
            ]

    return [float(center[0]), float(center[1])]


@dataclass
class _ExistingIssue:
    """Issue đã có trên CVAT (đã lọc theo tag của tool)."""

    id: int
    frame: int
    fingerprint: str
    resolved: bool


class CvatIssuePublisher:
    """Tạo/cập nhật issue trên CVAT từ :class:`QAReport`."""

    def __init__(
        self,
        client: Any,
        *,
        logger: logging.Logger | None = None,
        dry_run: bool = False,
        min_severity: Severity = Severity.ERROR,
        max_issues_per_job: int = 50,
        max_issues_total: int = 0,
        reopen_resolved: bool = False,
        resolve_stale: bool = False,
        post_details: bool = True,
        tag: str = ISSUE_TAG,
    ) -> None:
        """
        :param client: ``cvat_sdk.Client`` đã đăng nhập.
        :param dry_run: chỉ mô phỏng, không gọi API ghi.
        :param min_severity: chỉ đẩy issue có mức độ >= giá trị này.
        :param max_issues_per_job: giới hạn số issue tạo mới cho mỗi job.
        :param max_issues_total: giới hạn tổng số issue (0 = không giới hạn).
        :param reopen_resolved: mở lại issue đã resolved nếu lỗi vẫn còn.
        :param resolve_stale: tự đánh dấu resolved cho issue cũ không còn lỗi.
        :param post_details: ghi chi tiết JSON vào comment khi tạo issue.
        :param tag: tiền tố nhận diện issue do tool tạo.
        """
        self.client = client
        self.logger = logger or LOGGER
        self.dry_run = dry_run
        self.min_severity = min_severity
        self.max_issues_per_job = int(max_issues_per_job)
        self.max_issues_total = int(max_issues_total)
        self.reopen_resolved = reopen_resolved
        self.resolve_stale = resolve_stale
        self.post_details = post_details
        self.tag = tag

    # ------------------------------------------------------------------
    # Publish chính
    # ------------------------------------------------------------------
    def publish(self, report: QAReport, *, data: TaskData | None = None) -> PublishResult:
        """Đẩy toàn bộ issue của báo cáo lên CVAT.

        :param report: báo cáo QA/QC (đã có ``job_id`` cho từng issue).
        :param data: dữ liệu task (dùng để tính ``position`` theo kích thước frame).
        """
        result = PublishResult(dry_run=self.dry_run)

        selected = self._select_issues(report, result)
        by_job: dict[int, list[Issue]] = {}
        for issue in selected:
            job_id = issue.job_id
            if job_id is None:
                result.skipped_unmapped += 1
                self.logger.warning(
                    "Không xác định được job cho lỗi tại frame %s (rule=%s) - bỏ qua.",
                    issue.frame,
                    issue.rule_id,
                )
                continue
            by_job.setdefault(job_id, []).append(issue)

        total_published = 0
        # Job cần xử lý = job có lỗi hiện tại + job đã biết từ dữ liệu task
        # (job không còn lỗi vẫn phải được quét để resolve issue cũ).
        known_jobs = {job.id for job in (data.jobs if data is not None else ())}
        for job_id in sorted(set(by_job) | known_jobs):
            issues = by_job.get(job_id, [])
            existing = self._load_existing(job_id)
            # Tập fingerprint "còn lỗi" lấy TRƯỚC khi áp giới hạn số issue,
            # nếu không các issue bị cắt do --max-issues-per-job sẽ bị resolve oan.
            current_fps: set[str] = {issue.fingerprint for issue in issues}

            remaining = issues
            if self.max_issues_per_job > 0 and len(remaining) > self.max_issues_per_job:
                result.skipped_cap += len(remaining) - self.max_issues_per_job
                remaining = remaining[: self.max_issues_per_job]

            for issue in remaining:
                total_published += self._publish_one(job_id, issue, existing, result, data)
                if self.max_issues_total and total_published >= self.max_issues_total:
                    break

            if self.resolve_stale and not self.dry_run:
                self._resolve_stale(job_id, existing, current_fps, result)

            if self.max_issues_total and total_published >= self.max_issues_total:
                self.logger.info(
                    "Đã đạt giới hạn --max-issues-total=%s, dừng publish.",
                    self.max_issues_total,
                )
                break

        return result

    # ------------------------------------------------------------------
    # Nội bộ
    # ------------------------------------------------------------------
    def _select_issues(self, report: QAReport, result: PublishResult) -> list[Issue]:
        """Lọc issue theo mức độ tối thiểu (báo cáo đã sắp xếp severity giảm dần)."""
        threshold = self.min_severity.rank
        selected: list[Issue] = []
        for issue in report.issues:
            if SEVERITY_RANK[issue.severity] < threshold:
                result.skipped_severity += 1
                continue
            selected.append(issue)
        return selected

    def _load_existing(self, job_id: int) -> dict[str, _ExistingIssue]:
        """Đọc issue hiện có của job và index theo fingerprint do tool tạo."""
        existing: dict[str, _ExistingIssue] = {}
        try:
            items = self.client.issues.list(job_id=job_id)
        except Exception as exc:
            self.logger.warning(
                "Không đọc được danh sách issue của job #%s: %s: %s",
                job_id,
                type(exc).__name__,
                exc,
            )
            return existing

        for item in items:
            message = str(getattr(item, "message", "") or "")
            if self.tag and self.tag not in message:
                continue
            for value in extract_fingerprints(message):
                existing.setdefault(
                    value,
                    _ExistingIssue(
                        id=int(getattr(item, "id", 0) or 0),
                        frame=int(getattr(item, "frame", 0) or 0),
                        fingerprint=value,
                        resolved=bool(getattr(item, "resolved", False)),
                    ),
                )
        return existing

    def _publish_one(
        self,
        job_id: int,
        issue: Issue,
        existing: dict[str, _ExistingIssue],
        result: PublishResult,
        data: TaskData | None,
    ) -> int:
        """Tạo mới (hoặc mở lại) một issue. Trả ``1`` nếu coi như đã publish."""
        known = existing.get(issue.fingerprint)
        if known is not None:
            if not known.resolved:
                result.skipped_existing += 1
                self.logger.debug(
                    "Bỏ qua issue đã tồn tại: job=%s frame=%s fp=%s",
                    job_id,
                    issue.frame,
                    issue.fingerprint,
                )
                return 0
            if not self.reopen_resolved:
                result.skipped_resolved += 1
                return 0
            return self._reopen(job_id, issue, known, existing, result)

        return self._create(job_id, issue, existing, result, data)

    def _create(
        self,
        job_id: int,
        issue: Issue,
        existing: dict[str, _ExistingIssue],
        result: PublishResult,
        data: TaskData | None,
    ) -> int:
        """Tạo issue mới trên CVAT (kèm comment chi tiết nếu bật)."""
        message = build_message(issue, tag=self.tag)
        position = issue_position(issue, data)

        if self.dry_run:
            result.created += 1
            result.created_items.append((job_id, issue.frame, issue.fingerprint))
            self.logger.info(
                "[DRY-RUN] Sẽ tạo issue job=%s frame=%s fp=%s: %s",
                job_id,
                issue.frame,
                issue.fingerprint,
                issue.rule_id,
            )
            existing[issue.fingerprint] = _ExistingIssue(
                id=0, frame=issue.frame, fingerprint=issue.fingerprint, resolved=False
            )
            return 1

        try:
            created = self.client.issues.create(
                models.IssueWriteRequest(
                    job=job_id,
                    frame=int(issue.frame),
                    position=list(position),
                    message=message,
                )
            )
        except Exception as exc:
            result.failed += 1
            result.errors.append(
                f"Job {job_id}, frame {issue.frame}: tạo issue thất bại "
                f"({type(exc).__name__}: {exc})"
            )
            self.logger.error(result.errors[-1])
            return 0

        result.created += 1
        result.created_items.append((job_id, issue.frame, issue.fingerprint))
        existing[issue.fingerprint] = _ExistingIssue(
            id=int(getattr(created, "id", 0) or 0),
            frame=issue.frame,
            fingerprint=issue.fingerprint,
            resolved=False,
        )
        self.logger.info(
            "Đã tạo issue cho job=%s frame=%s (rule=%s, fp=%s).",
            job_id,
            issue.frame,
            issue.rule_id,
            issue.fingerprint,
        )

        if self.post_details:
            self._post_comment(int(getattr(created, "id", 0) or 0), build_comment(issue), result)

        return 1

    def _reopen(
        self,
        job_id: int,
        issue: Issue,
        known: _ExistingIssue,
        existing: dict[str, _ExistingIssue],
        result: PublishResult,
    ) -> int:
        """Mở lại issue đã bị đánh dấu resolved (lỗi vẫn còn)."""
        try:
            self.client.issues.retrieve(known.id).update(
                models.PatchedIssueWriteRequest(resolved=False)
            )
        except Exception as exc:
            result.failed += 1
            result.errors.append(
                f"Mở lại issue #{known.id} (job {job_id}) thất bại ({type(exc).__name__}: {exc})"
            )
            self.logger.error(result.errors[-1])
            return 0

        result.reopened += 1
        existing[issue.fingerprint] = _ExistingIssue(
            id=known.id, frame=known.frame, fingerprint=known.fingerprint, resolved=False
        )
        if self.post_details:
            self._post_comment(
                known.id,
                "QA: lỗi vẫn còn sau lần kiểm tra mới nhất, đã mở lại issue.\n"
                + build_comment(issue),
                result,
            )
        return 1

    def _resolve_stale(
        self,
        job_id: int,
        existing: dict[str, _ExistingIssue],
        current_fps: set[str],
        result: PublishResult,
    ) -> None:
        """Đánh dấu resolved cho issue cũ không còn xuất hiện trong lần chạy này."""
        for fingerprint, item in existing.items():
            if fingerprint in current_fps or item.resolved or not item.id:
                continue

            try:
                self.client.issues.retrieve(item.id).update(
                    models.PatchedIssueWriteRequest(resolved=True)
                )
            except Exception as exc:
                result.failed += 1
                result.errors.append(
                    f"Đánh dấu resolved issue #{item.id} (job {job_id}) thất bại "
                    f"({type(exc).__name__}: {exc})"
                )
                self.logger.error(result.errors[-1])
                continue

            result.resolved_stale += 1
            self.logger.info(
                "Đã đánh dấu resolved issue #%s (job=%s, fp=%s).",
                item.id,
                job_id,
                fingerprint,
            )
            if self.post_details:
                self._post_comment(
                    item.id,
                    "QA: lỗi này không còn xuất hiện trong lần kiểm tra mới nhất "
                    "-> tự động đánh dấu đã xử lý.",
                    result,
                )

    def _post_comment(self, issue_id: int, text: str, result: PublishResult) -> None:
        """Thêm comment vào issue (không làm hỏng lần chạy nếu thất bại)."""
        if not issue_id:
            return
        try:
            self.client.comments.create(models.CommentWriteRequest(issue=issue_id, message=text))
        except Exception as exc:
            result.errors.append(
                f"Không thêm được comment chi tiết cho issue #{issue_id} "
                f"({type(exc).__name__}: {exc})"
            )
            self.logger.warning(result.errors[-1])
