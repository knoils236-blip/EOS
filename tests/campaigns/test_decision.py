import copy
from types import SimpleNamespace as Row

from eos.campaigns.decision import build_decision
from eos.campaigns.entities.campaign import CampaignStatus
from eos.protocols.entities.protocol_run import ProtocolRunStatus


def evidence():
    campaign = Row(
        name="campaign",
        protocol="mixture_mock",
        status=CampaignStatus.COMPLETED,
        max_protocol_runs=3,
        meta={
            "optimizer": {
                "optimizer_config": {
                    "constructor_args": {
                        "inputs": [{"key": "prepare.stock_volume_ul"}],
                        "outputs": [{"key": "score.loss", "objective": {"type": "MinimizeObjective"}}],
                    }
                }
            }
        },
    )
    runs, tasks = [], []
    for index, backend, loss, valid in (
        (1, "mock", 0.2, True),
        (2, "simulation", 0.1, True),
        (3, "mock", 0.001, False),
    ):
        name = f"run_{index}"
        runs.append(Row(name=name, status=ProtocolRunStatus.COMPLETED, parameters={}, meta=None))
        outputs = {
            "prepare": {"sample": {"sample_id": "sample-" + name, "backend": backend}},
            "measure": {
                "observation": {"source": "synthetic_model", "model_version": "v1", "value_mm": 0.4, "unit": "mM"}
            },
            "quality": {
                "quality": {
                    "eligible": valid,
                    "failure_kind": "none" if valid else "measurement_invalid",
                    "reasons": [] if valid else ["reference_out_of_range"],
                }
            },
            "score": {"loss": loss},
        }
        tasks.extend(
            Row(
                name=step,
                protocol_run_name=name,
                output_parameters=out,
                input_parameters={"stock_volume_ul": 80},
                output_file_names=["observation.json"] if step == "measure" else [],
            )
            for step, out in outputs.items()
        )
    return campaign, runs, tasks


def test_quality_rejected_data_cannot_win_and_sources_are_never_merged():
    campaign, runs, tasks = evidence()
    snapshot = build_decision(campaign, runs, tasks, [], 3)
    assert {(best["backend"], best["value"]) for best in snapshot["best"]} == {("mock", 0.2), ("simulation", 0.1)}
    assert snapshot["rounds"][-1]["eligible"] is False
    assert snapshot["rounds"][-1]["failure_kind"] == "measurement_invalid"
    assert snapshot["rounds"][0]["selection"]["method"] == "unknown"
    assert snapshot["stop_reason"] == "已达到本实验计划的运行预算。"


def test_snapshot_id_tracks_data_changes_but_not_read_time():
    campaign, runs, tasks = evidence()
    first = build_decision(campaign, runs, tasks, [], 3)
    assert build_decision(campaign, runs, tasks, [], 3)["snapshot_id"] == first["snapshot_id"]
    changed = copy.deepcopy(tasks)
    changed[1].output_parameters["observation"]["value_mm"] = 0.5
    assert build_decision(campaign, runs, changed, [], 3)["snapshot_id"] != first["snapshot_id"]


def test_missing_quality_and_nonfinite_results_remain_unknown():
    campaign, runs, tasks = evidence()
    tasks[2].output_parameters["quality"] = {"unrelated": True}
    tasks[3].output_parameters["loss"] = float("nan")
    snapshot = build_decision(campaign, runs, tasks, [], 3)
    row = snapshot["rounds"][0]
    assert row["quality"] == "unknown"
    assert not row["eligible"]
    assert row["objectives"] == {}
