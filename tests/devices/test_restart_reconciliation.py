"""F02: one orchestrator per lab state, and devices interrupted by a restart stay unavailable until confirmed."""

import asyncio
from types import SimpleNamespace

import pytest

import eos.database.models  # registers every table, so foreign keys resolve
import eos.devices.device_manager as device_manager_module
from eos.allocation.entities.device_allocation import DeviceAllocationModel
from eos.configuration.eos_config import DatabaseType, DbConfig, SqliteDbConfig
from eos.database.exceptions import EosControlLockError
from eos.database.sqlite_db_interface import SqliteDbInterface
from eos.devices.device_manager import RECOVERY_META_KEY, DeviceManager
from eos.devices.entities.device import DeviceModel, DeviceStatus
from eos.tasks.entities.task import TaskModel, TaskStatus

LAB = "lab"


def sqlite(**kwargs) -> SqliteDbInterface:
    return SqliteDbInterface(DbConfig(type=DatabaseType.SQLITE, sqlite=SqliteDbConfig(**kwargs)))


class FakeActor:
    """Answers finalize_task like a device actor would, recording each call."""

    def __init__(self, answer):
        self.answer = answer
        self.calls = []
        self.finalize_task = SimpleNamespace(remote=self._finalize)

    async def _finalize(self, protocol_run_name, task_name, stop):
        self.calls.append((protocol_run_name, task_name, stop))
        if self.answer == "hang":
            await asyncio.Event().wait()
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def device(name: str, status: DeviceStatus = DeviceStatus.ACTIVE, meta: dict | None = None) -> DeviceModel:
    return DeviceModel(lab_name=LAB, name=name, type="t", computer="eos_computer", status=status, meta=meta or {})


@pytest.mark.asyncio
async def test_a_second_orchestrator_cannot_take_control_of_the_same_database(tmp_path):
    first, second = sqlite(db_dir=tmp_path, db_name="lab"), sqlite(db_dir=tmp_path, db_name="lab")
    await first.acquire_control_lock()
    with pytest.raises(EosControlLockError, match="Another EOS orchestrator"):
        await second.acquire_control_lock()
    await first.check_control_lock()

    await first.release_control_lock()
    await second.acquire_control_lock()
    await second.release_control_lock()


@pytest.mark.asyncio
async def test_reconciliation_keeps_unconfirmed_devices_unavailable_with_a_reason(monkeypatch):
    monkeypatch.setattr(device_manager_module, "RECOVERY_CONFIRM_TIMEOUT", 0.05)
    db_interface = sqlite(in_memory=True)
    await db_interface.initialize_database()
    async with db_interface.get_async_session() as db:
        db.add_all(
            [
                device("plain"),
                device("hung"),
                device("drained"),
                device("broken", DeviceStatus.INACTIVE, {RECOVERY_META_KEY: {"reason": "a gripper fault"}}),
                device("held"),
            ]
        )
        await db.flush()
        db.add(DeviceAllocationModel(lab_name=LAB, name="held", owner="run-1"))
        for name in ("plain", "hung", "drained"):
            db.add(
                TaskModel(
                    name=f"move-{name}",
                    type="Move",
                    devices={"arm": {"lab_name": LAB, "name": name}},
                    status=TaskStatus.RUNNING,
                )
            )

    manager = DeviceManager(configuration_manager=SimpleNamespace(devices=None))
    async with db_interface.get_async_session() as db:
        await manager.record_interrupted_state(db)

    actors = {
        "plain": FakeActor(None),  # an ordinary device: its work ran in the Ray caller, which is gone
        "hung": FakeActor("hang"),
        "drained": FakeActor({"available": False}),
        "broken": FakeActor(None),
        "held": FakeActor(None),
    }
    manager._device_actor_handles = {f"{LAB}.{name}": actor for name, actor in actors.items()}
    # Startup recreates the device rows from the lab definition, so reconcile fresh ones
    models = {name: device(name) for name in actors}
    await asyncio.gather(*(manager._reconcile(model) for model in models.values()))

    available = {name: model.status == DeviceStatus.ACTIVE for name, model in models.items()}
    assert available == {"plain": True, "hung": False, "drained": False, "broken": False, "held": True}

    assert actors["plain"].calls == [(None, "move-plain", True)]
    assert "did not confirm" in models["hung"].meta[RECOVERY_META_KEY]["reason"]
    assert "unavailable after stopping" in models["drained"].meta[RECOVERY_META_KEY]["reason"]
    # An inactive device keeps its earlier reason and is not asked to do anything
    assert models["broken"].meta[RECOVERY_META_KEY]["reason"] == "a gripper fault"
    assert actors["broken"].calls == []
    # Allocated devices with nothing running become available, with who held them on record
    held = models["held"].meta[RECOVERY_META_KEY]
    assert (held["available"], held["allocated_to"], held["by"]) == (True, "run-1", "startup reconciliation")
    # Each device is reconciled once; a later reload makes it available again without these facts
    assert manager._interrupted == {}
