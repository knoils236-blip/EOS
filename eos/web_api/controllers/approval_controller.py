from datetime import UTC, datetime
from typing import Any, ClassVar

from litestar import Controller, get, post
from pydantic import BaseModel, Field
from sqlalchemy import select

from eos.approvals.entities import ApprovalKind, ApprovalRequest, ApprovalRequestModel, ApprovalStatus
from eos.auth.authorization import require_role, stamp_owner
from eos.auth.entities.user_role import AuthenticatedUser, Role
from eos.campaigns.entities.campaign import CampaignSubmission
from eos.configuration.entities.definition import DefinitionModel
from eos.database.abstract_sql_db_interface import AsyncDbSession
from eos.orchestration.orchestrator import Orchestrator
from eos.protocols.entities.protocol_run import ProtocolRunSubmission
from eos.web_api.exception_handling import APIError


class ProtocolRunRequest(BaseModel):
    submission: ProtocolRunSubmission
    reason: str | None = Field(None, max_length=5000)


class CampaignRequest(BaseModel):
    submission: CampaignSubmission
    reason: str | None = Field(None, max_length=5000)


class Decision(BaseModel):
    note: str | None = Field(None, max_length=5000)


class ApprovalController(Controller):
    """
    Agent submissions: run at once on simulated labs, otherwise wait for a person to approve them.
    Agents reach EOS through MCP, which offers no approval tool, so approving stays a human step in the web UI.
    """

    path = "/approvals"
    guards: ClassVar = [require_role(Role.VIEWER)]

    @post("/protocol-runs", guards=[require_role(Role.SUBMITTER)])
    async def request_protocol_run(
        self, data: ProtocolRunRequest, db: AsyncDbSession, orchestrator: Orchestrator, user: AuthenticatedUser
    ) -> dict[str, Any]:
        stamp_owner(data.submission, user)
        return await self._request(db, orchestrator, "protocol_run", data.submission, data.submission.type, data.reason)

    @post("/campaigns", guards=[require_role(Role.SUBMITTER)])
    async def request_campaign(
        self, data: CampaignRequest, db: AsyncDbSession, orchestrator: Orchestrator, user: AuthenticatedUser
    ) -> dict[str, Any]:
        stamp_owner(data.submission, user)
        return await self._request(db, orchestrator, "campaign", data.submission, data.submission.protocol, data.reason)

    @get("/")
    async def list_requests(self, db: AsyncDbSession, status: ApprovalStatus | None = None) -> list[ApprovalRequest]:
        query = select(ApprovalRequestModel).order_by(ApprovalRequestModel.id.desc()).limit(200)
        if status is not None:
            query = query.where(ApprovalRequestModel.status == status)
        return [ApprovalRequest.model_validate(row) for row in (await db.execute(query)).scalars()]

    @post("/{approval_id:int}/approve", guards=[require_role(Role.SUBMITTER)])
    async def approve(
        self, approval_id: int, data: Decision, db: AsyncDbSession, orchestrator: Orchestrator, user: AuthenticatedUser
    ) -> ApprovalRequest:
        row = await _pending(db, approval_id)
        _decide(row, "approved", user, data.note)
        # A failed submission rolls the whole request back, leaving it pending to retry or reject
        await _submit(db, orchestrator, row.kind, row.payload)
        return ApprovalRequest.model_validate(row)

    @post("/{approval_id:int}/reject", guards=[require_role(Role.SUBMITTER)])
    async def reject(
        self, approval_id: int, data: Decision, db: AsyncDbSession, user: AuthenticatedUser
    ) -> ApprovalRequest:
        row = await _pending(db, approval_id)
        _decide(row, "rejected", user, data.note)
        return ApprovalRequest.model_validate(row)

    async def _request(
        self,
        db: AsyncDbSession,
        orchestrator: Orchestrator,
        kind: ApprovalKind,
        submission: ProtocolRunSubmission | CampaignSubmission,
        protocol_type: str,
        reason: str | None,
    ) -> dict[str, Any]:
        protocol = await _definition(db, "protocol", protocol_type)
        labs = list(protocol["labs"])
        real = [lab for lab in labs if not (await _definition(db, "lab", lab)).get("simulated", False)]
        payload = submission.model_dump(mode="json")
        if not real:
            await _submit(db, orchestrator, kind, payload)
            return {"status": "submitted", "labs": labs}
        row = ApprovalRequestModel(
            kind=kind, name=submission.name, labs=labs, payload=payload, reason=reason, requested_by=submission.owner
        )
        db.add(row)
        await db.flush()
        return {"status": "pending_approval", "approval_id": row.id, "real_labs": real}


async def _definition(db: AsyncDbSession, definition_type: str, name: str) -> dict[str, Any]:
    query = select(DefinitionModel).where(DefinitionModel.type == definition_type, DefinitionModel.name == name)
    definition = (await db.execute(query)).scalar_one_or_none()
    if definition is None:
        raise APIError(status_code=404, detail=f"Unknown {definition_type} '{name}'")
    return definition.data


async def _pending(db: AsyncDbSession, approval_id: int) -> ApprovalRequestModel:
    row = await db.get(ApprovalRequestModel, approval_id, with_for_update=True)
    if row is None:
        raise APIError(status_code=404, detail="Approval request not found")
    if row.status != "pending":
        raise APIError(status_code=409, detail=f"Approval request is already {row.status}")
    return row


def _decide(row: ApprovalRequestModel, status: ApprovalStatus, user: AuthenticatedUser, note: str | None) -> None:
    row.status, row.decided_by, row.note, row.decided_at = status, user.sub, note, datetime.now(UTC)


async def _submit(db: AsyncDbSession, orchestrator: Orchestrator, kind: str, payload: dict[str, Any]) -> None:
    if kind == "protocol_run":
        await orchestrator.protocols.submit_protocol_run(db, ProtocolRunSubmission.model_validate(payload))
    else:
        await orchestrator.campaigns.submit_campaign(db, CampaignSubmission.model_validate(payload))
