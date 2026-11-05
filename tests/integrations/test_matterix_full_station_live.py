"""Opt-in EOS + real Isaac Sim acceptance; uses a private database, Ray namespace and bridge port."""

import asyncio
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
import uuid
import zipfile

import pytest
import ray
import yaml
from dotenv import load_dotenv

from eos.configuration.configuration_manager import ConfigurationManager
from eos.configuration.eos_config import EosConfig
from eos.database.file_db_interface import FileDbInterface
from eos.devices.device_manager import DeviceManager
from eos.devices.entities.device import DeviceStatus
from eos.orchestration.orchestrator import Orchestrator
from eos.protocols.entities.protocol_run import ProtocolRunSubmission, ProtocolRunStatus
from eos.resources.resource_manager import ResourceManager
from eos.tasks.entities.task import TaskStatus
from eos.tasks.task_manager import TaskManager
from eos.utils.di.di_container import get_di_container

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not os.environ.get("EOS_TEST_MATTERIX_PORT"), reason="EOS_TEST_MATTERIX_PORT not set"),
]
ROOT = Path(__file__).resolve().parents[2]


async def test_seven_phases_twice_cancel_and_failure(tmp_path):
    root = ROOT
    load_dotenv(root / ".env")
    user_dir = tmp_path / "user"
    shutil.copytree(root / "user/matterix_sim", user_dir / "matterix_sim", ignore=shutil.ignore_patterns("__pycache__"))
    lab_path = user_dir / "matterix_sim/labs/matterix_pipetting_lab/lab.yml"
    lab = yaml.safe_load(await asyncio.to_thread(lab_path.read_text))
    lab["devices"]["matterix"]["init_parameters"]["port"] = int(os.environ["EOS_TEST_MATTERIX_PORT"])
    lab_path.write_text(yaml.safe_dump(lab, sort_keys=False))
    token = "mx_" + uuid.uuid4().hex[:10]
    ray.init(address="auto", namespace=token, ignore_reinit_error=True)
    config = EosConfig(
        user_dir=user_dir,
        packages={"matterix_sim"},
        db={"type": "sqlite", "sqlite": {"db_dir": tmp_path, "db_name": "acceptance"}},
    )
    config.file_db.bucket = os.environ.get("EOS_MATTERIX_ARCHIVE_BUCKET", config.file_db.bucket)
    orchestrator = Orchestrator(config)
    evidence = Path(os.environ.get("EOS_MATTERIX_EVIDENCE_DIR", str(tmp_path / "evidence")))
    await asyncio.to_thread(evidence.mkdir, parents=True, exist_ok=True)
    summary = {
        "simulation_mode": "physics",
        "hardware_calibrated": False,
        "ray_namespace": token,
        "archive_bucket": config.file_db.bucket,
        "runs": [],
    }
    await orchestrator.initialize()
    di = get_di_container()
    manager = di.get(DeviceManager)
    task_manager = di.get(TaskManager)
    resources = di.get(ResourceManager)
    file_db = di.get(FileDbInterface)
    actor = None

    async def drive(name, *, cancel=False, deadline_s=1200):
        deadline = time.monotonic() + deadline_s
        cancelled = False
        seen = set()
        while time.monotonic() < deadline:
            await orchestrator.spin_once()
            async with orchestrator.db_interface.get_async_session() as db:
                run = await orchestrator.protocols.get_protocol_run(db, name)
                tasks = await task_manager.get_tasks(db, protocol_run_name=name)
            for row in tasks:
                stamp = (row.name, row.status.value)
                if stamp not in seen:
                    print(name, *stamp, flush=True)
                    seen.add(stamp)
            if (
                cancel
                and not cancelled
                and any(t.name == "open_door" and t.status == TaskStatus.RUNNING for t in tasks)
            ):
                report = await actor.report.remote()
                if report["session"].get("runtime_status") == "running":
                    await orchestrator.protocols.cancel_protocol_run(name)
                    cancelled = True
            if run.status in {ProtocolRunStatus.COMPLETED, ProtocolRunStatus.FAILED, ProtocolRunStatus.CANCELLED}:
                record = {
                    "name": name,
                    "status": run.status.value,
                    "error": run.error_message,
                    "tasks": [t.model_dump(mode="json") for t in tasks],
                }
                summary["runs"].append(record)
                (evidence / "acceptance.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
                return run, tasks
            await asyncio.sleep(0.05)
        raise TimeoutError(f"EOS acceptance run {name} did not finish")

    async def submit(name, parameters=None):
        async with orchestrator.db_interface.get_async_session() as db:
            await orchestrator.protocols.submit_protocol_run(
                db,
                ProtocolRunSubmission(
                    name=name, type="matterix_full_station", owner="matterix-acceptance", parameters=parameters or {}
                ),
            )

    try:
        async with orchestrator.db_interface.get_async_session() as db:
            await orchestrator.loading.load_labs(db, {"matterix_pipetting_lab"})
            await orchestrator.loading.load_protocols(db, {"matterix_full_station"})
        actor = manager.get_device_actor("matterix_pipetting_lab", "matterix")
        initial_states = []
        for index in (1, 2):
            name = f"{token}_complete_{index}"
            await submit(name)
            run, tasks = await drive(name)
            assert run.status == ProtocolRunStatus.COMPLETED, run.error_message
            assert len(tasks) == 9
            assert all(t.status == TaskStatus.COMPLETED for t in tasks)
            by_name = {t.name: t for t in tasks}
            initial = by_name["begin"].output_parameters["state"]["latest_sample"]["semantics"]
            initial_states.append(initial)
            for row in tasks:
                assert "matterix-context.json" in row.output_file_names
                if row.type == "Matterix Capability":
                    assert row.output_parameters["passed"] is True
                    assert "matterix-evidence.zip" in row.output_file_names
                    data = await file_db.get_file(f"{name}/{row.name}/matterix-evidence.zip")
                    with zipfile.ZipFile(io.BytesIO(data)) as archive:
                        assert {"result.json", "events.jsonl", "samples.jsonl", "provenance.json"} <= set(
                            archive.namelist()
                        )
                        result = json.loads(archive.read("result.json"))
                        assert result["acceptance"]["passed"] is True
                    (evidence / f"{name}-{row.name}.zip").write_bytes(data)
            async with orchestrator.db_interface.get_async_session() as db:
                actual = by_name["syringe_transfer"].output_parameters["final_semantics"]["liquid_volumes_ml"][
                    "TransferBottle10ml_617"
                ]
                assert (await resources.get_resource(db, "MX_SOURCE")).meta["volume_ml"] == actual
                assert (await resources.get_resource(db, "MX_TIP_1")).meta["used"]
                assert (await resources.get_resource(db, "MX_TIP_2")).meta["used"]
        assert initial_states[0] == initial_states[1], "Independent runs must begin from identical scene state"

        name = token + "_cancel"
        await submit(name)
        run, tasks = await drive(name, cancel=True)
        assert run.status == ProtocolRunStatus.CANCELLED
        operation = next(t for t in tasks if t.name == "open_door")
        assert operation.status == TaskStatus.CANCELLED
        assert "matterix-evidence.zip" in operation.output_file_names
        assert not any(t.name == "prepare_vial" for t in tasks)
        report = await actor.report.remote()
        assert not report["session"].get("active_task_id")
        assert report["blocked"]

        await actor.recover.remote()
        async with orchestrator.db_interface.get_async_session() as db:
            await manager.set_device_status(db, "matterix_pipetting_lab", "matterix", DeviceStatus.ACTIVE)
        name = token + "_timeout"
        await submit(name, {"open_door": {"parameters": {"timeout_s": 1}}})
        run, tasks = await drive(name)
        assert run.status == ProtocolRunStatus.FAILED
        operation = next(t for t in tasks if t.name == "open_door")
        assert operation.status == TaskStatus.FAILED
        assert "matterix-evidence.zip" in operation.output_file_names
        assert not any(t.name == "prepare_vial" for t in tasks)
        async with orchestrator.db_interface.get_async_session() as db:
            device = await manager.get_device(db, "matterix_pipetting_lab", "matterix")
            assert device.status == DeviceStatus.INACTIVE
        summary["passed"] = True
    finally:
        (evidence / "acceptance.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        await orchestrator.terminate()
        await orchestrator.db_interface._async_engine.dispose()
        with (
            sqlite3.connect(tmp_path / "acceptance.db") as source,
            sqlite3.connect(evidence / f"{token}.sqlite3") as target,
        ):
            source.backup(target)
