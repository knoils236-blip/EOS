import copy
import dataclasses
import importlib
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "integrations/matterix"))
sys.path.insert(0, "/home/ry/projects/Matterix/source/matterix_sm")
sys.path.insert(0, "/home/ry/projects/Matterix/PipettingStation_20261031/source/matterix_plc")
from bridge import station_experiment as experiment
from bridge import station_semantics as semantics
from bridge import station_skills
from bridge.capabilities import catalog, validate_task
from bridge.station_worker import _configure_imports


def request(capability_id, volume):
    return {
        "request_id": "volume",
        "session_id": "session",
        "capability_id": capability_id,
        "parameters": {"volume_ul": volume},
    }


def with_task_history(final, events, commands=None):
    final = copy.deepcopy(final)
    device = final["device_state"]
    device["operation_events"] = [{"event": event, "object": obj} for event, obj in events]
    device["operation_commands"] = [{"action": action, "object": obj} for action, obj in commands or events]
    device["operation_event_count"] = len(device["operation_events"])
    device["command_event_count"] = len(device["operation_commands"])
    device["operation_status"] = "accepted"
    return final


def extrema(initial, changes=()):
    values = {name: [volume, volume] for name, volume in initial["liquid_volumes_ml"].items()}
    for name, low, high in changes:
        values[name][0] = low
        values[name][1] = high
    return values


def walk_config(node):
    yield node
    for child in getattr(node, "sub_actions", []) or []:
        yield from walk_config(child)


def config_volumes(node):
    return [
        float(volume)
        for item in walk_config(node)
        if isinstance((volume := getattr(item, "volume", None)), (int, float))
        and not isinstance(volume, bool)
    ]


@pytest.mark.parametrize(
    ("capability_id", "workflow_name", "default"),
    [
        ("station_full_station_transfer_small", "full_station_transfer_small", 100),
        ("station_full_station_transfer_big", "full_station_transfer_big", 100),
        ("station_full_station_syringe_transfer", "full_station_syringe_transfer", 50),
    ],
)
def test_full_station_volume_reaches_capability_and_exported_workflow(
    capability_id, workflow_name, default, monkeypatch
):
    capability = {item["id"]: item for item in catalog()}[capability_id]
    volume = capability["parameters"]["volume_ul"]
    assert volume["default"] == default
    assert (volume["min"], volume["max"]) == (20, 180)
    assert validate_task(request(capability_id, 74))["parameters"]["volume_ul"] == 74
    with pytest.raises(ValueError, match="between"):
        validate_task(request(capability_id, 181))

    @dataclasses.dataclass
    class PipetteTransferCfg:
        volume: float

    @dataclasses.dataclass
    class SyringeFilterTransferCfg:
        volume: float

    @dataclasses.dataclass
    class Workflow:
        sub_actions: list

    transfer_type = (
        SyringeFilterTransferCfg
        if workflow_name == "full_station_syringe_transfer"
        else PipetteTransferCfg
    )
    phase_workflow = Workflow([transfer_type(default)])
    monkeypatch.setattr(
        station_skills.importlib,
        "import_module",
        lambda _: types.SimpleNamespace(
            PipetteTransferCfg=PipetteTransferCfg,
            SyringeFilterTransferCfg=SyringeFilterTransferCfg,
        ),
    )
    configured = station_skills.apply_workflow_parameters(phase_workflow, {"volume_ul": 74})

    found = []

    def collect(node):
        if type(node).__name__ in {"PipetteTransferCfg", "SyringeFilterTransferCfg"}:
            found.append(node.volume)
        for child in getattr(node, "sub_actions", []) or []:
            collect(child)

    collect(configured)
    assert found == [74]


def test_native_exported_workflows_and_atomic_constructors_receive_requested_volume():
    compat = importlib.import_module("matterix_sm._compat")
    if not compat.HAS_ISAACLAB:
        pytest.skip("native station workflows require Isaac Lab configclass")

    _configure_imports(Path("/home/ry/projects/Matterix"))
    workflows = importlib.import_module("matterix_plc.actions.workflows")
    pump = importlib.import_module("matterix_plc.pump")
    exported = workflows.full_station()
    for index, expected_constructor in (
        (2, "PipetteTransferCfg"),
        (4, "PipetteTransferCfg"),
        (5, "SyringeFilterTransferCfg"),
    ):
        configured = station_skills.apply_workflow_parameters(
            exported.sub_actions[index], {"volume_ul": 180}
        )
        matching = [
            item
            for item in walk_config(configured)
            if type(item).__name__ == expected_constructor
        ]
        assert len(matching) == 1
        assert matching[0].volume == 180
        assert set(config_volumes(configured)) == {180.0}

    for workflow_name, constructor_name in (
        ("skill_aspirate_from", "AspirateFromCfg"),
        ("skill_dispense_to", "DispenseToCfg"),
        ("skill_syringe_aspirate", "SyringeAspirateCfg"),
        ("skill_syringe_dispense", "SyringeDispenseCfg"),
    ):
        configured = station_skills.build_workflow(workflow_name, {"volume_ul": 180})
        matching = [
            node
            for node in walk_config(configured)
            if type(node).__name__ == constructor_name
        ]
        assert len(matching) == 1
        assert matching[0].volume == 180

    low = pump.syringe_stroke_for_volume(20)
    high = pump.syringe_stroke_for_volume(180)
    assert 0 < low < high
    with pytest.raises(pump.PumpRangeError):
        pump.syringe_stroke_for_volume(2000)


def test_full_station_pipette_preconditions_and_acceptance_use_requested_volume():
    snapshot = {
        "experiment_phase": 2,
        "liquid_balance": {"air_ml": 0.0, "spilled_ml": 0.0},
        "liquid_volumes_ml": {
            semantics.SOURCE_BOTTLE: 0.08,
            semantics.TARGET_BOTTLE: 0.0,
            semantics.BIG_BOTTLE: 400.0,
            experiment.REACTOR: 70.0,
            semantics.FIXED_TIP: 0.0,
            experiment.TIP2: 0.0,
            semantics.SYRINGE_BARREL: 0.0,
        },
        "device_state": {
            "joint_positions": {"front_door": 0.0},
            "grips": {},
            "attached": {},
            "seated_tips": {},
            "cap_closed": {semantics.SMALL_VIAL: True, semantics.BIG_BOTTLE: True},
            "operation_events": [],
            "operation_commands": [],
            "operation_event_count": 0,
            "command_event_count": 0,
            "operation_status": "idle",
            "semantic_state": {"BigCapOpen": False, "SmallCapOpen": False, "TipOn": False},
        },
    }
    assert any(
        check["name"] == "吸液源液量充足"
        for check in semantics.check_preconditions("full_station_transfer_small", snapshot, {"volume_ul": 100})
    )

    volume_ml = 0.074
    final = copy.deepcopy(snapshot)
    final["liquid_volumes_ml"][semantics.SOURCE_BOTTLE] -= volume_ml
    final["liquid_volumes_ml"][semantics.TARGET_BOTTLE] += volume_ml
    final = with_task_history(
        final,
        [
            ("grasp", semantics.FIXED_TIP),
            ("release", semantics.FIXED_TIP),
            ("grasp", semantics.FIXED_TIP),
            ("release", semantics.FIXED_TIP),
        ],
        [
            ("aspirate", semantics.SOURCE_BOTTLE),
            ("dispense", semantics.TARGET_BOTTLE),
            ("grasp", semantics.FIXED_TIP),
            ("release", semantics.FIXED_TIP),
        ],
    )
    bounds = extrema(snapshot, [(semantics.SOURCE_BOTTLE, 0.006, 0.08), (semantics.FIXED_TIP, 0.0, volume_ml)])
    result = semantics.evaluate_postconditions(
        "full_station_transfer_small",
        snapshot,
        final,
        {"volume_ul": 74},
        [final],
        bounds,
    )
    assert result["passed"], result

    final["liquid_volumes_ml"][semantics.TARGET_BOTTLE] -= 0.01
    assert not semantics.evaluate_postconditions(
        "full_station_transfer_small",
        snapshot,
        final,
        {"volume_ul": 74},
        [final],
        bounds,
    )["passed"]


def test_full_station_self_transfer_and_syringe_acceptance_use_requested_volume():
    initial = {
        "experiment_phase": 4,
        "liquid_balance": {"air_ml": 0.0, "spilled_ml": 0.0},
        "liquid_volumes_ml": {
            semantics.SOURCE_BOTTLE: 8.0,
            semantics.TARGET_BOTTLE: 0.1,
            semantics.BIG_BOTTLE: 400.0,
            experiment.REACTOR: 70.0,
            semantics.FIXED_TIP: 0.0,
            experiment.TIP2: 0.0,
            semantics.SYRINGE_BARREL: 0.0,
        },
        "device_state": {
            "joint_positions": {"front_door": 0.0},
            "grips": {},
            "attached": {},
            "seated_tips": {},
            "cap_closed": {semantics.SMALL_VIAL: True, semantics.BIG_BOTTLE: True},
            "operation_events": [],
            "operation_commands": [],
            "operation_event_count": 0,
            "command_event_count": 0,
            "operation_status": "idle",
            "semantic_state": {"BigCapOpen": True, "SmallCapOpen": False, "TipOn": False},
        },
    }
    volume_ml = 0.074
    final = copy.deepcopy(initial)
    final = with_task_history(
        final,
        [
            ("grasp", experiment.TIP2),
            ("release", experiment.TIP2),
            ("cap_engaged", semantics.BIG_BOTTLE_CAP),
            ("release", semantics.BIG_BOTTLE_CAP),
        ],
    )
    bounds = extrema(initial, [(semantics.BIG_BOTTLE, 400 - volume_ml, 400), (experiment.TIP2, 0.0, volume_ml)])
    result = semantics.evaluate_postconditions(
        "full_station_transfer_big",
        initial,
        final,
        {"volume_ul": 74},
        [final],
        bounds,
    )
    assert result["passed"], result

    initial["experiment_phase"] = 5
    initial["device_state"]["semantic_state"]["BigCapOpen"] = False
    final = copy.deepcopy(initial)
    final["liquid_volumes_ml"][experiment.REACTOR] -= volume_ml
    final["liquid_volumes_ml"][semantics.SOURCE_BOTTLE] += volume_ml
    final = with_task_history(
        final,
        [
            ("grasp", semantics.SYRINGE_BARREL),
            ("release", semantics.SYRINGE_BARREL),
            ("grasp", semantics.SYRINGE_PISTON),
            ("release", semantics.SYRINGE_PISTON),
            ("filter_installed", "Filter_717"),
        ],
    )
    bounds = extrema(initial, [(experiment.REACTOR, 70 - volume_ml, 70), (semantics.SYRINGE_BARREL, 0.0, volume_ml)])
    result = semantics.evaluate_postconditions(
        "full_station_syringe_transfer",
        initial,
        final,
        {"volume_ul": 74},
        [final],
        bounds,
    )
    assert result["passed"], result

    final["liquid_volumes_ml"][experiment.REACTOR] += 0.024
    final["liquid_volumes_ml"][semantics.SOURCE_BOTTLE] -= 0.024
    assert not semantics.evaluate_postconditions(
        "full_station_syringe_transfer",
        initial,
        final,
        {"volume_ul": 74},
        [final],
        bounds,
    )["passed"]


def test_mixture_precondition_uses_requested_volume():
    state = {
        "experiment_phase": 2,
        "liquid_volumes_ml": {
            semantics.SOURCE_BOTTLE: 0.05,
            semantics.TARGET_BOTTLE: 0.0,
            semantics.BIG_BOTTLE: 400.0,
        },
        "device_state": {
            "operation_events": [],
            "semantic_state": {"BigCapOpen": False},
        },
    }
    assert semantics.check_preconditions("mixture_stock", state, {"volume_ul": 74})
    assert not semantics.check_preconditions("mixture_stock", state, {"volume_ul": 40})


def test_editor_labels_append_current_volume_without_fixed_amounts():
    labels = (Path(__file__).resolve().parents[2] / "web_ui/lib/labels.ts").read_text(encoding="utf-8")
    assert "parameters.volume_ul" in labels
    assert "matterixCapabilityLabel(parameters.capability_id, parameters)" in labels
    assert "小瓶向中瓶移液 100" not in labels
    assert "注射器过滤转移 50" not in labels
