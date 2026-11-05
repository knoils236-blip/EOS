"""Read-only decision evidence assembled from persisted runs and task outputs."""

from datetime import UTC, datetime
import hashlib
import json
import math
from typing import Any

from sqlalchemy import func, select

from eos.campaigns.entities.campaign import CampaignModel, CampaignSampleModel
from eos.protocols.entities.protocol_run import ProtocolRunModel
from eos.tasks.entities.task import TaskModel

WINDOW = 50


def finite_values(values: dict) -> dict[str, float]:
    return {
        key: value
        for key, value in values.items()
        if isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)
    }


def best_by_source(rounds: list[dict], objectives: list[dict]) -> list[dict]:
    best = []
    for objective in objectives:
        if objective["direction"] == "unknown":
            continue
        grouped = {}
        for row in rounds:
            value = row["objectives"].get(objective["key"])
            if not row["eligible"] or value is None:
                continue
            source = (row["backend"], row["data_source"], row["model_version"])
            previous = grouped.get(source)
            if previous is None or (
                value < previous["value"] if objective["direction"] == "min" else value > previous["value"]
            ):
                grouped[source] = {
                    "objective": objective["key"],
                    "value": value,
                    "run_name": row["run_name"],
                    "backend": source[0],
                    "data_source": source[1],
                    "model_version": source[2],
                }
        best.extend(grouped.values())
    return best


def build_decision(campaign: Any, runs: list, tasks: list, samples: list, total: int) -> dict:
    config = ((campaign.meta or {}).get("optimizer") or {}).get("optimizer_config") or {}
    args = config.get("constructor_args") or {}
    objectives = []
    for output in args.get("outputs", []):
        kind = (output.get("objective") or {}).get("type")
        direction = {"MinimizeObjective": "min", "MaximizeObjective": "max"}.get(kind, "unknown")
        objectives.append({"key": output["key"], "direction": direction})
    by_run = {run.name: [] for run in runs}
    for task in tasks:
        by_run[task.protocol_run_name].append(task)
    by_sample = {sample.protocol_run_name: sample for sample in samples}
    input_names = [item["key"] for item in args.get("inputs", [])]
    rounds = []
    for run in runs:
        rows = sorted(by_run[run.name], key=lambda row: row.name)
        outputs = [row.output_parameters or {} for row in rows]
        sample = next((out["sample"] for out in outputs if isinstance(out.get("sample"), dict)), {})
        observation = next((out["observation"] for out in outputs if isinstance(out.get("observation"), dict)), {})
        quality = next((out["quality"] for out in outputs if isinstance(out.get("quality"), dict)), {})
        recorded = by_sample.get(run.name)
        inputs = dict(recorded.inputs) if recorded else {}
        for name in input_names:
            task_name, parameter = name.split(".", 1)
            matching = next((row for row in rows if row.name == task_name), None)
            value = (matching.input_parameters or {}).get(parameter) if matching else None
            if value is None:
                value = (run.parameters or {}).get(task_name, {}).get(parameter)
            if value is not None:
                inputs[name] = value
        values = dict(recorded.outputs) if recorded else {}
        for objective in objectives:
            task_name, parameter = objective["key"].split(".", 1)
            matching = next((row for row in rows if row.name == task_name), None)
            value = (matching.output_parameters or {}).get(parameter) if matching else None
            if value is not None:
                values[objective["key"]] = value
        status = run.status.value
        verdict = (
            "passed"
            if quality.get("eligible") is True
            else "rejected"
            if quality.get("eligible") is False
            else "unknown"
        )
        eligibility = quality.get("eligible") if quality else None
        target = next((out["target_mm"] for out in outputs if "target_mm" in out), None)
        decision = (run.meta or {}).get("decision") or {"method": "unknown", "reason": "此历史运行未保存逐轮选择依据。"}
        rounds.append(
            {
                "run_name": run.name,
                "status": status,
                "inputs": finite_values(inputs),
                "objectives": finite_values(values),
                "selection": decision,
                "sample_id": sample.get("sample_id"),
                "backend": sample.get("backend", "unknown"),
                "data_source": observation.get("source", "unknown"),
                "model_version": observation.get("model_version"),
                "measurement": {
                    key: observation[key] for key in ("value_mm", "uncertainty_mm", "unit") if key in observation
                }
                | ({"target_mm": target} if target is not None else {}),
                "quality": verdict,
                "quality_reasons": quality.get("reasons", []),
                "eligible": status == "COMPLETED" and eligibility is True,
                "failure_kind": quality.get("failure_kind") if verdict == "rejected" else None,
                "files": [
                    {"task_name": row.name, "name": name} for row in rows for name in (row.output_file_names or [])
                ],
            }
        )
    status = campaign.status.value
    stop_reason = {
        "COMPLETED": "已达到本实验计划的运行预算。"
        if campaign.max_protocol_runs > 0 and total >= campaign.max_protocol_runs
        else "实验计划已完成；未记录具体停止原因。",
        "CANCELLED": "实验计划已取消。",
        "FAILED": "实验执行或优化失败，请查看失败轮次。",
        "SUSPENDED": "实验计划已挂起。",
    }.get(status)
    result = {
        "campaign_name": campaign.name,
        "status": status,
        "protocol": campaign.protocol,
        "generated_at": datetime.now(UTC).isoformat(),
        "total_runs": total,
        "window": WINDOW,
        "max_runs": campaign.max_protocol_runs,
        "stop_reason": stop_reason,
        "objectives": objectives,
        "best": best_by_source(rounds, objectives),
        "rounds": rounds,
        "constraints": args.get("constraints", []),
    }
    content = {key: value for key, value in result.items() if key != "generated_at"}
    result["snapshot_id"] = hashlib.sha256(json.dumps(content, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return result


async def load_decision(db: Any, campaign_name: str) -> dict | None:
    campaign = await db.scalar(select(CampaignModel).where(CampaignModel.name == campaign_name))
    if campaign is None:
        return None
    total = await db.scalar(
        select(func.count()).select_from(ProtocolRunModel).where(ProtocolRunModel.campaign == campaign_name)
    )
    runs = list(
        (
            await db.scalars(
                select(ProtocolRunModel)
                .where(ProtocolRunModel.campaign == campaign_name)
                .order_by(ProtocolRunModel.id.desc())
                .limit(WINDOW)
            )
        ).all()
    )[::-1]
    names = [run.name for run in runs]
    tasks = (
        list((await db.scalars(select(TaskModel).where(TaskModel.protocol_run_name.in_(names)))).all()) if names else []
    )
    samples = (
        list(
            (
                await db.scalars(select(CampaignSampleModel).where(CampaignSampleModel.protocol_run_name.in_(names)))
            ).all()
        )
        if names
        else []
    )
    return build_decision(campaign, runs, tasks, samples, total)
