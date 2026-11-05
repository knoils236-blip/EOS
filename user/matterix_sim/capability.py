"""Shared execution of Matterix capabilities for the generic and the typed capability tasks."""

from typing import Any, Literal

from pydantic import BaseModel

from eos import Context, Param
from integrations.matterix.bridge.capabilities import catalog


class CapabilityOutputs(BaseModel):
    request_id: str = Param(desc="Matterix request ID of this run.")
    passed: bool = Param(desc="Whether all acceptance checks of the capability passed.")
    sim_time_s: float = Param(unit="s", desc="Simulated time the capability took.")
    elapsed_wall_s: float = Param(unit="s", desc="Wall-clock time the capability took.")
    final_semantics: dict = Param(desc="Final simulated state, e.g. liquid volumes and device state.")
    acceptance: dict = Param(desc="Acceptance verdict and checks with actual and expected values.")
    run_dir: str = Param(desc="Bridge run directory with the full result and history.")


def _volume_operations() -> dict[str, dict]:
    """Operation-tier capabilities whose volume is a flat, optimizable task parameter, keyed by ID."""
    return {
        item["id"]: item["parameters"]
        for item in catalog()
        if item["tier"] == "operation" and "volume_ul" in item["parameters"]
    }


VOLUME_OPERATIONS = _volume_operations()
VolumeOperation = Literal[tuple(VOLUME_OPERATIONS)]
# One task type serves every volume operation, so they must share the parameter bounds
VOLUME_SPEC, TIMEOUT_SPEC = (
    next(iter(VOLUME_OPERATIONS.values()))[name] for name in ("volume_ul", "timeout_s")
)
for _capability_id, _parameters in VOLUME_OPERATIONS.items():
    for _name, _spec in (("volume_ul", VOLUME_SPEC), ("timeout_s", TIMEOUT_SPEC)):
        if (_parameters[_name]["min"], _parameters[_name]["max"]) != (_spec["min"], _spec["max"]):
            raise ValueError(f"'{_capability_id}' {_name} bounds differ from the other volume operations")


def run_capability(matterix: Any, context: Context, capability_id: str, parameters: dict) -> CapabilityOutputs:
    settings = context.meta.get("matterix") or {}
    if isinstance(settings, dict):
        overrides = {}
        for name in ("record_video", "hide_shell"):
            if name in settings:
                if not isinstance(settings[name], bool):
                    raise ValueError(f"{name} must be a boolean")
                overrides[name] = settings[name]
        parameters = {**parameters, **overrides}
    state: dict[str, Any] = matterix.run_capability(
        capability_id, parameters, context.protocol_run_name, context.task_name
    )
    if state["status"] != "succeeded":
        raise RuntimeError(f"Matterix capability '{capability_id}' ended as {state['status']}: {state.get('error')}")

    result = state["result"]
    acceptance = result.get("acceptance") or {}
    if acceptance.get("passed") is not True:
        raise RuntimeError(f"Matterix capability '{capability_id}' did not pass acceptance")
    return CapabilityOutputs(
        request_id=state["request_id"],
        passed=bool(acceptance.get("passed")),
        sim_time_s=float(result.get("elapsed_sim_s") or 0.0),
        elapsed_wall_s=float(state.get("elapsed_wall_s") or 0.0),
        final_semantics=(result.get("final") or {}).get("semantics") or {},
        acceptance=acceptance,
        run_dir=state["run_dir"],
    )
