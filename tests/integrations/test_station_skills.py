# ruff: noqa: N802

import copy
import dataclasses
import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "integrations/matterix"))
sys.path.insert(0, "/home/ry/projects/Matterix/source/matterix_sm")
sys.path.insert(0, "/home/ry/projects/Matterix/PipettingStation_20261031/source/matterix_plc")
from bridge import station_semantics as semantics
from bridge import station_skills
from bridge.capabilities import catalog, validate_task
from bridge.station_experiment import REACTOR
from bridge.station_experiment import WORKFLOWS as FULL_WORKFLOWS
from bridge.station_mixture import WORKFLOWS as MIXTURE_WORKFLOWS
from bridge.recording import OVERVIEW_TARGET
from bridge.station_worker import StationSessionWorker
from matterix_plc.actions import skills as native_skills
from matterix_plc.executor import TRAVERSE_AXIS, _default_height_model, plan_macro
from matterix_plc.object_frames import ConsumableIndex


ROOT = Path(__file__).resolve().parents[2] / "integrations/matterix/bridge"


def snapshot(
    *,
    sim_time=0.0,
    tip_on=False,
    syringe_on=False,
    filter_on=False,
    small_cap_open=False,
    big_cap_open=False,
):
    return {
        "sim_time_s": sim_time,
        "liquid_balance": {"air_ml": 0.0, "spilled_ml": 0.0},
        "liquid_volumes_ml": {
            REACTOR: 70.0,
            semantics.SOURCE_BOTTLE: 1.0,
            semantics.TARGET_BOTTLE: 0.1,
            semantics.BIG_BOTTLE: 400.0,
            semantics.FIXED_TIP: 0.0,
            station_skills.TIP_BY_INDEX[2]: 0.0,
            semantics.SYRINGE_BARREL: 0.0,
        },
        "device_state": {
            "joint_positions": {"front_door": 0.0, "z_small_cap": -0.2},
            "grips": {},
            "attached": {},  # Runtime only includes currently gripped objects.
            "seated_tips": {},
            "cap_closed": {semantics.SMALL_VIAL: not small_cap_open, semantics.BIG_BOTTLE: not big_cap_open},
            "operation_events": [],
            "operation_commands": [],
            "operation_event_count": 0,
            "command_event_count": 0,
            "operation_status": "idle",
            "semantic_state": {
                "TipOn": tip_on,
                "SyringeOn": syringe_on,
                "FilterHeadOn": filter_on,
                "SmallCapOpen": small_cap_open,
                "BigCapOpen": big_cap_open,
                "JawsOpen": {
                    "small_cap_grip": True,
                    "big_cap_grip": True,
                    "pg3": True,
                    "pg4": True,
                },
                "PipetteVolume_uL": 0.0,
                "SyringeVolume_uL": 0.0,
            },
        },
    }


def with_operation(state, *, event=None, action=None, object_name=None):
    state = copy.deepcopy(state)
    device = state["device_state"]
    if event is not None:
        device["operation_events"].append({"event": event, "object": object_name})
        device["operation_event_count"] += 1
    if action is not None:
        device["operation_commands"].append({"action": action, "object": object_name})
        device["command_event_count"] += 1
    device["operation_status"] = "accepted"
    return state


def result_checks(result):
    return {item["name"]: item for item in result["checks"]}


def test_records_reconcile_fully_and_expose_only_bounded_real_parameters():
    capabilities = {item["id"]: item for item in catalog()}
    raw_capabilities = json.loads((ROOT / "capabilities.json").read_text(encoding="utf-8"))
    raw_catalog = json.loads((ROOT / "station_catalog.json").read_text(encoding="utf-8"))
    assert len(station_skills.SKILLS) == 25

    for item in station_skills.SKILLS:
        if not item["source_workflow"]:
            assert getattr(native_skills, item["constructor"].split("(", 1)[0])
        capability = capabilities[item["id"]]
        assert capability["workflow_name"] == item["workflow_name"]
        assert capability["parameters"]["record_video"]["default"] is False
        assert capability["parameters"]["hide_shell"]["default"] is False
        enabled = raw_catalog["enabled_capabilities"][item["id"]]
        workflow = raw_catalog["workflows"][item["workflow_name"]]
        assert enabled["workflow_name"] == item["workflow_name"]
        assert workflow["enabled_capability"] == item["id"]
        assert workflow["source_symbol"] == (item["workflow_name"] if item["source_workflow"] else item["constructor"])
        if item["source_workflow"]:  # registered source workflows carry their per-workflow run
            assert [e["scope"] for e in workflow["verified_evidence"]] == ["kinematic_registered_workflow"]
        for evidence in workflow["reference_evidence"] + workflow["verified_evidence"]:
            assert evidence["sha256"] == station_skills._source_sha256(Path(evidence["path"]))

    mount = capabilities["station_skill_mount_tip"]["parameters"]["tip_index"]
    assert mount["type"] == "integer"
    assert mount["choices"] == [1, 2]
    for tip_index, tip_name in station_skills.TIP_BY_INDEX.items():
        assert plan_macro("装小TIP头", tip_index, resolve_operations=True).consumables["tip"] == tip_name
    for capability_id in (
        "station_skill_aspirate_from",
        "station_skill_dispense_to",
        "station_skill_small_vial_pick",
        "station_skill_small_vial_place",
        "station_skill_big_bottle_pick",
        "station_skill_big_bottle_place",
        "station_skill_syringe_pick",
        "station_skill_filter_head_mount",
        "station_skill_syringe_aspirate",
        "station_skill_syringe_dispense",
    ):
        index = capabilities[capability_id]["parameters"]["index"]
        assert index["type"] == "integer"
        assert index["choices"] == [1]
        assert (index["min"], index["max"]) == (1, 1)
    for capability_id, name in (("station_skill_aspirate_from", "zone"), ("station_skill_pipette_transfer", "source"),
                                ("station_skill_pipette_transfer", "target")):
        zone = capabilities[capability_id]["parameters"][name]
        assert zone["type"] == "string"
        assert zone["choices"] == list(station_skills.PIPETTE_ZONES)
    offered = {*station_skills.PIPETTE_ZONES, *station_skills.UNREACHABLE_PIPETTE_ZONES}
    assert set(native_skills.PIPETTE_ZONES) == offered
    assert station_skills.ZONE_LIQUID_OBJECTS["中瓶区2"] == REACTOR
    assert station_skills.WORKFLOWS.isdisjoint(FULL_WORKFLOWS)
    assert station_skills.WORKFLOWS.isdisjoint(MIXTURE_WORKFLOWS)
    for skill_id in (
        "station_skill_aspirate_from",
        "station_skill_dispense_to",
        "station_skill_syringe_aspirate",
        "station_skill_syringe_dispense",
        "station_skill_pipette_transfer",
        "station_skill_syringe_filter_transfer",
    ):
        volume = capabilities[skill_id]["parameters"]["volume_ul"]
        assert (volume["min"], volume["max"]) == (20, 180)
    for capability_id in (
        "station_pipette_xiaoping_to_zhongping1",
        "station_pipette_zhongping1",
    ):
        volume = capabilities[capability_id]["parameters"]["volume_ul"]
        assert (volume["min"], volume["max"]) == (20, 180)
    assert set(raw_catalog["enabled_capabilities"]) == {
        item["id"] for item in raw_capabilities if item["scene_id"] == "pipetting_realtime"
    }
    assert not any("pose" in name or "joint" in name for item in station_skills.SKILLS for name in item["parameters"])


def test_mount_and_eject_require_current_task_events_and_real_state():
    initial = snapshot()
    mounted = copy.deepcopy(initial)
    mounted["device_state"]["semantic_state"]["TipOn"] = True
    mounted["device_state"]["attached"][semantics.FIXED_TIP] = True
    mounted["device_state"]["seated_tips"][semantics.FIXED_TIP] = True
    mounted = with_operation(mounted, event="grasp", action="grasp", object_name=semantics.FIXED_TIP)
    result = station_skills.evaluate_postconditions("skill_mount_tip", initial, mounted, {"tip_index": 1}, [mounted])
    assert result["passed"], result

    stale = copy.deepcopy(mounted)
    stale["device_state"]["operation_events"].insert(0, {"event": "grasp", "object": semantics.FIXED_TIP})
    stale["device_state"]["operation_commands"].insert(0, {"action": "grasp", "object": semantics.FIXED_TIP})
    stale["device_state"]["operation_event_count"] = 2
    stale["device_state"]["command_event_count"] = 2
    baseline = copy.deepcopy(stale)
    baseline["device_state"]["operation_events"] = baseline["device_state"]["operation_events"][:1]
    baseline["device_state"]["operation_commands"] = baseline["device_state"]["operation_commands"][:1]
    baseline["device_state"]["operation_event_count"] = 1
    baseline["device_state"]["command_event_count"] = 1
    result = station_skills.evaluate_postconditions("skill_mount_tip", baseline, baseline, {"tip_index": 1}, [baseline])
    assert not result["passed"]

    ejected = with_operation(mounted, event="release", action="eject_tip", object_name=semantics.FIXED_TIP)
    ejected["device_state"]["semantic_state"]["TipOn"] = False
    ejected["device_state"]["attached"][semantics.FIXED_TIP] = False
    ejected["device_state"]["seated_tips"][semantics.FIXED_TIP] = False
    result = station_skills.evaluate_postconditions("skill_eject_tip", mounted, ejected, {}, [ejected])
    assert result["passed"], result


def test_pipette_liquid_acceptance_uses_new_delta_not_a_success_flag():
    initial = snapshot(tip_on=True)
    initial["device_state"]["attached"][semantics.FIXED_TIP] = True
    initial["device_state"]["seated_tips"][semantics.FIXED_TIP] = True
    final = copy.deepcopy(initial)
    final["liquid_volumes_ml"][semantics.SOURCE_BOTTLE] -= 0.05
    final["liquid_volumes_ml"][semantics.FIXED_TIP] += 0.05
    final["device_state"]["semantic_state"]["PipetteVolume_uL"] = 50.0
    final = with_operation(final, action="aspirate", object_name=semantics.SOURCE_BOTTLE)
    params = {"zone": "小瓶区", "volume_ul": 50}
    result = station_skills.evaluate_postconditions("skill_aspirate_from", initial, final, params, [final])
    assert result["passed"], result

    no_transfer = with_operation(initial, action="aspirate", object_name=semantics.SOURCE_BOTTLE)
    result = station_skills.evaluate_postconditions("skill_aspirate_from", initial, no_transfer, params, [no_transfer])
    assert not result["passed"]
    assert result_checks(result)["液量载体变化"]["passed"] is False

    weak = snapshot()
    weak["device_state"]["semantic_state"]["TipOn"] = True
    weak["liquid_volumes_ml"][semantics.SOURCE_BOTTLE] = 0.04
    failed = semantics.check_preconditions("skill_aspirate_from", weak, params)
    assert any(item["name"] == "源容器液量充足" for item in failed)
    assert not semantics.check_preconditions("skill_aspirate_from", weak, {"zone": "小瓶区", "volume_ul": 20})


def test_existing_independent_pipettes_accept_configurable_volume():
    initial = snapshot()
    final = copy.deepcopy(initial)
    final["liquid_volumes_ml"][semantics.SOURCE_BOTTLE] -= 0.05
    final["liquid_volumes_ml"][semantics.TARGET_BOTTLE] += 0.05
    final = with_operation(
        final,
        event="grasp",
        action="aspirate",
        object_name=semantics.FIXED_TIP,
    )
    result = semantics.evaluate_postconditions(
        "pipette_xiaoping_to_zhongping1",
        initial,
        final,
        {"volume_ul": 50},
        [final],
    )
    assert result["passed"], result

    self_initial = snapshot()
    self_final = copy.deepcopy(self_initial)
    self_final = with_operation(
        self_final,
        event="grasp",
        action="aspirate",
        object_name=station_skills.TIP_BY_INDEX[2],
    )
    self_final = with_operation(
        self_final,
        event="release",
        action="eject_tip",
        object_name=station_skills.TIP_BY_INDEX[2],
    )
    extrema = {name: [value, value] for name, value in self_initial["liquid_volumes_ml"].items()}
    extrema[semantics.TARGET_BOTTLE][0] -= 0.05
    extrema[station_skills.TIP_BY_INDEX[2]][1] = 0.05
    result = semantics.evaluate_postconditions(
        "pipette_zhongping1_tip2",
        self_initial,
        self_final,
        {"volume_ul": 50},
        [self_final],
        extrema,
    )
    assert result["passed"], result


def test_wait_and_joint_units_use_actual_reported_values():
    initial = snapshot()
    waited = copy.deepcopy(initial)
    waited["sim_time_s"] = 2.0
    assert station_skills.evaluate_postconditions("skill_wait", initial, waited, {"duration_s": 2}, [waited])["passed"]

    units = StationSessionWorker._load_joint_units()
    assert units["air_pump_piston"] == "m"
    assert units["front_door"] == "m"
    assert units["rotate_small_cap"] == "rad"
    assert set(units.values()) == {"m", "rad"}


def test_small_cap_home_reads_the_usd_joint_and_moves_toward_zero():
    initial = snapshot()
    final = copy.deepcopy(initial)
    final["device_state"]["joint_positions"]["z_small_cap"] = 0.0
    assert station_skills.evaluate_postconditions("skill_small_cap_home", initial, final, {}, [final])["passed"]
    final["device_state"]["joint_positions"]["z_small_cap"] = -0.3
    assert not station_skills.evaluate_postconditions("skill_small_cap_home", initial, final, {}, [final])["passed"]


def test_syringe_sequence_requires_both_components_and_filter_before_dispense():
    mounted = snapshot(syringe_on=True)
    mounted["device_state"]["attached"][semantics.SYRINGE_BARREL] = True
    mounted["device_state"]["attached"][semantics.SYRINGE_PISTON] = True
    assert not [
        item
        for item in semantics.check_preconditions("skill_syringe_aspirate", mounted, {"volume_ul": 50})
        if item["name"].startswith("当前")
    ]

    missing_piston = copy.deepcopy(mounted)
    missing_piston["device_state"]["attached"][semantics.SYRINGE_PISTON] = False
    failed = semantics.check_preconditions("skill_syringe_aspirate", missing_piston, {"volume_ul": 50})
    assert any(item["name"] == "当前已夹活塞" for item in failed)
    failed = semantics.check_preconditions("skill_syringe_dispense", mounted, {"volume_ul": 50})
    assert any(item["name"] == "过滤头已安装" for item in failed)

    filtered = copy.deepcopy(mounted)
    filtered["device_state"]["semantic_state"]["FilterHeadOn"] = True
    filtered = with_operation(
        filtered,
        event="filter_installed",
        action="install_filter",
        object_name="Filter",
    )
    result = station_skills.evaluate_postconditions("skill_filter_head_mount", mounted, filtered, {}, [filtered])
    assert result["passed"], result


@pytest.mark.parametrize(
    ("workflow", "object_name", "jaw_name", "picking"),
    [
        ("skill_small_vial_pick", semantics.SMALL_VIAL_CAP, "small_cap_grip", True),
        ("skill_small_vial_place", semantics.SMALL_VIAL_CAP, "small_cap_grip", False),
        ("skill_big_bottle_pick", semantics.BIG_BOTTLE_CAP, "big_cap_grip", True),
        ("skill_big_bottle_place", semantics.BIG_BOTTLE_CAP, "big_cap_grip", False),
    ],
)
def test_bottle_pick_place_acceptance_uses_current_task_attachments(
    workflow,
    object_name,
    jaw_name,
    picking,
):
    initial = snapshot()
    initial["device_state"]["attached"][object_name] = not picking
    initial["device_state"]["semantic_state"]["JawsOpen"][jaw_name] = picking
    final = copy.deepcopy(initial)
    final["device_state"]["attached"][object_name] = picking
    final["device_state"]["semantic_state"]["JawsOpen"][jaw_name] = not picking
    event = "grasp" if picking else "release"
    final = with_operation(final, event=event, action=event, object_name=object_name)
    result = station_skills.evaluate_postconditions(workflow, initial, final, {}, [final])
    assert result["passed"], result


@pytest.mark.parametrize(
    ("workflow", "semantic_name", "cap", "bottle", "event", "expected"),
    [
        (
            "skill_small_vial_decap",
            "SmallCapOpen",
            semantics.SMALL_VIAL_CAP,
            semantics.SMALL_VIAL,
            "cap_detached",
            True,
        ),
        (
            "skill_small_vial_recap",
            "SmallCapOpen",
            semantics.SMALL_VIAL_CAP,
            semantics.SMALL_VIAL,
            "cap_engaged",
            False,
        ),
        ("skill_big_bottle_decap", "BigCapOpen", semantics.BIG_BOTTLE_CAP, semantics.BIG_BOTTLE, "cap_detached", True),
        ("skill_big_bottle_recap", "BigCapOpen", semantics.BIG_BOTTLE_CAP, semantics.BIG_BOTTLE, "cap_engaged", False),
    ],
)
def test_cap_open_close_uses_current_events_and_real_geometry(
    workflow,
    semantic_name,
    cap,
    bottle,
    event,
    expected,
):
    initial = snapshot()
    initial["device_state"]["attached"][cap] = True
    initial["device_state"]["semantic_state"][semantic_name] = not expected
    initial["device_state"]["cap_closed"][bottle] = expected
    final = copy.deepcopy(initial)
    final["device_state"]["semantic_state"][semantic_name] = expected
    final["device_state"]["cap_closed"][bottle] = not expected
    final = with_operation(final, event=event, action="engage_cap", object_name=cap)
    result = station_skills.evaluate_postconditions(workflow, initial, final, {}, [final])
    assert result["passed"], result


def test_syringe_pick_and_discard_use_component_state_and_current_events():
    initial = snapshot()
    picked = copy.deepcopy(initial)
    picked["device_state"]["semantic_state"]["SyringeOn"] = True
    picked["device_state"]["semantic_state"]["JawsOpen"]["pg3"] = False
    picked["device_state"]["semantic_state"]["JawsOpen"]["pg4"] = False
    for component in (semantics.SYRINGE_BARREL, semantics.SYRINGE_PISTON):
        picked["device_state"]["attached"][component] = True
        picked = with_operation(picked, event="grasp", action="grasp", object_name=component)
    result = station_skills.evaluate_postconditions("skill_syringe_pick", initial, picked, {}, [picked])
    assert result["passed"], result

    discarded = copy.deepcopy(picked)
    discarded["device_state"]["semantic_state"]["SyringeOn"] = False
    discarded["device_state"]["semantic_state"]["FilterHeadOn"] = False
    for component in (semantics.SYRINGE_BARREL, semantics.SYRINGE_PISTON):
        discarded["device_state"]["attached"][component] = False
        discarded = with_operation(discarded, event="release", action="release_syringe", object_name=component)
    result = station_skills.evaluate_postconditions("skill_syringe_discard", picked, discarded, {}, [discarded])
    assert result["passed"], result


@pytest.mark.parametrize("operation", ["aspirate", "dispense"])
def test_syringe_liquid_operations_use_barrel_and_container_deltas(operation):
    initial = snapshot(syringe_on=True, filter_on=operation == "dispense")
    initial["device_state"]["attached"][semantics.SYRINGE_BARREL] = True
    initial["device_state"]["attached"][semantics.SYRINGE_PISTON] = True
    initial["liquid_volumes_ml"]["EightPositionReactor_662"] = 1.0
    final = copy.deepcopy(initial)
    volume_ml = 0.05
    if operation == "aspirate":
        final["liquid_volumes_ml"]["EightPositionReactor_662"] -= volume_ml
        final["liquid_volumes_ml"][semantics.SYRINGE_BARREL] += volume_ml
        final["device_state"]["semantic_state"]["SyringeVolume_uL"] = 50.0
        workflow = "skill_syringe_aspirate"
    else:
        initial["liquid_volumes_ml"][semantics.SYRINGE_BARREL] = volume_ml
        final = copy.deepcopy(initial)
        final["liquid_volumes_ml"][semantics.SYRINGE_BARREL] = 0.0
        final["liquid_volumes_ml"][semantics.SOURCE_BOTTLE] += volume_ml
        final["device_state"]["semantic_state"]["SyringeVolume_uL"] = 0.0
        initial["device_state"]["semantic_state"]["SyringeVolume_uL"] = 50.0
        workflow = "skill_syringe_dispense"
    final = with_operation(final, action=operation)
    result = station_skills.evaluate_postconditions(
        workflow,
        initial,
        final,
        {"volume_ul": 50},
        [final],
    )
    assert result["passed"], result


def test_syringe_dispense_rejects_empty_payload():
    state = snapshot(syringe_on=True, filter_on=True)
    state["device_state"]["attached"] = {semantics.SYRINGE_BARREL: True, semantics.SYRINGE_PISTON: True}
    checks = station_skills.precondition_checks("skill_syringe_dispense", state, {"volume_ul": 50})
    assert any(not c["passed"] and "液量" in c["name"] for c in checks)
    state["device_state"]["semantic_state"]["SyringeVolume_uL"] = 50
    checks = station_skills.precondition_checks("skill_syringe_dispense", state, {"volume_ul": 50})
    assert any(not c["passed"] and c["name"] == "针筒实际液量充足" for c in checks)
    state["liquid_volumes_ml"][semantics.SYRINGE_BARREL] = 0.05
    assert all(c["passed"] for c in station_skills.precondition_checks("skill_syringe_dispense", state, {"volume_ul": 50}))


@pytest.mark.parametrize(("receiver", "passed"), [(semantics.SOURCE_BOTTLE, True), (semantics.TARGET_BOTTLE, False)])
def test_syringe_dispense_acceptance_requires_the_transfer_bottle(receiver, passed):
    initial = snapshot(syringe_on=True, filter_on=True)
    initial["liquid_volumes_ml"][semantics.SYRINGE_BARREL] = 0.05
    initial["device_state"]["semantic_state"]["SyringeVolume_uL"] = 50
    final = copy.deepcopy(initial)
    final["liquid_volumes_ml"][semantics.SYRINGE_BARREL] = 0
    final["liquid_volumes_ml"][receiver] += 0.05
    final["device_state"]["semantic_state"]["SyringeVolume_uL"] = 0
    final = with_operation(final, action="dispense")
    result = station_skills.evaluate_postconditions("skill_syringe_dispense", initial, final, {"volume_ul": 50}, [final])
    assert result["passed"] is passed, result


def test_shell_count_uses_only_valid_selector_paths():
    class Prim:
        def __init__(self, valid):
            self.valid = valid

        def IsValid(self):
            return self.valid

    class Stage:
        def GetPrimAtPath(self, path):
            return Prim(path != "missing")

    assert StationSessionWorker._valid_shell_count(Stage(), ["present", "/Visuals/a", "/Visuals/b", "missing"]) == 3


def test_catalog_skill_entries_are_generated_from_station_skills():
    """capabilities.json and station_catalog.json must match SKILLS; run tools/sync_station_skills.py after edits."""
    raw_capabilities = {
        item["id"]: item for item in json.loads((ROOT / "capabilities.json").read_text(encoding="utf-8"))
    }
    raw_catalog = json.loads((ROOT / "station_catalog.json").read_text(encoding="utf-8"))
    for item in station_skills.SKILLS:
        name = item["workflow_name"]
        assert raw_capabilities[item["id"]] == station_skills.capability_entry(name)
        assert raw_catalog["enabled_capabilities"][item["id"]] == station_skills.catalog_enabled_entry(name)
        assert raw_catalog["workflows"][name] == station_skills.catalog_workflow_entry(name)
    skills = {item["id"] for item in station_skills.SKILLS if item["tier"] == "skill"}
    assert {item_id for item_id, item in raw_capabilities.items() if item["tier"] == "skill"} == skills


def test_pipetting_in_a_capped_zone_is_rejected_before_motion():
    params = {"zone": "大开盖区", "volume_ul": 50}
    failed = semantics.check_preconditions("skill_aspirate_from", snapshot(tip_on=True), params)
    assert [item["name"] for item in failed] == ["大开盖区 有已开盖的容器"]
    assert not semantics.check_preconditions("skill_aspirate_from", snapshot(tip_on=True, big_cap_open=True), params)
    uncapped = {"zone": "小瓶区", "volume_ul": 50}
    assert not semantics.check_preconditions("skill_aspirate_from", snapshot(tip_on=True), uncapped)


def test_pipetting_at_the_opening_station_targets_the_carried_bottle(monkeypatch):
    # The planner picks its target from CAD positions, where the 500 mL bottle sits in its home slot
    macro = "小移液机构去大开盖区吸液"
    assert plan_macro(macro, 1, resolve_operations=True).consumables == {}
    plan = plan_macro(macro, 1)
    x, y = _default_height_model()["pipette_pump"].tool_xy(
        plan.joint_targets["gantry"], plan.joint_targets[TRAVERSE_AXIS["pipette_pump"]]
    )
    original = ConsumableIndex.from_bundle()
    carried = ConsumableIndex(
        {
            name: dataclasses.replace(item, x=x, y=y) if name == semantics.BIG_BOTTLE else item
            for name, item in original._by_name.items()
        }
    )
    resolved = plan_macro(macro, 1, consumables=carried, resolve_operations=True).consumables
    assert resolved == {"bottle": semantics.BIG_BOTTLE}

    # Every loaded workflow is planned against live positions, whatever the zone
    planned = []
    monkeypatch.setattr(
        station_skills, "build_workflow", lambda name, parameters, settle_first: (name, parameters["zone"]))
    worker = object.__new__(StationSessionWorker)
    worker.scene_settled = True
    monkeypatch.setattr(worker, "_use_live_positions", planned.append, raising=False)
    for zone in ("大开盖区", "中瓶区1"):
        worker._load_workflow("skill_aspirate_from", {"zone": zone, "volume_ul": 50})
    assert planned == [("skill_aspirate_from", "大开盖区"), ("skill_aspirate_from", "中瓶区1")]


def test_only_an_unsettled_scene_prepends_the_spawn_settle(monkeypatch):
    def settles(workflow):
        return [type(node).__name__ for node in workflow.sub_actions].count("SettleCfg")

    # Native skill configs need Isaac Lab's configclass; a stand-in keeps the composition under test
    constructor = station_skills.descriptor("skill_aspirate_from")["constructor"]
    monkeypatch.setattr(native_skills, constructor, type(constructor, (types.SimpleNamespace,), {}))
    fresh = station_skills.build_workflow("skill_aspirate_from", {"volume_ul": 50})
    resting = station_skills.build_workflow("skill_aspirate_from", {"volume_ul": 50}, settle_first=False)
    assert settles(fresh) == 1
    assert settles(resting) == 0
    assert resting.sub_actions == fresh.sub_actions[1:]

    # The worker asks for the settle exactly while its scene is unsettled
    calls = []
    monkeypatch.setattr(
        station_skills, "build_workflow", lambda name, parameters, settle_first: calls.append(settle_first))
    worker = object.__new__(StationSessionWorker)
    for settled in (False, True):
        worker.scene_settled = settled
        worker._build_workflow("skill_mount_tip", {"tip_index": 1})
    assert calls == [True, False]


@dataclasses.dataclass
class FakeStep:
    """Stand-in for ProgramStepCfg, which needs Isaac Lab to construct."""

    macro: str
    index: int = 1
    consumables: object = None
    runtime_vars: dict = dataclasses.field(default_factory=dict)
    twin_alignment: bool = False
    twin_settle_offsets: dict = dataclasses.field(default_factory=dict)


def test_station_worker_refuses_kinematic_execution():
    cfg = types.SimpleNamespace(sim=types.SimpleNamespace(dt=1 / 250), decimation=1,
                                kinematic_mode=False, terminations=types.SimpleNamespace(time_out=None))
    StationSessionWorker._validate_station_cfg(cfg)
    cfg.kinematic_mode = True
    with pytest.raises(ValueError, match="requires physics"):
        StationSessionWorker._validate_station_cfg(cfg)


def test_station_control_space_excludes_service_doors():
    from matterix_plc.axes import ACTION_COLUMN_BY_USD_JOINT  # noqa: PLC0415

    names = sorted(ACTION_COLUMN_BY_USD_JOINT, key=ACTION_COLUMN_BY_USD_JOINT.get)
    terms = {
        "linear": types.SimpleNamespace(joint_names=names[:24]),
        "angular": types.SimpleNamespace(joint_names=names[24:28]),
        "pump": types.SimpleNamespace(joint_names=names[28:]),
    }
    cfg = types.SimpleNamespace(articulated_assets={"station": types.SimpleNamespace(action_terms=terms)})
    columns = StationSessionWorker._configure_station_actions(cfg)
    assert terms["angular"].joint_names == ["rotate_small_cap", "rotate_large_cap"]
    assert columns == (*range(24), 26, 27, 28)


def test_action_columns_follow_the_order_isaac_resolves_each_term():
    from matterix_plc.axes import ACTION_COLUMN_BY_USD_JOINT  # noqa: PLC0415

    # rig_manifest order, which differs from the articulation order Isaac Lab uses unless preserve_order is set
    linear = ["gantry_y", "carriage_x1", "carriage_x2", "front_door", "bottle_clamp_left", "bottle_clamp_right"]
    angular = ["rotate_large_cap", "rotate_small_cap"]
    terms = {"linear": types.SimpleNamespace(joint_names=list(linear), preserve_order=False),
             "angular": types.SimpleNamespace(joint_names=list(angular), preserve_order=False)}
    cfg = types.SimpleNamespace(articulated_assets={"station": types.SimpleNamespace(action_terms=terms)})
    columns = StationSessionWorker._configure_station_actions(cfg)
    assert all(term.preserve_order is True for term in terms.values())
    assert columns == tuple(ACTION_COLUMN_BY_USD_JOINT[name] for name in linear + angular)


def test_physics_receives_no_service_door_command_columns():
    import torch  # noqa: PLC0415

    received = []
    worker = object.__new__(StationSessionWorker)
    worker.station_action_columns = (*range(24), 26, 27, 28)
    worker.env = types.SimpleNamespace(step=lambda action, **kw: received.append((action, kw)))
    action = torch.arange(29, dtype=torch.float32).reshape(1, -1)
    semantic_actions = {"station": "operation"}
    worker._step_station(action, semantic_actions=semantic_actions)
    expected = [*range(24), 26, 27, 28]
    assert received[0][0].tolist() == [expected]
    assert received[0][1]["semantic_actions"] is semantic_actions
    assert worker.control == [expected]
    assert action.tolist() == [list(range(29))]


def test_service_doors_are_absent_from_protocol_joint_observations(monkeypatch):
    import torch  # noqa: PLC0415

    names = ["service_door_left", "rotate_small_cap", "service_door_right", "rotate_large_cap"]
    asset = types.SimpleNamespace(joint_names=names, data=types.SimpleNamespace(
        joint_pos=torch.tensor([[1.0, 2.0, 3.0, 4.0]])))
    worker = object.__new__(StationSessionWorker)
    worker.env = types.SimpleNamespace(scene=types.SimpleNamespace(articulations={"station": asset}))
    worker.joint_units = worker._load_joint_units()
    worker.session_sim_s, worker.completed_phase = 0.0, 0
    monkeypatch.setattr(semantics, "snapshot_semantics", lambda env: {"device_state": {}})
    monkeypatch.setattr(worker, "_station_driver", lambda: types.SimpleNamespace(
        operations=types.SimpleNamespace(air_ml=0.0, spilled_ml=0.0)))
    measured = worker._semantics()
    assert measured["jointsSI"] == {"rotate_small_cap": 2.0, "rotate_large_cap": 4.0}
    assert measured["device_state"]["joint_positions"] == measured["jointsSI"]
    assert not {"service_door_left", "service_door_right"} & worker.joint_units.keys()


@pytest.mark.parametrize("parameters", [
    {"service_door_left": 1.0},
    {"service_door_right": 1.0},
    {"joint_positions": {"service_door_left": 1.0, "service_door_right": 1.0}},
])
def test_service_door_targets_cannot_be_submitted_as_capability_parameters(parameters):
    with pytest.raises(ValueError, match="Unknown capability parameter"):
        validate_task({"request_id": "no-service-door", "session_id": "station",
                       "capability_id": "station_skill_mount_tip", "parameters": parameters})


def test_wait_only_sequence_holds_measured_joint_positions_and_prior_commands():
    import torch  # noqa: PLC0415
    from matterix_sm import StateMachine, WaitCfg  # noqa: PLC0415
    from matterix_plc.axes import ACTION_COLUMN_BY_USD_JOINT  # noqa: PLC0415

    names = list(reversed(ACTION_COLUMN_BY_USD_JOINT))
    positions = torch.arange(len(names), dtype=torch.float32).reshape(1, -1) / 1000
    asset = types.SimpleNamespace(joint_names=names, data=types.SimpleNamespace(joint_pos=positions))
    worker = object.__new__(StationSessionWorker)
    worker.env = types.SimpleNamespace(scene=types.SimpleNamespace(articulations={"station": asset}))
    worker.sm = StateMachine(num_envs=1, dt=0.004, device="cpu", status_interval_steps=0)
    worker.sm.set_action_sequence([WaitCfg(duration=0.02)])
    worker.sm.reset()
    worker.held_commands = {}
    worker._seed_station_commands()
    action, _ = worker.sm.step({})
    assert action is not None
    for name, column in ACTION_COLUMN_BY_USD_JOINT.items():
        assert action[0, column] == positions[0, names.index(name)]
    previous = torch.full_like(action, 0.05)
    worker.held_commands = {"station": previous}
    worker.sm.reset()
    worker._seed_station_commands()
    action, _ = worker.sm.step({})
    assert torch.equal(action, previous)


def test_closeup_follows_measured_tool_before_consumable_is_attached(monkeypatch):
    import numpy as np  # noqa: PLC0415

    transform = np.eye(4)
    transform[:3, 3] = (0.2, 0.3, 1.1)
    ops = types.SimpleNamespace(attached=lambda name: False, tip_mounts={}, poses={"syringe": transform},
                                tool_path=lambda body: "syringe")
    worker = object.__new__(StationSessionWorker)
    worker.camera_workflow = "skill_syringe_pick"
    worker.camera_frames = {"syringe_tip": {"body": "z_syringe", "offset": [0, 0, -0.2]}}
    monkeypatch.setattr(worker, "_station_driver", lambda: types.SimpleNamespace(operations=ops))
    assert worker._camera_focus() == pytest.approx((0.2, 0.3, 0.9))
    transform[0, 3] += 0.1
    assert worker._camera_focus() == pytest.approx((0.3, 0.3, 0.9))
    ops.poses = {}  # a fresh reset has no measured poses until the first physics tick
    assert worker._camera_focus() == OVERVIEW_TARGET


def test_steps_without_a_findable_target_are_refused_before_moving(monkeypatch):
    from matterix_plc.actions import program_action  # noqa: PLC0415

    monkeypatch.setattr(program_action, "ProgramStepCfg", FakeStep)
    workflow = types.SimpleNamespace(
        sub_actions=[
            FakeStep("装小TIP头"),
            types.SimpleNamespace(
                sub_actions=[FakeStep("小移液机构去大开盖区吸液"), FakeStep("小移液机构去大开盖区吸液")]
            ),
            FakeStep("小移液机构去中瓶区1吐液"),
            FakeStep("大开盖机构去大开盖区开盖"),
        ]
    )
    failed = StationSessionWorker._unresolved_targets(workflow)
    assert [item["name"] for item in failed] == ["小移液机构去大开盖区吸液 的目标耗材"]
    assert "131.7mm" in failed[0]["actual"]


def test_offered_pipette_zones_are_exactly_those_the_planner_can_target():
    """Every offered zone has a bottle at its slot or is an opening station a bottle is carried to first."""
    opening = set(station_skills.CAPPED_ZONES) - {"大瓶区"}
    for zone in native_skills.PIPETTE_ZONES:
        for action in ("吸液", "吐液"):
            resolved = bool(plan_macro(f"小移液机构去{zone}{action}", 1, resolve_operations=True).consumables)
            offered = zone in station_skills.PIPETTE_ZONES
            assert resolved == (offered and zone not in opening), (zone, action)


def test_live_index_moves_carried_objects_and_keeps_cad_for_the_rest(monkeypatch):
    import numpy as np  # noqa: PLC0415

    cad = ConsumableIndex.from_bundle()
    carried = np.eye(4)
    carried[:3, 3] = (cad[semantics.BIG_BOTTLE].x + 0.13, cad[semantics.BIG_BOTTLE].y, cad[semantics.BIG_BOTTLE].z)
    settled = np.eye(4)
    settled[:3, 3] = (
        cad[semantics.SOURCE_BOTTLE].x + 0.001,
        cad[semantics.SOURCE_BOTTLE].y,
        cad[semantics.SOURCE_BOTTLE].z,
    )
    poses = {semantics.BIG_BOTTLE: carried, semantics.SOURCE_BOTTLE: settled}
    unsimulated = "DesignD_DesignC_ChromatographyVial2ml_140"  # in the manifest, no measured pose: the reported crash

    def pose(name):
        return poses[name]  # KeyError for bodies without a measured pose, like the real operation layer

    ops = types.SimpleNamespace(objects=[*poses, unsimulated], pose=pose)
    worker = object.__new__(StationSessionWorker)
    monkeypatch.setattr(worker, "_station_driver", lambda: types.SimpleNamespace(operations=ops), raising=False)

    live = worker._live_consumables()
    assert live[semantics.BIG_BOTTLE].x == pytest.approx(cad[semantics.BIG_BOTTLE].x + 0.13)
    assert live[semantics.SOURCE_BOTTLE] == cad[semantics.SOURCE_BOTTLE]
    assert live[unsimulated] == cad[unsimulated]
    assert len(live) == len(cad)


def test_only_liquid_steps_use_live_positions_so_placing_back_finds_the_home_slot(monkeypatch):
    from matterix_plc.actions import program_action  # noqa: PLC0415

    # Placing the carried bottle back plans against its home slot, which only the CAD index still holds
    cad = ConsumableIndex.from_bundle()
    away = ConsumableIndex(
        {
            name: dataclasses.replace(item, x=item.x - 0.13) if name == semantics.BIG_BOTTLE else item
            for name, item in cad._by_name.items()
        }
    )
    assert "big_cap_lift" in plan_macro("大开盖机构去大瓶区放瓶", 1, consumables=cad).joint_targets
    assert "big_cap_lift" not in plan_macro("大开盖机构去大瓶区放瓶", 1, consumables=away).joint_targets

    monkeypatch.setattr(program_action, "ProgramStepCfg", FakeStep)
    worker = object.__new__(StationSessionWorker)
    monkeypatch.setattr(worker, "_live_consumables", lambda: "live", raising=False)
    steps = [
        FakeStep(macro)
        for macro in (
            "装小TIP头",
            "大开盖机构去大瓶区取瓶",
            "小移液机构去大开盖区吸液",
            "过滤机构去中瓶区2吸液",
            "小移液机构去中瓶区1吐液",
            "大开盖机构去大瓶区放瓶",
        )
    ]
    worker._use_live_positions(types.SimpleNamespace(sub_actions=steps))
    assert [step.consumables for step in steps] == [None, None, "live", "live", "live", None]


@pytest.mark.parametrize(("moved_ml", "passed"), [(0.04966, True), (0.0449, False)])
def test_aspirate_accepts_the_source_packages_5_ul_transfer_tolerance(moved_ml, passed):
    # 49.66 µL is what real PhysX moved for a 50 µL request (the piston counts as arrived within 5 µL)
    initial = snapshot(tip_on=True)
    final = copy.deepcopy(initial)
    final["liquid_volumes_ml"][semantics.SOURCE_BOTTLE] -= moved_ml
    final["liquid_volumes_ml"][semantics.FIXED_TIP] += moved_ml
    final["device_state"]["semantic_state"]["PipetteVolume_uL"] = 50.0
    final = with_operation(final, action="aspirate")
    result = station_skills.evaluate_postconditions(
        "skill_aspirate_from", initial, final, {"zone": "小瓶区", "volume_ul": 50}, [final])
    assert result["passed"] is passed, [c for c in result["checks"] if not c["passed"]]


def test_pipette_transfer_moves_liquid_between_zones_and_disposes_the_tip():
    initial = snapshot()
    final = copy.deepcopy(initial)
    final["liquid_volumes_ml"][semantics.SOURCE_BOTTLE] -= 0.0498
    final["liquid_volumes_ml"][semantics.TARGET_BOTTLE] += 0.0498
    for event in ("grasp", "release"):
        final = with_operation(final, event=event, object_name=semantics.FIXED_TIP)
    for action in ("aspirate", "dispense", "eject_tip"):
        final = with_operation(final, action=action)
    extrema = {name: [min(initial["liquid_volumes_ml"][name], final["liquid_volumes_ml"][name]),
                      max(initial["liquid_volumes_ml"][name], final["liquid_volumes_ml"][name])]
               for name in initial["liquid_volumes_ml"]}
    extrema[semantics.FIXED_TIP] = [0.0, 0.0498]
    extrema[semantics.SOURCE_BOTTLE][0] = initial["liquid_volumes_ml"][semantics.SOURCE_BOTTLE] - 0.0498
    params = {"source": "小瓶区", "target": "中瓶区1", "volume_ul": 50, "tip_index": 1}
    result = station_skills.evaluate_postconditions("skill_pipette_transfer", initial, final, params, [final], extrema)
    assert result["passed"], [c for c in result["checks"] if not c["passed"]]
    final["liquid_volumes_ml"][semantics.TARGET_BOTTLE] -= 0.0498  # liquid that never arrived
    result = station_skills.evaluate_postconditions("skill_pipette_transfer", initial, final, params, [final], extrema)
    assert not result["passed"]


def test_front_door_acceptance_reads_the_measured_joint():
    initial = snapshot()
    final = copy.deepcopy(initial)
    final["device_state"]["joint_positions"]["front_door"] = 0.389
    assert station_skills.evaluate_postconditions("skill_front_door", initial, final, {"open_m": 0.39}, [final])["passed"]
    final["device_state"]["joint_positions"]["front_door"] = 0.002836  # the blocked physics door
    assert not station_skills.evaluate_postconditions(
        "skill_front_door", initial, final, {"open_m": 0.39}, [final])["passed"]
