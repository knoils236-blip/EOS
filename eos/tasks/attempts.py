"""Execution attempts: each real launch of a task, persisted before the launch so that failures, cancellations,
restarts and resumes keep their own history instead of overwriting the logical task row."""

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func, select
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column

from eos.database.abstract_sql_db_interface import AsyncDbSession, Base

AttemptOutcome = Literal["running", "succeeded", "failed", "timed_out", "cancelled", "interrupted"]
# "safe": relaunching repeats nothing harmful; "manual": a person decides after a failed or uncertain attempt
RetryPolicy = Literal["safe", "manual"]


class TaskAttempt(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    protocol_run_name: str | None
    task_name: str
    attempt_number: int
    retry: RetryPolicy
    devices: dict[str, Any]
    external_request_id: str | None
    started_at: datetime
    ended_at: datetime | None
    outcome: AttemptOutcome
    error: str | None
    # None: no device confirmed anything (ordinary devices); otherwise what device finalization reported
    stop_confirmed: bool | None
    device_available: bool | None
    needs_manual_recovery: bool
    evidence: dict[str, Any]


class TaskAttemptModel(Base):
    __tablename__ = "task_attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # No foreign key to tasks: resuming a run deletes its unfinished task rows, but their attempts stay
    protocol_run_name: Mapped[str | None] = mapped_column(
        String(255), ForeignKey("protocol_runs.name", ondelete="CASCADE"), nullable=True, index=True
    )
    task_name: Mapped[str] = mapped_column(String(255), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    retry: Mapped[str] = mapped_column(String(8), nullable=False)
    devices: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default={})
    external_request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="running", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    stop_confirmed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    device_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    needs_manual_recovery: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(MutableDict.as_mutable(JSON), nullable=False, default={})

    __table_args__ = (UniqueConstraint("protocol_run_name", "task_name", "attempt_number", name="uq_task_attempt"),)


def _of_task(protocol_run_name: str | None, task_name: str) -> tuple:
    run = TaskAttemptModel.protocol_run_name
    return (run.is_(None) if protocol_run_name is None else run == protocol_run_name), (
        TaskAttemptModel.task_name == task_name
    )


async def start_attempt(
    db: AsyncDbSession, protocol_run_name: str | None, task_name: str, retry: RetryPolicy, devices: dict[str, Any]
) -> TaskAttemptModel:
    """Record a new attempt; numbering continues across retries and resumes of the same task."""
    previous = await db.scalar(
        select(func.max(TaskAttemptModel.attempt_number)).where(*_of_task(protocol_run_name, task_name))
    )
    attempt = TaskAttemptModel(
        id=str(uuid.uuid4()),
        protocol_run_name=protocol_run_name,
        task_name=task_name,
        attempt_number=(previous or 0) + 1,
        retry=retry,
        devices=devices,
        started_at=datetime.now(UTC),
        outcome="running",
        needs_manual_recovery=False,
        evidence={},
    )
    db.add(attempt)
    await db.flush()
    return attempt


async def other_attempts(db: AsyncDbSession, attempt: TaskAttemptModel) -> list[TaskAttemptModel]:
    """Every other attempt of the same task, across retries and resumes."""
    rows = await db.scalars(
        select(TaskAttemptModel).where(
            *_of_task(attempt.protocol_run_name, attempt.task_name), TaskAttemptModel.id != attempt.id
        )
    )
    return list(rows)


async def get_attempt(db: AsyncDbSession, attempt_id: str) -> TaskAttemptModel:
    return await db.get(TaskAttemptModel, attempt_id)


async def list_attempts(db: AsyncDbSession, protocol_run_name: str | None, task_name: str) -> list[TaskAttempt]:
    rows = await db.scalars(
        select(TaskAttemptModel)
        .where(*_of_task(protocol_run_name, task_name))
        .order_by(TaskAttemptModel.attempt_number)
    )
    return [TaskAttempt.model_validate(row) for row in rows]


async def end_attempt(
    db: AsyncDbSession,
    attempt_id: str,
    outcome: AttemptOutcome,
    error: str | None = None,
    files: list[str] | None = None,
    needs_manual_recovery: bool = False,
) -> None:
    attempt = await get_attempt(db, attempt_id)
    if attempt.outcome != "running":
        return  # Already ended, e.g. cancelled while its failure was being handled
    attempt.outcome, attempt.error, attempt.ended_at = outcome, error, datetime.now(UTC)
    attempt.needs_manual_recovery = needs_manual_recovery
    if files:
        attempt.evidence["files"] = list(dict.fromkeys([*attempt.evidence.get("files", []), *files]))
    await db.flush()


async def record_finalization(
    db: AsyncDbSession,
    attempt_id: str,
    slot: str,
    stop: bool,
    report: dict[str, Any] | None,
    error: Exception | None = None,
) -> None:
    """Record one device's finalization of the attempt. Several devices combine: any failure makes it False."""
    attempt = await get_attempt(db, attempt_id)
    confirmed = error is None
    available = confirmed and report["available"]
    if stop:
        attempt.stop_confirmed = confirmed and attempt.stop_confirmed is not False
    attempt.device_available = available and attempt.device_available is not False
    if report is not None and report.get("request_id"):
        attempt.external_request_id = report["request_id"]
    attempt.evidence.setdefault("finalization", {})[slot] = (
        {"error": str(error)} if error is not None else {"available": report["available"], "stop": stop}
    )
    await db.flush()


async def interrupt_running_attempts(db: AsyncDbSession) -> None:
    """At startup, close attempts left running by the previous orchestrator. Whether their devices stopped is
    decided by device reconciliation, not assumed here."""
    rows = await db.scalars(select(TaskAttemptModel).where(TaskAttemptModel.outcome == "running"))
    now = datetime.now(UTC)
    for attempt in rows:
        attempt.outcome, attempt.ended_at = "interrupted", now
        attempt.error = "The orchestrator stopped while this attempt was running"
        attempt.needs_manual_recovery = attempt.retry == "manual"
    await db.flush()
