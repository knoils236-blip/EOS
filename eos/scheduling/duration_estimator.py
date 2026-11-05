import math
from collections import defaultdict
from statistics import median

from sqlalchemy import select

from eos.configuration.entities.task_def import TaskDef
from eos.database.abstract_sql_db_interface import AbstractSqlDbInterface
from eos.tasks.entities.task import TaskModel, TaskStatus

# Recent completions read per refresh; enough for the window of every active task type
_HISTORY_ROWS = 5000


class TaskDurationEstimator:
    """
    Learns task durations from completed tasks so planning schedulers use measured times instead of the
    ``duration`` guesses written in protocols. The estimate for a task type is the median of its most recent
    ``window`` completions, used once at least ``min_samples`` exist.
    """

    def __init__(self, db_interface: AbstractSqlDbInterface, window: int = 20, min_samples: int = 3):
        self._db_interface = db_interface
        self._window = window
        self._min_samples = min_samples
        self._estimates: dict[str, int] = {}

    async def refresh(self) -> None:
        async with self._db_interface.get_async_session() as db:
            rows = await db.execute(
                select(TaskModel.type, TaskModel.start_time, TaskModel.end_time)
                .where(
                    TaskModel.status == TaskStatus.COMPLETED,
                    TaskModel.start_time.is_not(None),
                    TaskModel.end_time.is_not(None),
                )
                .order_by(TaskModel.end_time.desc())
                .limit(_HISTORY_ROWS)
            )
        samples: dict[str, list[float]] = defaultdict(list)
        for task_type, start, end in rows:
            if len(samples[task_type]) < self._window:
                samples[task_type].append((end - start).total_seconds())
        self._estimates = {
            task_type: max(1, math.ceil(median(seconds)))
            for task_type, seconds in samples.items()
            if len(seconds) >= self._min_samples
        }

    def estimate(self, task_type: str) -> int | None:
        return self._estimates.get(task_type)

    def apply(self, task: TaskDef) -> TaskDef:
        """Return the task with its learned duration, or unchanged if there is too little history."""
        learned = self._estimates.get(task.type)
        return task if learned is None else task.model_copy(update={"duration": learned})

    @property
    def estimates(self) -> dict[str, int]:
        return dict(self._estimates)
