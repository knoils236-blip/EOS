import json

from pydantic import BaseModel

from eos import Context, Param, task
from matterix_sim.devices.mixture_workstation.device import MixtureWorkstation


class PreparationOutputs(BaseModel):
    sample: dict = Param(desc="Versioned sample, composition, provenance and operation history.")
    sample_record: bytes = Param(file_name="sample.json", desc="Immutable preparation record.")


@task("Prepare Mixture")
async def prepare_mixture(
    workstation: MixtureWorkstation,
    context: Context,
    stock_volume_ul: float = Param(min=20, max=180, unit="uL", desc="Stock in a fixed 200 uL mixture."),
) -> PreparationOutputs:
    """Prepare a mixture using the configured mock or Matterix backend."""
    settings = context.meta.get("matterix") or {}
    sample = workstation.prepare(
        context.protocol_run_name,
        context.task_name,
        stock_volume_ul,
        settings=settings if isinstance(settings, dict) else None,
    )
    return PreparationOutputs(sample=sample, sample_record=json.dumps(sample, allow_nan=False).encode())
