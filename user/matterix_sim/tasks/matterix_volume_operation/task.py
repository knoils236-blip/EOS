from eos import Context, Param, task

from matterix_sim.capability import TIMEOUT_SPEC, VOLUME_SPEC, CapabilityOutputs, VolumeOperation, run_capability
from matterix_sim.devices.matterix_workstation.device import MatterixWorkstation


@task("Matterix Volume Operation")
async def matterix_volume_operation(
    matterix: MatterixWorkstation,
    context: Context,
    capability_id: VolumeOperation = Param(desc="Registered Matterix operation that transfers a volume."),
    volume_ul: float = Param(
        min=VOLUME_SPEC["min"], max=VOLUME_SPEC["max"], unit="uL", desc="Volume to transfer; optimizable."
    ),
    timeout_s: float = Param(
        TIMEOUT_SPEC["default"], min=TIMEOUT_SPEC["min"], max=TIMEOUT_SPEC["max"], unit="s",
        desc="Simulation timeout of the operation.",
    ),
) -> CapabilityOutputs:
    """Run a Matterix volume operation with flat parameters, so protocols and optimizers can reference them."""
    return run_capability(matterix, context, capability_id, {"volume_ul": volume_ul, "timeout_s": timeout_s})
