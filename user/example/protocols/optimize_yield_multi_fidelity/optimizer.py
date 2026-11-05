from bofire.data_models.acquisition_functions.acquisition_function import qLogNEI
from bofire.data_models.enum import SamplingMethodEnum
from bofire.data_models.features.continuous import ContinuousInput, ContinuousOutput
from bofire.data_models.features.task import CategoricalTaskInput
from bofire.data_models.objectives.identity import MaximizeObjective

from eos.optimization.abstract_sequential_optimizer import AbstractSequentialOptimizer
from eos.optimization.sequential_bayesian_optimizer import BayesianSequentialOptimizer


def eos_create_campaign_optimizer() -> tuple[dict, type[AbstractSequentialOptimizer]]:
    constructor_args = {
        "inputs": [
            ContinuousInput(key="measure.temperature", bounds=(20, 120)),
            # Fidelity 0 is the target (real experiment); 1 is the cheaper simulation
            CategoricalTaskInput(key="measure.fidelity", categories=["experiment", "simulation"], fidelities=[0, 1]),
        ],
        "outputs": [
            ContinuousOutput(key="measure.yield_pct", objective=MaximizeObjective(w=1.0)),
        ],
        "constraints": [],
        "acquisition_function": qLogNEI(),
        "num_initial_samples": 4,
        "initial_sampling_method": SamplingMethodEnum.SOBOL,
        "fidelity_thresholds": 0.1,
    }
    return constructor_args, BayesianSequentialOptimizer
