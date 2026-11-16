"""Observation quality gate between protocol run results and the campaign optimizer.

A protocol's optimizer definition may declare an ``observation_policy``. With one, only observations that pass it are
reported to the optimizer; rejected ones stay recorded with their reasons, and failed runs are never reported as
scientifically infeasible. Online reporting and optimizer restoration both decide with ``assess``, so a replay feeds
exactly what the live campaign fed. Campaigns without a policy keep the earlier, unassessed behavior.
"""

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eos.campaigns.exceptions import EosCampaignExecutionError
from eos.configuration.entities.protocol_def import ProtocolDef

_MISSING = object()


class ObservationPolicy(BaseModel):
    """References are ``task.output``, optionally followed by keys into a dict output (``measure.observation.id``)."""

    model_config = ConfigDict(extra="forbid")

    version: str
    units: dict[str, str]  # every optimizer objective and the unit its task output must be declared in
    quality: str  # a verdict dict with `eligible` (bool) and optional `reasons` (list of str)
    source: str | None = None  # where the value came from, e.g. mock, simulation, instrument
    allowed_sources: list[str] = Field(default_factory=list)
    same_observation: list[str] = Field(default_factory=list)  # ids that must all name one observation

    @model_validator(mode="after")
    def _sources_declared(self) -> "ObservationPolicy":
        if (self.source is None) != (not self.allowed_sources):
            raise ValueError("Give both 'source' and 'allowed_sources', or neither")
        return self

    def task_names(self) -> list[str]:
        refs = [self.quality, *self.same_observation, *([self.source] if self.source else [])]
        return sorted({ref.split(".", 1)[0] for ref in refs})


class Assessment(BaseModel):
    policy_version: str
    accepted: bool
    reasons: list[str]
    source: str | None = None


def check_objective_units(policy: ObservationPolicy, objectives: list[str], protocol: ProtocolDef, task_specs) -> None:
    """Refuse a campaign whose objectives are not declared in the units its policy requires, before any run."""
    if set(policy.units) != set(objectives):
        raise EosCampaignExecutionError(
            f"Observation policy '{policy.version}' must declare units for exactly the objectives {sorted(objectives)}"
        )
    tasks = {task.name: task for task in protocol.tasks}
    for ref, unit in policy.units.items():
        task_name, parameter = ref.split(".", 1)
        declared = task_specs.get_spec_by_type(tasks[task_name].type).output_parameters[parameter].unit
        if declared != unit:
            raise EosCampaignExecutionError(
                f"Objective '{ref}' is declared in '{declared}' but observation policy '{policy.version}' requires "
                f"'{unit}'"
            )


def _resolve(ref: str, outputs_by_task: dict[str, dict[str, Any]]) -> Any:
    task_name, *path = ref.split(".")
    value: Any = outputs_by_task.get(task_name, _MISSING)
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return _MISSING
        value = value[key]
    return value


def assess(
    policy: ObservationPolicy, outputs_by_task: dict[str, dict[str, Any]], objectives: dict[str, Any]
) -> Assessment:
    """Judge one completed protocol run: its task outputs by task name and its objective values."""
    reasons = []
    verdict = _resolve(policy.quality, outputs_by_task)
    if not isinstance(verdict, dict) or not isinstance(verdict.get("eligible"), bool):
        reasons.append("quality_unknown")
    elif not verdict["eligible"]:
        reasons += verdict.get("reasons") or ["quality_rejected"]

    ids = [_resolve(ref, outputs_by_task) for ref in policy.same_observation]
    if any(value is _MISSING for value in ids) or len(set(map(str, ids))) > 1:
        reasons.append("quality_for_other_observation")

    source = None
    if policy.source:
        value = _resolve(policy.source, outputs_by_task)
        source = None if value is _MISSING else str(value)
        if source not in policy.allowed_sources:
            reasons.append(f"source_not_allowed:{source}")

    for key in policy.units:
        value = objectives.get(key)
        if not isinstance(value, int | float) or isinstance(value, bool) or not math.isfinite(value):
            reasons.append(f"missing_objective:{key}")

    return Assessment(policy_version=policy.version, accepted=not reasons, reasons=reasons, source=source)
