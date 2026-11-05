"""Versioned sample and synthetic observation contracts for the mixture example."""

import hashlib
import math
import random
import statistics
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TOTAL_UL = 200.0
MIN_UL, MAX_UL = 20.0, 180.0
TARGET_MM = 0.37
MODEL_VERSION = "synthetic_concentration_v1"
BALANCE_TOLERANCE_UL = 0.001
CONTROL_TOLERANCE_MM = 0.02
MAX_RESPONSE_MM = 1.1
MAX_REPLICATE_STD_MM = 0.015


class Sample(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    schema_version: Literal[1] = 1
    sample_id: str
    protocol_run_name: str
    backend: Literal["mock", "simulation"]
    container_id: Literal["DesignC_BottleBody50ml_231"] = "DesignC_BottleBody50ml_231"
    stock_source_id: Literal["TransferBottle10ml_617"] = "TransferBottle10ml_617"
    diluent_source_id: Literal["DesignD_DesignC_BottleBody500ml_238"] = "DesignD_DesignC_BottleBody500ml_238"
    requested_stock_ul: float = Field(ge=MIN_UL, le=MAX_UL)
    stock_ul: float = Field(gt=0)
    diluent_ul: float = Field(gt=0)
    stock_concentration_mm: Literal[1.0] = 1.0
    fixture_id: Literal["stock_1mM_diluent_0mM_v1"] = "stock_1mM_diluent_0mM_v1"
    composition_source: Literal["mock_material_balance", "isaac_semantic_ledger"]
    hardware_calibrated: Literal[False] = False
    operations: list[dict]

    @model_validator(mode="after")
    def valid_balance(self) -> "Sample":
        if abs(self.stock_ul + self.diluent_ul - TOTAL_UL) > BALANCE_TOLERANCE_UL:
            raise ValueError("Mixture must contain 200 uL within semantic tolerance")
        expected = "mock_material_balance" if self.backend == "mock" else "isaac_semantic_ledger"
        if self.composition_source != expected:
            raise ValueError("Composition source does not match execution backend")
        if abs(self.stock_ul - self.requested_stock_ul) > BALANCE_TOLERANCE_UL:
            raise ValueError("Observed stock dose differs from the requested semantic dose")
        return self


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    schema_version: Literal[1] = 1
    observation_id: str
    sample_id: str
    source: Literal["synthetic_model"] = "synthetic_model"
    backend: Literal["mock", "simulation"]
    model_version: Literal[MODEL_VERSION] = MODEL_VERSION
    processing_version: Literal["mixture_analysis_v1"] = "mixture_analysis_v1"
    unit: Literal["mM"] = "mM"
    seed: int
    replicates: list[float] = Field(min_length=3, max_length=3)
    blank_mm: float
    reference_mm: float
    value_mm: float
    uncertainty_mm: float = Field(ge=0)
    raw_data_file: Literal["observation.json"] = "observation.json"
    hardware_calibrated: Literal[False] = False

    @model_validator(mode="after")
    def consistent_analysis(self) -> "Observation":
        if not math.isclose(self.value_mm, statistics.mean(self.replicates), abs_tol=1e-12):
            raise ValueError("Observation mean does not match raw replicates")
        return self


class QualityReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation_id: str
    sample_id: str
    eligible: bool
    failure_kind: Literal["none", "measurement_invalid"]
    reasons: list[str]
    policy_version: Literal["mixture_qc_v1"] = "mixture_qc_v1"


def sample_identity(protocol_run_name: str) -> str:
    return "sample-" + hashlib.sha256(protocol_run_name.encode()).hexdigest()[:24]


def synthetic_measurement(sample: Sample, seed: int) -> Observation:
    # Synthetic noise tests the feedback pipeline; it is not a calibrated instrument model.
    digest = hashlib.sha256(f"{MODEL_VERSION}:{seed}:{sample.sample_id}".encode()).hexdigest()
    rng = random.Random(int(digest, 16))  # noqa: S311 -- reproducible synthetic sensor noise
    concentration = sample.stock_concentration_mm * sample.stock_ul / (sample.stock_ul + sample.diluent_ul)
    sigma = 0.003
    readings = [concentration + rng.gauss(0, sigma) for _ in range(3)]
    return Observation(
        observation_id="obs-" + digest[:24],
        sample_id=sample.sample_id,
        backend=sample.backend,
        seed=seed,
        replicates=readings,
        blank_mm=rng.gauss(0, sigma),
        reference_mm=1 + rng.gauss(0, sigma),
        value_mm=statistics.mean(readings),
        uncertainty_mm=sigma / math.sqrt(3),
    )


def check_quality(observation: Observation) -> QualityReport:
    reasons = []
    if abs(observation.blank_mm) > CONTROL_TOLERANCE_MM:
        reasons.append("blank_out_of_range")
    if abs(observation.reference_mm - 1) > CONTROL_TOLERANCE_MM:
        reasons.append("reference_out_of_range")
    if not all(0 <= value <= MAX_RESPONSE_MM for value in observation.replicates):
        reasons.append("response_out_of_range")
    if statistics.stdev(observation.replicates) > MAX_REPLICATE_STD_MM:
        reasons.append("replicates_disagree")
    return QualityReport(
        observation_id=observation.observation_id,
        sample_id=observation.sample_id,
        eligible=not reasons,
        failure_kind="measurement_invalid" if reasons else "none",
        reasons=reasons,
    )
