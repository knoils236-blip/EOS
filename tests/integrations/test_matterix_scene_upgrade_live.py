"""Opt-in EOS + Isaac Sim check for current atomic pipetting station capabilities.

Uses a copied user package, private SQLite database, private Ray namespace and the bridge selected through
``EOS_TEST_MATTERIX_PORT``. No live run happens unless that variable is set.
"""

import asyncio
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
import uuid

from dotenv import load_dotenv
import pytest
import ray
import yaml

from eos.configuration.configuration_manager import ConfigurationManager
from eos.configuration.eos_config import EosConfig
from eos.configuration.protocol_graph import ProtocolGraph
from eos.database.file_db_interface import FileDbInterface
from eos.devices.device_manager import DeviceManager
from eos.orchestration.orchestrator import Orchestrator
from eos.protocols.entities.protocol_run import ProtocolRunStatus, ProtocolRunSubmission
from eos.resources.resource_manager import ResourceManager
from eos.scheduling.utils import compute_hold_users
from eos.tasks.entities.task import TaskStatus
from eos.tasks.task_definition import build_task_output_file_path
from eos.tasks.task_manager import TaskManager
from eos.utils.di.di_container import get_di_container
from eos.web_api.controllers.matterix_controller import _build_observation

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not os.environ.get("EOS_TEST_MATTERIX_PORT"), reason="EOS_TEST_MATTERIX_PORT not set"),
]

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_NAME = "matterix_scene_upgrade_smoke"
LAB_NAME = "matterix_pipetting_lab"
DEVICE_NAME = "matterix"
WALL_TIMEOUT_S = 1200
RECORDING_META = {"matterix": {"record_video": True, "hide_shell": True}}
CAPABILITY_STEPS = (
    ("small_vial_pick", "station_skill_small_vial_pick", {}),
    ("small_vial_decap", "station_skill_small_vial_decap", {}),
    ("small_vial_recap", "station_skill_small_vial_recap", {}),
    ("small_vial_place", "station_skill_small_vial_place", {}),
    ("small_cap_home", "station_skill_small_cap_home", {}),
    ("mount_tip", "station_skill_mount_tip", {"tip_index": 1}),
    ("aspirate", "station_skill_aspirate_from", {"zone": "小瓶区", "volume_ul": 50}),
    ("dispense", "station_skill_dispense_to", {"zone": "中瓶区1", "volume_ul": 50}),
    ("eject_tip", "station_skill_eject_tip", {}),
)
TASK_ORDER = ("begin", *(name for name, _, _ in CAPABILITY_STEPS), "finish")


def _assert_current_station_capabilities() -> None:
    catalog = json.loads((ROOT / "integrations/matterix/bridge/station_catalog.json").read_text())
    enabled = catalog["enabled_capabilities"]
    atomic = {capability_id for capability_id in enabled if capability_id.startswith("station_skill_")}
    assert catalog["scene_id"] == "pipetting_realtime"
    assert len(atomic) == 19
    assert {capability_id for _, capability_id, _ in CAPABILITY_STEPS} <= atomic


def _protocol_definition() -> dict:
    tasks = [
        {
            "name": "begin",
            "type": "Matterix Begin Experiment",
            "timeout": 240,
            "devices": {
                DEVICE_NAME: {
                    "lab_name": LAB_NAME,
                    "name": DEVICE_NAME,
                    "hold": True,
                }
            },
        }
    ]
    for task_name, capability_id, parameters in CAPABILITY_STEPS:
        previous = tasks[-1]["name"]
        tasks.append(
            {
                "name": task_name,
                "type": "Matterix Capability",
                "duration": 90,
                "timeout": 480,
                "retries": 0,
                "parameters": {
                    "capability_id": capability_id,
                    "parameters": parameters,
                },
                "dependencies": [previous],
                "devices": {DEVICE_NAME: {"ref": f"{previous}.{DEVICE_NAME}", "hold": True}},
            }
        )
    tasks.append(
        {
            "name": "finish",
            "type": "Matterix End Experiment",
            "timeout": 60,
            "parameters": {"expected_phase": 0},
            "dependencies": [tasks[-1]["name"]],
            "devices": {DEVICE_NAME: {"ref": f"{tasks[-1]['name']}.{DEVICE_NAME}", "hold": True}},
        }
    )
    return {
        "type": PROTOCOL_NAME,
        "desc": "Temporary bounded scene-upgrade check using current atomic station capabilities",
        "labs": [LAB_NAME],
        "tasks": tasks,
    }


def _write_protocol(user_dir: Path) -> Path:
    protocol_dir = user_dir / "matterix_sim" / "protocols" / PROTOCOL_NAME
    protocol_dir.mkdir(parents=True)
    protocol_path = protocol_dir / "protocol.yml"
    protocol_path.write_text(yaml.safe_dump(_protocol_definition(), sort_keys=False, allow_unicode=True))
    return protocol_path


def _configure_private_package(user_dir: Path, port: int) -> Path:
    package_dir = user_dir / "matterix_sim"
    shutil.copytree(
        ROOT / "user" / "matterix_sim",
        package_dir,
        ignore=shutil.ignore_patterns("__pycache__", "optimizer_editor_acceptance_c91e"),
    )
    lab_path = package_dir / "labs" / LAB_NAME / "lab.yml"
    lab = yaml.safe_load(lab_path.read_text())
    lab["devices"][DEVICE_NAME]["init_parameters"]["port"] = port
    lab_path.write_text(yaml.safe_dump(lab, sort_keys=False, allow_unicode=True))
    return _write_protocol(user_dir)


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str, allow_nan=False).encode()


async def _write_bytes(path: Path, data: bytes) -> None:
    await asyncio.to_thread(path.write_bytes, data)


async def _write_json(path: Path, value) -> None:
    await _write_bytes(path, _json_bytes(value))


async def _download(file_db: FileDbInterface, protocol_run_name: str, task_name: str, name: str) -> bytes:
    data = await file_db.get_file(build_task_output_file_path(protocol_run_name, task_name, name))
    assert data
    return data


async def _drive(orchestrator: Orchestrator, task_manager: TaskManager, protocol_run_name: str, evidence_dir: Path):
    seen = set()
    live_seen = False
    next_probe = 0.0
    async with asyncio.timeout(WALL_TIMEOUT_S):
        while True:
            await orchestrator.spin_once()
            async with orchestrator.db_interface.get_async_session() as db:
                run = await orchestrator.protocols.get_protocol_run(db, protocol_run_name)
                tasks = await task_manager.get_tasks(db, protocol_run_name=protocol_run_name)
            for task in tasks:
                stamp = (task.name, task.status.value)
                if stamp not in seen:
                    print(protocol_run_name, *stamp, flush=True)
                    seen.add(stamp)
                if (not live_seen and task.name in {"aspirate", "dispense"}
                        and task.status == TaskStatus.RUNNING and time.monotonic() >= next_probe):
                    next_probe = time.monotonic() + 1
                    detail = await _build_observation(orchestrator, task)
                    if detail["samples"]:
                        assert detail["source"] == "simulation"
                        assert detail["latest_sample"]["units"]["joints"]["air_pump_piston"] == "m"
                        await _write_json(evidence_dir / f"{protocol_run_name}-live-observation.json", detail)
                        live_seen = True
            if run.status in {
                ProtocolRunStatus.COMPLETED,
                ProtocolRunStatus.FAILED,
                ProtocolRunStatus.CANCELLED,
            }:
                return run, tasks, live_seen
            await asyncio.sleep(0.05)


async def test_current_atomic_scene_protocol_end_to_end(tmp_path):
    load_dotenv(ROOT / ".env")
    _assert_current_station_capabilities()
    port = int(os.environ["EOS_TEST_MATTERIX_PORT"])
    run_id = "matterix_scene_upgrade_" + uuid.uuid4().hex
    ray_namespace = "matterix_scene_" + uuid.uuid4().hex
    user_dir = tmp_path / "user"
    protocol_path = await asyncio.to_thread(_configure_private_package, user_dir, port)
    evidence_text = os.environ.get("EOS_MATTERIX_EVIDENCE_DIR", str(tmp_path / "evidence"))
    evidence_dir = await asyncio.to_thread(lambda: Path(evidence_text).expanduser().resolve())
    await asyncio.to_thread(evidence_dir.mkdir, parents=True, exist_ok=True)
    summary = {
        "run_id": run_id,
        "protocol": PROTOCOL_NAME,
        "bridge_port": port,
        "ray_namespace": ray_namespace,
        "recorded_meta": RECORDING_META,
        "scope": "atomic-operation lifecycle only; expected_phase=0 is not seven-phase acceptance",
        "passed": False,
    }

    ray.init(address="auto", namespace=ray_namespace, ignore_reinit_error=True)
    config = EosConfig(
        user_dir=user_dir,
        packages={"matterix_sim"},
        db={"type": "sqlite", "sqlite": {"db_dir": tmp_path, "db_name": run_id}},
    )
    config.file_db.bucket = os.environ.get("EOS_MATTERIX_ARCHIVE_BUCKET", config.file_db.bucket)
    summary["archive_bucket"] = config.file_db.bucket
    orchestrator = Orchestrator(config)
    di = get_di_container()
    actor = None
    rows = []

    try:
        await orchestrator.initialize()
        manager = di.get(DeviceManager)
        task_manager = di.get(TaskManager)
        resource_manager = di.get(ResourceManager)
        file_db = di.get(FileDbInterface)
        configuration_manager = di.get(ConfigurationManager)

        async with orchestrator.db_interface.get_async_session() as db:
            await orchestrator.loading.load_labs(db, {LAB_NAME})
            await orchestrator.loading.load_protocols(db, {PROTOCOL_NAME})
            await orchestrator.protocols.submit_protocol_run(
                db,
                ProtocolRunSubmission(
                    name=run_id,
                    type=PROTOCOL_NAME,
                    owner="matterix-scene-upgrade",
                    meta=RECORDING_META,
                ),
            )

        protocol = configuration_manager.protocols[PROTOCOL_NAME]
        assert tuple(task.name for task in protocol.tasks) == TASK_ORDER
        for previous, current in zip(protocol.tasks[:-1], protocol.tasks[1:], strict=True):
            assert current.devices[DEVICE_NAME] == f"{previous.name}.{DEVICE_NAME}"
            assert current.device_holds[DEVICE_NAME] is True
        hold_users, _ = compute_hold_users(ProtocolGraph(protocol))
        assert hold_users[("begin", DEVICE_NAME)] == frozenset(TASK_ORDER[1:])
        assert protocol.tasks[-1].parameters == {"expected_phase": 0}
        assert not any("full_station" in capability_id for _, capability_id, _ in CAPABILITY_STEPS)

        actor = manager.get_device_actor(LAB_NAME, DEVICE_NAME)
        run, rows, live_seen = await _drive(orchestrator, task_manager, run_id, evidence_dir)
        assert run.status == ProtocolRunStatus.COMPLETED, run.error_message
        assert live_seen, "Running tasks must expose actual telemetry before completion"
        assert run.meta == RECORDING_META
        by_name = {task.name: task for task in rows}
        assert set(by_name) == set(TASK_ORDER)
        rows = [by_name[name] for name in TASK_ORDER]
        assert all(task.status == TaskStatus.COMPLETED for task in rows)
        assert all(task.meta == RECORDING_META for task in rows)

        begin_state = by_name["begin"].output_parameters["state"]
        session_id = begin_state["session_id"]
        requests = {}
        for task_name, capability_id, parameters in CAPABILITY_STEPS:
            task = by_name[task_name]
            assert task.output_parameters["passed"] is True
            assert {
                "request.json",
                "result.json",
                "samples.jsonl",
                "simulation.mp4",
                "provenance.json",
                "matterix-evidence.zip",
                "matterix-context.json",
            } <= set(task.output_file_names)
            request = json.loads(await _download(file_db, run_id, task_name, "request.json"))
            result = json.loads(await _download(file_db, run_id, task_name, "result.json"))
            assert request["capability_id"] == capability_id
            assert request["session_id"] == session_id
            assert request["scene_id"] == "pipetting_realtime"
            for name, value in parameters.items():
                assert request["parameters"][name] == value
            assert request["parameters"]["record_video"] is True
            assert request["parameters"]["hide_shell"] is True
            assert result["source"] == "simulation"
            assert result["acceptance"]["passed"] is True
            requests[task_name] = request

        representative = by_name["aspirate"]
        observation = await _build_observation(orchestrator, representative)
        request = requests["aspirate"]
        result_bytes = await _download(file_db, run_id, "aspirate", "result.json")
        result = json.loads(result_bytes)
        provenance = json.loads(await _download(file_db, run_id, "aspirate", "provenance.json"))
        samples = observation["samples"]
        file_names = {item["name"] for item in observation["files"]}
        effective_parameters = observation["snapshot"]["effective"]["parameters"]["parameters"]
        five_tab_payload = {
            "curves": bool(samples) and bool(observation["latest_sample"]["units"]),
            "snapshot": observation["snapshot"]["submitted"]["meta"] == RECORDING_META,
            "acceptance": observation["acceptance"]["passed"] is True,
            "video": bool(observation["videos"]),
            "raw_files": {"request.json", "result.json", "samples.jsonl", "simulation.mp4"} <= file_names,
        }
        assert all(five_tab_payload.values()), five_tab_payload
        assert observation["source"] == "simulation"
        assert observation["protocol_run_name"] == run_id
        assert observation["task_name"] == "aspirate"
        assert all(sample["source"] == "simulation" for sample in samples)
        assert observation["latest_sample"]["units"]
        assert effective_parameters == request["parameters"]
        assert result["source"] == "simulation"
        assert provenance["source"] == "simulation"
        assert provenance["settings"]["simulation_mode"] == "physics"
        assert provenance["settings"]["hardware_calibrated"] is False
        assert provenance["request"] == request
        assert all(request["parameters"][name] is True for name in ("record_video", "hide_shell"))

        video = observation["videos"][0]
        video_bytes = await _download(file_db, run_id, "aspirate", video["name"])
        assert len(video_bytes) > 1_024
        assert b"ftyp" in video_bytes[:64]

        sample_bytes = await _download(file_db, run_id, "aspirate", "samples.jsonl")
        context_bytes = await _download(file_db, run_id, "aspirate", "matterix-context.json")
        request_bytes = await _download(file_db, run_id, "aspirate", "request.json")
        await _write_bytes(evidence_dir / f"{run_id}-request.json", request_bytes)
        await _write_bytes(evidence_dir / f"{run_id}-result.json", result_bytes)
        await _write_bytes(evidence_dir / f"{run_id}-samples.jsonl", sample_bytes)
        await _write_bytes(evidence_dir / f"{run_id}-provenance.json", _json_bytes(provenance))
        await _write_bytes(evidence_dir / f"{run_id}-matterix-context.json", context_bytes)
        await _write_bytes(evidence_dir / f"{run_id}-simulation.mp4", video_bytes)
        await _write_json(evidence_dir / f"{run_id}-observation.json", observation)
        await _write_json(evidence_dir / f"{run_id}-requests.json", requests)
        await _write_json(
            evidence_dir / f"{run_id}-task-summaries.json",
            {
                "protocol_run": run.model_dump(mode="json"),
                "tasks": [task.model_dump(mode="json") for task in rows],
            },
        )
        await asyncio.to_thread(shutil.copy2, protocol_path, evidence_dir / f"{run_id}-protocol.yml")

        final_semantics = by_name["finish"].output_parameters["state"]["latest_sample"]["semantics"]
        volumes = final_semantics["liquid_volumes_ml"]
        assert volumes["TransferBottle10ml_617"] == pytest.approx(7.95, abs=1e-5)
        assert volumes["DesignC_BottleBody50ml_231"] == pytest.approx(0.05, abs=1e-5)
        async with orchestrator.db_interface.get_async_session() as db:
            source_resource = await resource_manager.get_resource(db, "MX_SOURCE")
            tip_resource = await resource_manager.get_resource(db, "MX_TIP_1")
        assert source_resource.meta["volume_ml"] == pytest.approx(volumes["TransferBottle10ml_617"])
        assert source_resource.meta["source"] == "simulation"
        assert tip_resource.meta["used"] is True

        report = await actor.report.remote()
        assert report["experiment_owner"] is None
        assert report["session"]["status"] == "idle"
        assert report["session"]["active_task_id"] is None
        assert report["blocked"] is False
        summary["passed"] = True
    except Exception as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        summary["task_statuses"] = [
            {"name": task.name, "status": task.status.value, "error": task.error_message} for task in rows
        ]
        try:
            if orchestrator._initialized:
                await asyncio.wait_for(orchestrator.terminate(), timeout=90)
        except Exception as exc:
            summary["cleanup_error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            try:
                await orchestrator.db_interface._async_engine.dispose()
            except Exception as exc:
                summary["database_dispose_error"] = f"{type(exc).__name__}: {exc}"
            db_path = tmp_path / f"{run_id}.db"
            if db_path.exists():
                target = evidence_dir / f"{run_id}.sqlite3"
                with sqlite3.connect(db_path) as source, sqlite3.connect(target) as destination:
                    source.backup(destination)
            await _write_json(evidence_dir / f"{run_id}-summary.json", summary)
            await asyncio.to_thread(shutil.rmtree, user_dir, True)
