import json
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd

from eos.optimization.abstract_sequential_optimizer import AbstractSequentialOptimizer
from eos.optimization.exceptions import EosCampaignOptimizerDomainError

_BRIDGE = Path(__file__).with_name("atlas_bridge.py")
_RESULT_MARKER = "EOS_ATLAS_RESULT "


class AtlasOptimizer(AbstractSequentialOptimizer):
    """
    Bayesian optimization with Atlas, the planner of ChemOS 2.0.

    Atlas pins torch/botorch/pandas versions that conflict with EOS, so each recommendation runs
    ``atlas_bridge.py`` with the interpreter of a separate environment that has Atlas and Olympus installed.
    That interpreter must exist on whichever Ray node hosts the optimizer actor.

    :param inputs: One dict per dynamic parameter, with ``key`` (``task_name.param_name``) and ``type``:
        ``continuous`` (``low``, ``high``), ``discrete`` (``options``) or ``categorical`` (``options``,
        optional ``descriptors``, one numeric vector per option).
    :param outputs: One dict per objective, with ``key`` and ``goal`` (``min`` or ``max``).
    :param python: Path to the Python interpreter of the Atlas environment.
    :param planner: Keyword arguments for the Atlas planner. ``kind`` selects ``botorch`` (default) or ``tanimoto``.
    :param timeout: Seconds to wait for one recommendation.
    """

    def __init__(
        self,
        inputs: list[dict[str, Any]],
        outputs: list[dict[str, Any]],
        python: str,
        planner: dict[str, Any] | None = None,
        timeout: float = 600.0,
    ):
        for output in outputs:
            if output["goal"] not in ("min", "max"):
                raise EosCampaignOptimizerDomainError(f"Output '{output['key']}' goal must be 'min' or 'max'.")

        self._inputs = inputs
        self._outputs = outputs
        self._python = python
        self._planner = planner or {}
        self._timeout = timeout
        self._input_names = [i["key"] for i in inputs]
        self._output_names = [o["key"] for o in outputs]
        self._observations = pd.DataFrame(columns=self._input_names + self._output_names)

    def sample(self, num_protocol_runs: int = 1) -> pd.DataFrame:
        request = {
            "num": num_protocol_runs,
            "planner": self._planner,
            "params": [{**i, "name": i["key"]} for i in self._inputs],
            "objectives": [{"name": o["key"], "goal": o["goal"]} for o in self._outputs],
            "observations": [
                {"params": row[self._input_names].tolist(), "values": row[self._output_names].astype(float).tolist()}
                for _, row in self._observations.iterrows()
            ],
        }
        samples = self._run_bridge(request)
        return pd.DataFrame(samples, columns=self._input_names)

    def _run_bridge(self, request: dict[str, Any]) -> list[list]:
        # -I keeps the Atlas interpreter from importing EOS-side packages via PYTHONPATH or user site
        proc = subprocess.run(
            [self._python, "-I", str(_BRIDGE)],
            input=json.dumps(request, default=_json_default),
            capture_output=True,
            text=True,
            timeout=self._timeout,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"Atlas recommendation failed (exit {proc.returncode}):\n{proc.stderr[-2000:]}")
        result_line = next(line for line in reversed(proc.stdout.splitlines()) if line.startswith(_RESULT_MARKER))
        return json.loads(result_line.removeprefix(_RESULT_MARKER))["samples"]

    def report(self, inputs_df: pd.DataFrame, outputs_df: pd.DataFrame) -> None:
        rows = pd.concat([inputs_df[self._input_names], outputs_df[self._output_names]], axis=1)
        self._observations = rows if self._observations.empty else pd.concat([self._observations, rows])
        self._observations = self._observations.reset_index(drop=True)

    def get_optimal_solutions(self) -> pd.DataFrame:
        obs = self._observations.dropna(subset=self._output_names)  # NaN outputs mark failed runs
        # Express every objective as "lower is better" to test Pareto dominance
        signs = [1 if o["goal"] == "min" else -1 for o in self._outputs]
        costs = obs[self._output_names].astype(float).to_numpy() * signs
        optimal = [
            i for i, c in enumerate(costs) if not any((other <= c).all() and (other < c).any() for other in costs)
        ]
        return obs.iloc[optimal].reset_index(drop=True)

    def get_input_names(self) -> list[str]:
        return self._input_names

    def get_output_names(self) -> list[str]:
        return self._output_names

    def get_num_samples_reported(self) -> int:
        return len(self._observations)


def _json_default(value: Any) -> Any:
    """Convert numpy scalars from DataFrames to plain Python values."""
    if hasattr(value, "item"):
        return value.item()
    raise TypeError(f"Cannot serialize {type(value).__name__} for Atlas")
