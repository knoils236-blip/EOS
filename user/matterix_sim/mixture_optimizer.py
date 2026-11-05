import pandas as pd

from bofire.data_models.acquisition_functions.acquisition_function import qLogNEI
from bofire.data_models.features.continuous import ContinuousInput, ContinuousOutput
from bofire.data_models.objectives.identity import MinimizeObjective

from eos.optimization.sequential_bayesian_optimizer import BayesianSequentialOptimizer
from eos.optimization.beacon_optimizer import BeaconOptimizer
from eos.optimization.openai_connection import OpenAIConnection
from matterix_sim.mixture import TARGET_MM, TOTAL_UL


class MixtureOptimizer(BayesianSequentialOptimizer):
    def report(self, inputs_df: pd.DataFrame, outputs_df: pd.DataFrame) -> None:
        # Missing results cannot establish chemical infeasibility, regardless of the failure budget.
        valid = outputs_df["score.loss"].notna()
        if (~valid).any():
            self._clear_reported_pending(inputs_df.loc[~valid])
        if valid.any():
            super().report(inputs_df.loc[valid], outputs_df.loc[valid])


class MixtureBeaconOptimizer(BeaconOptimizer):
    def _create_optimizer(self, inputs: list, outputs: list, constraints: list) -> MixtureOptimizer:
        return MixtureOptimizer(
            inputs=inputs,
            outputs=outputs,
            constraints=constraints,
            acquisition_function=self._acquisition_function,
            num_initial_samples=self._num_initial_samples,
            initial_sampling_method=self._initial_sampling_method,
            surrogate_specs=self._surrogate_specs,
        )


def optimizer_definition() -> tuple[dict, type[MixtureBeaconOptimizer]]:
    return {
        "inputs": [ContinuousInput(key="prepare.stock_volume_ul", bounds=(20, 180))],
        "outputs": [ContinuousOutput(key="score.loss", objective=MinimizeObjective(w=1))],
        "constraints": [],
        "acquisition_function": qLogNEI(),
        "num_initial_samples": 3,
        "p_bayesian": 1.0,
        "p_ai": 0.0,
        "ai_model": "openai:" + (OpenAIConnection().model or "your-model"),
        "ai_additional_parameters": ["measure.observation", "quality.quality"],
        "ai_additional_context": "All responses in this example are synthetic, not real chemical measurements. "
        f"The total mixture volume is {TOTAL_UL} uL; stock is 1 mM, diluent is 0 mM, and the target is {TARGET_MM} mM. "
        "Choose stock volume in the registered domain. "
        "Use measured observations and their quality verdicts. "
        "Never infer chemical infeasibility from missing results.",
    }, MixtureBeaconOptimizer
