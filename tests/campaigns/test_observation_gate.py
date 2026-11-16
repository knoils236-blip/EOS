"""F05/F10: only observations that pass the campaign's observation policy reach its optimizer, once, with provenance."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from eos.campaigns.campaign_executor import CampaignExecutor
from eos.campaigns.exceptions import EosCampaignExecutionError
from eos.campaigns.observations import ObservationPolicy, assess, check_objective_units

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "user"))
from matterix_sim.mixture import check_quality, synthetic_measurement
from matterix_sim.mixture_optimizer import optimizer_definition
from matterix_sim.tasks.score_mixture.task import score_mixture

POLICY = ObservationPolicy(
    version="qc_v1",
    units={"score.loss": "mM^2"},
    quality="quality.quality",
    source="measure.observation.backend",
    allowed_sources=["mock"],
    same_observation=["measure.observation.id", "quality.quality.id"],
)


def outputs(eligible=True, backend="mock", quality_id="o1"):
    return {
        "measure": {"observation": {"id": "o1", "backend": backend}},
        "quality": {"quality": {"id": quality_id, "eligible": eligible, "reasons": [] if eligible else ["blank"]}},
    }


@pytest.mark.parametrize(
    ("run_outputs", "objectives", "reasons"),
    [
        (outputs(), {"score.loss": 0.1}, []),
        (outputs(eligible=False), {"score.loss": 0.1}, ["blank"]),  # A1: kept, not reported
        (outputs(backend="simulation"), {"score.loss": 0.1}, ["source_not_allowed:simulation"]),  # A3
        (outputs(quality_id="o2"), {"score.loss": 0.1}, ["quality_for_other_observation"]),  # A6
        (outputs(), {"score.loss": float("nan")}, ["missing_objective:score.loss"]),  # A7
        (
            {"measure": {"observation": {"id": "o1", "backend": "mock"}}},
            {"score.loss": 0.1},
            [
                "quality_unknown",
                "quality_for_other_observation",
            ],
        ),
    ],
)
def test_assessment_names_every_reason_an_observation_cannot_train_the_optimizer(run_outputs, objectives, reasons):
    verdict = assess(POLICY, run_outputs, objectives)
    assert (verdict.accepted, verdict.reasons, verdict.policy_version) == (not reasons, reasons, "qc_v1")


def test_objectives_in_another_unit_are_refused_before_any_run():  # A2
    protocol = SimpleNamespace(tasks=[SimpleNamespace(name="score", type="Score")])

    def specs(unit):
        loss = SimpleNamespace(unit=unit)
        return SimpleNamespace(get_spec_by_type=lambda _: SimpleNamespace(output_parameters={"loss": loss}))

    check_objective_units(POLICY, ["score.loss"], protocol, specs("mM^2"))
    with pytest.raises(EosCampaignExecutionError, match="declared in 'mM'"):
        check_objective_units(POLICY, ["score.loss"], protocol, specs("mM"))
    with pytest.raises(EosCampaignExecutionError, match="exactly the objectives"):
        check_objective_units(POLICY, ["score.loss", "score.cost"], protocol, specs("mM^2"))


def test_the_mixture_example_declares_a_policy_its_own_tasks_satisfy():
    args, _ = optimizer_definition("mock")
    policy = ObservationPolicy.model_validate(args["observation_policy"])
    assert score_mixture.spec.output_parameters["loss"].unit == policy.units["score.loss"]

    # Only what synthetic_measurement reads from a prepared sample
    sample = SimpleNamespace(
        sample_id="s1", backend="mock", stock_concentration_mm=1.0, stock_ul=74.0, diluent_ul=126.0
    )
    observation = synthetic_measurement(sample, seed=7)
    quality = check_quality(observation).model_dump(mode="json")
    run = {
        "measure": {"observation": observation.model_dump(mode="json")},
        "quality": {"quality": quality},
        "score": {"observation_id": observation.observation_id},
    }
    assert assess(policy, run, {"score.loss": 0.01}).accepted == quality["eligible"]
    # The same observation from the physics simulation never trains the mock protocol's optimizer
    run["measure"]["observation"]["backend"] = "simulation"
    assert "source_not_allowed:simulation" in assess(policy, run, {"score.loss": 0.01}).reasons


def executor(policy=POLICY) -> CampaignExecutor:
    runner = object.__new__(CampaignExecutor)
    runner._campaign_name = "c"
    runner._observation_policy = policy
    runner._queued_reports, runner._queued_discards = [], []
    runner._reported_runs = set()
    runner._pending_report = None
    runner._optimizer_input_names, runner._optimizer_output_names = ["prepare.volume"], ["score.loss"]
    runner._optimizer_additional_parameters = []
    runner._ensure_optimizer_initialized = AsyncMock()
    runner._campaign_optimizer_manager = SimpleNamespace(
        record_campaign_samples=AsyncMock(), record_assessments=AsyncMock()
    )
    runs = {"good": outputs(), "bad": outputs(eligible=False)}
    runner._task_manager = SimpleNamespace(
        get_tasks_by_protocol_runs=AsyncMock(
            return_value={
                (run, task): SimpleNamespace(output_parameters=values)
                for run, by_task in runs.items()
                for task, values in by_task.items()
            }
        )
    )
    frames = (pd.DataFrame({"prepare.volume": [70.0, 90.0]}), pd.DataFrame({"score.loss": [0.1, 0.2]}), {})
    runner._collect_protocol_run_results = AsyncMock(return_value=frames)
    runner._optimizer = SimpleNamespace(
        report=SimpleNamespace(remote=AsyncMock()),
        discard=SimpleNamespace(remote=AsyncMock()),
        report_and_get_meta=SimpleNamespace(remote=AsyncMock(return_value=None)),
    )
    return runner


async def test_rejected_observations_are_recorded_but_only_accepted_ones_are_reported():
    runner = executor()
    await runner._process_results_for_optimization(None, ["good", "bad"])

    recorded = runner._campaign_optimizer_manager.record_campaign_samples.await_args.kwargs["meta_list"]
    assert [m["quality_assessments"]["qc_v1"]["accepted"] for m in recorded] == [True, False]
    ((names, inputs, report),) = runner._queued_reports
    assert names == ["good"]
    assert inputs["prepare.volume"].tolist() == [70.0]
    assert report["score.loss"].tolist() == [0.1]
    assert runner._queued_discards[0]["prepare.volume"].tolist() == [90.0]

    await runner._try_flush_reports()
    runner._optimizer.discard.remote.assert_awaited_once()


async def test_restoration_feeds_exactly_what_the_live_campaign_fed_and_no_failures():  # A4, A5
    runner = executor()
    runner._failed_protocol_runs = ["network_dropped"]
    runner._campaign_manager = SimpleNamespace(
        get_campaign_meta=AsyncMock(return_value=None),
        get_campaign_protocol_run_names=AsyncMock(return_value=["good", "bad"]),
    )
    await runner._restore_optimizer_state(None)

    ((inputs, report), _) = runner._optimizer.report.remote.await_args
    assert inputs["prepare.volume"].tolist() == [70.0]
    assert report["score.loss"].tolist() == [0.1]
    runner._optimizer.report.remote.assert_awaited_once()  # the failed run is not reported as infeasible
    assert set(runner._campaign_optimizer_manager.record_assessments.await_args.args[2]) == {"good", "bad"}


async def test_a_result_arriving_twice_trains_the_optimizer_once():  # F10-A2
    runner = executor()
    await runner._process_results_for_optimization(None, ["good", "bad"])
    await runner._process_results_for_optimization(None, ["good", "bad"])  # e.g. completion seen again
    await runner._try_flush_reports()

    (inputs, _report), _ = runner._optimizer.report_and_get_meta.remote.call_args
    assert inputs["prepare.volume"].tolist() == [70.0]
    assert runner._reported_runs == {"good"}


async def test_each_run_records_which_suggestion_batch_and_configuration_proposed_it():  # F10-A1
    runner = executor()
    runner._optimizer_config_id = "config-1"
    runner._save_optimizer_meta_dict = AsyncMock()
    runner._protocol_run_manager = SimpleNamespace(set_sampling_decision=AsyncMock())
    runner._db_interface = SimpleNamespace(get_async_session=_NoSession)
    future = asyncio.get_running_loop().create_future()
    future.set_result((pd.DataFrame({"prepare.stock_volume_ul": [40.0, 120.0]}), None))
    pending = SimpleNamespace(future=future, protocol_runs=[("c_run_1", {}), ("c_run_2", {})])

    await runner._resolve_pending_sample(pending)

    decisions = [call.args[2] for call in runner._protocol_run_manager.set_sampling_decision.await_args_list]
    suggestions = [d["suggestion"] for d in decisions]
    assert {s["id"] for s in suggestions} == {suggestions[0]["id"]}  # one batch
    assert [(s["index"], s["values"], s["optimizer_config_id"]) for s in suggestions] == [
        (0, {"prepare.stock_volume_ul": 40.0}, "config-1"),
        (1, {"prepare.stock_volume_ul": 120.0}, "config-1"),
    ]


class _NoSession:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False
