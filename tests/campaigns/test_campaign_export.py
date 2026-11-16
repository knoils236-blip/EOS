"""F11: a campaign export is complete, says what it could not verify, and carries no secrets."""

import pytest

import eos.database.models  # registers every table, so foreign keys resolve
from eos.campaigns.entities.campaign import CampaignModel, CampaignSampleModel, CampaignStatus
from eos.campaigns.export import export_campaign
from eos.configuration.eos_config import DatabaseType, DbConfig, SqliteDbConfig
from eos.database.exceptions import EosFileDbError
from eos.database.sqlite_db_interface import SqliteDbInterface
from eos.protocols.entities.protocol_run import ProtocolRunModel, ProtocolRunStatus
from eos.protocols.manifest import REDACTED, RunManifest, save_manifest
from eos.tasks.attempts import end_attempt, start_attempt
from eos.tasks.entities.task import TaskModel, TaskStatus

RUNS = 60  # more than the decision view's 50-run window


class FileStore:
    def __init__(self, files):
        self.files = files

    async def stream_file(self, key):
        if key not in self.files:
            raise EosFileDbError(f"Error streaming file at path '{key}': NoSuchKey")
        yield self.files[key]


@pytest.fixture
async def db():
    db_interface = SqliteDbInterface(DbConfig(type=DatabaseType.SQLITE, sqlite=SqliteDbConfig(in_memory=True)))
    await db_interface.initialize_database()
    async with db_interface.get_async_session() as session:
        yield session


async def test_export_holds_every_round_and_names_missing_evidence(db):
    db.add(
        CampaignModel(
            name="c",
            protocol="mixture_mock",
            owner="lab",
            optimize=True,
            max_protocol_runs=RUNS,
            status=CampaignStatus.COMPLETED,
            meta={"note": "endpoint https://robot:s3cret@lab.example/api"},
        )
    )
    await save_manifest(db, RunManifest({"labs": {"lab": {"password": REDACTED}}}, "m" * 64))
    for i in range(RUNS):
        name = f"c_run_{i}"
        db.add(
            ProtocolRunModel(
                name=name,
                type="mixture_mock",
                owner="lab",
                campaign="c",
                parameters={},
                status=ProtocolRunStatus.COMPLETED,
                manifest_hash="m" * 64 if i else None,
            )
        )
        await db.flush()
        db.add(
            TaskModel(
                name="measure",
                protocol_run_name=name,
                type="Measure",
                output_parameters={"observation": {"value_mm": 0.4}},
                output_file_names=["observation.json"],
                status=TaskStatus.COMPLETED,
            )
        )
        db.add(CampaignSampleModel(campaign_name="c", protocol_run_name=name, inputs={"x": i}, outputs={"y": 1}))
    first = await start_attempt(db, "c_run_0", "measure", "safe", {})
    await end_attempt(db, first.id, "failed", error="instrument offline")
    await db.flush()

    store = FileStore({f"c_run_{i}/measure/observation.json": b"{}" for i in range(1, RUNS)})  # run 0's file is gone
    exported = await export_campaign(db, store, "c")

    assert len(exported["decision"]["rounds"]) == RUNS
    assert len(exported["protocol_runs"]) == RUNS
    assert len(exported["samples"]) == RUNS
    assert exported["integrity"] == {
        "complete": False,
        "missing_files": ["c_run_0/measure/observation.json"],
        "runs_without_manifest": ["c_run_0"],
    }
    present = next(f for f in exported["files"] if f["key"] == "c_run_1/measure/observation.json")
    assert (present["available"], present["size"], len(present["sha256"])) == (True, 2, 64)
    assert [(a["protocol_run_name"], a["outcome"]) for a in exported["attempts"]] == [("c_run_0", "failed")]
    assert list(exported["manifests"]) == ["m" * 64]
    assert "s3cret" not in str(exported)


async def test_unknown_campaign_has_no_export(db):
    assert await export_campaign(db, FileStore({}), "missing") is None
