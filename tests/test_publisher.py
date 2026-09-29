"""Test publisher đẩy issue lên CVAT (dùng client giả, không cần server)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from conftest import make_shape, make_task_data, run_rules
from qaqc.fingerprint import extract_fingerprint
from qaqc.model import JobInfo, Severity
from qaqc.publishers import CvatIssuePublisher, build_comment, build_message
from qaqc.publishers.cvat_issues import issue_position
from qaqc.report import Issue, QAReport


# ---------------------------------------------------------------------------
# Client giả
# ---------------------------------------------------------------------------
class _Record:
    """Bản ghi issue trên "server" giả."""

    def __init__(self, issue_id: int, job: int, frame: int, message: str) -> None:
        self.id = issue_id
        self.job = job
        self.frame = frame
        self.message = message
        self.resolved = False


class _IssueEntity:
    """Giống ``cvat_sdk.core.proxies.issues.Issue`` (thuộc tính + update)."""

    def __init__(self, record: _Record) -> None:
        self._record = record
        self.id = record.id
        self.frame = record.frame
        self.message = record.message
        self.resolved = record.resolved

    def update(self, values: Any) -> _IssueEntity:
        self._record.resolved = bool(values.resolved)
        self.resolved = self._record.resolved
        return self


class _FakeIssuesRepo:
    """Giống ``client.issues``."""

    def __init__(self, store: list[_Record]) -> None:
        self._store = store

    def list(self, **kwargs: Any) -> list[_IssueEntity]:
        job_id = kwargs.get("job_id")
        return [
            _IssueEntity(record) for record in self._store if job_id is None or record.job == job_id
        ]

    def create(self, spec: Any) -> _IssueEntity:
        record = _Record(len(self._store) + 1, spec.job, spec.frame, spec.message)
        self._store.append(record)
        return _IssueEntity(record)

    def retrieve(self, issue_id: int) -> _IssueEntity:
        for record in self._store:
            if record.id == issue_id:
                return _IssueEntity(record)
        raise KeyError(issue_id)


class _FakeCommentsRepo:
    """Giống ``client.comments``."""

    def __init__(self) -> None:
        self.comments: list[tuple[int, str]] = []

    def create(self, spec: Any) -> Any:
        self.comments.append((spec.issue, spec.message))
        return spec


class FakeClient:
    """Client CVAT giả để test idempotency/throttle mà không gọi mạng."""

    def __init__(self) -> None:
        self.store: list[_Record] = []
        self.issues = _FakeIssuesRepo(self.store)
        self.comments = _FakeCommentsRepo()


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------
@pytest.fixture
def client() -> FakeClient:
    """Client giả rỗng."""
    return FakeClient()


@pytest.fixture
def task_data():
    """Task có 2 job và 1 lỗi error ở mỗi job."""
    return make_task_data(
        [
            make_shape("s1", frame=1, points=(60, 10, 60, 40)),
            make_shape("s2", frame=6, shape_id=2, points=(10, 10, 12, 12)),
        ],
        jobs=[
            JobInfo(id=7, start_frame=0, stop_frame=4),
            JobInfo(id=8, start_frame=5, stop_frame=9),
        ],
    )


@pytest.fixture
def report(task_data) -> QAReport:
    """Báo cáo có lỗi error (job 7) và warning (job 8)."""
    return run_rules(task_data, {"invalid_size": {}, "tiny_box": {}})


# ---------------------------------------------------------------------------
# Hàm thuần
# ---------------------------------------------------------------------------
def test_build_message_contains_tag_fingerprint_and_severity() -> None:
    """Message chứa tag nhận diện, severity, fingerprint và version."""
    issue = Issue(
        rule_id="invalid_size",
        severity=Severity.ERROR,
        task_id=42,
        frame=3,
        message="[invalid_size] ...",
        fingerprint="deadbeef",
    )
    message = build_message(issue)
    assert message.startswith("[QA][sev=error][fp=deadbeef][v=1] ")
    assert extract_fingerprint(message) == "deadbeef"


def test_build_comment_is_json_with_details() -> None:
    """Comment chi tiết là JSON chứa rule/frame/details."""
    issue = Issue(
        rule_id="duplicate_bbox",
        severity=Severity.ERROR,
        task_id=42,
        frame=0,
        message="msg",
        fingerprint="deadbeef",
        details={"iou": 0.91},
    )
    payload = json.loads(build_comment(issue))
    assert payload["rule_id"] == "duplicate_bbox"
    assert payload["details"] == {"iou": 0.91}
    assert payload["schema"] == "qaqc/issue/1"


def test_issue_position_uses_bbox_center_and_clamps() -> None:
    """``position`` là tâm bbox và được kẹp trong khung ảnh."""
    data = make_task_data([], frame_size=(100, 50))
    inside = Issue(
        rule_id="r",
        severity=Severity.ERROR,
        task_id=1,
        frame=0,
        message="m",
        fingerprint="f",
        details={"bbox": [10, 5, 50, 45]},
    )
    assert issue_position(inside, data) == [30.0, 25.0]

    outside = Issue(
        rule_id="r",
        severity=Severity.ERROR,
        task_id=1,
        frame=0,
        message="m",
        fingerprint="f",
        details={"bbox": [90, 40, 110, 60]},
    )
    assert issue_position(outside, data) == [99.0, 49.0]
    assert issue_position(inside, None) == [30.0, 25.0]


def test_issue_position_defaults_to_origin() -> None:
    """Không có bbox -> ``[0, 0]`` (vẫn đúng frame)."""
    issue = Issue(
        rule_id="r",
        severity=Severity.ERROR,
        task_id=1,
        frame=2,
        message="m",
        fingerprint="f",
    )
    assert issue_position(issue) == [0.0, 0.0]


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------
def test_publish_creates_issue_with_details_comment(client, report, task_data) -> None:
    """Tạo issue cho lỗi error (mặc định) và đính kèm comment JSON."""
    publisher = CvatIssuePublisher(client, max_issues_per_job=0)
    result = publisher.publish(report, data=task_data)

    assert result.created == 1
    assert result.skipped_severity == 1  # tiny_box là warning
    assert len(client.store) == 1
    record = client.store[0]
    assert record.job == 7
    assert record.frame == 1
    assert extract_fingerprint(record.message)
    assert json.loads(client.comments.comments[0][1])["rule_id"] == "invalid_size"


def test_publish_is_idempotent(client, report, task_data) -> None:
    """Chạy lại lần 2 không tạo issue trùng (dựa trên fingerprint)."""
    publisher = CvatIssuePublisher(client, max_issues_per_job=0)
    publisher.publish(report, data=task_data)
    second = publisher.publish(report, data=task_data)

    assert second.created == 0
    assert second.skipped_existing == 1
    assert len(client.store) == 1


def test_publish_respects_min_severity(client, report, task_data) -> None:
    """``--publish-severity warning`` đẩy cả lỗi warning."""
    publisher = CvatIssuePublisher(client, min_severity=Severity.WARNING, max_issues_per_job=0)
    result = publisher.publish(report, data=task_data)

    assert result.created == 2
    assert {record.job for record in client.store} == {7, 8}


def test_publish_caps_issues_per_job(client) -> None:
    """Giới hạn số issue mỗi job để không spam CVAT."""
    data = make_task_data(
        [
            make_shape(f"s{index}", frame=index, shape_id=index, points=(60, 10, 60, 40))
            for index in range(3)
        ],
        jobs=[JobInfo(id=7, start_frame=0, stop_frame=9)],
    )
    report = run_rules(data, {"invalid_size": {}})
    result = CvatIssuePublisher(client, max_issues_per_job=2).publish(report, data=data)

    assert result.created == 2
    assert result.skipped_cap == 1
    assert len(client.store) == 2


def test_publish_caps_total_issues(client, report, task_data) -> None:
    """Giới hạn tổng số issue cho cả lần chạy."""
    publisher = CvatIssuePublisher(client, max_issues_per_job=0, max_issues_total=1)
    result = publisher.publish(report, data=task_data)
    assert result.created == 1
    assert len(client.store) == 1


def test_publish_dry_run_writes_nothing(client, report, task_data) -> None:
    """``--dry-run`` chỉ mô phỏng, không gọi API ghi."""
    publisher = CvatIssuePublisher(client, dry_run=True, max_issues_per_job=0)
    result = publisher.publish(report, data=task_data)

    assert result.dry_run is True
    assert result.created == 1
    assert client.store == []
    assert client.comments.comments == []
    assert result.created_items and result.created_items[0][0] == 7


def test_publish_skips_issues_without_job(client) -> None:
    """Không map được frame -> job thì bỏ qua và ghi nhận."""
    data = make_task_data([make_shape("s1", frame=1, points=(60, 10, 60, 40))])
    publisher = CvatIssuePublisher(client, max_issues_per_job=0)
    result = publisher.publish(run_rules(data, {"invalid_size": {}}), data=data)

    assert result.skipped_unmapped == 1
    assert client.store == []


def test_publish_skips_resolved_issues_by_default(client, report, task_data) -> None:
    """Issue đã resolved thì không tạo lại (mặc định)."""
    publisher = CvatIssuePublisher(client, max_issues_per_job=0)
    publisher.publish(report, data=task_data)
    client.store[0].resolved = True

    result = publisher.publish(report, data=task_data)
    assert result.skipped_resolved == 1
    assert result.created == 0


def test_publish_reopens_resolved_issue_when_enabled(client, report, task_data) -> None:
    """``--reopen-resolved`` mở lại issue nếu lỗi vẫn còn."""
    CvatIssuePublisher(client, max_issues_per_job=0).publish(report, data=task_data)
    client.store[0].resolved = True

    reopening = CvatIssuePublisher(client, max_issues_per_job=0, reopen_resolved=True)
    result = reopening.publish(report, data=task_data)

    assert result.reopened == 1
    assert client.store[0].resolved is False
    assert len(client.store) == 1


def test_publish_resolves_stale_issues(client, report, task_data) -> None:
    """``--resolve-stale`` đánh dấu resolved cho issue cũ không còn lỗi."""
    CvatIssuePublisher(client, max_issues_per_job=0).publish(report, data=task_data)
    assert client.store[0].resolved is False

    clean = run_rules(make_task_data([]), {"invalid_size": {}})
    result = CvatIssuePublisher(client, resolve_stale=True).publish(clean, data=task_data)

    assert result.resolved_stale == 1
    assert client.store[0].resolved is True


def test_publish_without_details_comment(client, report, task_data) -> None:
    """``--no-details`` không tạo comment chi tiết."""
    publisher = CvatIssuePublisher(client, max_issues_per_job=0, post_details=False)
    publisher.publish(report, data=task_data)
    assert client.comments.comments == []


def test_publish_result_summary_lines(client, report, task_data) -> None:
    """Tóm tắt kết quả publish có đủ số liệu."""
    result = CvatIssuePublisher(client, max_issues_per_job=0).publish(report, data=task_data)
    text = "\n".join(result.summary_lines())
    assert "tạo mới 1" in text
    assert "Bỏ qua" in text
    assert result.published == 1


def test_publish_reports_api_errors(client, report, task_data) -> None:
    """Lỗi khi gọi API không làm hỏng cả lần chạy, được ghi vào ``errors``."""

    def boom(spec: Any) -> Any:
        raise RuntimeError("server từ chối")

    client.issues.create = boom  # type: ignore[method-assign]
    result = CvatIssuePublisher(client, max_issues_per_job=0).publish(report, data=task_data)

    assert result.created == 0
    assert result.failed == 1
    assert any("server từ chối" in error for error in result.errors)
