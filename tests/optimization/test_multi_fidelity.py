import math

import pandas as pd
import pytest
from bofire.data_models.acquisition_functions.acquisition_function import qLogNEI
from bofire.data_models.features.continuous import ContinuousInput, ContinuousOutput
from bofire.data_models.features.task import CategoricalTaskInput
from bofire.data_models.objectives.identity import MaximizeObjective

from eos.optimization.exceptions import EosCampaignOptimizerDomainError
from eos.optimization.sequential_bayesian_optimizer import BayesianSequentialOptimizer


def _yield(temperature: float, fidelity: str) -> float:
    """True optimum at 0.6; the cheap simulation is shifted and slightly wrong."""
    bias = 0.05 * math.sin(8 * temperature) if fidelity == "simulation" else 0.0
    return -((temperature - 0.6) ** 2) + bias


def _optimizer(**kwargs) -> BayesianSequentialOptimizer:
    return BayesianSequentialOptimizer(
        inputs=[
            ContinuousInput(key="measure.temperature", bounds=(0, 1)),
            CategoricalTaskInput(key="measure.fidelity", categories=["experiment", "simulation"], fidelities=[0, 1]),
        ],
        constraints=[],
        acquisition_function=qLogNEI(),
        num_initial_samples=4,
        **kwargs,
    )


class TestMultiFidelity:
    @pytest.mark.slow
    def test_uses_both_fidelities_and_ranks_only_the_target(self):
        optimizer = _optimizer(outputs=[ContinuousOutput(key="measure.yield", objective=MaximizeObjective(w=1.0))])

        for _ in range(10):
            # EOS reports inputs rebuilt from the database, indexed from 0
            params = optimizer.sample().reset_index(drop=True)
            results = pd.DataFrame(
                {
                    "measure.yield": [
                        _yield(t, f)
                        for t, f in zip(params["measure.temperature"], params["measure.fidelity"], strict=True)
                    ]
                }
            )
            optimizer.report(params, results)

        history = optimizer._optimizer.experiments
        assert set(history["measure.fidelity"]) == {"experiment", "simulation"}
        best = optimizer.get_optimal_solutions()
        assert set(best["measure.fidelity"]) == {"experiment"}

    def test_rejects_multiple_outputs(self):
        with pytest.raises(EosCampaignOptimizerDomainError, match="exactly one output"):
            _optimizer(
                outputs=[
                    ContinuousOutput(key="measure.yield", objective=MaximizeObjective(w=1.0)),
                    ContinuousOutput(key="measure.purity", objective=MaximizeObjective(w=1.0)),
                ]
            )


def test_rejects_empty_outputs():
    with pytest.raises(EosCampaignOptimizerDomainError, match="at least one input and one output"):
        _optimizer(outputs=[])
