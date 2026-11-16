"""F06: containers hold one position each, moves are confirmed facts, splits are traceable, events apply once."""

import pytest
from sqlalchemy.exc import IntegrityError

import eos.database.models  # registers every table, so foreign keys resolve
from eos.configuration.eos_config import DatabaseType, DbConfig, SqliteDbConfig
from eos.database.sqlite_db_interface import SqliteDbInterface
from eos.resources.exceptions import EosResourceStateError
from eos.resources.materials import (
    ContainerModel,
    Move,
    PositionCheck,
    PositionUnknown,
    SampleModel,
    Split,
    apply_material_event,
    apply_material_events,
    lineage,
    reserve_position,
)


@pytest.fixture
async def db():
    db_interface = SqliteDbInterface(DbConfig(type=DatabaseType.SQLITE, sqlite=SqliteDbConfig(in_memory=True)))
    await db_interface.initialize_database()
    async with db_interface.get_async_session() as session:
        session.add_all(
            [
                ContainerModel(id="vial", type="vial", capacity_ul=1000, position="rack.A1", position_known=True),
                ContainerModel(id="tube", type="tube", capacity_ul=200, position="rack.A2", position_known=True),
            ]
        )
        await session.flush()
        session.add(SampleModel(id="stock", container_id="vial", volume_ul=800, removed=False, details={}))
        await session.flush()
        yield session


async def test_two_containers_can_never_occupy_one_position(db):  # A1
    with pytest.raises(EosResourceStateError, match="occupied"):
        await apply_material_event(db, Move(event_id="m1", container_id="tube", target="rack.A1"), actor="device")
    with pytest.raises(EosResourceStateError, match="already held"):
        await reserve_position(db, "tube", "rack.A1")
    # The database refuses it too, whatever path a write takes
    (await db.get(ContainerModel, "tube")).position = "rack.A1"
    with pytest.raises(IntegrityError):
        await db.flush()
    await db.rollback()


async def test_a_reserved_target_does_not_move_the_container(db):  # A3
    await reserve_position(db, "vial", "station.dock")
    with pytest.raises(EosResourceStateError, match="already held"):
        await reserve_position(db, "tube", "station.dock")
    vial = await db.get(ContainerModel, "vial")
    assert (vial.position, vial.reserved_position) == ("rack.A1", "station.dock")

    await apply_material_event(db, Move(event_id="m1", container_id="vial", target="station.dock"), actor="device")
    assert (vial.position, vial.reserved_position, vial.position_known) == ("station.dock", None, True)


async def test_an_interrupted_move_blocks_work_until_someone_checks(db):  # A4
    await apply_material_event(
        db, PositionUnknown(event_id="u1", container_id="vial", note="gripper stopped"), "device"
    )
    vial = await db.get(ContainerModel, "vial")
    assert (vial.position, vial.position_known) == ("rack.A1", False)  # last confirmed position kept
    with pytest.raises(EosResourceStateError, match="unknown position"):
        await reserve_position(db, "vial", "station.dock")

    await apply_material_event(db, PositionCheck(event_id="c1", container_id="vial", position="rack.B1"), "user:ana")
    assert (vial.position, vial.position_known) == ("rack.B1", True)


async def test_splits_are_traceable_and_respect_capacity(db):  # A5
    await apply_material_event(
        db, Split(event_id="s1", sample_id="stock", new_sample_id="aliquot", container_id="tube", volume_ul=150), "dev"
    )
    assert [s.id for s in await lineage(db, "aliquot")] == ["aliquot", "stock"]
    assert (await db.get(SampleModel, "stock")).volume_ul == 650
    with pytest.raises(EosResourceStateError, match="does not fit"):
        await apply_material_event(
            db, Split(event_id="s2", sample_id="stock", new_sample_id="b", container_id="tube", volume_ul=100), "dev"
        )


async def test_a_change_reported_twice_applies_once(db):  # A6
    split = {
        "kind": "split",
        "event_id": "s1",
        "sample_id": "stock",
        "new_sample_id": "a",
        "container_id": "tube",
        "volume_ul": 100,
    }
    await apply_material_events(db, [split], actor="device")
    await apply_material_events(db, [split], actor="device")  # e.g. the device result was resent
    assert (await db.get(SampleModel, "stock")).volume_ul == 700
    with pytest.raises(EosResourceStateError, match="different change"):
        await apply_material_events(db, [{**split, "volume_ul": 50}], actor="device")
