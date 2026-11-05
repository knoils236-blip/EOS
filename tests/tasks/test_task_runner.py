import pytest

from eos.tasks.exceptions import EosTaskExecutionError
from eos.tasks.task_runner import run_task


class SilaStyleError(Exception):
    """Like sila2's DefinedExecutionError: __init__ arguments differ from args, so unpickling fails."""

    def __init__(self, error_node: str, message: str):
        super().__init__(f"{error_node}: {message}")


class RaisingTask:
    def __init__(self, error: Exception):
        self.error = error

    async def execute(self, *args):
        raise self.error


def run(task: RaisingTask) -> None:
    # Call the undecorated function so the test runs without a Ray cluster
    run_task._function(task, None, "run", "task", {}, {}, {}, {})


class TestRunTask:
    def test_unpicklable_error_becomes_task_error_with_original_message(self):
        with pytest.raises(EosTaskExecutionError, match=r"^SilaStyleError: DeviceCrashed: controller crashed$"):
            run(RaisingTask(SilaStyleError("DeviceCrashed", "controller crashed")))

    def test_picklable_error_passes_through(self):
        with pytest.raises(ValueError, match="bad input"):
            run(RaisingTask(ValueError("bad input")))
