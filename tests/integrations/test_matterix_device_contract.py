import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "user"))
from matterix_sim.devices.matterix_workstation.device import MatterixWorkstation, SilaClientContext
from matterix_sim.tasks.matterix_capability.task import matterix_capability
from matterix_sim.tasks.matterix_volume_operation.task import matterix_volume_operation
from matterix_sim.tasks.end_experiment.task import end_experiment
from eos import Context
from eos.devices.base_device import DeviceStatus
from eos.devices.exceptions import EosDeviceInitializationError
from eos.integrations.sila import SilaDevice

SMALL_VIAL = "DesignD_DesignC_ChromatographyVial2ml_190"
BIG_BOTTLE = "DesignD_DesignC_BottleBody500ml_238"


@pytest.mark.parametrize("status", ["idle", "interrupted"])
async def test_initialization_waits_for_actual_session_readiness(monkeypatch, status):
    monkeypatch.setattr("eos.devices.base_device.register_async_exit_callback", lambda *args: None)
    workstation = MatterixWorkstation("station", "lab")
    monkeypatch.setattr(SilaDevice, "_initialize", AsyncMock())
    calls = []

    async def call(command, argument):
        calls.append(command)
        if command == "CreateSession":
            return {"status": "starting"}
        assert command == "GetSession"
        assert workstation._status != DeviceStatus.IDLE
        return {"status": status, "error": "missing default_environment.usd"}

    monkeypatch.setattr(workstation, "_call", call)
    monkeypatch.setattr(workstation, "_cleanup", AsyncMock())
    parameters = {"address": "127.0.0.1", "port": 65200, "insecure": True, "scene_id": "pipetting_realtime"}
    if status == "idle":
        await workstation.initialize(parameters)
        assert workstation._status == DeviceStatus.IDLE
        workstation._cleanup.assert_not_awaited()
    else:
        with pytest.raises(EosDeviceInitializationError, match=r"missing default_environment\.usd"):
            await workstation.initialize(parameters)
        assert workstation._status == DeviceStatus.ERROR
        workstation._cleanup.assert_awaited_once()
    assert calls == ["CreateSession", "GetSession"]


@pytest.fixture
async def device(monkeypatch):
    device = MatterixWorkstation("station", "lab")
    device._session_id = "session"
    device._scene_id = "pipetting_realtime"
    device._experiment_owner = "run"
    device._generation = 1
    device._blocked = False
    device._requests, device._results = {}, {}
    device._stopped = set()
    tip = "_1mlTipBox_222_Tip_85"
    device._resource_map = {"TIP": tip}
    device._ready_timeout_s = 2
    submitted = []
    session = {
        "status": "idle",
        "generation": 1,
        "runtime": {
            "latest_sample": {
                "semantics": {
                    "liquid_volumes_ml": {tip: 0},
                    "device_state": {
                        "operation_status": "idle",
                        "operation_events": [
                            {"event": "grasp", "object": tip},
                            {"event": "release", "object": tip},
                        ],
                        "operation_commands": [],
                        "grips": {},
                        "attached": {
                            tip: False,
                            "SyringeComponent1_676": False,
                            "SyringeComponent2_677": False,
                        },
                        "seated_tips": {tip: False},
                        "cap_closed": {SMALL_VIAL: True, BIG_BOTTLE: True, "unused_empty_vial": False},
                        "semantic_state": {"TipOn": False, "SyringeOn": False, "FilterHeadOn": False},
                    },
                }
            }
        },
    }

    async def call(command, argument):
        if command == "GetSession":
            return session
        raise AssertionError(command)

    def submit(payload):
        request = json.loads(payload)
        submitted.append(request)
        result = {
            "request_id": request["request_id"],
            "status": "succeeded",
            "result": {"acceptance": {"passed": True}},
        }
        return SimpleNamespace(done=True, get_responses=lambda: SimpleNamespace(Result=json.dumps(result)))

    @asynccontextmanager
    async def connect(*args, **kwargs):
        yield SimpleNamespace(Matterix=SimpleNamespace(SubmitTask=submit, GetRunArchive=lambda _: (b"zip-evidence",)))

    monkeypatch.setattr(device, "_call", call)
    monkeypatch.setattr(SilaClientContext, "connect", connect)
    return device, submitted, session


async def test_request_identity_is_stable_and_parameters_cannot_change(device):
    workstation, requests, _ = device
    first = await workstation.run_capability("transfer", {}, "run", "phase")
    second = await workstation.run_capability("transfer", {}, "run", "phase")
    assert first["request_id"] == second["request_id"]
    assert requests[0] == requests[1]
    with pytest.raises(ValueError, match="different parameters"):
        await workstation.run_capability("transfer", {"timeout_s": 1}, "run", "phase")


async def test_finalize_archives_identity_and_updates_resource_snapshot(device):
    workstation, _, _ = device
    state = await workstation.run_capability("transfer", {}, "run", "phase")
    report = await workstation.finalize_task("run", "phase")
    assert report["available"]
    assert report["files"]["matterix-evidence.zip"] == b"zip-evidence"
    context = json.loads(report["files"]["matterix-context.json"])
    assert context["eos"]["request_id"] == state["request_id"]
    assert report["resource_updates"]["TIP"]["used"]


async def test_stop_before_delayed_ray_submission_prevents_operation(device):
    workstation, requests, _ = device
    report = await workstation.finalize_task("run", "phase", stop=True)
    assert not report["available"]
    with pytest.raises(RuntimeError, match="frozen"):
        await workstation.run_capability("transfer", {}, "run", "phase")
    assert requests == []


async def test_other_experiment_cannot_enter_owned_station(device):
    workstation, requests, _ = device
    with pytest.raises(RuntimeError, match="owning experiment"):
        await workstation.run_capability("transfer", {}, "other", "phase")
    assert not requests


async def test_station_operation_requires_a_protocol_even_when_unowned(device):
    workstation, requests, _ = device
    workstation._experiment_owner = None
    with pytest.raises(RuntimeError, match="owning experiment"):
        await workstation.run_capability("transfer", {})
    assert not requests


async def test_cancel_during_startup_does_not_submit_a_late_reset(device, monkeypatch):
    workstation, _, session = device
    workstation._experiment_owner = None
    entered, ready = asyncio.Event(), asyncio.Event()

    async def wait_ready(*args):
        entered.set()
        await ready.wait()
        return session

    monkeypatch.setattr(workstation, "_wait_ready", wait_ready)
    beginning = asyncio.create_task(workstation.begin_experiment("run", "begin"))
    await entered.wait()
    await workstation.finalize_task("run", "begin", stop=True)
    ready.set()
    with pytest.raises(RuntimeError, match="stopped before scene reset"):
        await beginning


async def test_success_status_without_acceptance_is_not_task_success():
    def run_capability(*args):
        return {"status": "succeeded", "result": {}}

    fake = SimpleNamespace(run_capability=run_capability)
    with pytest.raises(Exception, match="did not pass acceptance"):
        await matterix_capability.execute(
            Context("phase", "run"), {"matterix": fake}, {"capability_id": "transfer"}, {}, {}
        )


async def test_protocol_supplies_completion_condition(device, monkeypatch):
    workstation, _, session = device
    session["runtime"]["latest_sample"]["semantics"]["experiment_phase"] = 2

    async def ready():
        return session

    monkeypatch.setattr(workstation, "_wait_ready", ready)
    with pytest.raises(RuntimeError, match="expected 7"):
        await workstation.end_experiment("run", "finish", 7)
    await workstation.end_experiment("run", "finish", 2)
    assert workstation._experiment_owner is None


async def test_atomic_end_rejects_held_disposable_and_preserves_ownership(device, monkeypatch):
    workstation, _, session = device
    await workstation.run_capability("station_full_station_take_tip", {}, "run", "pickup")
    semantics = session["runtime"]["latest_sample"]["semantics"]
    semantics["experiment_phase"] = 0
    semantics["device_state"] = {
        "operation_status": "accepted",
        "operation_events": [{"event": "grasp", "object": "_1mlTipBox_222_Tip_85"}],
        "operation_commands": [],
        "grips": {"tip": "held"},
        "attached": {"_1mlTipBox_222_Tip_85": True},
        "seated_tips": {"_1mlTipBox_222_Tip_85": True},
        "cap_closed": {SMALL_VIAL: True, BIG_BOTTLE: True},
        "semantic_state": {"TipOn": True},
    }

    async def ready():
        return session

    monkeypatch.setattr(workstation, "_wait_ready", ready)
    with pytest.raises(RuntimeError, match="closure"):
        await workstation.end_experiment("run", "finish", 0)
    assert workstation._experiment_owner == "run"


async def test_atomic_end_requires_disposal_evidence_for_used_tip(device, monkeypatch):
    workstation, _, session = device
    await workstation.run_capability("station_full_station_take_tip", {}, "run", "pickup")
    semantics = session["runtime"]["latest_sample"]["semantics"]
    semantics["experiment_phase"] = 0
    tip = "_1mlTipBox_222_Tip_85"
    semantics["device_state"] = {
        "operation_status": "accepted",
        "operation_events": [{"event": "grasp", "object": tip}],
        "operation_commands": [],
        "grips": {},
        "attached": {tip: False},
        "seated_tips": {tip: False},
        "cap_closed": {SMALL_VIAL: True, BIG_BOTTLE: True},
        "semantic_state": {"TipOn": False},
    }

    async def ready():
        return session

    monkeypatch.setattr(workstation, "_wait_ready", ready)
    with pytest.raises(RuntimeError, match="disposal"):
        await workstation.end_experiment("run", "finish", 0)
    assert workstation._experiment_owner == "run"


async def test_atomic_end_rejects_open_cap_and_preserves_ownership(device, monkeypatch):
    workstation, _, session = device
    await workstation.run_capability("station_full_station_take_tip", {}, "run", "pickup")
    semantics = session["runtime"]["latest_sample"]["semantics"]
    semantics["experiment_phase"] = 0
    semantics["device_state"]["cap_closed"][SMALL_VIAL] = False

    async def ready():
        return session

    monkeypatch.setattr(workstation, "_wait_ready", ready)
    with pytest.raises(RuntimeError, match="cap"):
        await workstation.end_experiment("run", "finish", 0)
    assert workstation._experiment_owner == "run"


async def test_atomic_end_allows_filter_mount_on_released_syringe(device, monkeypatch):
    workstation, _, session = device
    await workstation.run_capability("station_full_station_syringe_transfer", {}, "run", "syringe")
    semantics = session["runtime"]["latest_sample"]["semantics"]
    semantics["experiment_phase"] = 0
    barrel = "SyringeComponent1_676"
    piston = "SyringeComponent2_677"
    filt = "Filter_717"
    semantics["device_state"] = {
        "operation_status": "accepted",
        "operation_events": [
            {"event": "grasp", "object": barrel},
            {"event": "grasp", "object": piston},
            {"event": "filter_installed", "object": filt, "barrel": barrel},
            {"event": "release", "object": barrel},
            {"event": "release", "object": piston},
        ],
        "operation_commands": [],
        "grips": {filt: f"/World/Connections/FilterMount_{filt}"},
        "attached": {barrel: False, piston: False, filt: True},
        "seated_tips": {},
        "cap_closed": {SMALL_VIAL: True, BIG_BOTTLE: True},
        "semantic_state": {"TipOn": False, "SyringeOn": False, "FilterHeadOn": False},
    }

    async def ready():
        return session

    monkeypatch.setattr(workstation, "_wait_ready", ready)
    state = await workstation.end_experiment("run", "finish", 0)
    assert state["runtime"]["experiment_acceptance"]["passed"] is True
    assert workstation._experiment_owner is None


async def test_atomic_end_requires_release_evidence_for_used_consumables(device, monkeypatch):
    workstation, _, session = device
    await workstation.run_capability("station_full_station_syringe_transfer", {}, "run", "syringe")
    semantics = session["runtime"]["latest_sample"]["semantics"]
    semantics["experiment_phase"] = 0
    semantics["device_state"] = {
        "operation_status": "accepted",
        "operation_events": [
            {"event": "filter_installed", "object": "Filter_717", "barrel": "SyringeComponent1_676"},
        ],
        "operation_commands": [],
        "grips": {},
        "attached": {"Filter_717": True},
        "seated_tips": {},
        "cap_closed": {SMALL_VIAL: True, BIG_BOTTLE: True},
        "semantic_state": {"TipOn": False, "SyringeOn": False, "FilterHeadOn": False},
    }

    async def ready():
        return session

    monkeypatch.setattr(workstation, "_wait_ready", ready)
    with pytest.raises(RuntimeError, match="disposal"):
        await workstation.end_experiment("run", "finish", 0)
    assert workstation._experiment_owner == "run"


async def test_atomic_end_rejects_empty_failed_unfinished_or_active_operations(device, monkeypatch):
    workstation, _, session = device

    async def ready():
        return session

    monkeypatch.setattr(workstation, "_wait_ready", ready)
    with pytest.raises(RuntimeError, match="no executed capabilities"):
        await workstation.end_experiment("run", "empty", 0)

    await workstation.run_capability("station_full_station_take_tip", {}, "run", "pickup")
    workstation._requests[("run", "failed")] = {"request_id": "failed"}
    workstation._results[("run", "failed")] = {
        "status": "succeeded",
        "result": {"acceptance": {"passed": False}},
    }
    with pytest.raises(RuntimeError, match="failed, nonterminal, or unaccepted"):
        await workstation.end_experiment("run", "failed", 0)
    assert workstation._experiment_owner == "run"

    workstation._results[("run", "failed")] = {
        "status": "running",
        "result": {"acceptance": {"passed": True}},
    }
    with pytest.raises(RuntimeError, match="failed, nonterminal, or unaccepted"):
        await workstation.end_experiment("run", "unfinished", 0)
    assert workstation._experiment_owner == "run"

    workstation._results[("run", "failed")] = {
        "status": "succeeded",
        "result": {"acceptance": {"passed": True}},
    }
    session["active_task_id"] = "still-active"
    with pytest.raises(RuntimeError, match="active task"):
        await workstation.end_experiment("run", "active", 0)
    assert workstation._experiment_owner == "run"


async def test_atomic_end_rejects_unaccepted_operation_status_and_exact_phase_seven(device, monkeypatch):
    workstation, _, session = device
    await workstation.run_capability("station_full_station_take_tip", {}, "run", "pickup")
    semantics = session["runtime"]["latest_sample"]["semantics"]
    semantics["experiment_phase"] = 0
    semantics["device_state"] = {"operation_status": "rejected: collision"}

    async def ready():
        return session

    monkeypatch.setattr(workstation, "_wait_ready", ready)
    with pytest.raises(RuntimeError, match="operation status"):
        await workstation.end_experiment("run", "finish", 0)
    with pytest.raises(RuntimeError, match="expected 7"):
        await workstation.end_experiment("run", "finish", 7)
    assert workstation._experiment_owner == "run"


def test_atomic_end_parameter_documents_its_nonphysical_scope():
    parameter = end_experiment.spec.get_parameter("expected_phase")
    assert parameter.value == 7
    assert parameter.min == 0
    assert parameter.max == 7
    assert "does not claim completion or physical acceptance" in parameter.desc


async def test_volume_operation_sends_flat_parameters_to_the_capability():
    calls = []

    def run_capability(*args):
        calls.append(args)
        return {"status": "succeeded", "request_id": "r", "run_dir": "d", "result": {"acceptance": {"passed": True}}}

    fake = SimpleNamespace(run_capability=run_capability)
    outputs, _, _ = await matterix_volume_operation.execute(
        Context("transfer", "run"),
        {"matterix": fake},
        {"capability_id": "station_full_station_transfer_small", "volume_ul": 75.0},
        {},
        {},
    )
    assert calls == [
        ("station_full_station_transfer_small", {"volume_ul": 75.0, "timeout_s": 120.0}, "run", "transfer")
    ]
    assert outputs["passed"] is True
