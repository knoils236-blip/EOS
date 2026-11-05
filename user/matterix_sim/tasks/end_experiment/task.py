from eos import Context, Param, task
from matterix_sim.devices.matterix_workstation.device import MatterixWorkstation
from matterix_sim.tasks.begin_experiment.task import ExperimentState


@task("Matterix End Experiment")
async def end_experiment(
    matterix: MatterixWorkstation,
    context: Context,
    expected_phase: int = Param(
        7,
        min=0,
        max=7,
        desc=(
            "Accepted operation phase to verify. Use 0 for atomic-operation lifecycle checks only; "
            "this does not claim completion or physical acceptance of the seven-phase experiment."
        ),
    ),
) -> ExperimentState:
    """Verify the protocol's completion condition before releasing its workstation.

    ``expected_phase=0`` verifies this protocol's executed atomic operations and idle ownership,
    plus station cap closure and used-consumable disposal. It does not claim completion or physical
    end-to-end acceptance of the seven-phase experiment.
    """
    state = matterix.end_experiment(context.protocol_run_name, context.task_name, expected_phase)
    return ExperimentState(session_id=state["session_id"], generation=state["generation"], state=state["runtime"])
