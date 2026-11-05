import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import eos.tasks.task_executor as task_executor_module
from eos.scheduling.entities.scheduled_task import ScheduledTask
from eos.tasks.entities.task import TaskSubmission
from eos.tasks.exceptions import EosTaskCancellationError
from eos.tasks.task_executor import TaskExecutionContext, TaskExecutor


@pytest.mark.parametrize("process_method", ["process_tasks", "process_new_tasks"])
async def test_cancellation_during_launch_stops_the_task_before_releasing_resources(monkeypatch, process_method):
    executor = object.__new__(TaskExecutor)
    submission = TaskSubmission(name="launching", type="Sleep")
    context = TaskExecutionContext(
        None,
        "launching",
        submission,
        ScheduledTask(name="launching", protocol_run_name=None, devices={}, resources={}),
        initialized=True,
    )
    future = asyncio.get_running_loop().create_future()
    executor._pending_tasks = {context.task_key: context}
    executor._task_futures = {context.task_key: future}
    executor._max_concurrent_tasks = None
    executor._lock = asyncio.Lock()
    events = []
    launching = asyncio.Event()
    finish_launch = asyncio.Event()
    task_ref = object()

    @asynccontextmanager
    async def session():
        yield None

    async def launch(*args):
        launching.set()
        await finish_launch.wait()
        events.append("launched")
        return task_ref

    async def release(*args):
        events.append("released")

    def stop(ref, force):
        assert ref is task_ref
        assert force is True
        events.append("stopped")

    executor._db_interface = SimpleNamespace(get_async_session=session)
    executor._task_manager = SimpleNamespace(cancel_task=AsyncMock())
    executor._scheduler = SimpleNamespace(release_task=release)
    executor._execute_task = launch
    monkeypatch.setattr(task_executor_module.ray, "cancel", stop)

    processing = asyncio.create_task(getattr(executor, process_method)())
    cancelling = None
    try:
        await asyncio.wait_for(launching.wait(), timeout=2)
        cancelling = asyncio.create_task(executor.cancel_task(None, "launching"))
        await asyncio.sleep(0)
        finish_launch.set()
        await asyncio.wait_for(asyncio.gather(processing, cancelling), timeout=2)

        with pytest.raises(EosTaskCancellationError):
            await future
        assert events == ["launched", "stopped", "released"]
        assert context.task_ref is task_ref
        assert not executor._pending_tasks
        assert not executor._task_futures
    finally:
        finish_launch.set()
        for task in (processing, cancelling):
            if task is not None:
                task.cancel()
        await asyncio.gather(*(task for task in (processing, cancelling) if task is not None), return_exceptions=True)
