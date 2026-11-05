import json
from pathlib import Path

import pytest
import yaml

from integrations.matterix.bridge.beaker_motion import (
    BeakerMotionState,
    BeakerTaskMonitor,
    atomic_ledger_failure,
)
from integrations.matterix.bridge.beaker_workspace import load_beaker_workspace
from integrations.matterix.bridge.session_actions import load_runtime_workflow


BASELINE = [0.6, 0.05, 0.05]
WORKFLOW_PATH = Path(__file__).resolve().parents[2] / (
    "integrations/matterix/bridge/session_workflows.yaml")
MATTERIX_ROOT = "/home/ry/projects/Matterix"


def sample(*, height_m=0.0, gripper=(-0.02, 0.02), linear=(0.0, 0.0, 0.0)):
    position = [BASELINE[0], BASELINE[1], BASELINE[2] + height_m]
    return {
        "beaker": {
            "position_w": position,
            "quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
            "linear_velocity_w": list(linear),
            "angular_velocity_w": [0.0, 0.0, 0.0],
        },
        "robot": {
            "ee_position_w": [
                BASELINE[0],
                BASELINE[1],
                BASELINE[2] + height_m - 0.02,
            ],
            "ee_quaternion_xyzw": [0.0, 0.0, 0.0, 1.0],
            "gripper_position": list(gripper),
        },
    }


def initial_state(max_lift_m=0.2):
    return BeakerMotionState.initial(
        sample(),
        max_lift_m=max_lift_m,
        calibration_status="verified",
        method="test calibration",
    )


def grasped_state():
    state = initial_state()
    state.require_action_allowed("grasp_beaker")
    return state.grasped(sample(), "grasp-1")


def test_missing_workspace_uses_unverified_conservative_twenty_centimetre_bound(tmp_path):
    workspace = load_beaker_workspace(tmp_path / "missing.json")

    assert workspace.max_lift_m == 0.2
    assert workspace.calibration_status == "unverified"
    assert "legacy conservative" in workspace.method


def test_verified_workspace_is_loaded_without_promoting_its_claim(tmp_path):
    path = tmp_path / "beaker_workspace.json"
    path.write_text(
        json.dumps({
            "schema_version": 1,
            "scene_id": "franka_beaker",
            "max_lift_m": 0.137,
            "calibration_status": "verified",
            "method": "bounded simulation sweep",
        }),
        encoding="utf-8",
    )

    workspace = load_beaker_workspace(path)

    assert workspace.max_lift_m == 0.137
    assert workspace.calibration_status == "verified"
    assert workspace.method == "bounded simulation sweep"


def test_lowering_preserves_cumulative_lift_and_blocks_repeated_climbing():
    state = grasped_state().lifted(0.08, sample(height_m=0.08), "lift-1")
    state = state.lowered(0.05, sample(height_m=0.03), "lower-1")

    assert state.requested_height_m == pytest.approx(0.03)
    assert state.accumulated_requested_lift_m == pytest.approx(0.08)
    with pytest.raises(ValueError, match="cumulative"):
        state.lifted_candidate(0.15)


def test_motion_target_is_absolute_and_retains_the_grasp_offset():
    state = grasped_state()

    target = state.target_ee_position_w(0.08)

    assert target == pytest.approx(
        [BASELINE[0], BASELINE[1], BASELINE[2] + 0.06])


def test_lift_and_lower_cannot_cross_height_bounds():
    state = grasped_state()

    with pytest.raises(ValueError, match="calibrated_height"):
        state.lifted_candidate(0.21)
    with pytest.raises(ValueError, match="below_original_support"):
        state.lowered_candidate(0.01)


def test_saved_beaker_protocol_returns_to_support_with_catalog_defaults():
    from integrations.matterix.bridge.capabilities import validate_task

    path = Path(__file__).resolve().parents[2] / "user/matterix_sim/protocols/test/protocol.yml"
    protocol = yaml.safe_load(path.read_text())
    state = initial_state(max_lift_m=load_beaker_workspace().max_lift_m)
    for task in protocol["tasks"]:
        settings = task["parameters"]
        capability = settings["capability_id"]
        normalized = validate_task({
            "request_id": task["name"], "session_id": "protocol-regression",
            "capability_id": capability, "parameters": settings.get("parameters", {}),
        })["parameters"]
        if capability == "grasp_beaker":
            state = state.grasped(sample(), task["name"])
        elif capability == "lift_beaker":
            state = state.lifted_candidate(normalized["height_m"])
        elif capability == "lower_beaker":
            state = state.lowered_candidate(normalized["height_m"])
        elif capability == "release_beaker":
            assert state.requested_height_m == pytest.approx(0.0)
    assert state.requested_height_m == pytest.approx(0.0)


def test_failed_candidate_does_not_enter_success_ledger_and_freezing_is_uncertain():
    state = grasped_state()
    candidate = state.lifted_candidate(0.1)

    assert state.requested_height_m == pytest.approx(0.0)
    assert candidate.requested_height_m == pytest.approx(0.1)
    frozen = state.frozen("physical_acceptance_failed")
    assert frozen.held
    assert frozen.requested_height_m == pytest.approx(0.0)
    assert frozen.uncertain
    assert frozen.uncertainty_reason == "physical_acceptance_failed"
    with pytest.raises(ValueError, match="beaker_motion_uncertain"):
        frozen.lifted_candidate(0.01)


def test_grasp_requires_closed_stable_hold_before_success():
    state = initial_state()
    monitor = BeakerTaskMonitor(
        "grasp_beaker",
        state,
        sample(gripper=(-0.04, 0.04)),
        {"hold_s": 0.2},
    )
    target = [0.6, 0.05, 0.03, 0.0, 0.0, 0.0, 1.0, -1.0]

    monitor.update(sample(), target, 0.1)
    assert not monitor.passed()
    monitor.update(sample(), target, 0.1)

    assert monitor.passed()
    assert monitor.report()["grasp_offset_drift_m"] == pytest.approx(0.0)


def test_grasp_fails_when_closed_command_does_not_narrow_the_fingers():
    state = initial_state()
    monitor = BeakerTaskMonitor(
        "grasp_beaker",
        state,
        sample(gripper=(-0.04, 0.04)),
        {"hold_s": 0.2},
    )
    target = [0.6, 0.05, 0.03, 0.0, 0.0, 0.0, 1.0, -1.0]

    monitor.update(sample(gripper=(-0.04, 0.04)), target, 0.1)
    monitor.update(sample(gripper=(-0.04, 0.04)), target, 0.1)

    assert monitor.report()["gripper_close_delta_m"] == pytest.approx(0.0)
    assert not monitor.passed()


def test_lift_acceptance_uses_measured_position_not_request_completion():
    state = grasped_state()
    state.lifted_candidate(0.1)
    monitor = BeakerTaskMonitor(
        "lift_beaker",
        state,
        sample(),
        {"height_m": 0.1, "hold_s": 0.2},
    )
    target = [0.6, 0.05, 0.13, 0.0, 0.0, 0.0, 1.0, -1.0]

    monitor.update(sample(height_m=0.04), target, 0.1)
    monitor.update(sample(height_m=0.04), target, 0.1)

    report = monitor.report()
    assert report["requested_delta_m"] == pytest.approx(0.1)
    assert report["measured_delta_m"] == pytest.approx(0.04)
    assert report["height_error_m"] == pytest.approx(0.06)
    assert not report["passed"]


def test_lift_acceptance_requires_matching_height_and_continuous_stability():
    state = grasped_state()
    state.lifted_candidate(0.1)
    monitor = BeakerTaskMonitor(
        "lift_beaker",
        state,
        sample(),
        {"height_m": 0.1, "hold_s": 0.2},
    )
    target = [0.6, 0.05, 0.13, 0.0, 0.0, 0.0, 1.0, -1.0]

    monitor.update(sample(height_m=0.1), target, 0.1)
    assert not monitor.passed()
    monitor.update(sample(height_m=0.1), target, 0.1)

    assert not monitor.passed()  # The arrival step is motion, not a stable hold interval.
    monitor.update(sample(height_m=0.1), target, 0.1)
    assert monitor.passed()
    assert monitor.report()["actual_height_m"] == pytest.approx(0.1)


def test_stability_uses_observed_motion_and_keeps_noisy_raw_velocity():
    state = grasped_state()
    monitor = BeakerTaskMonitor("lift_beaker", state, sample(), {"height_m": 0.1, "hold_s": 0.2})
    target = [0.6, 0.05, 0.13, 0.0, 0.0, 0.0, 1.0, -1.0]
    noisy = sample(height_m=0.1, linear=(0.0, 0.0, 0.2))
    for _ in range(3):
        monitor.update(noisy, target, 0.1)
    assert monitor.passed()
    assert monitor.last["raw_linear_speed_mps"] == pytest.approx(0.2)
    assert monitor.last["linear_speed_mps"] == pytest.approx(0.0)
    moving = sample(height_m=0.115)
    monitor.update(moving, target, 0.1)
    assert not monitor.passed()


def test_physical_height_guard_rejects_above_limit_and_below_support():
    state = grasped_state()
    above = BeakerTaskMonitor(
        "lift_beaker",
        state.lifted_candidate(0.2),
        sample(),
        {"height_m": 0.2, "hold_s": 0.1},
    )
    above.update(
        sample(height_m=0.221),
        [0.6, 0.05, 0.13, 0.0, 0.0, 0.0, 1.0, -1.0],
        0.1,
    )
    assert above.report()["hard_failure"] == "beaker_above_calibrated_limit"

    below_state = state.lifted(
        0.01, sample(height_m=0.01), "lift-small")
    below = BeakerTaskMonitor(
        "lower_beaker",
        below_state,
        sample(height_m=0.01),
        {"height_m": 0.01, "hold_s": 0.1},
    )
    below.update(
        sample(height_m=-0.001),
        [0.6, 0.05, 0.03, 0.0, 0.0, 0.0, 1.0, -1.0],
        0.1,
    )
    assert below.report()["hard_failure"] == "beaker_below_original_support"


def test_lift_fails_when_gripper_opening_shows_grasp_was_lost():
    state = grasped_state()
    state.lifted_candidate(0.1)
    monitor = BeakerTaskMonitor(
        "lift_beaker",
        state,
        sample(),
        {"height_m": 0.1, "hold_s": 0.1},
    )

    monitor.update(
        sample(height_m=0.1, gripper=(-0.035, 0.035)),
        [0.6, 0.05, 0.13, 0.0, 0.0, 0.0, 1.0, -1.0],
        0.1,
    )

    assert monitor.report()["hard_failure"] == "beaker_grasp_lost"


def test_release_gate_requires_support_stability_and_observed_opening():
    state = grasped_state().lifted(0.1, sample(height_m=0.1), "lift-1")
    state = state.lowered(0.1, sample(height_m=0.0), "lower-1")
    monitor = BeakerTaskMonitor(
        "release_beaker", state, sample(), {"hold_s": 0.2})
    closed_target = [0.6, 0.05, 0.03, 0.0, 0.0, 0.0, 1.0, -1.0]

    monitor.update(sample(), closed_target, 0.1)
    assert not monitor.release_ready()
    monitor.update(sample(), closed_target, 0.1)
    assert monitor.release_ready()

    open_target = [0.6, 0.05, 0.03, 0.0, 0.0, 0.0, 1.0, 1.0]
    open_sample = sample(gripper=(-0.03, 0.03))
    monitor.update(open_sample, open_target, 0.1)
    assert not monitor.passed()
    monitor.update(open_sample, open_target, 0.1)

    report = monitor.report()
    assert report["released"]
    assert report["supported"]
    assert report["passed"]


def test_release_during_unsupported_motion_never_passes():
    state = grasped_state().lifted(0.1, sample(height_m=0.1), "lift-1")
    monitor = BeakerTaskMonitor(
        "release_beaker", state, sample(height_m=0.1), {"hold_s": 0.1})
    open_target = [0.6, 0.05, 0.13, 0.0, 0.0, 0.0, 1.0, 1.0]

    monitor.update(
        sample(height_m=0.1, gripper=(-0.03, 0.03)), open_target, 0.1)
    monitor.update(
        sample(height_m=0.1, gripper=(-0.03, 0.03)), open_target, 0.1)

    assert not monitor.report()["release_ready"]
    assert not monitor.report()["passed"]
    assert monitor.report()["hard_failure"] == "released_beaker_lost_stable_support"

    monitor.update(
        sample(height_m=0.0, gripper=(-0.03, 0.03)), open_target, 0.1)
    assert monitor.report()["hard_failure"] == "released_beaker_lost_stable_support"
    assert not monitor.report()["passed"]


def test_atomic_skill_rejects_legacy_held_pose_without_motion_ledger():
    failure = atomic_ledger_failure(
        "lower_beaker",
        {"request_id": "legacy-pickup"},
        None,
    )

    assert failure == "beaker_motion_ledger_unavailable_for_legacy_held_beaker"


def test_atomic_workflows_keep_grasp_still_and_lift_lower_in_opposite_directions():
    grasp = load_runtime_workflow(
        WORKFLOW_PATH,
        "grasp_beaker",
        MATTERIX_ROOT,
        parameters={"hold_s": 0.5, "timeout_s": 12},
    )
    lift = load_runtime_workflow(
        WORKFLOW_PATH,
        "lift_beaker",
        MATTERIX_ROOT,
        parameters={"height_m": 0.03, "hold_s": 0.5, "timeout_s": 12},
        ee_start_w=[0.6, 0.05, 0.03],
        target_ee_w=[0.6, 0.05, 0.06],
        target_orientation_xyzw=[0.0, 0.0, 0.0, 1.0],
    )
    lower = load_runtime_workflow(
        WORKFLOW_PATH,
        "lower_beaker",
        MATTERIX_ROOT,
        parameters={"height_m": 0.02, "hold_s": 0.5, "timeout_s": 12},
        ee_start_w=[0.6, 0.05, 0.06],
        target_ee_w=[0.6, 0.05, 0.04],
        target_orientation_xyzw=[0.0, 0.0, 0.0, 1.0],
    )

    assert grasp["actions"][0]["lift"] == [0.0, 0.0, 0.0]
    assert lift["actions"][0]["type"] == "move_to_pose"
    assert lift["actions"][-2]["position_w"] == pytest.approx([0.6, 0.05, 0.06])
    assert lower["actions"][0]["type"] == "move_to_pose"
    assert lower["actions"][-2]["position_w"] == pytest.approx([0.6, 0.05, 0.04])


def test_release_workflow_waits_before_open_and_after_open():
    release = load_runtime_workflow(
        WORKFLOW_PATH,
        "release_beaker",
        MATTERIX_ROOT,
        parameters={"hold_s": 0.5, "timeout_s": 12},
    )

    assert [action["type"] for action in release["actions"]] == ["wait", "open", "wait"]
