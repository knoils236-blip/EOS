"""Scene discovery and generated tasks must work without starting equipment."""

import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

from eos.configuration.configuration_manager import ConfigurationManager
from eos.configuration.entities.protocol_def import ProtocolDef
from eos.configuration.entities.task_def import TaskDef
from eos.configuration.validation import ProtocolValidator
from eos.devices.base_device import BaseDevice
from eos.orchestration.services.lab_service import LabService

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "user"))


@pytest.fixture
def config():
    return ConfigurationManager(str(ROOT / "user"), {"matterix_sim"})


async def test_scene_discovery_is_offline_filtered_and_does_not_expose_connection_settings(config, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("Discovery must not initialize equipment")

    monkeypatch.setattr(BaseDevice, "initialize", forbidden)
    scenes = await LabService(config, Mock()).get_editor_scenes()
    by_lab = {scene["lab_name"]: scene for scene in scenes}
    assert set(by_lab) == {"matterix_lab", "matterix_pipetting_lab", "mixture_mock_lab", "mixture_matterix_lab"}
    station = by_lab["matterix_pipetting_lab"]
    assert station["scene_id"] == "pipetting_realtime"
    assert station["loaded"] is False
    operations = {preset["id"] for preset in station["presets"]}
    assert "station_take_drop_tip_big_bottle" in operations
    assert "station_full_station_open_door" in operations
    assert "pickup_beaker" not in operations
    stock = next(p for p in station["presets"] if p["id"] == "station_mixture_stock")
    assert stock["parameter_specs"]["volume_ul"]["label"] == "移液体积"
    assert stock["parameter_specs"]["volume_ul"]["unit"] == "µL"
    assert {p["id"] for p in by_lab["matterix_lab"]["presets"]} == {
        "grasp_beaker", "lift_beaker", "lower_beaker", "release_beaker", "wait"
    }
    assert not config.labs
    text = json.dumps(scenes)
    assert "init_parameters" not in text
    assert "127.0.0.1" not in text
    assert "65200" not in text


async def test_registered_presets_build_a_valid_nine_task_station_protocol(config):
    scenes = await LabService(config, Mock()).get_editor_scenes()
    scene = next(s for s in scenes if s["lab_name"] == "matterix_pipetting_lab")
    presets = {p["id"]: p for p in scene["presets"]}
    ids = [
        "begin_experiment",
        "station_full_station_open_door",
        "station_full_station_prepare_vial",
        "station_full_station_transfer_small",
        "station_full_station_open_bottle",
        "station_full_station_transfer_big",
        "station_full_station_syringe_transfer",
        "station_full_station_close_door",
        "end_experiment",
    ]
    tasks = []
    for index, identifier in enumerate(ids):
        preset = presets[identifier]
        assignment = (
            {"ref": f"step{index - 1}.matterix"}
            if index
            else {"lab_name": scene["lab_name"], "name": scene["device_name"]}
        )
        assignment["hold"] = identifier != "end_experiment"
        tasks.append(
            TaskDef(
                name=f"step{index}",
                **preset["task"],
                devices={"matterix": assignment},
                dependencies=[f"step{index - 1}"] if index else [],
            )
        )
    config.load_lab(scene["lab_name"])
    protocol = ProtocolDef(
        type="editor_station", desc="Editor preset validation", labs=[scene["lab_name"]], tasks=tasks
    )
    ProtocolValidator(protocol, list(config.labs.values())).validate()
    assert tasks[2].parameters["parameters"]["timeout_s"] == 120
    assert tasks[3].type == "Matterix Volume Operation"
    assert tasks[3].parameters == {
        "capability_id": "station_full_station_transfer_small", "volume_ul": 100.0, "timeout_s": 120.0
    }
    assert tasks[-1].parameters["expected_phase"] == 7


async def test_catalog_defaults_are_independent_between_discoveries(config):
    service = LabService(config, Mock())
    scenes = await service.get_editor_scenes()
    presets = next(s for s in scenes if s["scene_id"] == "franka_beaker")["presets"]
    next(p for p in presets if p["id"] == "lift_beaker")["task"]["parameters"]["parameters"]["height_m"] = 99
    fresh = next(s for s in await service.get_editor_scenes() if s["scene_id"] == "franka_beaker")["presets"]
    assert next(p for p in fresh if p["id"] == "lift_beaker")["task"]["parameters"]["parameters"]["height_m"] == 0.1


async def test_existing_station_phase_exposes_volume_but_recording_is_run_level(config):
    scenes = await LabService(config, Mock()).get_editor_scenes()
    station = next(scene for scene in scenes if scene["scene_id"] == "pipetting_realtime")
    transfer = next(p for p in station["presets"] if p["id"] == "station_full_station_transfer_small")
    assert "volume_ul" in transfer["parameter_specs"]
    assert transfer["parameter_specs"]["volume_ul"]["default"] == 100
    assert "100" not in transfer["label"]
    for scene in scenes:
        for preset in scene["presets"]:
            assert not {"record_video", "hide_shell"} & preset.get("parameter_specs", {}).keys()
            parameters = preset["task"].get("parameters", {})
            assert not {"record_video", "hide_shell"} & (parameters.keys() | parameters.get("parameters", {}).keys())


async def test_skills_are_a_separate_group_and_fixed_or_hidden_entries_stay_out(config):
    scenes = await LabService(config, Mock()).get_editor_scenes()
    station = next(scene for scene in scenes if scene["scene_id"] == "pipetting_realtime")
    groups = {preset["id"]: preset["group"] for preset in station["presets"]}
    assert groups["station_skill_mount_tip"] == "原子技能"
    assert groups["station_full_station_open_door"] == "场景操作"
    aspirate = next(p for p in station["presets"] if p["id"] == "station_skill_aspirate_from")
    assert "index" not in aspirate["parameter_specs"]
    assert "index" not in aspirate["task"]["parameters"]["parameters"]
    beaker = next(scene for scene in scenes if scene["scene_id"] == "franka_beaker")
    assert not {"pickup_beaker", "return_beaker"} & {preset["id"] for preset in beaker["presets"]}


@pytest.mark.parametrize(
    ("parameters", "error"),
    [
        ({"capability_id": "station_full_station_transfer_small", "volume_ul": 500.0}, "greater than .max."),
        ({"capability_id": "station_full_station_open_door", "volume_ul": 100.0}, "not one of the choices"),
    ],
)
def test_volume_operation_is_validated_when_the_protocol_loads(config, parameters, error):
    config.load_lab("matterix_pipetting_lab")
    task = TaskDef(
        name="transfer",
        type="Matterix Volume Operation",
        parameters=parameters,
        devices={"matterix": {"lab_name": "matterix_pipetting_lab", "name": "matterix"}},
    )
    protocol = ProtocolDef(type="volume_check", desc="Volume bounds", labs=["matterix_pipetting_lab"], tasks=[task])
    with pytest.raises(Exception, match=error):
        ProtocolValidator(protocol, list(config.labs.values())).validate()
