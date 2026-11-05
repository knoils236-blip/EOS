"""
Runs one Atlas recommendation in a separate Python environment that has Atlas and Olympus installed.

Atlas pins old torch/botorch/pandas versions that cannot share a process with EOS, so ``AtlasOptimizer``
runs this file with that environment's interpreter. Reads a JSON request from stdin, writes
``{"samples": [[...], ...]}`` to stdout. Uses only the standard library plus Atlas/Olympus.
"""

import json
import sys


def recommend(request: dict) -> list[list]:
    import numpy as np  # noqa: PLC0415
    from olympus.campaigns import Campaign, ParameterSpace  # noqa: PLC0415
    from olympus.objects import ParameterCategorical, ParameterContinuous, ParameterDiscrete  # noqa: PLC0415

    param_space = ParameterSpace()
    for p in request["params"]:
        if p["type"] == "continuous":
            param_space.add(ParameterContinuous(name=p["name"], low=p["low"], high=p["high"]))
        elif p["type"] == "discrete":
            param_space.add(ParameterDiscrete(name=p["name"], options=p["options"]))
        else:
            descriptors = p.get("descriptors") or [None] * len(p["options"])
            param_space.add(ParameterCategorical(name=p["name"], options=p["options"], descriptors=descriptors))

    objectives = request["objectives"]
    value_space = ParameterSpace()
    for o in objectives:
        value_space.add(ParameterContinuous(name=o["name"]))

    campaign = Campaign()
    campaign.set_param_space(param_space)
    campaign.set_value_space(value_space)
    for obs in request["observations"]:
        # Olympus reads plain lists as a batch of observations, so each one must be an array
        campaign.add_observation(np.array(obs["params"], dtype=object), np.array(obs["values"], dtype=float))

    options = dict(request["planner"])
    kind = options.pop("kind", "botorch")
    if kind == "tanimoto":
        from atlas.optimizers.tanimoto import TanimotoPlanner as Planner  # noqa: PLC0415
    else:
        from atlas.optimizers.gp import BoTorchPlanner as Planner  # noqa: PLC0415

    if len(objectives) > 1:
        # Atlas scalarizes multiple objectives into one merit that is always minimized
        options.update(goal="minimize", is_moo=True, value_space=value_space, goals=[o["goal"] for o in objectives])
    else:
        options["goal"] = "minimize" if objectives[0]["goal"] == "min" else "maximize"

    planner = Planner(batch_size=request["num"], **options)
    planner.set_param_space(param_space)
    samples = planner.recommend(campaign.observations)
    names = [p["name"] for p in request["params"]]
    return [[_plain(sample.to_dict()[name]) for name in names] for sample in samples]


def _plain(value: object) -> object:
    """Convert numpy scalars to JSON-serializable Python values."""
    return value.item() if hasattr(value, "item") else value


if __name__ == "__main__":
    result = recommend(json.load(sys.stdin))
    # Atlas prints progress to stdout, so the result goes on its own marked last line
    sys.stdout.write("\nEOS_ATLAS_RESULT " + json.dumps({"samples": result}) + "\n")
