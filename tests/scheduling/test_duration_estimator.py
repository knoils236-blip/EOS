from datetime import UTC, datetime, timedelta

from eos.configuration.entities.task_def import TaskDef
from eos.protocols.entities.protocol_run import ProtocolRunSubmission
from eos.scheduling.duration_estimator import TaskDurationEstimator
from eos.tasks.entities.task import TaskModel, TaskStatus
from tests.fixtures import *


async def _add_completed(
    db_interface, task_type: str, seconds: list[float], status=TaskStatus.COMPLETED, devices=None, tag=""
) -> None:
    end = datetime(2026, 1, 1, tzinfo=UTC)
    async with db_interface.get_async_session() as db:
        for i, duration in enumerate(seconds):
            finished = end + timedelta(minutes=i)
            db.add(
                TaskModel(
                    name=f"{task_type}_{status.value}_{tag}{i}",
                    type=task_type,
                    devices=devices or {},
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

        # median(10, 12, 100, 11.2) = 11.6, rounded up; failures ignored; device-less tasks have an empty backend
        assert estimator.estimates == {("Noop", ()): 12}

    async def test_window_keeps_only_most_recent(self, db_interface, clear_db, setup_lab_protocol):
        await _add_completed(db_interface, "Noop", [100, 100, 100, 5, 5, 5])  # later rows are more recent
        estimator = TaskDurationEstimator(db_interface, window=3, min_samples=3)

        await estimator.refresh()

        assert estimator.estimate("Noop", ()) == 5

    async def test_scheduler_plans_with_learned_durations(
        self, db_interface, clear_db, heuristic_scheduler, protocol_graph, protocol_run_manager
    ):
        d1 = {"device_1": {"lab_name": "abstract_lab", "name": "D1"}}
        await _add_completed(db_interface, "Noop DT1", [30, 30, 30], devices=d1)
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


MOCK = {"arm": {"lab_name": "mock_lab", "name": "arm"}}
REAL = {"arm": {"lab_name": "real_lab", "name": "arm"}}


def _task(devices) -> TaskDef:
    return TaskDef.model_validate({"name": "move", "type": "Move", "duration": 60, "devices": devices})


@pytest.mark.parametrize("setup_lab_protocol", [("abstract_lab", "abstract_protocol")], indirect=True)
async def test_each_backend_has_its_own_duration_and_too_little_history_keeps_the_protocol_estimate(
    db_interface, clear_db, setup_lab_protocol
):  # F09-A1
    await _add_completed(db_interface, "Move", [2, 2, 2], devices=MOCK, tag="mock")
    await _add_completed(db_interface, "Move", [40, 40], devices=REAL, tag="real")
    estimator = TaskDurationEstimator(db_interface, min_samples=3)
    await estimator.refresh()

    assert estimator.apply(_task(MOCK)).duration == 2
    assert estimator.apply(_task(REAL)).duration == 60  # 2 real samples are not enough; mock ones never stand in
    # Dynamic allocation: the backend is unknown and history spans two, so nothing is mixed
    assert estimator.apply(_task({"arm": {"allocation_type": "dynamic", "device_type": "arm"}})).duration == 60
