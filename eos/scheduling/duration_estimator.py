import math
from collections import defaultdict
from statistics import median

from sqlalchemy import select

from eos.configuration.entities.task_def import DeviceAssignmentDef, TaskDef
from eos.database.abstract_sql_db_interface import AbstractSqlDbInterface
from eos.tasks.entities.task import TaskModel, TaskStatus

# Recent completions read per refresh; enough for the window of every active task type
_HISTORY_ROWS = 5000

# The devices a task ran on, e.g. ("mock_lab.arm",). A mock and a real arm run the same task type at different speeds.
Backend = tuple[str, ...]


def _backend(devices: dict) -> Backend:
    return tuple(sorted(f"{d['lab_name']}.{d['name']}" for d in devices.values()))


class TaskDurationEstimator:
    """
    Learns task durations from completed tasks so planning schedulers use measured times instead of the
    ``duration`` guesses written in protocols. Estimates are per task type and backend (the devices it ran on): the
    median of the most recent ``window`` completions, used once at least ``min_samples`` exist. Durations run from
    dispatch to completion, so time spent waiting for devices is not counted.
    """

    def __init__(self, db_interface: AbstractSqlDbInterface, window: int = 20, min_samples: int = 3):
        self._db_interface = db_interface
        self._window = window
        self._min_samples = min_samples
        self._estimates: dict[tuple[str, Backend], int] = {}
        self._backends: dict[str, set[Backend]] = {}

    async def refresh(self) -> None:
        async with self._db_interface.get_async_session() as db:
            rows = await db.execute(
                select(TaskModel.type, TaskModel.devices, TaskModel.start_time, TaskModel.end_time)
                .where(
                    TaskModel.status == TaskStatus.COMPLETED,
                    TaskModel.start_time.is_not(None),
                    TaskModel.end_time.is_not(None),
                )
                .order_by(TaskModel.end_time.desc())
                .limit(_HISTORY_ROWS)
            )
        samples: dict[tuple[str, Backend], list[float]] = defaultdict(list)
        self._backends: dict[str, set[Backend]] = defaultdict(set)
        for task_type, devices, start, end in rows:
            key = (task_type, _backend(devices))
            self._backends[task_type].add(key[1])
            if len(samples[key]) < self._window:
                samples[key].append((end - start).total_seconds())
        self._estimates = {
            key: max(1, math.ceil(median(seconds)))
            for key, seconds in samples.items()
            if len(seconds) >= self._min_samples
        }

    def estimate(self, task_type: str, backend: Backend | None) -> int | None:
        """Learned seconds for a task type on a backend. With the backend unknown (dynamic allocation), only a
        type whose whole history comes from one backend has an estimate; otherwise backends would be mixed."""
        if backend is None:
            known = self._backends.get(task_type, set())
            if len(known) != 1:
                return None
            backend = next(iter(known))
        return self._estimates.get((task_type, backend))

    def apply(self, task: TaskDef) -> TaskDef:
        """Return the task with its learned duration, or unchanged (the protocol's own estimate) without enough
        history for its backend."""
        static = all(isinstance(d, DeviceAssignmentDef) for d in task.devices.values())
        backend = _backend({slot: d.model_dump() for slot, d in task.devices.items()}) if static else None
        learned = self.estimate(task.type, backend)
        return task if learned is None else task.model_copy(update={"duration": learned})

    @property
    def estimates(self) -> dict[tuple[str, Backend], int]:
        return dict(self._estimates)
