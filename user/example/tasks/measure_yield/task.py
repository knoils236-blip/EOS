import asyncio
import math
import random
from typing import Literal

from pydantic import BaseModel

from eos import Param, task


class YieldOutputs(BaseModel):
    yield_pct: float = Param(unit="%", desc="Measured or simulated reaction yield.")


@task("Measure Yield")
async def measure_yield(
    temperature: float = Param(60.0, unit="C", min=20.0, max=120.0, desc="Reaction temperature."),
    fidelity: Literal["experiment", "simulation"] = Param(
        "experiment", desc="'experiment' is accurate but slow; 'simulation' is fast but systematically off."
    ),
) -> YieldOutputs:
    """Toy reaction for multi-fidelity optimization. The true optimum is near 78 C; the simulation places it near 70 C."""
    if fidelity == "simulation":
        return YieldOutputs(yield_pct=88 * math.exp(-(((temperature - 70) / 22) ** 2)) + 3)
    await asyncio.sleep(2)  # stands in for a long real experiment
    return YieldOutputs(yield_pct=90 * math.exp(-(((temperature - 78) / 18) ** 2)) + random.gauss(0, 0.5))
