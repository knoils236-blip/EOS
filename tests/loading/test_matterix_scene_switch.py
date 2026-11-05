from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from eos.configuration.exceptions import EosConfigurationError
from eos.orchestration.exceptions import EosProtocolInUseError
from eos.orchestration.services.loading_service import LoadingService
from eos.protocols.entities.protocol_run import ProtocolRunStatus
from eos.web_api.exception_handling import general_exception_handler


def lab(scene, port=65200, device_type="matterix_workstation", **parameters):
    device = SimpleNamespace(
        type=device_type,
        init_parameters={"address": "127.0.0.1", "port": port, "scene_id": scene, **parameters},
    )
    return SimpleNamespace(devices={"station": device})


@pytest.fixture
def switcher():
    old, new = lab("pipetting_realtime"), lab("franka_beaker")
    cm = SimpleNamespace(
        labs={"old_lab": old},
        protocols={"old_protocol": SimpleNamespace(labs=["old_lab"])},
        package_manager=SimpleNamespace(
            read_protocol=lambda name: SimpleNamespace(labs=["new_lab"]),
            read_lab=lambda name: {"old_lab": old, "new_lab": new}[name],
        ),
        get_loaded_labs=lambda: {"old_lab": True, "new_lab": False},
    )
    report = {"blocked": False, "experiment_owner": None, "session": {"status": "idle", "active_task_id": None}}
    actor = SimpleNamespace(report=SimpleNamespace(remote=AsyncMock(return_value=report)))
    service = LoadingService.__new__(LoadingService)
    service._configuration_manager = cm
    service._device_manager = SimpleNamespace(get_device_actor=lambda *args: actor)
    service._check_lab_usage = AsyncMock()

    async def unload(db, names):
        for name in names:
            cm.labs.pop(name)

    async def load(db, names):
        for name in names:
            cm.labs[name] = cm.package_manager.read_lab(name)

    service.unload_labs = AsyncMock(side_effect=unload)
    service.load_labs = AsyncMock(side_effect=load)
    service.load_protocols = AsyncMock()
    return service, cm, report, new


async def test_protocol_load_switches_idle_matterix_scene(switcher):
    service, cm, _, _ = switcher
    await service._load_protocol_labs(None, {"new_protocol"}, True)
    service.unload_labs.assert_awaited_once_with(None, {"old_lab"})
    assert set(cm.labs) == {"new_lab"}


@pytest.mark.parametrize("status", ["interrupted", "ended"])
async def test_unowned_terminated_session_does_not_trap_scene_switch(switcher, status):
    service, cm, report, _ = switcher
    report["session"].update(status=status, error="fatal: missing default_environment.usd")
    await service._load_protocol_labs(None, {"new_protocol"}, True)
    service.unload_labs.assert_awaited_once_with(None, {"old_lab"})
    assert set(cm.labs) == {"new_lab"}


@pytest.mark.parametrize("status", ["interrupted", "ended"])
@pytest.mark.parametrize("occupied", [{"blocked": True}, {"experiment_owner": "unfinished"}])
async def test_terminated_session_with_unfinished_experiment_still_requires_recovery(switcher, status, occupied):
    service, _, report, _ = switcher
    report.update(occupied)
    report["session"]["status"] = status
    with pytest.raises(EosProtocolInUseError, match=status):
        await service._load_protocol_labs(None, {"new_protocol"}, True)
    service.unload_labs.assert_not_awaited()


async def test_scene_conflict_explains_underlying_session_failure(switcher):
    service, _, report, _ = switcher
    report["blocked"] = True
    report["session"].update(status="interrupted", error="fatal: missing default_environment.usd")
    with pytest.raises(EosProtocolInUseError, match=r"default_environment\.usd"):
        await service._load_protocol_labs(None, {"new_protocol"}, True)


@pytest.mark.parametrize(
    "changes",
    [
        {"blocked": True},
        {"experiment_owner": "unfinished"},
        {"session": {"status": "frozen", "active_task_id": None}},
        {"session": {"status": "starting", "active_task_id": None}},
        {"session": {"status": "idle", "active_task_id": "task"}},
    ],
)
async def test_uncertain_scene_is_never_automatically_unloaded(switcher, changes):
    service, cm, report, _ = switcher
    report.update(changes)
    with pytest.raises(EosProtocolInUseError):
        await service._load_protocol_labs(None, {"new_protocol"}, True)
    service.unload_labs.assert_not_awaited()
    service.load_labs.assert_not_awaited()
    assert set(cm.labs) == {"old_lab"}


async def test_running_protocol_prevents_scene_switch(switcher):
    service, _, _, _ = switcher
    service._check_lab_usage.side_effect = EosProtocolInUseError("running protocol")
    with pytest.raises(EosProtocolInUseError):
        await service._load_protocol_labs(None, {"new_protocol"}, True)
    service.unload_labs.assert_not_awaited()
    service.load_labs.assert_not_awaited()


async def test_scene_switch_restores_previous_lab_and_protocols_on_failure(switcher):
    service, cm, _, _ = switcher
    load = service.load_labs.side_effect

    async def fail_new(db, names):
        if "new_lab" in names:
            raise RuntimeError("worker failed")
        await load(db, names)

    service.load_labs.side_effect = fail_new
    with pytest.raises(RuntimeError, match="worker failed"):
        await service._load_protocol_labs(None, {"new_protocol"}, True)
    assert set(cm.labs) == {"old_lab"}
    service.load_protocols.assert_awaited_once_with(None, {"old_protocol"}, load_dependencies=False)


@pytest.mark.parametrize(("port", "device_type"), [(65201, "matterix_workstation"), (65200, "other_device")])
async def test_unrelated_devices_are_not_unloaded(switcher, port, device_type):
    service, cm, _, new = switcher
    new.devices["station"].init_parameters["port"] = port
    new.devices["station"].type = device_type
    await service._load_protocol_labs(None, {"new_protocol"}, True)
    service.unload_labs.assert_not_awaited()
    assert set(cm.labs) == {"old_lab", "new_lab"}


async def test_conflicting_labs_in_one_request_fail_before_switching(switcher):
    service, _, _, _ = switcher
    service._configuration_manager.package_manager.read_protocol = lambda name: SimpleNamespace(
        labs=["old_lab", "new_lab"]
    )
    with pytest.raises(EosConfigurationError, match="Matterix"):
        await service._load_protocol_labs(None, {"new_protocol"}, True)
    service.unload_labs.assert_not_awaited()
    service.load_labs.assert_not_awaited()


async def test_mixture_matterix_uses_same_exclusive_bridge(switcher):
    service, _, _, new = switcher
    new.devices["station"].type = "mixture_workstation"
    new.devices["station"].init_parameters["backend"] = "matterix"
    await service._load_protocol_labs(None, {"new_protocol"}, True)
    service.unload_labs.assert_awaited_once_with(None, {"old_lab"})


async def test_suspended_protocol_protects_its_lab(switcher):
    service, _, _, _ = switcher
    service._protocol_run_manager = SimpleNamespace(
        get_protocol_runs=AsyncMock(
            side_effect=lambda db, status: (
                [SimpleNamespace(name="paused", type="old_protocol")]
                if status == ProtocolRunStatus.SUSPENDED.value
                else []
            )
        )
    )
    service._check_tasks_using_devices = AsyncMock(return_value=[])
    with pytest.raises(EosProtocolInUseError):
        await LoadingService._check_lab_usage(service, None, "old_lab")


async def test_missing_device_report_does_not_unload_scene(switcher):
    service, _, _, _ = switcher
    service._device_manager.get_device_actor(None, None).report.remote.side_effect = RuntimeError("unreachable")
    with pytest.raises(RuntimeError, match="unreachable"):
        await service._load_protocol_labs(None, {"new_protocol"}, True)
    service.unload_labs.assert_not_awaited()


def test_scene_in_use_returns_http_conflict_without_server_error_traceback(monkeypatch):
    logger = Mock()
    monkeypatch.setattr("eos.web_api.exception_handling.log", logger)
    request = SimpleNamespace(method="POST", url=SimpleNamespace(path="/api/protocols/load"))
    response = general_exception_handler(request, EosProtocolInUseError("scene in use"))
    assert response.status_code == 409
    logger.warning.assert_called_once()
    logger.error.assert_not_called()
