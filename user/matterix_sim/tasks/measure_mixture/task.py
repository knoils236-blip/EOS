from pydantic import BaseModel

from eos import Param, task
from matterix_sim.mixture import Sample, synthetic_measurement


class MeasurementOutputs(BaseModel):
    observation: dict = Param(desc="Synthetic observation; never a hardware measurement.")
    raw_data: bytes = Param(file_name="observation.json", desc="Seed, model, controls and raw replicates.")


@task("Measure Mixture")
async def measure_mixture(
    sample: dict = Param(desc="Preparation record."),
    seed: int = Param(20261102, desc="Reproducible synthetic sensor seed."),
) -> MeasurementOutputs:
    """Measure with a documented synthetic concentration model and independent noise draws."""
    observation = synthetic_measurement(Sample.model_validate(sample), seed)
    return MeasurementOutputs(
        observation=observation.model_dump(mode="json"), raw_data=observation.model_dump_json().encode()
    )
