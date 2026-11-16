import asyncio
from typing import ClassVar
from unittest.mock import patch

from eos.configuration.entities.task_def import TaskDef, DeviceAssignmentDef
from eos.protocols.entities.protocol_run import ProtocolRunSubmission
from eos.scheduling.entities.scheduled_task import ScheduledTask
import eos
from eos.tasks.attempts import (
    TaskAttemptModel,
    interrupt_running_attempts,
    list_attempts,
    record_finalization,
    start_attempt,
)
from eos.protocols.manifest import RunManifest, save_manifest
from eos.tasks.entities.task import TaskSubmission, TaskStatus
from eos.tasks.exceptions import EosTaskCancellationError, EosTaskExecutionError
from tests.fixtures import *


@pytest.mark.parametrize(
    "setup_lab_protocol",
    [("small_lab", "water_purification")],
    indirect=True,
)
class TestTaskExecutor:
    @staticmethod
    async def _attempts(db_interface, task_name):
        async with db_interface.get_async_session() as db:
            return await list_attempts(db, "water_purification", task_name)

    async def _setup_protocol_run(self, db, protocol_run_manager):
        await protocol_run_manager.create_protocol_run(
            db, ProtocolRunSubmission(type="water_purification", name="water_purification", owner="test")
        )

    async def _process_until_done(self, task_executor, future, timeout_seconds=10):
        """Helper to process tasks until completion or timeout."""
        timeout = asyncio.create_task(asyncio.sleep(timeout_seconds))
        while not future.done() and not timeout.done():
            await task_executor.process_tasks()
            await asyncio.sleep(0.1)

        if timeout.done() and not future.done():
            raise TimeoutError(f"Task processing timed out after {timeout_seconds} seconds")

    @pytest.mark.asyncio
    async def test_request_task_execution(
        self,
        task_executor,
        protocol_run_manager,
        protocol_graph,
        db_interface,
    ):
        async with db_interface.get_async_session() as db:
            await self._setup_protocol_run(db, protocol_run_manager)

        task = protocol_graph.get_task("mixing")
        task.parameters["time"] = 5
        devices = {"magnetic_mixer": DeviceAssignmentDef(lab_name="small_lab", name="magnetic_mixer")}
        task.devices = devices

        # Test multiple executions
        for task_name in ["mixing", "mixing2", "mixing3"]:
            task_submission = TaskSubmission.from_def(task, "water_purification")
            task_submission.name = task_name

            scheduled_task = ScheduledTask(
                name=task_name,
                protocol_run_name="water_purification",
                devices=devices,
                resources={},
            )

            future = asyncio.create_task(task_executor.request_task_execution(task_submission, scheduled_task))
            await self._process_until_done(task_executor, future)

            task_output_parameters, _, _ = await future
            assert task_output_parameters["mixing_time"] == 5

    @pytest.mark.asyncio
    async def test_cancel_task(self, task_executor, protocol_run_manager, db_interface):
        async with db_interface.get_async_session() as db:
            await self._setup_protocol_run(db, protocol_run_manager)

        devices = {}
        sleep_config = TaskDef(
            name="sleep_task",
            type="Sleep",
            devices=devices,
            parameters={"time": 5},
        )
        task_submission = TaskSubmission.from_def(sleep_config, "water_purification")

        scheduled_task = ScheduledTask(
            name="sleep_task",
            protocol_run_name="water_purification",
            devices=devices,
            resources={},
        )

        future = asyncio.create_task(task_executor.request_task_execution(task_submission, scheduled_task))

        # Give task time to start
        for _ in range(5):
            await task_executor.process_tasks()
            await asyncio.sleep(0.1)

        await task_executor.cancel_task(task_submission.protocol_run_name, task_submission.name)
        await self._process_until_done(task_executor, future, timeout_seconds=2)

        with pytest.raises(EosTaskCancellationError):
            await future
        assert [a.outcome for a in await self._attempts(db_interface, "sleep_task")] == ["cancelled"]

        assert not task_executor._pending_tasks
        async with db_interface.get_async_session() as db:
            task = await task_executor._task_manager.get_task(db, "water_purification", "sleep_task")
        assert task.status == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_failed_task_is_marked_failed_in_db(
        self, task_executor, protocol_run_manager, protocol_graph, db_interface, task_manager
    ):
        """A task that raises an exception should be marked FAILED in the database."""
        async with db_interface.get_async_session() as db:
            await self._setup_protocol_run(db, protocol_run_manager)

        task = protocol_graph.get_task("mixing")
        task.parameters["time"] = 5
        devices = {"magnetic_mixer": DeviceAssignmentDef(lab_name="small_lab", name="magnetic_mixer")}
        task.devices = devices

        @eos.task("Magnetic Mixing")
        async def failing_task() -> None:
            raise RuntimeError("Simulated device failure")

        task_submission = TaskSubmission.from_def(task, "water_purification")
        task_submission.name = "failing_mix"
        scheduled_task = ScheduledTask(
            name="failing_mix",
            protocol_run_name="water_purification",
            devices=devices,
            resources={},
        )

        with patch.dict(task_executor._task_plugin_registry.plugin_types, {"Magnetic Mixing": failing_task}):
            future = asyncio.create_task(task_executor.request_task_execution(task_submission, scheduled_task))
            await self._process_until_done(task_executor, future)

            with pytest.raises(EosTaskExecutionError):
                await future

        async with db_interface.get_async_session() as db:
            task_record = await task_manager.get_task(db, "water_purification", "failing_mix")
            assert task_record.status == TaskStatus.FAILED
            assert task_record.error_message is not None

    async def _run_sleep_task(self, task_executor, name, task_timeout=None, retries=0, time=0):
        task_submission = TaskSubmission.from_def(
            TaskDef(name=name, type="Sleep", parameters={"time": time}, timeout=task_timeout, retries=retries),
            "water_purification",
        )
        scheduled_task = ScheduledTask(name=name, protocol_run_name="water_purification", devices={}, resources={})
        future = asyncio.create_task(task_executor.request_task_execution(task_submission, scheduled_task))
        await self._process_until_done(task_executor, future, timeout_seconds=30)
        return future

    @pytest.mark.asyncio
    async def test_task_exceeding_timeout_fails(self, task_executor, protocol_run_manager, db_interface, task_manager):
        async with db_interface.get_async_session() as db:
            await self._setup_protocol_run(db, protocol_run_manager)

        future = await self._run_sleep_task(task_executor, "slow_task", task_timeout=1, time=30)

        with pytest.raises(EosTaskExecutionError, match="timed out after 1 s"):
            await future
        # Waiting too long is recorded apart from the operation failing
        (attempt,) = await self._attempts(db_interface, "slow_task")
        assert (attempt.outcome, attempt.ended_at is not None) == ("timed_out", True)
        async with db_interface.get_async_session() as db:
            task_record = await task_manager.get_task(db, "water_purification", "slow_task")
            assert task_record.status == TaskStatus.FAILED

    @pytest.mark.asyncio
    async def test_failed_task_is_retried(self, task_executor, protocol_run_manager, db_interface, tmp_path):
        async with db_interface.get_async_session() as db:
            await self._setup_protocol_run(db, protocol_run_manager)
        attempts = tmp_path / "attempts"

        @eos.task("Sleep")
        async def flaky(time: int = eos.Param(0)) -> None:
            # Ray runs each attempt in a worker process, so count attempts in a file
            with attempts.open("a") as f:
                f.write("x")
            if len(attempts.read_text()) == 1:
                raise RuntimeError("transient instrument error")

        with patch.dict(task_executor._task_plugin_registry.plugin_types, {"Sleep": flaky}):
            future = await self._run_sleep_task(task_executor, "flaky_task", retries=2)

        await future
        assert attempts.read_text() == "xx"
        history = await self._attempts(db_interface, "flaky_task")
        assert [(a.attempt_number, a.outcome) for a in history] == [(1, "failed"), (2, "succeeded")]
        assert "transient instrument error" in history[0].error

    @pytest.mark.asyncio
    async def test_task_fails_after_retries_are_exhausted(
        self, task_executor, protocol_run_manager, db_interface, tmp_path
    ):
        async with db_interface.get_async_session() as db:
            await self._setup_protocol_run(db, protocol_run_manager)
        attempts = tmp_path / "attempts"

        @eos.task("Sleep")
        async def broken(time: int = eos.Param(0)) -> None:
            with attempts.open("a") as f:
                f.write("x")
            raise RuntimeError("instrument offline")

        with patch.dict(task_executor._task_plugin_registry.plugin_types, {"Sleep": broken}):
            future = await self._run_sleep_task(task_executor, "broken_task", retries=1)

        with pytest.raises(EosTaskExecutionError, match="instrument offline"):
            await future
        assert attempts.read_text() == "xx"

    @pytest.mark.asyncio
    async def test_manual_retry_task_is_never_relaunched(
        self, task_executor, protocol_run_manager, db_interface, tmp_path
    ):
        async with db_interface.get_async_session() as db:
            await self._setup_protocol_run(db, protocol_run_manager)
        attempts = tmp_path / "attempts"

        @eos.task("Sleep", retry="manual")
        async def dispense(time: int = eos.Param(0)) -> None:
            with attempts.open("a") as f:
                f.write("x")
            raise RuntimeError("dispense state unknown")

        with patch.dict(task_executor._task_plugin_registry.plugin_types, {"Sleep": dispense}):
            future = await self._run_sleep_task(task_executor, "dispense_task", retries=3)

        with pytest.raises(EosTaskExecutionError, match="dispense state unknown"):
            await future
        assert attempts.read_text() == "x"
        (attempt,) = await self._attempts(db_interface, "dispense_task")
        assert (attempt.outcome, attempt.retry, attempt.needs_manual_recovery) == ("failed", "manual", True)

    @pytest.mark.asyncio
    async def test_resume_keeps_the_attempts_of_deleted_task_rows(
        self, task_executor, protocol_run_manager, db_interface, tmp_path
    ):
        async with db_interface.get_async_session() as db:
            await self._setup_protocol_run(db, protocol_run_manager)

        @eos.task("Sleep")
        async def broken(time: int = eos.Param(0)) -> None:
            raise RuntimeError("first run failed")

        with patch.dict(task_executor._task_plugin_registry.plugin_types, {"Sleep": broken}):
            future = await self._run_sleep_task(task_executor, "resumed_task")
        with pytest.raises(EosTaskExecutionError):
            await future

        # Resuming deletes the failed task row; the second run is a new attempt next to the first
        async with db_interface.get_async_session() as db:
            await protocol_run_manager.delete_non_completed_tasks(db, "water_purification")
        await (await self._run_sleep_task(task_executor, "resumed_task"))
        history = await self._attempts(db_interface, "resumed_task")
        assert [(a.attempt_number, a.outcome) for a in history] == [(1, "failed"), (2, "succeeded")]

    @pytest.mark.asyncio
    async def test_a_run_never_executes_plugin_code_other_than_planned(
        self, task_executor, protocol_run_manager, db_interface, tmp_path
    ):
        async with db_interface.get_async_session() as db:
            await self._setup_protocol_run(db, protocol_run_manager)
            planned = {"task_plugins": {"Sleep": "fingerprint-when-planned"}, "device_plugins": {}}
            await save_manifest(db, RunManifest(planned, "a" * 64))
            await protocol_run_manager.set_manifest_hash(db, "water_purification", "a" * 64)
        ran = tmp_path / "ran"

        @eos.task("Sleep")
        async def reloaded(time: int = eos.Param(0)) -> None:
            ran.write_text("new code ran")

        with patch.dict(task_executor._task_plugin_registry.plugin_types, {"Sleep": reloaded}):
            future = await self._run_sleep_task(task_executor, "after_reload")

        with pytest.raises(EosTaskExecutionError, match="not the version planned"):
            await future
        assert not ran.exists()
        assert await self._attempts(db_interface, "after_reload") == []


@pytest.mark.asyncio
async def test_restart_closes_running_attempts_and_flags_manual_ones(db_interface, clear_db):
    async with db_interface.get_async_session() as db:
        safe = await start_attempt(db, None, "measure", "safe", {})
        manual = await start_attempt(db, None, "dispense", "manual", {})
        await interrupt_running_attempts(db)
    assert [(a.outcome, a.needs_manual_recovery) for a in (safe, manual)] == [
        ("interrupted", False),
        ("interrupted", True),
    ]


class FakeFileDb:
    files: ClassVar[dict] = {}  # shared, since the executor builds its own instance

    def __init__(self, _config):
        pass

    async def get_file(self, key):
        return self.files[key]

    async def store_file(self, key, data):
        self.files[key] = data


@pytest.mark.parametrize("setup_lab_protocol", [("small_lab", "water_purification")], indirect=True)
@pytest.mark.asyncio
async def test_a_new_attempt_keeps_the_files_of_earlier_ones(task_executor, db_interface, clear_db, monkeypatch):
    monkeypatch.setattr("eos.tasks.task_executor.FileDbInterface", FakeFileDb)
    FakeFileDb.files = {"on_demand/mix/result.csv": b"attempt 1"}
    async with db_interface.get_async_session() as db:
        first = await start_attempt(db, None, "mix", "safe", {})
        first.outcome, first.evidence = "failed", {"files": ["on_demand/mix/result.csv"]}
        second = await start_attempt(db, None, "mix", "safe", {})
        await task_executor._keep_earlier_attempt_files(db, second)
        await db.flush()
        kept = (await db.get(TaskAttemptModel, first.id)).evidence["files"]
        # A third attempt does not copy files that are already kept under their own attempt
        third = await start_attempt(db, None, "mix", "safe", {})
        await task_executor._keep_earlier_attempt_files(db, third)

    assert kept == ["on_demand/mix/attempts/1/result.csv"]
    assert FakeFileDb.files["on_demand/mix/attempts/1/result.csv"] == b"attempt 1"
    assert (second.attempt_number, third.attempt_number) == (2, 3)
    assert len(FakeFileDb.files) == 2


@pytest.mark.asyncio
async def test_one_unconfirmed_device_makes_the_attempt_unconfirmed(db_interface, clear_db):
    async with db_interface.get_async_session() as db:
        attempt = await start_attempt(db, None, "transfer", "manual", {})
        await record_finalization(db, attempt.id, "arm", True, {"available": True, "request_id": "req-1"})
        assert (attempt.stop_confirmed, attempt.device_available) == (True, True)
        await record_finalization(db, attempt.id, "pipette", True, None, TimeoutError("no answer"))
    assert (attempt.stop_confirmed, attempt.device_available, attempt.external_request_id) == (False, False, "req-1")
    assert attempt.evidence["finalization"]["pipette"] == {"error": "no answer"}
