from pydantic import BaseModel

from eos import Context, Param, task
from matterix_sim.devices.matterix_workstation.device import MatterixWorkstation


class ExperimentState(BaseModel):
    session_id: str = Param(desc="Persistent Matterix session.")
    generation: int = Param(desc="Scene reset generation.")
    state: dict = Param(desc="Verified initial state or final experiment state.")


@task("Matterix Begin Experiment")
async def begin_experiment(matterix: MatterixWorkstation, context: Context) -> ExperimentState:
    """Reset and verify the simulated fixture while holding the workstation."""
    if context.protocol_run_name is None:
        raise ValueError("An experiment must belong to an EOS protocol run")
    settings = context.meta.get("matterix") or {}
    state = matterix.begin_experiment(
        context.protocol_run_name,
        context.task_name,
        record_video=settings.get("record_video") if isinstance(settings, dict) else None,
        hide_shell=settings.get("hide_shell") if isinstance(settings, dict) else None,
    )
    return ExperimentState(session_id=state["session_id"], generation=state["generation"], state=state["runtime"])
