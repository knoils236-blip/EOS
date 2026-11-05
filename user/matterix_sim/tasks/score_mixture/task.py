from pydantic import BaseModel

from eos import Param, task
from matterix_sim.mixture import TARGET_MM, Observation, QualityReport, check_quality


class MixtureScore(BaseModel):
    target_mm: float = Param(unit="mM", desc="Concentration target used by this objective.")
    loss: float = Param(unit="mM^2", desc="Squared distance of accepted synthetic response from the target.")
    measured_mm: float = Param(unit="mM")
    uncertainty_mm: float = Param(unit="mM")
    source: str = Param(desc="Always synthetic_model in this example.")
    observation_id: str = Param()


@task("Score Mixture")
async def score_mixture(
    observation: dict = Param(desc="Measurement record."),
    quality: dict = Param(desc="Quality report for this exact observation."),
) -> MixtureScore:
    """Only quality-approved data enter the optimizer; invalid data stop this campaign."""
    reading = Observation.model_validate(observation)
    verdict = QualityReport.model_validate(quality)
    if verdict != check_quality(reading):
        raise ValueError("Quality report does not match this observation")
    if not verdict.eligible:
        raise ValueError("measurement_invalid: " + ", ".join(verdict.reasons))
    return MixtureScore(
        target_mm=TARGET_MM,
        loss=(reading.value_mm - TARGET_MM) ** 2,
        measured_mm=reading.value_mm,
        uncertainty_mm=reading.uncertainty_mm,
        source=reading.source,
        observation_id=reading.observation_id,
    )
