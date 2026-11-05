"""Run a bounded Franka beaker lift sweep and emit calibration evidence JSON.

This script starts Isaac Sim and is intentionally not executed by CPU-only tests.
"""

from __future__ import annotations

import argparse
import copy
from datetime import UTC, datetime
import importlib.util
import math
import os
from pathlib import Path
import re
import sys
import time
import traceback


INTEGRATION_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(INTEGRATION_ROOT))
from bridge.common import dumps, write_json
from bridge.session_actions import (
    action_output,
    configure_local_table_asset,
    configure_pickup_cfg,
    control_target,
    seed_state_machine,
)
from bridge.beaker_motion import (
    FLOOR_TOLERANCE_M,
    GRASP_OFFSET_TOLERANCE_M,
    GRIPPER_CLOSE_DELTA_M,
    GRIPPER_OPEN_DELTA_M,
    HEIGHT_TOLERANCE_M,
    HOLD_ORIENTATION_DRIFT_TOLERANCE_RAD,
    HOLD_POSITION_DRIFT_TOLERANCE_M,
    HORIZONTAL_DRIFT_TOLERANCE_M,
    ANGULAR_SPEED_TOLERANCE_RADPS,
    LINEAR_SPEED_TOLERANCE_MPS,
    SUPPORT_HEIGHT_TOLERANCE_M,
    TILT_TOLERANCE_RAD,
)


TABLE_ASSET_USD = INTEGRATION_ROOT / "assets/SeattleLabTable/table_instanceable.usd"
TABLE_ASSET_MANIFEST = INTEGRATION_ROOT / "assets/SeattleLabTable/manifest.json"


def _vector(value):
    value = value.torch if hasattr(value, "torch") else value
    return value[0].detach().cpu().tolist()


def _tilt(quaternion_xyzw):
    x, y, _z, _w = quaternion_xyzw
    return math.acos(max(-1.0, min(1.0, 1.0 - 2.0 * (x * x + y * y))))


def _quaternion_angle_difference(first, second):
    first_norm = math.sqrt(sum(value * value for value in first))
    second_norm = math.sqrt(sum(value * value for value in second))
    if first_norm == 0 or second_norm == 0:
        raise ValueError("Quaternion observations must have non-zero norm")
    dot = abs(sum(
        first[index] * second[index] for index in range(4)
    ) / (first_norm * second_norm))
    return 2.0 * math.acos(min(1.0, max(-1.0, dot)))


def _joint_margins(robot):
    limits = robot.data.joint_pos_limits[0].detach().cpu().tolist()
    positions = _vector(robot.data.joint_pos)
    result = {}
    for name, position, (lower, upper) in zip(robot.joint_names, positions, limits):
        if re.fullmatch(r"panda_joint[1-7]", name):
            result[name] = min(position - lower, upper - position)
    return result


def _snapshot(env, mdp, sim_time_s, control, action_name=None):
    robot = env.scene["robot"]
    beaker_position = _vector(mdp.object_world_pos(env, "beaker"))
    beaker_quaternion = _vector(mdp.object_world_quat(env, "beaker"))
    ee_position = _vector(mdp.ee_world_pos(env, "robot"))
    gripper = _vector(mdp.gripper_pos(env, "robot"))
    margins = _joint_margins(robot)
    return {
        "sim_time_s": sim_time_s,
        "action": action_name,
        "beaker_position_w": beaker_position,
        "beaker_quaternion_xyzw": beaker_quaternion,
        "beaker_linear_velocity_w": _vector(mdp.object_lin_vel(env, "beaker")),
        "beaker_angular_velocity_w": _vector(mdp.object_ang_vel(env, "beaker")),
        "beaker_tilt_rad": _tilt(beaker_quaternion),
        "ee_position_w": ee_position,
        "ee_quaternion_xyzw": _vector(mdp.ee_world_quat(env, "robot")),
        "grasp_offset_w": [
            beaker_position[index] - ee_position[index] for index in range(3)],
        "gripper_position": gripper,
        "joint_limit_margin_rad": margins,
        "minimum_joint_limit_margin_rad": min(margins.values()),
        "control_target": copy.deepcopy(control),
    }


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Calibrate a bounded Franka beaker lift range; this launches Isaac Sim.")
    parser.add_argument(
        "--matterix-root",
        type=Path,
        default=Path(os.environ.get("MATTERIX_PATH", "/home/ry/projects/Matterix")),
    )
    parser.add_argument("--lower-m", type=float, default=0.02)
    parser.add_argument("--upper-m", type=float, default=0.2)
    parser.add_argument("--step-m", type=float, default=0.02)
    parser.add_argument("--max-iterations", type=int, default=50)
    parser.add_argument("--hold-s", type=float, default=1.0)
    parser.add_argument("--control-timeout-s", type=float, default=20.0)
    parser.add_argument("--wall-timeout-s", type=float, default=300.0)
    from isaaclab.app import AppLauncher

    AppLauncher.add_app_launcher_args(parser)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for name in ("lower_m", "upper_m", "step_m", "hold_s", "control_timeout_s", "wall_timeout_s"):
        value = getattr(args, name)
        if not math.isfinite(value) or value <= 0:
            parser.error(f"--{name.replace('_', '-')} must be positive and finite")
    if args.upper_m <= args.lower_m:
        parser.error("--upper-m must be greater than --lower-m")
    if args.max_iterations <= 0:
        parser.error("--max-iterations must be positive")
    return args


def _run_sequence(worker, actions, timeout_s):
    worker.sm.set_action_sequence(actions)
    worker.sm.reset()
    seed_state_machine(worker.sm, worker.control, worker.action_space, worker.torch)
    observations = worker.env.observation_manager.compute()
    worker.sm.update_scene_data_from_obs(observations)
    samples = []
    started = worker.sim_time_s
    complete = False
    while True:
        if time.monotonic() >= worker.deadline:
            raise TimeoutError("calibration wall timeout")
        if worker.sim_time_s - started > timeout_s:
            return False, samples, "control_timeout"
        if complete:
            action_name, semantics = "Wait", None
        else:
            status_before = worker.sm.get_status()
            action_index = status_before[0]["action_idx"] if status_before else None
            action_name = (
                None if action_index is None or action_index >= len(worker.sm.actions)
                else type(worker.sm.actions[action_index]).__name__)
            action, semantics = worker.sm.step(observations)
            command_target = action_output(worker.sm)
        observations, _, terminated, truncated, _ = worker.env.step(
            action, semantic_actions=semantics)
        if bool(terminated.any()) or bool(truncated.any()):
            return False, samples, "environment_terminated"
        worker.sim_time_s += worker.env.step_dt
        worker.control = command_target
        sample = _snapshot(worker.env, worker.mdp, worker.sim_time_s, command_target, action_name)
        samples.append(sample)
        with worker.args.output.with_suffix(".samples.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(dumps(sample) + "\n")
        if bool(worker.sm.action_sequence_failure.any()):
            return False, samples, "primitive_timeout"
        if bool(worker.sm.action_sequence_success.all()):
            complete = True
            if _stable_tail(samples, "Wait", worker.args.hold_s, worker.env.step_dt):
                return True, samples, None


def _stable_tail(samples, action_name, hold_s, step_dt):
    tail = [sample for sample in samples if sample["action"] == action_name]
    if not tail:
        return False
    anchor = tail[-1]
    stable_s = 0.0
    for index in range(len(tail) - 1, 0, -1):
        sample, previous = tail[index], tail[index - 1]
        elapsed = sample["sim_time_s"] - previous["sim_time_s"]
        linear_speed = math.dist(sample["beaker_position_w"], previous["beaker_position_w"]) / elapsed
        angular_speed = _quaternion_angle_difference(
            sample["beaker_quaternion_xyzw"], previous["beaker_quaternion_xyzw"]) / elapsed
        position_drift = math.sqrt(sum(
            (sample["beaker_position_w"][index] - anchor["beaker_position_w"][index]) ** 2
            for index in range(3)
        ))
        orientation_drift = _quaternion_angle_difference(
            sample["beaker_quaternion_xyzw"],
            anchor["beaker_quaternion_xyzw"],
        )
        if (position_drift <= HOLD_POSITION_DRIFT_TOLERANCE_M
                and orientation_drift <= HOLD_ORIENTATION_DRIFT_TOLERANCE_RAD
                and sample["beaker_tilt_rad"] <= TILT_TOLERANCE_RAD
                and linear_speed <= LINEAR_SPEED_TOLERANCE_MPS
                and angular_speed <= ANGULAR_SPEED_TOLERANCE_RADPS):
            stable_s += step_dt
        else:
            break
    return stable_s + 1e-9 >= hold_s


class CalibrationWorker:
    def __init__(self, args, app):
        from matterix.envs import MatterixBaseEnv, mdp
        from matterix_sm import StateMachine
        from matterix_sm.robot_action_spaces import FRANKA_IK_ACTION_SPACE
        from isaaclab.scene import InteractiveSceneCfg
        import isaacsim
        import omni.kit.app

        extensions = omni.kit.app.get_app().get_extension_manager()
        extensions.add_path(str(Path(isaacsim.__file__).parent / "extsDeprecated"))
        if not extensions.set_extension_enabled_immediate(
                "isaacsim.robot_motion.motion_generation", True):
            raise RuntimeError(
                "The installed Franka Lula kinematics extension could not be enabled")
        from bridge.beaker_ik import FrankaPoseAction

        self.app = app
        self.args = args
        self.torch = __import__("torch")
        self.mdp = mdp
        self.sim_time_s = 0.0
        self.control = None
        self.deadline = time.monotonic() + args.wall_timeout_s
        task_file = (
            args.matterix_root
            / "source/matterix_tasks/matterix_tasks/test_dev_tasks/test_franka_beaker_lift.py")
        task_spec = importlib.util.spec_from_file_location("calibration_beaker_task", task_file)
        if task_spec is None or task_spec.loader is None:
            raise RuntimeError(f"Cannot import Franka beaker task: {task_file}")
        task_module = importlib.util.module_from_spec(task_spec)
        sys.modules[task_spec.name] = task_module
        task_spec.loader.exec_module(task_module)
        cfg = copy.deepcopy(task_module.FrankaBeakerLiftEnvTestCfg())
        cfg.scene = InteractiveSceneCfg(
            num_envs=1, env_spacing=cfg.env_spacing, replicate_physics=False)
        cfg.articulated_assets["robot"].action_terms["arm_action"].class_type = (
            FrankaPoseAction)
        cfg.sim.device = args.device
        # TGS needs per-iteration forces for meaningful velocity-based stability checks.
        cfg.sim.physics.enable_external_forces_every_iteration = True
        cfg.seed = 1
        cfg.episode_length_s = max(30.0, args.wall_timeout_s)
        cfg.recorders = None
        cfg.ui_window_class_type = None
        cfg.events.randomize_beaker_position = None
        cfg.articulated_assets["robot"].event_terms.pop(
            "randomize_franka_joint_state", None)
        cfg.terminations.time_out = None
        configure_local_table_asset(cfg, TABLE_ASSET_USD)
        self.env = MatterixBaseEnv(cfg)
        self.sm = StateMachine(
            num_envs=1, dt=self.env.step_dt, device=self.env.device,
            status_interval_steps=0)
        self.action_space = FRANKA_IK_ACTION_SPACE
        observations, _ = self.env.reset(seed=1)
        initial = _snapshot(self.env, self.mdp, self.sim_time_s, None, "settling")
        self.control = control_target({
            "robot": {
                "ee_position_w": initial["ee_position_w"],
                "ee_quaternion_xyzw": _vector(
                    self.mdp.ee_world_quat(self.env, "robot")),
            },
        }, 1.0)
        settle_steps = max(1, round(1.0 / self.env.step_dt))
        with self.torch.inference_mode():
            for _ in range(settle_steps):
                observations, _, terminated, truncated, _ = self.env.step(
                    self.torch.tensor(
                        [self.control], dtype=self.torch.float32,
                        device=self.env.device))
                if bool(terminated.any()) or bool(truncated.any()):
                    raise RuntimeError("environment_terminated_during_settle")
                self.sim_time_s += self.env.step_dt
        self.sm.update_scene_data_from_obs(observations)
        self.baseline = _snapshot(
            self.env, self.mdp, self.sim_time_s, self.control, "baseline")

    def _grasp(self):
        from matterix_sm import PickObjectCfg, WaitCfg

        self._set_damping(80.0)
        pick = PickObjectCfg(
            agent_assets="robot",
            object="beaker",
            post_grasp_offset=(0.0, 0.0, 0.0),
            action_space_info=self.action_space,
        )
        configure_pickup_cfg(pick, 0.0, self.args.control_timeout_s, 0.1)
        pick.sub_actions.pop()
        return _run_sequence(
            self,
            [
                pick,
                WaitCfg(duration=self.args.hold_s + self.env.step_dt),
            ],
            self.args.control_timeout_s + self.args.hold_s,
        )

    def _set_damping(self, damping):
        self.env.scene["robot"].write_joint_damping_to_sim_index(
            damping=self.torch.full(
                (1, 7), damping, device=self.env.device),
            joint_ids=list(range(7)),
        )

    def _move(self, requested_height_m):
        from matterix_sm import MoveToPoseCfg, WaitCfg

        self._set_damping(20.0)
        target = [
            self.baseline["beaker_position_w"][0] - self.grasp_offset[0],
            self.baseline["beaker_position_w"][1] - self.grasp_offset[1],
            self.baseline["beaker_position_w"][2]
            + requested_height_m - self.grasp_offset[2],
        ]
        start = self.control[:3]
        count = max(1, math.ceil(math.dist(start, target) / 0.02))
        actions = []
        x, y, z, w = self.grasp_quaternion
        for index in range(1, count + 1):
            position = [
                begin + (end - begin) * index / count
                for begin, end in zip(start, target)
            ]
            actions.append(MoveToPoseCfg(
                agent_assets="robot",
                target_positions=self.torch.tensor(
                    [position], dtype=self.torch.float32, device=self.env.device),
                target_orientations=self.torch.tensor(
                    [[x, y, z, w]], dtype=self.torch.float32,
                    device=self.env.device),
                timeout=self.args.control_timeout_s,
                position_threshold=0.01,
                orientation_threshold=0.1,
                action_space_info=self.action_space,
            ))
        actions.append(WaitCfg(duration=self.args.hold_s + self.env.step_dt))
        return _run_sequence(
            self,
            actions,
            self.args.control_timeout_s + self.args.hold_s,
        )

    def _release(self):
        from matterix_sm import OpenGripperCfg, WaitCfg

        self._set_damping(20.0)
        actions = [
            OpenGripperCfg(
                agent_assets="robot",
                duration=min(self.args.hold_s, 1.0),
                timeout=self.args.control_timeout_s,
                action_space_info=self.action_space,
            ),
            WaitCfg(duration=self.args.hold_s + self.env.step_dt),
        ]
        return _run_sequence(
            self,
            actions,
            self.args.control_timeout_s + self.args.hold_s,
        )

    def _phase_summary(self, samples, closed_spacing=None):
        if not samples:
            return {
                "sequence_completed": False,
                "hold_stable": False,
                "minimum_joint_limit_margin_rad": None,
            }
        final = samples[-1]
        minimum_margin = min(
            sample["minimum_joint_limit_margin_rad"] for sample in samples)
        spacing = abs(
            final["gripper_position"][0] - final["gripper_position"][1])
        return {
            "sample_count": len(samples),
            "actual_height_m": (
                final["beaker_position_w"][2]
                - self.baseline_position_w[2]),
            "hold_stable": _stable_tail(
                samples, "Wait", self.args.hold_s, self.env.step_dt),
            "grasp_drift_m": (
                None if closed_spacing is None
                else math.dist(
                    final["grasp_offset_w"],
                    self.grasp_offset,
                )),
            "tilt_rad": final["beaker_tilt_rad"],
            "horizontal_drift_m": math.hypot(
                final["beaker_position_w"][0] - self.baseline_position_w[0],
                final["beaker_position_w"][1] - self.baseline_position_w[1],
            ),
            "minimum_joint_limit_margin_rad": minimum_margin,
            "final_joint_limit_margin_rad": final["joint_limit_margin_rad"],
            "gripper_spacing_m": spacing,
            "gripper_open_delta_m": (
                None if closed_spacing is None else spacing - closed_spacing),
        }

    def run(self):
        attempts = []
        self.attempts = attempts
        observed_max_m = 0.0
        upper_reached = False
        remaining_iterations = self.args.max_iterations
        self.baseline_position_w = self.baseline["beaker_position_w"]
        grasp_references = []

        targets = [self.args.lower_m]
        while (targets[-1] < self.args.upper_m - 1e-12
               and len(targets) < self.args.max_iterations):
            targets.append(min(self.args.upper_m, targets[-1] + self.args.step_m))
        if targets[-1] >= self.args.upper_m - 1e-12:
            upper_reached = True

        for requested_height_m in targets:
            if remaining_iterations <= 0:
                break
            remaining_iterations -= 1
            attempt = {
                "requested_height_m": requested_height_m,
                "phases": {},
                "passed": False,
            }
            grasp_success, grasp_samples, grasp_failure = self._grasp()
            grasp = grasp_samples[-1] if grasp_samples else None
            if grasp is not None:
                self.grasp_offset = grasp["grasp_offset_w"]
                self.grasp_quaternion = grasp["ee_quaternion_xyzw"]
                self.closed_gripper_spacing_m = abs(
                    grasp["gripper_position"][0] - grasp["gripper_position"][1])
                grasp_references.append({
                    "requested_height_m": requested_height_m,
                    "position_w": grasp["beaker_position_w"],
                    "ee_quaternion_xyzw": grasp["ee_quaternion_xyzw"],
                    "grasp_offset_w": self.grasp_offset,
                    "gripper_position": grasp["gripper_position"],
                })
            initial_open_spacing_m = abs(
                self.baseline["gripper_position"][0]
                - self.baseline["gripper_position"][1])
            grasp_phase = self._phase_summary(grasp_samples)
            grasp_phase["sequence_completed"] = grasp_success
            grasp_phase["gripper_close_delta_m"] = (
                initial_open_spacing_m - grasp_phase["gripper_spacing_m"]
                if grasp is not None else None)
            attempt["phases"]["grasp"] = grasp_phase
            grasp_passed = (
                grasp_success
                and grasp is not None
                and grasp_phase["hold_stable"]
                and grasp_phase["gripper_close_delta_m"]
                >= GRIPPER_CLOSE_DELTA_M - 1e-9
                and grasp_phase["minimum_joint_limit_margin_rad"] > 0
            )
            if not grasp_passed:
                attempt["failure"] = grasp_failure or "grasp_acceptance_failed"
                attempts.append(attempt)
                upper_reached = False
                break

            lift_success, lift_samples, lift_failure = self._move(requested_height_m)
            lift_phase = self._phase_summary(
                lift_samples, closed_spacing=self.closed_gripper_spacing_m)
            lift_phase["sequence_completed"] = lift_success
            actual_height_m = lift_phase.get("actual_height_m")
            height_error_m = (
                None if actual_height_m is None
                else abs(actual_height_m - requested_height_m)
            )
            lift_phase["height_error_m"] = height_error_m
            attempt["phases"]["lift"] = lift_phase
            lift_passed = (
                lift_success
                and lift_phase["hold_stable"]
                and height_error_m is not None
                and height_error_m <= HEIGHT_TOLERANCE_M
                and actual_height_m >= -FLOOR_TOLERANCE_M
                and actual_height_m <= requested_height_m + HEIGHT_TOLERANCE_M
                and lift_phase["grasp_drift_m"] <= GRASP_OFFSET_TOLERANCE_M
                and lift_phase["horizontal_drift_m"] <= HORIZONTAL_DRIFT_TOLERANCE_M
                and lift_phase["tilt_rad"] <= TILT_TOLERANCE_RAD
                and lift_phase["minimum_joint_limit_margin_rad"] > 0
            )
            if not lift_passed:
                attempt["failure"] = lift_failure or "lift_acceptance_failed"
                attempts.append(attempt)
                upper_reached = False
                break

            lower_success, lower_samples, lower_failure = self._move(0.0)
            lower_phase = self._phase_summary(
                lower_samples, closed_spacing=self.closed_gripper_spacing_m)
            lower_phase["sequence_completed"] = lower_success
            attempt["phases"]["lower"] = lower_phase
            lower_passed = (
                lower_success
                and lower_phase["hold_stable"]
                and -FLOOR_TOLERANCE_M <= lower_phase["actual_height_m"]
                <= SUPPORT_HEIGHT_TOLERANCE_M
                and lower_phase["grasp_drift_m"] <= GRASP_OFFSET_TOLERANCE_M
                and lower_phase["horizontal_drift_m"] <= HORIZONTAL_DRIFT_TOLERANCE_M
                and lower_phase["tilt_rad"] <= TILT_TOLERANCE_RAD
                and lower_phase["minimum_joint_limit_margin_rad"] > 0
            )
            if not lower_passed:
                attempt["failure"] = lower_failure or "lower_acceptance_failed"
                attempts.append(attempt)
                upper_reached = False
                break

            release_success, release_samples, release_failure = self._release()
            release_phase = self._phase_summary(
                release_samples, closed_spacing=self.closed_gripper_spacing_m)
            release_phase["sequence_completed"] = release_success
            release_phase["passed"] = (
                release_success
                and release_phase["hold_stable"]
                and -FLOOR_TOLERANCE_M <= release_phase["actual_height_m"]
                <= SUPPORT_HEIGHT_TOLERANCE_M
                and release_phase["tilt_rad"] <= TILT_TOLERANCE_RAD
                and release_phase["horizontal_drift_m"] <= HORIZONTAL_DRIFT_TOLERANCE_M
                and release_phase["gripper_open_delta_m"]
                >= GRIPPER_OPEN_DELTA_M - 1e-9
                and release_phase["minimum_joint_limit_margin_rad"] > 0
            )
            attempt["phases"]["release"] = release_phase
            passed = (
                grasp_passed
                and lift_passed
                and lower_passed
                and release_phase["passed"]
            )
            attempt["passed"] = passed
            attempt["failure"] = None if passed else (
                release_failure or "release_acceptance_failed")
            attempts.append(attempt)
            if not passed:
                upper_reached = False
                break
            observed_max_m = min(requested_height_m, actual_height_m)
        return {
            "schema_version": 1,
            "scene_id": "franka_beaker",
            "source": "Isaac Sim bounded lift sweep",
            "generated_at": datetime.now(UTC).isoformat(),
            "status": "verified" if upper_reached else "incomplete",
            "method": (
                "complete grasp/lift/lower/release cycle for each requested height; "
                "Lula pose solve with continuous joint-target ramping through PD drives"),
            "parameters": {
                "lower_m": self.args.lower_m,
                "upper_m": self.args.upper_m,
                "step_m": self.args.step_m,
                "max_iterations": self.args.max_iterations,
                "hold_s": self.args.hold_s,
                "control_timeout_s": self.args.control_timeout_s,
                "wall_timeout_s": self.args.wall_timeout_s,
            },
            "criteria": {
                "height_tolerance_m": HEIGHT_TOLERANCE_M,
                "support_height_tolerance_m": SUPPORT_HEIGHT_TOLERANCE_M,
                "horizontal_drift_tolerance_m": HORIZONTAL_DRIFT_TOLERANCE_M,
                "grasp_offset_tolerance_m": GRASP_OFFSET_TOLERANCE_M,
                "hold_position_drift_tolerance_m": HOLD_POSITION_DRIFT_TOLERANCE_M,
                "hold_orientation_drift_tolerance_rad": (
                    HOLD_ORIENTATION_DRIFT_TOLERANCE_RAD),
                "tilt_tolerance_rad": TILT_TOLERANCE_RAD,
                "linear_speed_tolerance_mps": LINEAR_SPEED_TOLERANCE_MPS,
                "angular_speed_tolerance_radps": ANGULAR_SPEED_TOLERANCE_RADPS,
                "gripper_close_delta_m": GRIPPER_CLOSE_DELTA_M,
                "gripper_open_delta_m": GRIPPER_OPEN_DELTA_M,
            },
            "initial_pose": {
                "beaker_position_w": self.baseline["beaker_position_w"],
                "beaker_quaternion_xyzw": self.baseline["beaker_quaternion_xyzw"],
                "beaker_tilt_rad": self.baseline["beaker_tilt_rad"],
                "ee_position_w": self.baseline["ee_position_w"],
                "gripper_position": self.baseline["gripper_position"],
                "joint_limit_margin_rad": self.baseline["joint_limit_margin_rad"],
            },
            "grasp_references": grasp_references,
            "attempts": attempts,
            "observed_max_height_m": observed_max_m,
            "requested_upper_bound_reached": upper_reached,
            "workspace_suggestion": {
                "schema_version": 1,
                "scene_id": "franka_beaker",
                "max_lift_m": observed_max_m,
                "calibration_status": "verified" if upper_reached else "unverified",
                "method": (
                    "current-code full grasp/lift/lower/release cycle; "
                    "see attempts, criteria, and initial_pose"),
            },
            "limitation": (
                "This is a bounded fixed-scene controller observation, not a universal "
                "Franka reachable-height claim or real-hardware result."),
        }


def main():
    from isaaclab.app import AppLauncher

    args = _parse_args()
    args.matterix_root = args.matterix_root.expanduser().resolve()
    os.environ["MATTERIX_PATH"] = str(args.matterix_root)
    for package in ("matterix", "matterix_sm", "matterix_assets", "matterix_tasks"):
        sys.path.insert(0, str(args.matterix_root / "source" / package))
    launcher = AppLauncher(args)
    worker = None
    report = None
    exit_code = 0
    try:
        if not TABLE_ASSET_USD.is_file() or not TABLE_ASSET_MANIFEST.is_file():
            raise FileNotFoundError("SeattleLabTable local asset cache is incomplete")
        worker = CalibrationWorker(args, launcher.app)
        with worker.torch.inference_mode():
            report = worker.run()
    except Exception as exc:
        report = {
            "schema_version": 1,
            "scene_id": "franka_beaker",
            "source": "Isaac Sim bounded lift sweep",
            "generated_at": datetime.now(UTC).isoformat(),
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "attempts": getattr(worker, "attempts", []),
        }
        traceback.print_exc()
        exit_code = 1
    finally:
        if report is not None:
            write_json(args.output.expanduser().resolve(), report)
        if worker is not None:
            worker.env.close()
        launcher.app.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

