import json
import os
import sys

import pandas as pd
import pytest

from eos.optimization import atlas_optimizer
from eos.optimization.atlas_optimizer import AtlasOptimizer
from eos.optimization.exceptions import EosCampaignOptimizerDomainError

# Stands in for atlas_bridge.py: saves the request, prints Atlas-style noise, then the marked result
FAKE_BRIDGE = """
import json, sys
request = json.load(sys.stdin)
json.dump(request, open(sys.argv[0] + ".request.json", "w"))
print("[INFO] training surrogate")
print("EOS_ATLAS_RESULT " + json.dumps({"samples": [[0.5, "b"]] * request["num"]}))
"""

INPUTS = [
    {"key": "synth.temp", "type": "continuous", "low": 0.0, "high": 1.0},
    {"key": "synth.mol", "type": "categorical", "options": ["a", "b"]},
]
OUTPUTS = [{"key": "dft.gain", "goal": "max"}, {"key": "exp.loss", "goal": "min"}]


@pytest.fixture
def fake_bridge(tmp_path, monkeypatch):
    bridge = tmp_path / "bridge.py"
    bridge.write_text(FAKE_BRIDGE)
    monkeypatch.setattr(atlas_optimizer, "_BRIDGE", bridge)
    return bridge


class TestAtlasOptimizer:
    def test_sample_sends_observations_and_parses_marked_result(self, fake_bridge):
        optimizer = AtlasOptimizer(INPUTS, OUTPUTS, python=sys.executable, planner={"kind": "tanimoto"})
        optimizer.report(
            pd.DataFrame({"synth.temp": [0.1], "synth.mol": ["a"]}),
            pd.DataFrame({"dft.gain": [2.0], "exp.loss": [0.3]}),
        )

        samples = optimizer.sample(2)

        assert samples.to_dict("records") == [{"synth.temp": 0.5, "synth.mol": "b"}] * 2
        request = json.loads((fake_bridge.parent / "bridge.py.request.json").read_text())
        assert request["planner"] == {"kind": "tanimoto"}
        assert [p["name"] for p in request["params"]] == ["synth.temp", "synth.mol"]
        assert request["objectives"] == [{"name": "dft.gain", "goal": "max"}, {"name": "exp.loss", "goal": "min"}]
        assert request["observations"] == [{"params": [0.1, "a"], "values": [2.0, 0.3]}]

    def test_bridge_failure_raises_with_stderr(self, tmp_path, monkeypatch):
        bridge = tmp_path / "bridge.py"
        bridge.write_text("import sys; sys.exit('atlas exploded')")
        monkeypatch.setattr(atlas_optimizer, "_BRIDGE", bridge)
        optimizer = AtlasOptimizer(INPUTS, OUTPUTS, python=sys.executable)

        with pytest.raises(RuntimeError, match="atlas exploded"):
            optimizer.sample()

    def test_pareto_front_respects_goals(self):
        optimizer = AtlasOptimizer(INPUTS, OUTPUTS, python=sys.executable)
        optimizer.report(
            pd.DataFrame({"synth.temp": [0.1, 0.2, 0.3, 0.4], "synth.mol": ["a", "b", "a", "b"]}),
            pd.DataFrame({"dft.gain": [1.0, 3.0, 2.0, 3.0], "exp.loss": [0.1, 0.5, 0.6, 0.9]}),
        )

        front = optimizer.get_optimal_solutions()

        assert front["synth.temp"].tolist() == [0.1, 0.2]
        assert optimizer.get_num_samples_reported() == 4

    def test_rejects_unknown_goal(self):
        with pytest.raises(EosCampaignOptimizerDomainError):
            AtlasOptimizer(INPUTS, [{"key": "y", "goal": "maximize"}], python=sys.executable)


@pytest.mark.slow
@pytest.mark.skipif(not os.environ.get("EOS_TEST_ATLAS_PYTHON"), reason="EOS_TEST_ATLAS_PYTHON not set")
def test_real_atlas_tanimoto_two_objectives():
    """ChemOS 2.0 setup: one molecule choice with fingerprints, two maximized gain objectives."""
    options = [f"m{i}" for i in range(6)]
    descriptors = [[(i >> b) & 1 for b in range(8)] for i in range(6)]
    optimizer = AtlasOptimizer(
        inputs=[{"key": "synth.mol", "type": "categorical", "options": options, "descriptors": descriptors}],
        outputs=[{"key": "dft.gain", "goal": "max"}, {"key": "exp.gain", "goal": "max"}],
        python=os.environ["EOS_TEST_ATLAS_PYTHON"],
        planner={
            "kind": "tanimoto",
            "num_init_design": 1,
            "use_descriptors": True,
            "acquisition_optimizer_kind": "genetic",
        },
    )
    optimizer.report(
        pd.DataFrame({"synth.mol": options[:3]}),
        pd.DataFrame({"dft.gain": [0.2, 0.5, 0.1], "exp.gain": [0.3, 0.4, 0.2]}),
    )

    samples = optimizer.sample()

    assert samples.columns.tolist() == ["synth.mol"]
    assert samples["synth.mol"].iloc[0] in options
