import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from eos.configuration.configuration_manager import ConfigurationManager
from eos.configuration.exceptions import EosConfigurationError
from eos.auth.entities.user_role import AuthenticatedUser, Role
from eos.devices.exceptions import EosDeviceInitializationError
from eos.orchestration.services.loading_service import LoadingService
from eos.utils.async_rlock import AsyncRLock
from eos.web_api.controllers import protocol_controller


@pytest.fixture
def service(monkeypatch):
    cm = ConfigurationManager("tests/user")
    service = LoadingService.__new__(LoadingService)
    service._configuration_manager = cm
    service._loading_lock = AsyncRLock()

    async def initialize_lab(db, name):
        cm.load_lab(name)

    service._load_single_lab = AsyncMock(side_effect=initialize_lab)
    service._check_protocol_usage = AsyncMock()
    monkeypatch.setattr(cm.def_sync, "mark_protocols_loaded", AsyncMock())
    monkeypatch.setattr(cm.def_sync, "sync_protocol_defs", AsyncMock())
    return service


async def test_loading_protocol_initializes_its_unloaded_lab(service):
    cm = service._configuration_manager
    assert "small_lab" not in cm.labs
    await service.load_protocols(None, {"water_purification"})
    assert "small_lab" in cm.labs
    assert "water_purification" in cm.protocols
    service._load_single_lab.assert_awaited_once_with(None, "small_lab")


@pytest.mark.parametrize("load_dependencies", [True, False])
async def test_loading_protocol_keeps_existing_lab_and_is_idempotent(service, load_dependencies):
    cm = service._configuration_manager
    cm.load_lab("small_lab")
    original = cm.labs["small_lab"]
    await service.load_protocols(None, {"water_purification"}, load_dependencies=load_dependencies)
    await service.load_protocols(None, {"water_purification"}, load_dependencies=load_dependencies)
    assert cm.labs["small_lab"] is original
    service._load_single_lab.assert_not_awaited()


async def test_unknown_dependency_fails_before_initializing_devices(service, monkeypatch):
    cm = service._configuration_manager
    protocol = cm.package_manager.read_protocol("water_purification")
    protocol.labs.append("missing_lab")
    monkeypatch.setattr(cm.package_manager, "read_protocol", lambda name: protocol)
    with pytest.raises(EosConfigurationError, match=r"实验室定义不存在.*missing_lab"):
        await service.load_protocols(None, {"water_purification"})
    service._load_single_lab.assert_not_awaited()
    assert not cm.protocols


async def test_reloading_protocol_initializes_new_lab_dependency(service, monkeypatch):
    cm = service._configuration_manager
    cm.load_lab("small_lab")
    await service.load_protocols(None, {"water_purification"})
    protocol = cm.package_manager.read_protocol("water_purification")
    protocol.labs.append("multiplication_lab")
    monkeypatch.setattr(cm.package_manager, "read_protocol", lambda name: protocol)
    await service.reload_protocols(None, {"water_purification"})
    assert "multiplication_lab" in cm.labs
    assert "multiplication_lab" in cm.protocols["water_purification"].labs
    service._load_single_lab.assert_awaited_once_with(None, "multiplication_lab")


async def test_unprivileged_load_does_not_initialize_lab(service):
    with pytest.raises(EosConfigurationError, match=r"超级用户.*small_lab"):
        await service.load_protocols(None, {"water_purification"}, load_dependencies=False)
    service._load_single_lab.assert_not_awaited()


async def test_device_failure_does_not_mark_protocol_loaded(service):
    service._load_single_lab.side_effect = RuntimeError("bridge offline")
    with pytest.raises(EosDeviceInitializationError, match="bridge offline"):
        await service.load_protocols(None, {"water_purification"})
    assert not service._configuration_manager.protocols
    service._configuration_manager.def_sync.mark_protocols_loaded.assert_not_awaited()


async def test_concurrent_protocol_loads_initialize_a_dependency_once(service):
    initialize = service._load_single_lab.side_effect

    async def delayed(db, name):
        await asyncio.sleep(0)
        await initialize(db, name)

    service._load_single_lab.side_effect = delayed
    await asyncio.gather(*(service.load_protocols(None, {"water_purification"}) for _ in range(2)))
    service._load_single_lab.assert_awaited_once()


@pytest.mark.parametrize("operation", ["load", "reload"])
@pytest.mark.parametrize(
    ("auth_disabled", "role", "allowed"),
    [
        (True, None, True),
        (False, Role.SUPERUSER, True),
        (False, Role.LAB_ADMIN, False),
    ],
)
async def test_protocol_api_only_allows_superuser_to_initialize_labs(
    monkeypatch, operation, auth_disabled, role, allowed
):
    monkeypatch.setattr(protocol_controller, "is_auth_disabled", lambda request: auth_disabled)
    monkeypatch.setattr(protocol_controller, "get_user_roles", AsyncMock(return_value=[SimpleNamespace(role=role)]))
    loading = SimpleNamespace(load_protocols=AsyncMock(), reload_protocols=AsyncMock(return_value={"test"}))
    handler = getattr(protocol_controller.ProtocolController, f"{operation}_protocols").fn
    data_type = protocol_controller.ProtocolTypes if operation == "load" else protocol_controller.ReloadProtocolsRequest
    await handler(
        None,
        data_type(protocol_types=["test"]),
        None,
        SimpleNamespace(loading=loading),
        request=object(),
        user=AuthenticatedUser(sub="tester"),
    )
    assert getattr(loading, f"{operation}_protocols").call_args.kwargs["load_dependencies"] is allowed
