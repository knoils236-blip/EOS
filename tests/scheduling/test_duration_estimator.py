from datetime import UTC, datetime, timedelta

from eos.protocols.entities.protocol_run import ProtocolRunSubmission
from eos.scheduling.duration_estimator import TaskDurationEstimator
from eos.tasks.entities.task import TaskModel, TaskStatus
from tests.fixtures import *


async def _add_completed(db_interface, task_type: str, seconds: list[float], status=TaskStatus.COMPLETED) -> None:
    end = datetime(2026, 1, 1, tzinfo=UTC)
    async with db_interface.get_async_session() as db:
        for i, duration in enumerate(seconds):
            finished = end + timedelta(minutes=i)
            db.add(
                TaskModel(
                    name=f"{task_type}_{status.value}_{i}",
                    type=task_type,
                    status=status,
                    start_time=finished - timedelta(seconds=duration),
                    end_time=finished,
                )
            )
        await db.commit()


@pytest.mark.parametrize("setup_lab_protocol", [("abstract_lab", "abstract_protocol")], indirect=True)
class TestTaskDurationEstimator:
    async def test_median_of_recent_completions(self, db_interface, clear_db, setup_lab_protocol):
        await _add_completed(db_interface, "Noop", [10, 12, 100, 11.2])
        await _add_completed(db_interface, "Noop", [999], status=TaskStatus.FAILED)
        await _add_completed(db_interface, "Rare", [5, 5])
        estimator = TaskDurationEstimator(db_interface, window=20, min_samples=3)

        await estimator.refresh()

        assert estimator.estimates == {"Noop": 12}  # median(10, 12, 100, 11.2) = 11.6, rounded up; failures ignored

    async def test_window_keeps_only_most_recent(self, db_interface, clear_db, setup_lab_protocol):
        await _add_completed(db_interface, "Noop", [100, 100, 100, 5, 5, 5])  # later rows are more recent
        estimator = TaskDurationEstimator(db_interface, window=3, min_samples=3)

        await estimator.refresh()

        assert estimator.estimate("Noop") == 5

    async def test_scheduler_plans_with_learned_durations(
        self, db_interface, clear_db, heuristic_scheduler, protocol_graph, protocol_run_manager
    ):
        await _add_completed(db_interface, "Noop DT1", [30, 30, 30])
        heuristic_scheduler.use_duration_estimator(TaskDurationEstimator(db_interface))
        async with db_interface.get_async_session() as db:
            await protocol_run_manager.create_protocol_run(
                db, ProtocolRunSubmission(type="abstract_protocol", name="run1", owner="test")
            )
            await db.commit()

        await heuristic_scheduler.register_protocol_run("run1", "abstract_protocol", protocol_graph)

        planned = heuristic_scheduler._task_defs["run1"]
        for name, task in planned.items():
            original = protocol_graph.get_task(name)
            assert task.duration == (30 if task.type == "Noop DT1" else original.duration)
            assert original.duration != 30  # the loaded protocol itself is unchanged
