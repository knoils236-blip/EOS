import asyncio
from types import SimpleNamespace

import pytest

from eos.configuration.entities.lab_def import LabDef
from eos.configuration.entities.protocol_def import ProtocolDef
from eos.configuration.entities.task_def import DeviceAssignmentDef, DynamicDeviceAssignmentDef, TaskDef
from eos.protocols.entities.protocol_run import ProtocolRunSubmission
from eos.protocols.exceptions import EosProtocolRunExecutionError
from eos.protocols.preflight import remap_protocol_labs, split_preflight
from eos.protocols.protocol_executor_factory import ProtocolExecutorFactory
from eos.orchestration.services.protocol_service import ProtocolService


def _lab(name: str, devices: dict[str, str], *, simulated: bool = False) -> LabDef:
    return LabDef.model_validate(
        {
            "name": name,
            "desc": name,
            "simulated": simulated,
            "devices": {d: {"type": t, "computer": "eos_computer"} for d, t in devices.items()},
        }
    )


LABS = {
    "real": _lab("real", {"robot": "arm", "reader": "plate_reader"}),
    "twin": _lab("twin", {"robot": "arm", "reader": "plate_reader"}, simulated=True),
    "bad_twin": _lab("bad_twin", {"robot": "gripper"}, simulated=True),
}

PROTOCOL = ProtocolDef(
    type="assay",
    desc="assay",
    labs=["real"],
    tasks=[
        TaskDef(
            name="move", type="Move", devices={"robot": DeviceAssignmentDef(lab_name="real", name="robot", hold=True)}
        ),
        TaskDef(name="read", type="Read", devices={"reader": DynamicDeviceAssignmentDef(device_type="plate_reader")}),
        TaskDef(name="move_back", type="Move", devices={"robot": "move.robot"}, dependencies=["move", "read"]),
    ],
)


class TestRemapProtocolLabs:
    def test_moves_devices_to_twin_lab(self):
        twin = remap_protocol_labs(PROTOCOL, {"real": "twin"}, LABS)

        assert twin.labs == ["twin"]
        assert twin.tasks[0].devices["robot"].lab_name == "twin"
        assert twin.tasks[0].device_holds == {"robot": True}
        assert twin.tasks[1].devices["reader"].allowed_labs == ["twin"]  # never falls back to real equipment
        assert twin.tasks[2].devices["robot"] == "move.robot"
        assert PROTOCOL.tasks[0].devices["robot"].lab_name == "real"  # the loaded protocol is untouched

    def test_rejects_twin_without_matching_device(self):
        with pytest.raises(EosProtocolRunExecutionError, match="no device 'robot'"):
            remap_protocol_labs(PROTOCOL, {"real": "bad_twin"}, LABS)

    def test_rejects_unloaded_twin_and_unused_lab(self):
        with pytest.raises(EosProtocolRunExecutionError, match="not loaded"):
            remap_protocol_labs(PROTOCOL, {"real": "nowhere"}, LABS)
        with pytest.raises(EosProtocolRunExecutionError, match="does not use"):
            remap_protocol_labs(PROTOCOL, {"twin": "real"}, LABS)

    def test_rejects_real_lab_as_preflight_target(self):
        with pytest.raises(EosProtocolRunExecutionError, match="simulated"):
            remap_protocol_labs(PROTOCOL, {"real": "real"}, LABS)

    def test_rejects_partial_mapping_that_leaves_real_equipment(self):
        protocol = PROTOCOL.model_copy(update={"labs": ["real", "other_real"]})
        labs = {**LABS, "other_real": _lab("other_real", {})}
        with pytest.raises(EosProtocolRunExecutionError, match="other_real"):
            remap_protocol_labs(protocol, {"real": "twin"}, labs)

    @pytest.mark.parametrize("lab_map", [{}, None, [], "real:twin", {"real": []}])
    def test_rejects_invalid_mapping(self, lab_map):
        with pytest.raises(EosProtocolRunExecutionError, match="mapping"):
            remap_protocol_labs(PROTOCOL, lab_map, LABS)


def test_split_preflight():
    submission = ProtocolRunSubmission(
        name="run1", type="assay", parameters={"move": {"speed": 1}}, meta={"preflight": {"real": "twin"}, "note": "x"}
    )

    preflight, real = split_preflight(submission)

    assert preflight.name == "run1__preflight"
    assert preflight.parameters == submission.parameters
    assert preflight.meta == {"note": "x", "lab_map": {"real": "twin"}, "preflight_for": "run1"}
    assert real.name == "run1"
    assert real.meta == {"note": "x", "preflight_run": "run1__preflight"}


@pytest.mark.parametrize("key", ["preflight", "lab_map"])
@pytest.mark.parametrize("value", [{}, None, [], "real:twin"])
async def test_submission_rejects_invalid_mapping_instead_of_starting_real_devices(key, value):
    config = SimpleNamespace(protocols={"assay": PROTOCOL}, labs=LABS)
    factory = object.__new__(ProtocolExecutorFactory)
    factory._configuration_manager = config
    service = object.__new__(ProtocolService)
    service._configuration_manager = config
    service._protocol_executor_factory = factory
    service._protocol_run_submission_lock = asyncio.Lock()
    service._submitted_protocol_runs = {}
    service._awaiting_preflight = {}
    submission = ProtocolRunSubmission(name="invalid", type="assay", meta={key: value})

    with pytest.raises(EosProtocolRunExecutionError, match="mapping"):
        await service.submit_protocol_run(None, submission)

    assert not service._submitted_protocol_runs
    assert not service._awaiting_preflight
