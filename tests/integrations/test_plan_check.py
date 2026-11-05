"""Offline order checks of pipetting-station steps, as used by protocol validation and AI drafting."""

from pathlib import Path
import re
import sys
from unittest.mock import Mock

import pytest

from eos.configuration.configuration_manager import ConfigurationManager
from eos.configuration.entities.protocol_def import ProtocolDef
from eos.orchestration.services.lab_service import LabService
from integrations.matterix.bridge.capabilities import catalog
from integrations.matterix.bridge.plan_check import check_plan, scene_notes

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "user"))
SCENE = "pipetting_realtime"
BEGIN = {"task": "begin", "type": "Matterix Begin Experiment", "parameters": {}}
FULL = ["open_door", "prepare_vial", "transfer_small", "open_bottle", "transfer_big", "syringe_transfer", "close_door"]


def end(phase):
    return {"task": "end", "type": "Matterix End Experiment", "parameters": {"expected_phase": phase}}


def op(task, capability_id, **parameters):
    if "volume_ul" in parameters:
        return {"task": task, "type": "Matterix Volume Operation",
                "parameters": {"capability_id": capability_id, **parameters}}
    return {"task": task, "type": "Matterix Capability",
            "parameters": {"capability_id": capability_id, "parameters": parameters}}


def messages(steps):
    return [(error["task"], error["message"]) for error in check_plan(SCENE, steps)]


def test_valid_plans_pass():
    assert messages([BEGIN, *(op(name, f"station_full_station_{name}") for name in FULL), end(7)]) == []
    assert messages([
        BEGIN,
        op("fill", "station_pipette_xiaoping_to_zhongping1", volume_ul=100.0),
        op("self", "station_pipette_zhongping1", volume_ul=80.0),
        end(0),
    ]) == []
    mixture = [
        op("door", "station_full_station_open_door"),
        op("vial", "station_full_station_prepare_vial"),
        op("stock", "station_mixture_stock", volume_ul=80.0),
        op("bottle", "station_full_station_open_bottle"),
        op("diluent", "station_mixture_diluent", volume_ul=120.0),
        op("close", "station_mixture_close"),
    ]
    assert messages([BEGIN, *mixture, end(6)]) == []


def test_self_transfer_into_the_initially_empty_bottle_is_rejected():
    errors = messages([BEGIN, op("self", "station_pipette_zhongping1", volume_ul=100.0), end(0)])
    assert len(errors) == 1
    assert errors[0][0] == "self"
    assert "中瓶区1 至少有 100 µL" in errors[0][1]


def test_skipped_phase_reused_tip_missing_begin_and_unordered_steps_are_rejected():
    skipped = [BEGIN, *(op(name, f"station_full_station_{name}") for name in FULL if name != "open_bottle"), end(7)]
    assert [task for task, _ in messages(skipped)] == ["transfer_big"]
    reused = [
        BEGIN,
        op("fill", "station_pipette_xiaoping_to_zhongping1", volume_ul=100.0),
        op("again", "station_full_station_transfer_small", volume_ul=50.0),
    ]
    assert "TIP 1 号位" in messages(reused)[-1][1]
    assert [task for task, _ in messages([op("fill", "station_pipette_xiaoping_to_zhongping1", volume_ul=50.0)])] == [
        "fill"
    ]
    unordered = [BEGIN, {**op("door", "station_full_station_open_door"), "ordered": False}]
    assert "没有依赖" in messages(unordered)[0][1]


def test_runtime_only_values_and_liquid_skills_are_not_guessed():
    dynamic = [BEGIN, op("self", "station_pipette_zhongping1", volume_ul="eos_dynamic"), end(0)]
    assert messages(dynamic) == []
    ejected_tip = [
        BEGIN,
        op("fill", "station_pipette_xiaoping_to_zhongping1", volume_ul=100.0),
        op("dispense", "station_skill_dispense_to", zone="中瓶区1", volume_ul=100.0),
    ]
    assert "先装 TIP" in messages(ejected_tip)[0][1]
    assert check_plan("franka_beaker", [op("x", "lift_beaker")]) == []


def test_scene_notes_name_registered_capabilities():
    ids = {item["id"] for item in catalog()}
    text = "\n".join(scene_notes(SCENE))
    assert "station_pipette_zhongping1" in text
    assert set(re.findall(r"station_[a-z0-9_]+", text)) <= ids


@pytest.fixture
def config():
    return ConfigurationManager(str(ROOT / "user"), {"matterix_sim"})


def test_lab_service_checks_the_ai_drafted_protocol_in_dependency_order(config):
    protocol = ProtocolDef.model_validate(
        {
            "type": "test_2",
            "desc": "AI draft that failed at runtime",
            "labs": ["matterix_pipetting_lab"],
            "tasks": [
                {"name": "end_experiment", "type": "Matterix End Experiment", "dependencies": ["pipette_volume"],
                 "parameters": {"expected_phase": 0}, "devices": {"matterix": "begin_experiment.matterix"}},
                {"name": "pipette_volume", "type": "Matterix Volume Operation", "dependencies": ["begin_experiment"],
                 "parameters": {"capability_id": "station_pipette_zhongping1", "volume_ul": 100.0},
                 "devices": {"matterix": "begin_experiment.matterix"}},
                {"name": "begin_experiment", "type": "Matterix Begin Experiment",
                 "devices": {"matterix": {"lab_name": "matterix_pipetting_lab", "name": "matterix"}}},
            ],
        }
    )
    errors = LabService(config, Mock()).check_device_plans(protocol)
    assert [error["task"] for error in errors] == ["pipette_volume"]


def test_pipette_skills_need_a_tip_its_contents_and_an_open_container():
    # The editor-built protocol that failed at runtime: aspirating at the empty decap station
    capped = [
        BEGIN,
        op("mount", "station_skill_mount_tip", tip_index=1),
        op("aspirate", "station_skill_aspirate_from", zone="大开盖区", volume_ul=50),
        op("dispense", "station_skill_dispense_to", zone="大开盖区", volume_ul=50),
        end(0),
    ]
    errors = messages(capped)
    assert [task for task, _ in errors[:2]] == ["aspirate", "dispense"]
    assert "大开盖区本身没有容器" in errors[0][1]
    assert [task for task, _ in errors[2:]] == ["end", "end"]
    opened = [
        BEGIN,
        op("mount", "station_skill_mount_tip", tip_index=1),
        op("pick", "station_skill_big_bottle_pick"),
        op("decap", "station_skill_big_bottle_decap"),
        op("aspirate", "station_skill_aspirate_from", zone="大开盖区", volume_ul=50),
        op("dispense", "station_skill_dispense_to", zone="中瓶区1", volume_ul=50),
        op("self", "station_skill_aspirate_from", zone="中瓶区1", volume_ul=50),
        op("back", "station_skill_dispense_to", zone="中瓶区1", volume_ul=50),
        op("eject", "station_skill_eject_tip"),
        op("recap", "station_skill_big_bottle_recap"),
        op("place", "station_skill_big_bottle_place"),
        end(0),
    ]
    assert messages(opened) == []
    assert "先装 TIP" in messages([BEGIN, op("a", "station_skill_aspirate_from", zone="小瓶区", volume_ul=50)])[0][1]
    empty_tip = [BEGIN, op("m", "station_skill_mount_tip", tip_index=1),
                 op("d", "station_skill_dispense_to", zone="中瓶区1", volume_ul=50)]
    assert "TIP 内此时只有 0 µL" in messages(empty_tip)[0][1]
    empty_zone = [BEGIN, op("m", "station_skill_mount_tip", tip_index=1),
                  op("a", "station_skill_aspirate_from", zone="中瓶区1", volume_ul=50)]
    assert "中瓶区1只有 0 µL" in messages(empty_zone)[0][1]


def test_out_of_range_parameters_are_rejected():
    steps = [
        BEGIN,
        op("mount", "station_skill_mount_tip", tip_index=1),
        op("pick", "station_skill_big_bottle_pick"),
        op("decap", "station_skill_big_bottle_decap"),
        op("aspirate", "station_skill_aspirate_from", zone="大开盖区", volume_ul=200),
    ]
    errors = messages(steps)
    assert errors[0][0] == "aspirate"
    assert "超出允许范围" in errors[0][1]
    assert "超出允许范围" in messages([BEGIN, op("mount", "station_skill_mount_tip", tip_index=3)])[0][1]
    assert messages([BEGIN, op("mount", "station_skill_mount_tip", tip_index="eos_dynamic")]) == []


def test_pipetting_the_reactor_is_rejected_with_the_syringe_alternative():
    steps = [BEGIN, op("m", "station_skill_mount_tip", tip_index=1),
             op("a", "station_skill_aspirate_from", zone="中瓶区2", volume_ul=50)]
    assert "station_skill_syringe_aspirate" in messages(steps)[0][1]


def test_syringe_skills_model_pick_filter_payload_and_freshness():
    missing_pick = [
        BEGIN,
        op("aspirate", "station_skill_syringe_aspirate", volume_ul=50),
    ]
    assert "针筒" in messages(missing_pick)[0][1]

    filtered_aspirate = [
        BEGIN,
        op("pick", "station_skill_syringe_pick"),
        op("filter", "station_skill_filter_head_mount"),
        op("aspirate", "station_skill_syringe_aspirate", volume_ul=50),
    ]
    assert "过滤头" in messages(filtered_aspirate)[0][1]

    missing_filter = [
        BEGIN,
        op("pick", "station_skill_syringe_pick"),
        op("aspirate", "station_skill_syringe_aspirate", volume_ul=50),
        op("dispense", "station_skill_syringe_dispense", volume_ul=50),
    ]
    assert "station_skill_filter_head_mount" in messages(missing_filter)[0][1]

    insufficient_payload = [
        BEGIN,
        op("pick", "station_skill_syringe_pick"),
        op("aspirate", "station_skill_syringe_aspirate", volume_ul=20),
        op("filter", "station_skill_filter_head_mount"),
        op("dispense", "station_skill_syringe_dispense", volume_ul=50),
    ]
    assert "20 µL" in messages(insufficient_payload)[0][1]

    complete = [
        BEGIN,
        op("pick", "station_skill_syringe_pick"),
        op("aspirate", "station_skill_syringe_aspirate", volume_ul=50),
        op("filter", "station_skill_filter_head_mount"),
        op("dispense", "station_skill_syringe_dispense", volume_ul=40),
        op("discard", "station_skill_syringe_discard"),
        end(0),
    ]
    assert messages(complete) == []

    reused = [
        BEGIN,
        op("pick", "station_skill_syringe_pick"),
        op("discard", "station_skill_syringe_discard"),
        op("pick", "station_skill_syringe_pick"),
    ]
    assert "已经 consumed" in messages(reused)[0][1]


def test_independent_tip_and_syringe_tools_and_legacy_syringe_take():
    independent_tools = [
        BEGIN,
        op("mount", "station_skill_mount_tip", tip_index=1),
        op("pick", "station_skill_syringe_pick"),
        op("aspirate", "station_skill_syringe_aspirate", volume_ul=50),
        op("filter", "station_skill_filter_head_mount"),
        op("discard", "station_skill_syringe_discard"),
        op("eject", "station_skill_eject_tip"),
        end(0),
    ]
    assert messages(independent_tools) == []

    legacy_take = [BEGIN, op("take", "station_filter_take_syringe"), end(0)]
    errors = "\n".join(message for _, message in messages(legacy_take))
    assert "mounted syringe/filter" in errors
    assert "disposal" in errors

    repeated_pick = [
        BEGIN,
        op("take", "station_filter_take_syringe"),
        op("again", "station_skill_syringe_pick"),
    ]
    assert "已经 consumed" in messages(repeated_pick)[0][1]


def test_end_experiment_rejects_open_caps_held_items_and_missing_disposal():
    held_tip = [
        BEGIN,
        op("mount", "station_skill_mount_tip", tip_index=1),
        op("pick", "station_skill_big_bottle_pick"),
        op("decap", "station_skill_big_bottle_decap"),
        end(0),
    ]
    held_errors = "\n".join(message for _, message in messages(held_tip))
    assert "未关瓶盖" in held_errors
    assert "TIP" in held_errors
    assert "held tools" in held_errors
    assert "disposal evidence" in held_errors

    held_syringe = [
        BEGIN,
        op("pick", "station_skill_syringe_pick"),
        op("aspirate", "station_skill_syringe_aspirate", volume_ul=50),
        op("filter", "station_skill_filter_head_mount"),
        end(0),
    ]
    syringe_errors = "\n".join(message for _, message in messages(held_syringe))
    assert "mounted syringe/filter" in syringe_errors
    assert "disposal" in syringe_errors


def test_container_skills_share_the_runtime_preconditions():
    decap_unheld = [BEGIN, op("decap", "station_skill_small_vial_decap"), end(0)]
    assert "需要先夹取小瓶" in messages(decap_unheld)[0][1]
    recap_closed = [BEGIN, op("pick", "station_skill_big_bottle_pick"), op("recap", "station_skill_big_bottle_recap")]
    assert "大瓶盖没有打开" in messages(recap_closed)[0][1]
    # Phase 4 picks and decaps the bottle; ending there still holds it
    opened = [BEGIN, *(op(name, f"station_full_station_{name}") for name in FULL[:4]), end(4)]
    assert "held tools：大瓶" in "\n".join(message for _, message in messages(opened))


@pytest.mark.parametrize("capability_id", ["station_mixture_stock", "station_mixture_diluent"])
def test_out_of_range_mixture_volume_is_reported(capability_id):
    errors = messages([BEGIN, op("mix", capability_id, volume_ul=200)])
    assert "移液体积 200µL 超出允许范围" in errors[0][1]


def test_source_composites_share_tip_and_cap_rules():
    reused = [BEGIN, op("drop", "station_take_drop_tip"), op("mount", "station_skill_mount_tip", tip_index=1)]
    assert "TIP 1 号位" in messages(reused)[0][1]
    capped = [BEGIN, op("t", "station_skill_pipette_transfer", source="大瓶区", target="中瓶区1", volume_ul=50)]
    assert "需要先开盖" in messages(capped)[0][1]
    transfer = [BEGIN, op("t", "station_skill_pipette_transfer", source="小瓶区", target="中瓶区1", volume_ul=50),
                op("self", "station_pipette_zhongping1", volume_ul=50), op("pp", "station_small_cap_pick_place"),
                op("f", "station_skill_syringe_filter_transfer", volume_ul=50), end(0)]
    assert messages(transfer) == []
