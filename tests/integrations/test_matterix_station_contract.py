import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "integrations/matterix"))
from bridge import station_experiment as experiment
from bridge import station_semantics as semantics
from bridge.capabilities import catalog, validate_task
from bridge.common import validate_session


def snapshot(phase=0):
    return {
        "experiment_phase": phase,
        "liquid_balance": {"air_ml": 0.0, "spilled_ml": 0.0},
        "liquid_volumes_ml": {
            semantics.SOURCE_BOTTLE: 8.0,
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


def test_seven_phases_are_registered_and_dynamic_volume_is_bounded():
    registered = {value["workflow_name"] for value in catalog()}
    assert set(experiment.WORKFLOWS) <= registered
    request = {
        "request_id": "test",
        "session_id": "session",
        "capability_id": "station_full_station_transfer_small",
        "parameters": {"volume_ul": 74},
    }
    assert validate_task(request)["parameters"]["volume_ul"] == 74
    request["parameters"]["volume_ul"] = 200
    with pytest.raises(ValueError, match="between"):
        validate_task(request)


def test_video_is_an_explicit_session_opt_in():
    request = {"session_id": "s", "experiment_id": "e", "scene_id": "pipetting_realtime"}
    assert validate_session(request) == request
    assert validate_session({**request, "record_video": False}) == request
    assert validate_session({**request, "record_video": True})["record_video"] is True
    with pytest.raises(ValueError, match="boolean"):
        validate_session({**request, "record_video": "true"})


def test_door_acceptance_requires_observed_motion_and_destination():
    initial = snapshot()
    final = copy.deepcopy(initial)
    final["device_state"]["joint_positions"]["front_door"] = 0.39
    workflow = "full_station_open_door"
    assert experiment.evaluate(workflow, initial, final, [], {})["passed"]
    assert not experiment.evaluate(workflow, initial, initial, [], {})["passed"]
    initial["device_state"]["joint_positions"]["front_door"] = 0.39
    assert not experiment.evaluate(workflow, initial, final, [], {})["passed"]


def test_phase_order_used_tip_and_reactor_immersion_are_preconditions():
    state = snapshot(4)
    state["device_state"]["semantic_state"]["BigCapOpen"] = True
    assert all(c["passed"] for c in experiment.preconditions("full_station_transfer_big", state))
    state["device_state"]["operation_events"].append({"event": "grasp", "object": experiment.TIP2})
    assert not all(c["passed"] for c in experiment.preconditions("full_station_transfer_big", state))
    state = snapshot(5)
    state["liquid_volumes_ml"][experiment.REACTOR] = 0.1
    assert not all(c["passed"] for c in experiment.preconditions("full_station_syringe_transfer", state))
    assert not all(c["passed"] for c in experiment.preconditions("full_station_close_door", snapshot(0)))


def test_reset_verifies_liquid_caps_consumables_and_actual_joints():
    state = snapshot()
    assert experiment.initial_checks(state)["passed"]
    state["liquid_volumes_ml"][semantics.TARGET_BOTTLE] = 0.1
    assert not experiment.initial_checks(state)["passed"]
    state = snapshot()
    state["device_state"]["grips"][semantics.FIXED_TIP] = "tool"
    assert not experiment.initial_checks(state)["passed"]
    state = snapshot()
    state["device_state"]["operation_status"] = "accepted"
    assert not experiment.initial_checks(state)["passed"]


def test_same_final_volume_does_not_prove_self_transfer():
    initial = snapshot(4)
    initial["device_state"]["semantic_state"]["BigCapOpen"] = True
    final = copy.deepcopy(initial)
    final["device_state"]["semantic_state"]["BigCapOpen"] = False
    final["device_state"]["operation_events"] = [
        {"event": event, "object": obj}
        for obj, events in (
            (experiment.TIP2, ("grasp", "release")),
            (semantics.BIG_BOTTLE_CAP, ("cap_engaged", "release")),
        )
        for event in events
    ]
    final["device_state"].update(operation_event_count=4, command_event_count=4)
    extrema = {name: [value, value] for name, value in initial["liquid_volumes_ml"].items()}
    assert not experiment.evaluate("full_station_transfer_big", initial, final, [final], extrema)["passed"]
    extrema[semantics.BIG_BOTTLE][0] -= 0.1
    extrema[experiment.TIP2][1] = 0.1
    assert experiment.evaluate("full_station_transfer_big", initial, final, [final], extrema)["passed"]
    final["liquid_volumes_ml"][semantics.TARGET_BOTTLE] += 0.05
    assert not experiment.evaluate("full_station_transfer_big", initial, final, [final], extrema)["passed"]


def syringe_result(volume=0.0500034294):
    initial = snapshot(5)
    final = copy.deepcopy(initial)
    final["liquid_volumes_ml"][experiment.REACTOR] -= volume
    final["liquid_volumes_ml"][semantics.SOURCE_BOTTLE] += volume
    events = [
        {"event": e, "object": obj}
        for obj in (semantics.SYRINGE_BARREL, semantics.SYRINGE_PISTON)
        for e in ("grasp", "release")
    ]
    events.append({"event": "filter_installed", "object": "Filter_717", "barrel": semantics.SYRINGE_BARREL})
    final["device_state"].update(
        operation_events=events,
        operation_event_count=5,
        command_event_count=8,
        grips={"Filter_717": "/World/Connections/FilterMount_Filter_717"},
    )
    extrema = {name: [value, value] for name, value in initial["liquid_volumes_ml"].items()}
    extrema[experiment.REACTOR][0] -= volume
    extrema[semantics.SYRINGE_BARREL][1] = volume
    return initial, final, extrema


def test_syringe_goal_uses_export_controller_tolerance_but_conservation_stays_strict():
    initial, final, extrema = syringe_result()
    assert experiment.evaluate("full_station_syringe_transfer", initial, final, [final], extrema)["passed"]
    final["liquid_volumes_ml"][semantics.TARGET_BOTTLE] += 0.0001
    assert not experiment.evaluate("full_station_syringe_transfer", initial, final, [final], extrema)["passed"]
    initial, final, extrema = syringe_result(0.056)
    assert not experiment.evaluate("full_station_syringe_transfer", initial, final, [final], extrema)["passed"]


def test_discarded_filter_attachment_does_not_hide_a_held_piston_or_air():
    initial, final, extrema = syringe_result()
    final["device_state"]["grips"][semantics.SYRINGE_PISTON] = "/World/Connections/Grasp_piston"
    assert not experiment.evaluate("full_station_syringe_transfer", initial, final, [final], extrema)["passed"]
    # Air is accepted up to the source package's 5 µL (run_full_workflow.py), not beyond
    initial, final, extrema = syringe_result()
    final["liquid_balance"]["air_ml"] = 0.001
    assert experiment.evaluate("full_station_syringe_transfer", initial, final, [final], extrema)["passed"]
    final["liquid_balance"]["air_ml"] = 0.006
    assert not experiment.evaluate("full_station_syringe_transfer", initial, final, [final], extrema)["passed"]
