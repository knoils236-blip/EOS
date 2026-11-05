from eos import Context, Param, task

from matterix_sim.capability import CapabilityOutputs, run_capability
from matterix_sim.devices.matterix_workstation.device import MatterixWorkstation


@task("Matterix Capability")
async def matterix_capability(
    matterix: MatterixWorkstation,
    context: Context,
    capability_id: str = Param(desc="Registered Matterix capability, e.g. pickup_beaker."),
    parameters: dict = Param({}, desc="Capability parameters, e.g. {'lift_height_m': 0.1}."),
) -> CapabilityOutputs:
    """Run a registered capability on the Matterix (Isaac Sim) workstation."""
    return run_capability(matterix, context, capability_id, parameters)
