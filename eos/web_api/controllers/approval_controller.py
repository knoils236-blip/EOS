from datetime import UTC, datetime
from typing import Any, ClassVar

from litestar import Controller, get, post
from pydantic import BaseModel, Field
from sqlalchemy import select

from eos.approvals.entities import ApprovalKind, ApprovalRequest, ApprovalRequestModel, ApprovalStatus
from eos.auth.authorization import DEV_SUPERUSER, require_role, stamp_owner
from eos.auth.entities.user_role import AuthenticatedUser, Role
from eos.campaigns.entities.campaign import CampaignSubmission
from eos.database.abstract_sql_db_interface import AsyncDbSession
from eos.orchestration.orchestrator import Orchestrator
from eos.protocols.entities.protocol_run import ProtocolRunSubmission
from eos.protocols.exceptions import EosProtocolRunExecutionError
from eos.tasks.entities.task import TaskSubmission
from eos.web_api.admission import labs_needing_approval, protocol_labs
from eos.web_api.exception_handling import APIError
from eos.protocols.preflight import reject_reserved_meta


class ProtocolRunRequest(BaseModel):
    submission: ProtocolRunSubmission
    reason: str | None = Field(None, max_length=5000)


class CampaignRequest(BaseModel):
    submission: CampaignSubmission
    reason: str | None = Field(None, max_length=5000)


class TaskRequest(BaseModel):
    submission: TaskSubmission
    reason: str | None = Field(None, max_length=5000)


class Decision(BaseModel):
    note: str | None = Field(None, max_length=5000)


class ApprovalController(Controller):
    """
    Submissions that go through the execution admission policy (eos.web_api.admission): they start at once, or wait
    for a lab admin to approve them. Deciding is a signed-in person's step: automation cannot, nor can the requester.
    """

    path = "/approvals"
    guards: ClassVar = [require_role(Role.VIEWER)]

    @post("/protocol-runs", guards=[require_role(Role.SUBMITTER)])
    async def request_protocol_run(
        self, data: ProtocolRunRequest, db: AsyncDbSession, orchestrator: Orchestrator, user: AuthenticatedUser
    ) -> dict[str, Any]:
        stamp_owner(data.submission, user)
        _reject_reserved_meta(data.submission)
        labs = await protocol_labs(db, data.submission.type)
        return await _request(db, orchestrator, user, "protocol_run", data.submission, labs, data.reason)

    @post("/campaigns", guards=[require_role(Role.SUBMITTER)])
    async def request_campaign(
        self, data: CampaignRequest, db: AsyncDbSession, orchestrator: Orchestrator, user: AuthenticatedUser
    ) -> dict[str, Any]:
        stamp_owner(data.submission, user)
        labs = await protocol_labs(db, data.submission.protocol)
        return await _request(db, orchestrator, user, "campaign", data.submission, labs, data.reason)

    @post("/tasks", guards=[require_role(Role.SUBMITTER)])
    async def request_task(
        self, data: TaskRequest, db: AsyncDbSession, orchestrator: Orchestrator, user: AuthenticatedUser
    ) -> dict[str, Any]:
        if user is not DEV_SUPERUSER:
            data.submission.meta["submitted_by"] = user.sub
        labs = sorted({device.lab_name for device in data.submission.devices.values()})
        return await _request(db, orchestrator, user, "task", data.submission, labs, data.reason)

    @get("/")
    async def list_requests(self, db: AsyncDbSession, status: ApprovalStatus | None = None) -> list[ApprovalRequest]:
        query = select(ApprovalRequestModel).order_by(ApprovalRequestModel.id.desc()).limit(200)
        if status is not None:
            query = query.where(ApprovalRequestModel.status == status)
        return [ApprovalRequest.model_validate(row) for row in (await db.execute(query)).scalars()]

    @post("/{approval_id:int}/approve", guards=[require_role(Role.LAB_ADMIN)])
    async def approve(
        self, approval_id: int, data: Decision, db: AsyncDbSession, orchestrator: Orchestrator, user: AuthenticatedUser
    ) -> ApprovalRequest:
        row = await _locked(db, approval_id)
        _require_decider(user)
        if row.requested_by == user.sub and user is not DEV_SUPERUSER:
            raise APIError(status_code=403, detail="Requesters cannot approve their own submissions")
        # Retrying an approval whose response was lost returns it without submitting a second time
        if row.status == "approved":
            return ApprovalRequest.model_validate(row)
        _require_pending(row)
        if row.manifest_hash is not None and _plan_hash(orchestrator, row.kind, row.payload) != row.manifest_hash:
            raise APIError(
                status_code=409,
                detail="The plan changed after this request was made (definitions, lab configuration or plugin code). "
                "Reject it and submit again so the new plan is what gets approved.",
            )
        _decide(row, "approved", user, data.note)
        # A failed submission rolls the whole request back, leaving it pending to retry or reject
        await _submit(db, orchestrator, row.kind, row.payload)
        return ApprovalRequest.model_validate(row)

    @post("/{approval_id:int}/reject", guards=[require_role(Role.LAB_ADMIN)])
    async def reject(
        self, approval_id: int, data: Decision, db: AsyncDbSession, user: AuthenticatedUser
    ) -> ApprovalRequest:
        row = await _locked(db, approval_id)
        _require_decider(user)
        _require_pending(row)
        _decide(row, "rejected", user, data.note)
        return ApprovalRequest.model_validate(row)


async def _request(
    db: AsyncDbSession,
    orchestrator: Orchestrator,
    user: AuthenticatedUser,
    kind: ApprovalKind,
    submission: ProtocolRunSubmission | CampaignSubmission | TaskSubmission,
    labs: list[str],
    reason: str | None,
) -> dict[str, Any]:
    payload = submission.model_dump(mode="json")
    needing = await labs_needing_approval(db, labs, user)
    if not needing:
        await _submit(db, orchestrator, kind, payload)
        return {"status": "submitted", "labs": labs}
    row = ApprovalRequestModel(
        kind=kind,
        name=submission.name,
        labs=labs,
        payload=payload,
        reason=reason,
        requested_by=user.sub,
        manifest_hash=_plan_hash(orchestrator, kind, payload),
    )
    db.add(row)
    await db.flush()
    return {"status": "pending_approval", "approval_id": row.id, "approval_labs": needing}


_SUBMISSION_TYPES = {"protocol_run": ProtocolRunSubmission, "campaign": CampaignSubmission, "task": TaskSubmission}


def _plan_hash(orchestrator: Orchestrator, kind: str, payload: dict[str, Any]) -> str:
    try:
        return orchestrator.protocols.plan_hash(kind, _SUBMISSION_TYPES[kind].model_validate(payload))
    except EosProtocolRunExecutionError as e:
        raise APIError(status_code=409, detail=str(e)) from e


async def _locked(db: AsyncDbSession, approval_id: int) -> ApprovalRequestModel:
    row = await db.get(ApprovalRequestModel, approval_id, with_for_update=True)
    if row is None:
        raise APIError(status_code=404, detail="Approval request not found")
    return row


def _require_decider(user: AuthenticatedUser) -> None:
    if user.automation:
        raise APIError(status_code=403, detail="Automation cannot decide approval requests; sign in to decide")


def _require_pending(row: ApprovalRequestModel) -> None:
    if row.status != "pending":
        raise APIError(status_code=409, detail=f"Approval request is already {row.status}")


def _decide(row: ApprovalRequestModel, status: ApprovalStatus, user: AuthenticatedUser, note: str | None) -> None:
    row.status, row.decided_by, row.note, row.decided_at = status, user.sub, note, datetime.now(UTC)


async def _submit(db: AsyncDbSession, orchestrator: Orchestrator, kind: str, payload: dict[str, Any]) -> None:
    submission = _SUBMISSION_TYPES[kind].model_validate(payload)
    if kind == "protocol_run":
        await orchestrator.protocols.submit_protocol_run(db, submission)
    elif kind == "campaign":
        await orchestrator.campaigns.submit_campaign(db, submission)
    else:
        await orchestrator.tasks.submit_task(db, submission)


def _reject_reserved_meta(submission: ProtocolRunSubmission) -> None:
    try:
        reject_reserved_meta(submission)
    except EosProtocolRunExecutionError as e:
        raise APIError(status_code=400, detail=str(e)) from e
