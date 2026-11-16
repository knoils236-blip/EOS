"""F07: a device of the right type but insufficient range is refused, including when allocation is dynamic."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from eos.configuration.entities.protocol_def import ProtocolDef
from eos.devices.base_device import BaseDevice
from eos.orchestration.services.lab_service import LabService
from eos.tasks.entities.task import TaskSubmission
from eos.tasks.exceptions import EosTaskExecutionError
from eos.tasks.task_executor import TaskExecutor


class Mixer(BaseDevice, type="mixer"):
    @classmethod
    def check_plan(cls, init_parameters, steps):
        return [
            {
                "task": step["task"],
                "message": f"speed {step['parameters']['speed']} exceeds {init_parameters['max_speed']}",
            }
            for step in steps
            if step["parameters"].get("speed", 0) > init_parameters["max_speed"]
        ]


LAB = SimpleNamespace(
    devices={
        "slow": SimpleNamespace(type="mixer", init_parameters={"max_speed": 50}),
        "fast": SimpleNamespace(type="mixer", init_parameters={"max_speed": 100}),
    }
)
CONFIG = SimpleNamespace(
    package_manager=SimpleNamespace(read_lab=lambda _: LAB),
    labs={"lab": LAB},
    devices=SimpleNamespace(plugin_types={"mixer": Mixer}, get_plugin_class_type=lambda _: Mixer),
)


def protocol(speed: float) -> ProtocolDef:
    return ProtocolDef.model_validate(
        {
            "type": "mix",
            "desc": "mixing",
            "labs": ["lab"],
            "tasks": [
                {
                    "name": "mix",
                    "type": "Mix",
                    "parameters": {"speed": speed},
                    "devices": {"mixer": {"allocation_type": "dynamic", "device_type": "mixer"}},
                }
            ],
        }
    )


@pytest.mark.parametrize(("speed", "refused"), [(80, False), (120, True)])
def test_a_dynamic_step_needs_at_least_one_candidate_able_to_run_it(speed, refused):  # A2, static
    errors = LabService(CONFIG, Mock()).check_device_plans(protocol(speed))
    assert bool(errors) == refused
    if refused:
        assert "lab.slow: speed 120 exceeds 50" in errors[0]["message"]
        assert "lab.fast: speed 120 exceeds 100" in errors[0]["message"]


def test_the_device_actually_allocated_is_checked_before_dispatch():  # A1, A2 at runtime
    executor = object.__new__(TaskExecutor)
    executor._configuration_manager = CONFIG

    def submission(device):
        return TaskSubmission(
            name="mix", type="Mix", input_parameters={"speed": 80}, devices={"m": {"lab_name": "lab", "name": device}}
        )

    executor._require_devices_can_run(submission("fast"))
    with pytest.raises(EosTaskExecutionError, match=r"'lab\.slow' cannot run task 'mix': speed 80 exceeds 50"):
        executor._require_devices_can_run(submission("slow"))
