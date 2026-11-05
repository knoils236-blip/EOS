import asyncio
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from eos.database.abstract_sql_db_interface import Base
from eos.resources.entities.resource import ResourceModel
from eos.resources.exceptions import EosResourceStateError
from eos.resources.inventory import InventoryEvent, InventoryEventModel, apply_inventory_event
from eos.resources.resource_manager import ResourceManager

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "user"))
from matterix_sim.inventory import consumable_events


@pytest.fixture
async def sessions(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'stock.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda conn: Base.metadata.create_all(conn, tables=[ResourceModel.__table__, InventoryEventModel.__table__])
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory.begin() as db:
        db.add(ResourceModel(name="tips", type="consumable", lab="lab", meta={"consumable": True, "quantity": 3}))
    yield factory
    await engine.dispose()


def event(event_id="first", delta=-1):
    return InventoryEvent(resource_name="tips", event_id=event_id, delta=delta, source="simulation")


async def test_consumption_duplicate_replenishment_and_restart(sessions):
    async with sessions.begin() as db:
        assert (await apply_inventory_event(db, event())).meta["quantity"] == 2
    async with sessions.begin() as db:
        assert (await apply_inventory_event(db, event())).meta["quantity"] == 2
        assert (await apply_inventory_event(db, event("refill", 5))).meta["quantity"] == 7
    async with sessions() as db:
        assert len((await db.scalars(select(InventoryEventModel))).all()) == 2
        assert (await db.get(ResourceModel, "tips")).meta["quantity"] == 7


async def test_conflict_insufficient_stock_and_rollback(sessions):
    async with sessions.begin() as db:
        await apply_inventory_event(db, event())
    with pytest.raises(EosResourceStateError, match="different change"):
        async with sessions.begin() as db:
            await apply_inventory_event(db, event(delta=-2))
    with pytest.raises(EosResourceStateError, match="Insufficient stock"):
        async with sessions.begin() as db:
            await apply_inventory_event(db, event("too-many", -3))
    async with sessions() as db:
        assert (await db.get(ResourceModel, "tips")).meta["quantity"] == 2
        assert await db.get(InventoryEventModel, ("tips", "too-many")) is None


async def test_concurrent_events_do_not_lose_counts(sessions):
    async def record(identity):
        async with sessions.begin() as db:
            return (await apply_inventory_event(db, event(identity))).meta["quantity"]

    await asyncio.gather(record("a"), record("b"), record("a"))
    async with sessions() as db:
        assert (await db.get(ResourceModel, "tips")).meta["quantity"] == 1


async def test_unloading_lab_preserves_consumable_stock(sessions):
    manager = object.__new__(ResourceManager)
    manager._locks = {}
    async with sessions.begin() as db:
        db.add(ResourceModel(name="beaker", type="beaker", lab="lab", meta={}))
        await db.flush()
        await apply_inventory_event(db, event())
        await manager._remove_resources_for_labs(db, {"lab"})
    async with sessions() as db:
        assert (await db.get(ResourceModel, "tips")).meta["quantity"] == 2
        assert await db.get(ResourceModel, "beaker") is None


def test_device_events_only_count_observed_first_use_per_session():
    semantics = {
        "device_state": {
            "operation_events": [
                {"event": "grasp", "object": "tip1"},
                {"event": "release", "object": "vial1"},
                {"event": "grasp", "object": "tip1"},
            ]
        }
    }
    mapping = {"tips": ["tip1", "tip2"], "vials": ["vial1"]}
    events = consumable_events(semantics, mapping, "session", 1)
    assert len(events) == 1
    assert events[0]["resource_name"] == "tips"
    assert events[0]["delta"] == -1
    assert events == consumable_events(semantics, mapping, "session", 1)
    assert events[0]["event_id"] != consumable_events(semantics, mapping, "session", 2)[0]["event_id"]
    assert consumable_events({}, mapping, "session", 1) == []


@pytest.mark.parametrize("delta", [True, 1.5, "1"])
def test_event_rejects_noninteger_counts(delta):
    with pytest.raises(ValidationError):
        event(delta=delta)
