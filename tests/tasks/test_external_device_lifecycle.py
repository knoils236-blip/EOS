import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from eos.devices.base_device import BaseDevice
from eos.devices.entities.device import DeviceStatus
from eos.scheduling.entities.scheduled_task import ScheduledTask
from eos.tasks.entities.task import TaskSubmission
from eos.tasks.exceptions import EosTaskCancellationError, EosTaskExecutionError
from eos.tasks.task_executor import TaskExecutionContext, TaskExecutor
import eos.tasks.task_executor as module


class ExternalDevice(BaseDevice):
    async def finalize_task(self, protocol_run_name, task_name, stop=False):
        raise NotImplementedError


@pytest.fixture
async def executor(monkeypatch):
    events = []
    executor = object.__new__(TaskExecutor)
    submission = TaskSubmission(
        name="transfer",
        type="External",
        protocol_run_name="run",
        devices={"station": {"lab_name": "lab", "name": "station"}},
    )
    context = TaskExecutionContext(
        "run",
        "transfer",
        submission,
        ScheduledTask(name="transfer", protocol_run_name="run", devices={}, resources={}),
        initialized=True,
        execution_started=True,
        task_ref=object(),
    )
    future = asyncio.get_running_loop().create_future()
    executor._pending_tasks = {context.task_key: context}
    executor._task_futures = {context.task_key: future}
    executor._lock = asyncio.Lock()
    executor._configuration_manager = SimpleNamespace(
        labs={"lab": SimpleNamespace(devices={"station": SimpleNamespace(type="external")})},
        devices=SimpleNamespace(get_plugin_class_type=lambda _: ExternalDevice),
    )
    executor._file_db_config = object()
    row = SimpleNamespace(output_parameters=None, output_resources=None, output_file_names=None)

    @asynccontextmanager
    async def session():
        yield None

    async def finish(*args):
        events.append("remote_stop_confirmed")
        return {
            "parameters": {"status": "cancelled"},
            "files": {"evidence.zip": b"evidence"},
            "resource_updates": {},
            "available": False,
        }

    async def upload(*args):
        events.append("archived")

    async def status(*args):
        assert args[-1] == DeviceStatus.INACTIVE
        events.append("deactivated")

    async def release(*args):
        events.append("released")

    async def add_output(db, run, task, parameters, resources, files):
        row.output_parameters, row.output_resources, row.output_file_names = parameters, resources, files

    finalize = AsyncMock(side_effect=finish)
    executor._device_manager = SimpleNamespace(
        get_device_actor=lambda *args: SimpleNamespace(finalize_task=SimpleNamespace(remote=finalize)),
        set_device_status=AsyncMock(side_effect=status),
    )
    executor._resource_manager = SimpleNamespace()
    executor._db_interface = SimpleNamespace(get_async_session=session)
    executor._task_manager = SimpleNamespace(
        get_task=AsyncMock(return_value=row), add_task_output=AsyncMock(side_effect=add_output), cancel_task=AsyncMock()
    )
    executor._scheduler = SimpleNamespace(release_task=release)
    monkeypatch.setattr(module, "FileDbInterface", lambda _: SimpleNamespace(store_file=AsyncMock(side_effect=upload)))
    monkeypatch.setattr(module.ray, "cancel", lambda *args, **kwargs: events.append("ray_cancelled"))
    return executor, context, future, events, finalize, row


async def test_cancel_stops_remote_work_and_archives_before_releasing(executor):
    runner, _context, future, events, finalize, row = executor
    await runner.cancel_task("run", "transfer")
    with pytest.raises(EosTaskCancellationError):
        await future
    assert events == ["remote_stop_confirmed", "archived", "deactivated", "ray_cancelled", "released"]
    assert row.output_file_names == ["evidence.zip"]
    assert row.output_parameters["station_execution"]["status"] == "cancelled"
    finalize.assert_awaited_once_with("run", "transfer", True)


async def test_device_consumption_is_processed_after_archiving_even_when_stopped(executor, monkeypatch):
    runner, context, _, _, finalize, row = executor
    event = {"resource_name": "tips", "event_id": "used-tip-1", "delta": -1, "source": "simulation"}
    finalize.side_effect = None
    finalize.return_value = {
        "parameters": {"status": "cancelled"},
        "files": {"evidence.zip": b"evidence"},
        "resource_updates": {},
        "inventory_events": [event],
        "available": True,
    }

    async def record(db, events):
        assert row.output_file_names == ["evidence.zip"]
        assert events == [event]

    apply = AsyncMock(side_effect=record)
    monkeypatch.setattr(module, "apply_inventory_events", apply)
    await runner._finalize_devices(None, context, stop=True)
    apply.assert_awaited_once_with(None, [event])


async def test_unconfirmed_stop_deactivates_device_and_records_error(executor):
    runner, _context, future, events, finalize, row = executor
    finalize.side_effect = TimeoutError("worker did not acknowledge")
    await runner.cancel_task("run", "transfer")
    with pytest.raises(EosTaskCancellationError):
        await future
    assert events == ["deactivated", "ray_cancelled", "released"]
    assert "acknowledge" in row.output_parameters["station_finalization_error"]


async def test_timeout_uses_same_stop_path_before_killing_ray(executor, monkeypatch):
    runner, context, future, events, finalize, _row = executor
    context.task_submission.timeout = 1
    context.started_at = 0
    monkeypatch.setattr(module.ray, "wait", lambda *args, **kwargs: ([], []))
    with pytest.raises(EosTaskExecutionError, match="timed out"):
        await runner._check_task_completion(None, context)
    assert events == ["remote_stop_confirmed", "archived", "deactivated", "ray_cancelled"]
    await runner._finalize_devices(None, context, True)
    finalize.assert_awaited_once()
    future.cancel()


async def test_archive_failure_keeps_state_and_blocks_device(executor, monkeypatch):
    runner, context, future, events, _finalize, row = executor
    monkeypatch.setattr(
        module,
        "FileDbInterface",
        lambda _: SimpleNamespace(store_file=AsyncMock(side_effect=OSError("storage unavailable"))),
    )
    with pytest.raises(EosTaskExecutionError, match="storage unavailable"):
        await runner._finalize_devices(None, context, stop=False)
    assert row.output_parameters["station_execution"]["status"] == "cancelled"
    assert "storage unavailable" in row.output_parameters["station_finalization_error"]
    assert "deactivated" in events
    assert "released" not in events
    future.cancel()


async def test_success_cannot_release_an_unavailable_external_device_as_completed(executor):
    runner, context, future, events, _finalize, _row = executor
    with pytest.raises(EosTaskExecutionError, match="unavailable after its operation"):
        await runner._finalize_devices(None, context, stop=False)
    assert "deactivated" in events
    assert "released" not in events
    future.cancel()


@pytest.mark.parametrize("failure_stage", ["archive", "complete", "release"])
async def test_completed_operation_is_not_retried_after_a_persistence_error(executor, monkeypatch, failure_stage):
    runner, context, future, _events, finalize, _row = executor
    context.task_submission.retries = 1
    context.task_ref = asyncio.get_running_loop().create_future()
    context.task_ref.set_result(({}, {}, []))
    monkeypatch.setattr(module.ray, "wait", lambda *args, **kwargs: ([context.task_ref], []))
    finalize.return_value = {"parameters": {}, "files": {}, "resource_updates": {}, "available": True}
    finalize.side_effect = None
    runner._task_manager.complete_task = AsyncMock()
    error = OSError("storage unavailable")
    if failure_stage == "archive":
        runner._task_manager.add_task_output.side_effect = error
    elif failure_stage == "complete":
        runner._task_manager.complete_task.side_effect = error
    else:
        runner._scheduler.release_task = AsyncMock(side_effect=error)
    runner._handle_task_failure = AsyncMock()

    await runner._process_single_task(context)

    runner._handle_task_failure.assert_awaited_once_with(None, context, error)
    assert context.attempt == 0
    assert not future.done()
    future.cancel()


@pytest.mark.parametrize("available", [True, False])
async def test_worker_failure_finalizes_devices_before_retry_and_respects_inactive_status(
    executor, monkeypatch, available
):
    runner, context, future, _events, finalize, _row = executor
    context.task_submission.retries = 1
    context.task_ref = asyncio.get_running_loop().create_future()
    error = RuntimeError("worker failed")
    context.task_ref.set_exception(error)
    monkeypatch.setattr(module.ray, "wait", lambda *args, **kwargs: ([context.task_ref], []))
    finalize.return_value = {"parameters": {}, "files": {}, "resource_updates": {}, "available": available}
    finalize.side_effect = None
    runner._device_manager.get_device = AsyncMock(
        return_value=SimpleNamespace(status=DeviceStatus.ACTIVE if available else DeviceStatus.INACTIVE)
    )
    runner._handle_task_failure = AsyncMock()

    await runner._process_single_task(context)

    finalize.assert_awaited_once_with("run", "transfer", True)
    if available:
        assert context.attempt == 1
        assert not context.execution_started
        runner._handle_task_failure.assert_not_awaited()
    else:
        assert context.attempt == 0
        runner._handle_task_failure.assert_awaited_once_with(None, context, error)
    future.cancel()


async def test_finalization_error_does_not_skip_other_devices(executor):
    runner, context, future, _events, finalize, row = executor
    context.task_submission.devices["second"] = context.task_submission.devices["station"].model_copy(
        update={"name": "station2"}
    )
    runner._configuration_manager.labs["lab"].devices["station2"] = SimpleNamespace(type="external")
    finalize.side_effect = [
        OSError("first device archive failed"),
        {"parameters": {"status": "succeeded"}, "files": {}, "resource_updates": {}, "available": True},
    ]

    with pytest.raises(EosTaskExecutionError, match="first device archive failed"):
        await runner._finalize_devices(None, context, stop=False)

    assert finalize.await_count == 2
    assert row.output_parameters["second_execution"]["status"] == "succeeded"
    future.cancel()


@pytest.mark.parametrize("available", [True, False])
async def test_timeout_only_retries_after_confirming_device_is_active(executor, monkeypatch, available):
    runner, context, future, events, finalize, _row = executor
    context.task_submission.timeout = 1
    context.task_submission.retries = 1
    context.started_at = 0
    monkeypatch.setattr(module.ray, "wait", lambda *args, **kwargs: ([], []))
    finalize.return_value = {"parameters": {}, "files": {}, "resource_updates": {}, "available": available}
    finalize.side_effect = None
    runner._device_manager.get_device = AsyncMock(
        return_value=SimpleNamespace(status=DeviceStatus.ACTIVE if available else DeviceStatus.INACTIVE)
    )
    runner._handle_task_failure = AsyncMock()

    await runner._process_single_task(context)

    finalize.assert_awaited_once_with("run", "transfer", True)
    assert "ray_cancelled" in events
    assert context.attempt == int(available)
    if available:
        runner._handle_task_failure.assert_not_awaited()
    else:
        assert "timed out" in str(runner._handle_task_failure.await_args.args[2])
    future.cancel()
