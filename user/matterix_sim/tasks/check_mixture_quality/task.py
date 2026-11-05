from pydantic import BaseModel

from eos import Param, task
from matterix_sim.mixture import Observation, check_quality


class QualityOutputs(BaseModel):
    quality: dict = Param(desc="Eligibility, control checks and structured invalidity reasons.")
    quality_record: bytes = Param(file_name="quality.json")


@task("Check Mixture Quality")
async def check_mixture_quality(observation: dict = Param(desc="Synthetic measurement record.")) -> QualityOutputs:
    """Keep bad observations available for diagnosis while excluding them from scoring."""
    quality = check_quality(Observation.model_validate(observation))
    return QualityOutputs(quality=quality.model_dump(mode="json"), quality_record=quality.model_dump_json().encode())
