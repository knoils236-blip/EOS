import asyncio
import json
from pathlib import Path
import sys

import pytest
import pandas as pd
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "user"))

from eos.configuration.configuration_manager import ConfigurationManager
from matterix_sim.devices.mixture_workstation.device import MixtureWorkstation
from matterix_sim.mixture import Observation, Sample, check_quality, synthetic_measurement
from matterix_sim.tasks.score_mixture.task import score_mixture
from matterix_sim.mixture_optimizer import MixtureOptimizer
from eos.optimization.sequential_bayesian_optimizer import BayesianSequentialOptimizer

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
async def workstation(tmp_path):
    device = MixtureWorkstation("mixture", "test_lab")
    await device.initialize({"backend": "mock", "journal_dir": tmp_path})
    try:
        yield device
    finally:
        await device.cleanup()


@pytest.mark.parametrize("volume", [20.0, 74.0, 180.0])
async def test_mock_preparation_conserves_volume_and_tracks_sample(workstation, volume):
    sample = Sample.model_validate(await workstation.prepare("experiment", "prepare", volume))
    assert sample.stock_ul + sample.diluent_ul == 200
    assert sample.stock_ul == volume
    assert sample.backend == "mock"
    assert sample.composition_source == "mock_material_balance"
    assert len(sample.operations) == 2
    report = await workstation.finalize_task("experiment", "prepare")
    assert report["available"]
    assert json.loads(report["files"]["preparation-context.json"])["sample_id"] == sample.sample_id


@pytest.mark.parametrize("volume", [19.9, 180.1, float("nan"), float("inf")])
async def test_invalid_preparation_does_not_create_an_execution(workstation, volume):
    with pytest.raises(ValueError, match="20 and 180"):
        await workstation.prepare("experiment", "prepare", volume)
    assert not workstation._runs


async def test_same_preparation_is_idempotent_and_changed_input_is_rejected(workstation):
    first = await workstation.prepare("experiment", "prepare", 74)
    assert await workstation.prepare("experiment", "prepare", 74) == first
    with pytest.raises(ValueError, match="different preparation"):
        await workstation.prepare("experiment", "prepare", 75)
    second = await workstation.prepare("experiment2", "prepare", 74)
    assert second["sample_id"] != first["sample_id"]


async def test_stopped_device_requires_explicit_recovery(workstation):
    await workstation.finalize_task("experiment", "prepare", stop=True)
    with pytest.raises(RuntimeError, match="recovery"):
        await workstation.prepare("experiment", "prepare", 74)
    await workstation.recover()
    assert (await workstation.prepare("new_experiment", "prepare", 74))["backend"] == "mock"


async def test_measurement_is_reproducible_and_quality_gate_binds_exact_observation(workstation):
    sample = Sample.model_validate(await workstation.prepare("experiment", "prepare", 74))
    observation = synthetic_measurement(sample, seed=42)
    assert observation == synthetic_measurement(sample, seed=42)
    assert observation != synthetic_measurement(sample, seed=43)
    assert observation.source == "synthetic_model"
    assert observation.hardware_calibrated is False
    quality = check_quality(observation)
    result = await score_mixture.fn(observation.model_dump(), quality.model_dump())
    assert result.loss < 0.001
    wrong = quality.model_copy(update={"observation_id": "some-other-observation"})
    with pytest.raises(ValueError, match="does not match"):
        await score_mixture.fn(observation.model_dump(), wrong.model_dump())


async def test_failed_controls_are_archivable_but_never_scored(workstation):
    sample = Sample.model_validate(await workstation.prepare("experiment", "prepare", 74))
    raw = synthetic_measurement(sample, seed=42).model_dump()
    raw["reference_mm"] = 0.5
    reading = Observation.model_validate(raw)
    quality = check_quality(reading)
    assert quality.failure_kind == "measurement_invalid"
    assert quality.reasons == ["reference_out_of_range"]
    with pytest.raises(ValueError, match="measurement_invalid"):
        await score_mixture.fn(raw, quality.model_dump())
    raw["value_mm"] = float("nan")
    with pytest.raises(ValidationError):
        Observation.model_validate(raw)


def test_both_backends_load_the_same_experiment_and_optimizer_contract():
    config = ConfigurationManager(str(ROOT / "user"), {"matterix_sim"})
    for backend in ("mock", "matterix"):
        config.load_lab("mixture_" + backend + "_lab")
        config.load_protocol("mixture_" + backend)
        protocol = config.protocols["mixture_" + backend]
        assert protocol.labs == ["mixture_" + backend + "_lab"]
        assert [t.name for t in protocol.tasks] == ["prepare", "measure", "quality", "score"]
        assert protocol.tasks[0].parameters == {"stock_volume_ul": "eos_dynamic"}
        assert all(t.retries == 0 for t in protocol.tasks)


async def test_restart_reuses_completed_sample_and_freezes_uncertain_execution(tmp_path):
    config = {"backend": "mock", "journal_dir": tmp_path}
    first = MixtureWorkstation("mixture", "restart")
    await first.initialize(config)
    sample = await first.prepare("completed", "prepare", 74)
    await first.cleanup()
    second = MixtureWorkstation("mixture", "restart")
    await second.initialize(config)
    assert await second.prepare("completed", "prepare", 74) == sample
    second._runs[("uncertain", "prepare")] = {"volume_ul": 75, "active": "stock", "reports": [], "operations": []}
    await second._persist(("uncertain", "prepare"))
    await second.cleanup()
    third = MixtureWorkstation("mixture", "restart")
    await third.initialize(config)
    try:
        with pytest.raises(RuntimeError, match="recovery"):
            await third.prepare("new", "prepare", 74)
        await third.recover()
        with pytest.raises(RuntimeError, match="unresolved"):
            await third.prepare("uncertain", "prepare", 75)
        assert await third.prepare("new", "prepare", 74)
    finally:
        await third.cleanup()


def test_failed_results_clear_pending_without_training_the_objective(monkeypatch):
    optimizer = object.__new__(MixtureOptimizer)
    cleared, reported = [], []
    monkeypatch.setattr(optimizer, "_clear_reported_pending", lambda inputs: cleared.extend(inputs.index))
    monkeypatch.setattr(
        BayesianSequentialOptimizer, "report", lambda self, inputs, outputs: reported.extend(outputs.index)
    )
    optimizer.report(
        pd.DataFrame({"prepare.stock_volume_ul": [50, 74]}), pd.DataFrame({"score.loss": [float("nan"), 0.001]})
    )
    assert cleared == [0]
    assert reported == [1]


async def test_cancelling_active_preparation_stops_remote_command_and_prevents_later_steps(workstation):
    entered, stopped = asyncio.Event(), asyncio.Event()
    commands, cancellations = [], []

    class Remote:
        async def begin_experiment(self, *args):
            pass

        async def run_capability(self, capability, *args):
            commands.append(capability)
            entered.set()
            await stopped.wait()
            return {"status": "cancelled", "error": "requested", "result": {"acceptance": {"passed": False}}}

        async def finalize_task(self, run, step, stop=False):
            if stop:
                cancellations.append(step)
                stopped.set()
            return {"files": {"evidence.zip": b"remote evidence"}, "resource_updates": {}, "available": not stop}

        async def cleanup(self):
            pass

    workstation._driver = Remote()
    preparing = asyncio.create_task(workstation.prepare("cancelled", "prepare", 74))
    await entered.wait()
    report = await workstation.finalize_task("cancelled", "prepare", stop=True)
    with pytest.raises(RuntimeError, match="Execution failed"):
        await preparing
    assert commands == ["station_full_station_open_door"]
    assert cancellations == ["prepare-open_door"]
    assert not report["available"]
    assert report["files"]["open_door-evidence.zip"] == b"remote evidence"
