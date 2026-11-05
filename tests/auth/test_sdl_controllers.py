"""Agent-facing endpoints: the lab notebook and approval of submissions to labs that are not simulated."""

from collections.abc import AsyncGenerator
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from litestar import Router
from litestar.di import Provide
from litestar.middleware import DefineMiddleware
from litestar.testing import create_test_client

from eos.auth.entities.user_role import AuthenticatedUser, Role, UserRoleModel
from eos.auth.middleware import EosAuthenticationMiddleware
from eos.auth.token_validator import TokenValidationError
from eos.configuration.entities.definition import DefinitionModel
from eos.configuration.eos_config import DatabaseType, DbConfig, SqliteDbConfig
from eos.database.abstract_sql_db_interface import AsyncDbSession
from eos.database.sqlite_db_interface import SqliteDbInterface
from eos.orchestration.orchestrator import Orchestrator
from eos.web_api.controllers.approval_controller import ApprovalController
from eos.web_api.controllers.notebook_controller import NotebookController
from eos.web_api.dependencies import provide_current_user

USERS = {"agent-token": AuthenticatedUser(sub="agent"), "viewer-token": AuthenticatedUser(sub="viewer")}


class Validator:
    async def validate(self, token: str) -> AuthenticatedUser:
        if token not in USERS:
            raise TokenValidationError("Invalid token")
        return USERS[token]

    async def fetch_userinfo(self, token: str) -> dict | None:
        return None


def definition(kind: str, name: str, data: dict) -> DefinitionModel:
    return DefinitionModel(type=kind, name=name, data=data, package_name="pkg", source_path="/x")


@pytest.fixture
async def db_interface():
    db_interface = SqliteDbInterface(DbConfig(type=DatabaseType.SQLITE, sqlite=SqliteDbConfig(in_memory=True)))
    await db_interface.initialize_database()
    async with db_interface.get_async_session() as db:
        db.add(UserRoleModel(sub="agent", role=Role.SUBMITTER, granted_by="test"))
        db.add(UserRoleModel(sub="viewer", role=Role.VIEWER, granted_by="test"))
        db.add(definition("lab", "twin_lab", {"name": "twin_lab", "simulated": True}))
        db.add(definition("lab", "real_lab", {"name": "real_lab"}))
        db.add(definition("protocol", "on_twin", {"type": "on_twin", "labs": ["twin_lab"]}))
        db.add(definition("protocol", "on_real", {"type": "on_real", "labs": ["twin_lab", "real_lab"]}))
    return db_interface


@pytest.fixture
def orchestrator():
    orchestrator = Mock(spec=Orchestrator)
    orchestrator.protocols = SimpleNamespace(submit_protocol_run=AsyncMock())
    orchestrator.campaigns = SimpleNamespace(submit_campaign=AsyncMock())
    return orchestrator


@pytest.fixture
def client(db_interface, orchestrator):
    async def provide_db() -> AsyncGenerator[AsyncDbSession, None]:
        async with db_interface.get_async_session() as db:
            yield db

    with create_test_client(
        route_handlers=[Router(path="/api", route_handlers=[ApprovalController, NotebookController])],
        middleware=[DefineMiddleware(EosAuthenticationMiddleware)],
        state={"db_interface": db_interface, "token_validator": Validator()},
        dependencies={
            "db": Provide(provide_db),
            "user": Provide(provide_current_user, sync_to_thread=False),
            "orchestrator": Provide(lambda: orchestrator, sync_to_thread=False),
        },
    ) as client:
        yield client


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def run(protocol_type: str) -> dict:
    return {"submission": {"name": f"{protocol_type}-1", "type": protocol_type}, "reason": "test the hypothesis"}


def test_simulated_lab_runs_start_at_once(client, orchestrator):
    response = client.post("/api/approvals/protocol-runs", json=run("on_twin"), headers=auth("agent-token"))
    assert response.json() == {"status": "submitted", "labs": ["twin_lab"]}
    submission = orchestrator.protocols.submit_protocol_run.await_args.args[1]
    assert (submission.name, submission.owner) == ("on_twin-1", "agent")


def test_real_lab_runs_wait_for_approval_then_submit_once(client, orchestrator):
    response = client.post("/api/approvals/protocol-runs", json=run("on_real"), headers=auth("agent-token"))
    body = response.json()
    assert body["status"] == "pending_approval"
    assert body["real_labs"] == ["real_lab"]
    orchestrator.protocols.submit_protocol_run.assert_not_awaited()

    pending = client.get("/api/approvals/?status=pending", headers=auth("viewer-token")).json()
    assert [(r["name"], r["reason"], r["requested_by"]) for r in pending] == [
        ("on_real-1", "test the hypothesis", "agent")
    ]

    approval_id = body["approval_id"]
    assert (
        client.post(f"/api/approvals/{approval_id}/approve", json={}, headers=auth("viewer-token")).status_code == 403
    )
    approved = client.post(f"/api/approvals/{approval_id}/approve", json={"note": "ok"}, headers=auth("agent-token"))
    assert approved.json()["status"] == "approved"
    orchestrator.protocols.submit_protocol_run.assert_awaited_once()
    again = client.post(f"/api/approvals/{approval_id}/reject", json={}, headers=auth("agent-token"))
    assert again.status_code == 409


def test_failed_submission_keeps_the_request_pending(client, orchestrator):
    approval_id = client.post("/api/approvals/protocol-runs", json=run("on_real"), headers=auth("agent-token")).json()[
        "approval_id"
    ]
    orchestrator.protocols.submit_protocol_run.side_effect = RuntimeError("lab busy")
    response = client.post(f"/api/approvals/{approval_id}/approve", json={}, headers=auth("agent-token"))
    assert response.status_code >= 400
    pending = client.get("/api/approvals/?status=pending", headers=auth("viewer-token")).json()
    assert [r["id"] for r in pending] == [approval_id]


def test_rejected_campaign_is_never_submitted(client, orchestrator):
    campaign = {
        "submission": {"name": "c1", "protocol": "on_real", "optimize": False, "global_parameters": {"t": {"x": 1}}}
    }
    approval_id = client.post("/api/approvals/campaigns", json=campaign, headers=auth("agent-token")).json()[
        "approval_id"
    ]
    rejected = client.post(
        f"/api/approvals/{approval_id}/reject", json={"note": "too costly"}, headers=auth("agent-token")
    )
    assert (rejected.json()["status"], rejected.json()["note"]) == ("rejected", "too costly")
    orchestrator.campaigns.submit_campaign.assert_not_awaited()


def test_notebook_records_authors_and_filters_by_campaign(client):
    for kind, content, campaign in [
        ("hypothesis", "More stock raises the concentration linearly", "c1"),
        ("decision", "Narrow the stock range to 60-90 uL", "c1"),
        ("note", "unrelated", None),
    ]:
        response = client.post(
            "/api/notebook/",
            json={"kind": kind, "content": content, "campaign_name": campaign},
            headers=auth("agent-token"),
        )
        assert response.status_code == 201, response.text
    entries = client.get("/api/notebook/?campaign_name=c1", headers=auth("viewer-token")).json()
    assert [(e["kind"], e["author"]) for e in entries] == [("hypothesis", "agent"), ("decision", "agent")]
    assert (
        client.post("/api/notebook/", json={"kind": "note", "content": "x"}, headers=auth("viewer-token")).status_code
        == 403
    )
    assert (
        client.post("/api/notebook/", json={"kind": "guess", "content": "x"}, headers=auth("agent-token")).status_code
        == 400
    )
