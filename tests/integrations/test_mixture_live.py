"""Opt-in EOS scheduling/optimization and representative real Isaac execution."""

import asyncio
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time
import uuid

from dotenv import load_dotenv
import pytest
import ray
import yaml

from eos.campaigns.entities.campaign import CampaignSubmission, CampaignStatus
from eos.configuration.eos_config import EosConfig
from eos.database.file_db_interface import FileDbInterface
from eos.orchestration.orchestrator import Orchestrator
from eos.protocols.entities.protocol_run import ProtocolRunSubmission, ProtocolRunStatus
from eos.tasks.task_manager import TaskManager
from eos.utils.di.di_container import get_di_container

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "user"))
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(os.environ.get("EOS_TEST_MIXTURE_LIVE") != "1", reason="EOS_TEST_MIXTURE_LIVE not enabled"),
]


@pytest.mark.parametrize("backend", ["mock", "matterix"])
async def test_closed_loop_and_execution_contract(tmp_path, backend):
    if backend == "matterix" and not os.environ.get("EOS_TEST_MATTERIX_PORT"):
        pytest.skip("EOS_TEST_MATTERIX_PORT not set")
    load_dotenv(ROOT / ".env")
    user_dir = tmp_path / "user"
    shutil.copytree(ROOT / "user/matterix_sim", user_dir / "matterix_sim", ignore=shutil.ignore_patterns("__pycache__"))
    lab_name, protocol = "mixture_" + backend + "_lab", "mixture_" + backend
    lab_path = user_dir / "matterix_sim/labs" / lab_name / "lab.yml"
    lab = yaml.safe_load(await asyncio.to_thread(lab_path.read_text))
    init = lab["devices"]["workstation"]["init_parameters"]
    init["journal_dir"] = str(tmp_path / "journal")
    if backend == "matterix":
        init["port"] = int(os.environ["EOS_TEST_MATTERIX_PORT"])
    lab_path.write_text(yaml.safe_dump(lab, sort_keys=False))
    token = "mixture_" + uuid.uuid4().hex[:10]
    ray.init(address="auto", namespace=token, ignore_reinit_error=True)
    config = EosConfig(
        user_dir=user_dir,
        packages={"matterix_sim"},
        db={"type": "sqlite", "sqlite": {"db_dir": tmp_path, "db_name": "acceptance"}},
    )
    orchestrator = Orchestrator(config)
    evidence = Path(os.environ.get("EOS_MIXTURE_EVIDENCE_DIR", str(tmp_path / "evidence")))
    await asyncio.to_thread(evidence.mkdir, parents=True, exist_ok=True)
    summary = {"namespace": token, "backend": backend, "source": "synthetic_model", "runs": []}
    await orchestrator.initialize()
    tasks = get_di_container().get(TaskManager)
    files = get_di_container().get(FileDbInterface)

    async def drive(name, campaign=False):
        deadline = time.monotonic() + 1800
        previous = None
        while time.monotonic() < deadline:
            await orchestrator.spin_once()
            async with orchestrator.db_interface.get_async_session() as db:
                run = (
                    await orchestrator.campaigns.get_campaign(db, name)
                    if campaign
                    else await orchestrator.protocols.get_protocol_run(db, name)
                )
            if previous != run.status:
                print(backend, name, run.status.value, flush=True)
                previous = run.status
            if run.status.value in {"COMPLETED", "FAILED", "CANCELLED"}:
                return run
            await asyncio.sleep(0.05)
        raise TimeoutError(name)

    async def record(name):
        async with orchestrator.db_interface.get_async_session() as db:
            rows = await tasks.get_tasks(db, protocol_run_name=name)
        assert len(rows) == 4
        assert all(row.status.value == "COMPLETED" for row in rows)
        by_name = {row.name: row for row in rows}
        sample = by_name["prepare"].output_parameters["sample"]
        assert sample["backend"] == ("simulation" if backend == "matterix" else "mock")
        assert abs(sample["stock_ul"] + sample["diluent_ul"] - 200) < 0.001
        assert by_name["quality"].output_parameters["quality"]["eligible"]
        assert by_name["score"].output_parameters["source"] == "synthetic_model"
        for row in rows:
            for filename in row.output_file_names:
                data = await files.get_file(f"{name}/{row.name}/{filename}")
                path = evidence / name / row.name / filename
                await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
                await asyncio.to_thread(path.write_bytes, data)
        summary["runs"].append({"name": name, "tasks": [row.model_dump(mode="json") for row in rows]})
        print(backend, name, "dose", sample["stock_ul"], "loss", by_name["score"].output_parameters["loss"], flush=True)

    try:
        async with orchestrator.db_interface.get_async_session() as db:
            await orchestrator.loading.load_labs(db, {lab_name})
            await orchestrator.loading.load_protocols(db, {protocol})
        if backend == "mock":
            name = token + "_campaign"
            async with orchestrator.db_interface.get_async_session() as db:
                await orchestrator.campaigns.submit_campaign(
                    db,
                    CampaignSubmission(
                        name=name,
                        protocol=protocol,
                        owner="mixture-acceptance",
                        optimize=True,
                        max_protocol_runs=8,
                        max_concurrent_protocol_runs=1,
                        max_failed_protocol_runs=0,
                    ),
                )
            campaign = await drive(name, campaign=True)
            assert campaign.status == CampaignStatus.COMPLETED, campaign.error_message
            for index in range(1, 9):
                await record(f"{name}_run_{index}")
            losses = [
                next(t for t in r["tasks"] if t["name"] == "score")["output_parameters"]["loss"]
                for r in summary["runs"]
            ]
            assert min(losses) < 0.01
            summary["campaign"] = campaign.model_dump(mode="json")
        else:
            for volume in (20, 74, 180):
                name = token + "_dose_" + str(volume)
                async with orchestrator.db_interface.get_async_session() as db:
                    await orchestrator.protocols.submit_protocol_run(
                        db,
                        ProtocolRunSubmission(
                            name=name,
                            type=protocol,
                            owner="mixture-acceptance",
                            parameters={"prepare": {"stock_volume_ul": volume}},
                        ),
                    )
                run = await drive(name)
                assert run.status == ProtocolRunStatus.COMPLETED, run.error_message
                await record(name)
        summary["passed"] = True
    finally:
        (evidence / (backend + "-acceptance.json")).write_text(json.dumps(summary, indent=2))
        await orchestrator.terminate()
        await orchestrator.db_interface._async_engine.dispose()
        with (
            sqlite3.connect(tmp_path / "acceptance.db") as source,
            sqlite3.connect(evidence / (token + ".sqlite3")) as target,
        ):
            source.backup(target)


async def test_active_matterix_preparation_cancel(tmp_path):
    port = os.environ.get("EOS_TEST_MATTERIX_PORT")
    if not port:
        pytest.skip("EOS_TEST_MATTERIX_PORT not set")
    from matterix_sim.devices.mixture_workstation.device import MixtureWorkstation  # noqa: PLC0415

    device = MixtureWorkstation("cancel", "mixture_validation")
    await device.initialize({"backend": "matterix", "port": int(port), "journal_dir": tmp_path})
    preparing = asyncio.create_task(device.prepare("mixture_cancel_validation", "prepare", 74))
    try:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            report = await device.report()
            if report["execution"]["session"].get("runtime_status") == "running":
                break
            if preparing.done():
                await preparing
                pytest.fail("Preparation ended before cancellation could be exercised")
            await asyncio.sleep(0.1)
        else:
            pytest.fail("No active remote command was observed")
        report = await device.finalize_task("mixture_cancel_validation", "prepare", stop=True)
        with pytest.raises(RuntimeError):
            await preparing
        state = await device.report()
        assert state["blocked"]
        assert not state["execution"]["session"].get("active_task_id")
        assert not report["available"]
        assert "open_door-matterix-evidence.zip" in report["files"]
        evidence = Path(os.environ.get("EOS_MIXTURE_EVIDENCE_DIR", str(tmp_path))) / "cancellation"
        await asyncio.to_thread(evidence.mkdir, parents=True, exist_ok=True)
        for name, data in report["files"].items():
            await asyncio.to_thread((evidence / name).write_bytes, data)
        await asyncio.to_thread(
            (evidence / "result.json").write_text,
            json.dumps({"passed": True, "state": state, "parameters": report["parameters"]}, indent=2),
        )
    finally:
        if not preparing.done():
            preparing.cancel()
        await device.cleanup()
