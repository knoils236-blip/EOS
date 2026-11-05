from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from litestar import Litestar
from litestar.di import Provide
from litestar.middleware import DefineMiddleware
from litestar.testing import AsyncTestClient

from eos.database.abstract_sql_db_interface import AsyncDbSession
from eos.auth.entities.user_role import Role
from eos.resources.entities.resource import Resource
from eos.web_api.controllers import resource_controller as module
from eos.web_api.controllers.resource_controller import ResourceController
from eos.web_api.exception_handling import general_exception_handler


@pytest.fixture
def manager(monkeypatch):
    manager = SimpleNamespace(
        get_resource=AsyncMock(return_value=Resource(name="tips", type="consumable", meta={"consumable": True})),
        update_resource=AsyncMock(),
    )
    monkeypatch.setattr(module, "get_resource_manager", lambda: manager)
    return manager


@asynccontextmanager
async def client_for(db, role=None):
    async def provide_db() -> AsyncDbSession:
        return db

    class SetRoleMiddleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            scope["_eos_user_roles"] = [SimpleNamespace(role=role)]
            await self.app(scope, receive, send)

    app = Litestar(
        [ResourceController],
        dependencies={"db": Provide(provide_db)},
        exception_handlers={Exception: general_exception_handler},
        state={"token_validator": object()} if role else {},
        middleware=[DefineMiddleware(SetRoleMiddleware)] if role else [],
    )
    async with AsyncTestClient(app=app) as client:
        yield client


async def test_inventory_request_contract_and_invalid_counts(monkeypatch):
    db = AsyncDbSession()
    db.commit = AsyncMock()
    apply = AsyncMock(return_value=Resource(name="tips", type="consumable", meta={"quantity": 4}))
    monkeypatch.setattr(module, "apply_inventory_event", apply)
    body = {"resource_name": "tips", "event_id": "refill-1", "delta": 3, "source": "manual"}
    async with client_for(db) as client:
        response = await client.post("/resources/inventory-events", json=body)
        assert response.status_code == 201, response.text
        assert response.json()["meta"]["quantity"] == 4
        assert apply.call_args.args[1].delta == 3
        db.commit.assert_awaited_once()
        response = await client.post("/resources/inventory-events", json={**body, "delta": 1.5})
        assert response.status_code == 400
        assert apply.await_count == 1


async def test_reset_cannot_restore_consumable_quantity(manager):
    db = AsyncDbSession()
    db.commit = AsyncMock()
    async with client_for(db) as client:
        response = await client.post("/resources/reset", json={"resource_names": ["tips"]})
        assert response.status_code == 409
        manager.update_resource.assert_not_awaited()
        db.commit.assert_not_awaited()


async def test_viewer_cannot_change_inventory(monkeypatch):
    apply = AsyncMock()
    monkeypatch.setattr(module, "apply_inventory_event", apply)
    async with client_for(AsyncDbSession(), Role.VIEWER) as client:
        response = await client.post(
            "/resources/inventory-events",
            json={"resource_name": "tips", "event_id": "refill", "delta": 1, "source": "manual"},
        )
        assert response.status_code == 403
        apply.assert_not_awaited()
