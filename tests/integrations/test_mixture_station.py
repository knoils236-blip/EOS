import copy

import pytest

from tests.integrations.test_matterix_station_contract import snapshot
from bridge import station_mixture as mixture
from bridge import station_semantics as semantics
from bridge.capabilities import validate_task
from bridge.station_experiment import TIP2


@pytest.mark.parametrize("volume", [20, 74, 180])
def test_registered_recipe_accepts_only_supported_volume(volume):
    request = {"request_id": "dose", "session_id": "session", "capability_id": "station_mixture_stock",
               "parameters": {"volume_ul": volume}}
    assert validate_task(request)["parameters"]["volume_ul"] == volume
    request["parameters"]["volume_ul"] = 200
    with pytest.raises(ValueError, match="between"):
        validate_task(request)


@pytest.mark.parametrize("stock", [True, False])
def test_transfer_requires_observed_material_balance_and_correct_final_mixture(stock):
    initial = snapshot(2 if stock else 4)
    if not stock:
        initial["device_state"]["semantic_state"]["BigCapOpen"] = True
        initial["liquid_volumes_ml"][semantics.TARGET_BOTTLE] = 0.074
    final = copy.deepcopy(initial)
    volume = 0.074 if stock else 0.126
    source, tip = (semantics.SOURCE_BOTTLE, semantics.FIXED_TIP) if stock else (semantics.BIG_BOTTLE, TIP2)
    final["liquid_volumes_ml"][source] -= volume
    final["liquid_volumes_ml"][semantics.TARGET_BOTTLE] += volume
    final["device_state"].update(operation_events=[{"event": event, "object": tip} for event in ("grasp", "release")],
                                  operation_event_count=2, command_event_count=2)
    extrema = {name: [value, value] for name, value in initial["liquid_volumes_ml"].items()}
    extrema[source][0] -= volume
    extrema[tip][1] = volume
    workflow = "mixture_stock" if stock else "mixture_diluent"
    assert mixture.evaluate(workflow, initial, final, {"volume_ul": volume * 1000}, [final], extrema)["passed"]
    final["liquid_volumes_ml"][semantics.TARGET_BOTTLE] += 0.01
    assert not mixture.evaluate(workflow, initial, final, {"volume_ul": volume * 1000}, [final], extrema)["passed"]


def test_recipe_close_is_a_distinct_completion_condition():
    initial = snapshot(5)
    initial["device_state"]["joint_positions"]["front_door"] = 0.39
    final = copy.deepcopy(initial)
    final["device_state"]["joint_positions"]["front_door"] = 0
    assert mixture.evaluate("mixture_close", initial, final, {}, [], {})["passed"]
    assert not mixture.evaluate("mixture_close", initial, initial, {}, [], {})["passed"]
