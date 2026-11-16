"""Complete, verifiable export of a campaign's record: every round, not the decision view's recent window.

It joins what the system keeps apart: the plan each run executed (manifest), how each task was attempted, which
observations passed the quality gate, what the optimizer suggested and from which configuration, approvals, and the
raw files. Files are checked against object storage at export time, so missing evidence is reported, never implied.
"""

import hashlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select

from eos.approvals.entities import ApprovalRequest, ApprovalRequestModel
from eos.campaigns.decision import load_decision
from eos.campaigns.entities.campaign import CampaignSampleModel
from eos.database.abstract_sql_db_interface import AsyncDbSession
from eos.database.exceptions import EosFileDbError
from eos.database.file_db_interface import FileDbInterface
from eos.protocols.entities.protocol_run import ProtocolRunModel
from eos.protocols.manifest import RunManifestModel, redact
from eos.tasks.attempts import TaskAttempt, TaskAttemptModel
from eos.tasks.entities.task import TaskModel
from eos.tasks.task_definition import build_task_output_file_path

EXPORT_SCHEMA = 1


async def _file_record(file_db: FileDbInterface, key: str) -> dict[str, Any]:
    digest, size = hashlib.sha256(), 0
    try:
        async for chunk in file_db.stream_file(key):
            digest.update(chunk)
            size += len(chunk)
    except EosFileDbError as e:
        return {"key": key, "available": False, "error": str(e)}
    return {"key": key, "available": True, "size": size, "sha256": digest.hexdigest()}


async def export_campaign(db: AsyncDbSession, file_db: FileDbInterface, campaign_name: str) -> dict[str, Any] | None:
    decision = await load_decision(db, campaign_name, window=None)
    if decision is None:
        return None
    exported_at = datetime.now(UTC).isoformat()

    runs = list(
        await db.scalars(
            select(ProtocolRunModel).where(ProtocolRunModel.campaign == campaign_name).order_by(ProtocolRunModel.id)
        )
    )
    names = [run.name for run in runs]
    hashes = {run.manifest_hash for run in runs if run.manifest_hash}
    manifests = {
        m.hash: m.content for m in await db.scalars(select(RunManifestModel).where(RunManifestModel.hash.in_(hashes)))
    }
    tasks = list(
        await db.scalars(
            select(TaskModel)
            .where(TaskModel.protocol_run_name.in_(names))
            .order_by(TaskModel.protocol_run_name, TaskModel.id)
        )
    )
    attempts = [
        TaskAttempt.model_validate(row).model_dump(mode="json")
        for row in await db.scalars(
            select(TaskAttemptModel)
            .where(TaskAttemptModel.protocol_run_name.in_(names))
            .order_by(TaskAttemptModel.protocol_run_name, TaskAttemptModel.task_name, TaskAttemptModel.attempt_number)
        )
    ]
    samples = [
        {"protocol_run_name": s.protocol_run_name, "inputs": s.inputs, "outputs": s.outputs, "meta": s.meta}
        for s in await db.scalars(
            select(CampaignSampleModel)
            .where(CampaignSampleModel.campaign_name == campaign_name)
            .order_by(CampaignSampleModel.created_at, CampaignSampleModel.protocol_run_name)
        )
    ]
    approvals = [
        ApprovalRequest.model_validate(row).model_dump(mode="json")
        for row in await db.scalars(
            select(ApprovalRequestModel).where(
                or_(
                    (ApprovalRequestModel.kind == "campaign") & (ApprovalRequestModel.name == campaign_name),
                    (ApprovalRequestModel.kind == "protocol_run") & ApprovalRequestModel.name.in_(names),
                )
            )
        )
    ]

    # Every file any task output or attempt refers to, including earlier attempts kept under their own keys
    keys = [
        build_task_output_file_path(task.protocol_run_name, task.name, name)
        for task in tasks
        for name in task.output_file_names or []
    ]
    keys += [key for attempt in attempts for key in attempt["evidence"].get("files", [])]
    files = [await _file_record(file_db, key) for key in dict.fromkeys(keys)]
    missing = [f["key"] for f in files if not f["available"]]

    return redact(
        {
            "schema": EXPORT_SCHEMA,
            "campaign": campaign_name,
            # SQL rows and object storage are read separately, not in one transaction: this is what both held
            # between the export's start and the file checks, which ran last
            "boundary": {"exported_at": exported_at, "protocol_runs": len(names), "files_checked": len(files)},
            "integrity": {
                "complete": not missing,
                "missing_files": missing,
                "runs_without_manifest": [run.name for run in runs if not run.manifest_hash],
            },
            "decision": decision,
            "protocol_runs": [
                {
                    "name": run.name,
                    "status": run.status.value,
                    "parameters": run.parameters,
                    "meta": run.meta,
                    "manifest_hash": run.manifest_hash,
                    "error_message": run.error_message,
                }
                for run in runs
            ],
            "manifests": manifests,
            "tasks": [
                {
                    "protocol_run_name": task.protocol_run_name,
                    "name": task.name,
                    "type": task.type,
                    "status": task.status.value,
                    "input_parameters": task.input_parameters,
                    "output_parameters": task.output_parameters,
                    "error_message": task.error_message,
                }
                for task in tasks
            ],
            "attempts": attempts,
            "samples": samples,
            "approvals": approvals,
            "files": files,
        }
    )
