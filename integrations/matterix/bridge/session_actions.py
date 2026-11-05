"""Configuration and acceptance helpers for reusable Matterix sessions."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import math
from pathlib import Path
import sys


LIFT_TOLERANCE_M = 0.02
MINIMUM_LIFT_M = 0.05
RETURN_HORIZONTAL_TOLERANCE_M = 0.03
RETURN_VERTICAL_TOLERANCE_M = 0.02
RETURN_POSITION_DRIFT_TOLERANCE_M = 0.001
RETURN_ORIENTATION_DRIFT_TOLERANCE_RAD = 0.02
RETURN_TILT_TOLERANCE_RAD = 0.1
GRIPPER_OPEN_DELTA_M = 0.006
GRASP_STABILITY_TOLERANCE_M = 0.015
# TGS solver velocities stayed noisy while the released beaker drifted only 24 um over 25 s.


def _finite_vector(value, length, name):
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise ValueError(f"{name} must contain {length} numbers")
    result = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
            raise ValueError(f"{name} must contain finite numbers")
        result.append(float(item))
    return result


def _load_workflow_module(matterix_root):
    path = Path(matterix_root).expanduser().resolve() / "scripts/workflow_yaml.py"
    if not path.is_file():
        raise ValueError(f"Matterix workflow loader does not exist: {path}")
    key = "matterix_session_workflow_yaml"
    module = sys.modules.get(key)
    if module is None:
        spec = importlib.util.spec_from_file_location(key, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot import Matterix workflow loader: {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(key, None)
            raise
    return module


def _single_action(spec, kind):
    matches = [step for step in spec["actions"] if step["type"] == kind]
    if len(matches) != 1:
        raise ValueError(f"Workflow '{spec['name']}' must contain exactly one {kind} action")
    return matches[0]


def load_runtime_workflow(
    workflow_path,
    workflow_name,
    matterix_root,
    *,
    parameters=None,
    return_reference=None,
    beaker_baseline=None,
    beaker_current=None,
    ee_start_w=None,
    target_ee_w=None,
    target_orientation_xyzw=None,
):
    """Load a Matterix YAML workflow and return a parameterized runtime copy."""
    loader = _load_workflow_module(matterix_root)
    loaded = loader.load_workflow_file(workflow_path, workflow_name)
    spec = copy.deepcopy(loaded.spec)
    spec["name"] = loaded.name
    values = copy.deepcopy(parameters or {})

    timeout = float(values.get("timeout_s", 30.0))
    if workflow_name == "pickup_beaker":
        height = float(values["lift_height_m"])
        hold_s = float(values["hold_s"])
        pick = _single_action(spec, "pick")
        pick["lift"] = [0.0, 0.0, height]
        pick["orientation_tolerance"] = 0.1
        pick["timeout"] = timeout
        _single_action(spec, "wait")["seconds"] = hold_s
    elif workflow_name == "grasp_beaker":
        hold_s = float(values["hold_s"])
        pick = _single_action(spec, "pick")
        pick["lift"] = [0.0, 0.0, 0.0]
        pick["orientation_tolerance"] = 0.1
        pick["timeout"] = timeout
        _single_action(spec, "wait")["seconds"] = hold_s
    elif workflow_name in {"lift_beaker", "lower_beaker"}:
        hold_s = float(values["hold_s"])
        move = _single_action(spec, "move_to_pose")
        target = _finite_vector(target_ee_w, 3, "target_ee_w")
        start = (
            target
            if ee_start_w is None
            else _finite_vector(ee_start_w, 3, "ee_start_w")
        )
        orientation = _finite_vector(
            target_orientation_xyzw, 4, "target_orientation_xyzw")
        move["position_w"] = target
        x, y, z, w = orientation
        move["quaternion_wxyz"] = [w, x, y, z]
        move["timeout"] = timeout
        _single_action(spec, "wait")["seconds"] = hold_s
        distance = math.dist(start, target)
        count = max(1, math.ceil(distance / 0.02))
        spec["actions"] = [
            {
                **move,
                "position_w": [
                    begin + (end - begin) * index / count
                    for begin, end in zip(start, target)
                ],
            }
            for index in range(1, count + 1)
        ] + [spec["actions"][-1]]
    elif workflow_name == "release_beaker":
        hold_s = float(values["hold_s"])
        waits = [step for step in spec["actions"] if step["type"] == "wait"]
        if len(waits) != 2:
            raise ValueError("release_beaker must contain support-gate and post-release waits")
        waits[0]["seconds"] = hold_s
        waits[1]["seconds"] = hold_s
        release = _single_action(spec, "open")
        release["seconds"] = min(hold_s, 1.0)
        release["timeout"] = timeout
    elif workflow_name == "return_beaker":
        hold_s = float(values["hold_s"])
        position, quaternion_wxyz = make_return_pose(
            return_reference, beaker_baseline, beaker_current)
        move = _single_action(spec, "move_to_pose")
        move["position_w"] = position
        move["quaternion_wxyz"] = quaternion_wxyz
        move["timeout"] = timeout
        _single_action(spec, "open")["seconds"] = hold_s
        for action in spec["actions"]:
            if "timeout" in action:
                action["timeout"] = timeout
        if any(action["type"] in {"pick", "close"} for action in spec["actions"]):
            raise ValueError("return_beaker cannot grasp the beaker again")
    elif workflow_name == "wait":
        _single_action(spec, "wait")["seconds"] = float(values["duration_s"])
    else:
        raise ValueError(f"Unsupported session workflow: {workflow_name}")
    return spec


def make_return_pose(reference, baseline, current):
    """Anchor the saved grasp pose to the original beaker table baseline."""
    if not isinstance(reference, dict):
        raise ValueError("A successful pickup reference is required")
    position = _finite_vector(reference.get("ee_position_w"), 3, "reference.ee_position_w")
    quaternion = _finite_vector(
        reference.get("ee_quaternion_xyzw"), 4, "reference.ee_quaternion_xyzw")
    baseline = _finite_vector(baseline, 3, "beaker_baseline")
    current = _finite_vector(current, 3, "beaker_current")
    target = [position[index] + baseline[index] - current[index] for index in range(3)]
    return target, [quaternion[3], quaternion[0], quaternion[1], quaternion[2]]


def control_target(snapshot, gripper_value):
    """Return Franka's [position, quaternion, gripper] control vector."""
    position = _finite_vector(
        snapshot["robot"]["ee_position_w"], 3, "robot.ee_position_w")
    quaternion = _finite_vector(
        snapshot["robot"]["ee_quaternion_xyzw"], 4, "robot.ee_quaternion_xyzw")
    if isinstance(gripper_value, bool) or not isinstance(gripper_value, (int, float)):
        raise ValueError("gripper_value must be a finite number")
    gripper = float(gripper_value)
    if not math.isfinite(gripper) or not -1.0 <= gripper <= 1.0:
        raise ValueError("gripper_value must be from -1 to 1")
    return position + quaternion + [gripper]


def configure_local_table_asset(env_cfg, usd_path):
    """Point both the object config and its already-expanded spawner at a local USD."""
    local_usd_path = str(Path(usd_path).expanduser().resolve())
    table_cfg = env_cfg.objects["table"]
    table_cfg.usd_path = local_usd_path
    table_cfg.spawn.usd_path = local_usd_path
    if table_cfg.usd_path != table_cfg.spawn.usd_path:
        raise RuntimeError("table USD paths disagree after local asset configuration")
    return table_cfg


def configure_pickup_cfg(pick_cfg, lift_height_m, timeout_s, lift_orientation_tolerance_rad=0.1):
    """Apply runtime values to the expanded post-grasp lift only."""
    height = float(lift_height_m)
    timeout = float(timeout_s)
    orientation = float(lift_orientation_tolerance_rad)
    if not math.isfinite(height) or height < 0:
        raise ValueError("lift_height_m must be non-negative and finite")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout_s must be positive and finite")
    if not math.isfinite(orientation) or orientation <= 0:
        raise ValueError("orientation_tolerance_rad must be positive and finite")
    offset = (0.0, 0.0, height)
    pick_cfg.post_grasp_offset = offset
    lift_actions = []
    for sub_action in pick_cfg.sub_actions:
        if "MoveRelativeCfg" in type(sub_action).__name__:
            lift_actions.append(sub_action)
    if len(lift_actions) != 1:
        raise ValueError("PickObjectCfg must expand to exactly one post-grasp MoveRelativeCfg")
    lift_action = lift_actions[0]
    lift_action.position_offset = offset
    lift_action.orientation_threshold = orientation
    lift_action.timeout = timeout
    return pick_cfg


def seed_state_machine(sm, target, action_space_info, torch):
    """Give a fresh state machine the prior task's complete control target."""
    values = _finite_vector(target, action_space_info.total_dim, "control target")
    tensor = torch.tensor([values], dtype=torch.float32, device=sm.device)
    sm.action_dict["robot"] = tensor
    sm._agent_action_space_info["robot"] = action_space_info
    # WaitCfg has no agents; this is the state-machine hold path for a one-step sequence.
    sm._last_action_result = tensor
    return tensor


def action_output(sm):
    if sm.action_dict:
        value = next(iter(sm.action_dict.values()))
    else:
        value = sm._last_action_result
    if value is None:
        raise RuntimeError("State machine did not preserve a robot control target")
    value = value.torch if hasattr(value, "torch") else value
    return value[0].detach().cpu().tolist()


class LiftCheck:
    """Require the requested effective lift for a continuous hold window."""

    def __init__(self, baseline_z, requested_lift_m, hold_s):
        self.baseline_z = float(baseline_z)
        self.requested_lift_m = float(requested_lift_m)
        self.minimum_lift_m = max(MINIMUM_LIFT_M, self.requested_lift_m - LIFT_TOLERANCE_M)
        self.hold_s = float(hold_s)
        self.held_s = 0.0
        self.maximum_lift_m = -math.inf

    def update(self, z, dt):
        values = (self.baseline_z, self.requested_lift_m, self.hold_s, z, dt)
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values):
            raise ValueError("Lift measurements must be finite numbers")
        if dt <= 0:
            raise ValueError("Lift time step must be positive")
        lift = float(z) - self.baseline_z
        self.maximum_lift_m = max(self.maximum_lift_m, lift)
        self.held_s = self.held_s + dt if lift >= self.minimum_lift_m else 0.0
        return self.held_s + 1e-9 >= self.hold_s

    def report(self):
        return {
            "baseline_z_m": self.baseline_z,
            "requested_lift_m": self.requested_lift_m,
            "minimum_lift_m": self.minimum_lift_m,
            "required_hold_s": self.hold_s,
            "current_hold_s": self.held_s,
            "maximum_lift_m": None if self.maximum_lift_m == -math.inf else self.maximum_lift_m,
            "passed": self.held_s + 1e-9 >= self.hold_s,
        }


class ReturnCheck:
    """Require release at the table and a continuously stable beaker pose."""

    def __init__(self, baseline_position, closed_gripper_position, hold_s):
        self.baseline = _finite_vector(baseline_position, 3, "beaker baseline")
        self.closed_gripper = _finite_vector(closed_gripper_position, 2, "closed gripper position")
        self.hold_s = float(hold_s)
        self.stable_s = 0.0
        self.last_horizontal_error_m = None
        self.last_vertical_error_m = None
        self.last_gripper_open_delta_m = None
        self.last_position_drift_m = None
        self.last_orientation_drift_rad = None
        self.last_tilt_rad = None
        self.last_linear_speed_mps = None
        self.last_angular_speed_radps = None
        self.position_anchor_w = None
        self.quaternion_anchor_xyzw = None
        self.on_table = False
        self.released = False

    def update(self, sample, dt, command_target):
        if not isinstance(dt, (int, float)) or isinstance(dt, bool) or not math.isfinite(dt) or dt <= 0:
            raise ValueError("Return time step must be positive and finite")
        target = _finite_vector(command_target, 8, "control target")
        beaker = sample["beaker"]
        position = _finite_vector(beaker["position_w"], 3, "beaker position")
        if "quaternion_xyzw" not in beaker:
            raise ValueError("beaker quaternion_xyzw observation is required")
        quaternion = _normalized_quaternion(
            beaker["quaternion_xyzw"], "beaker quaternion")
        linear = _finite_vector(beaker["linear_velocity_w"], 3, "beaker linear velocity")
        angular = _finite_vector(beaker["angular_velocity_w"], 3, "beaker angular velocity")
        gripper = _finite_vector(sample["robot"]["gripper_position"], 2, "gripper position")
        x, y, z, w = quaternion
        local_z_in_world_z = 1.0 - 2.0 * (x * x + y * y)
        tilt = math.acos(min(1.0, max(-1.0, local_z_in_world_z)))

        horizontal_error = math.hypot(
            position[0] - self.baseline[0], position[1] - self.baseline[1])
        vertical_error = abs(position[2] - self.baseline[2])
        on_table = (
            horizontal_error <= RETURN_HORIZONTAL_TOLERANCE_M
            and vertical_error <= RETURN_VERTICAL_TOLERANCE_M
        )
        upright = tilt <= RETURN_TILT_TOLERANCE_RAD
        closed_spacing = abs(self.closed_gripper[0] - self.closed_gripper[1])
        current_spacing = abs(gripper[0] - gripper[1])
        open_delta = current_spacing - closed_spacing
        released = target[-1] >= 0.5 and open_delta >= GRIPPER_OPEN_DELTA_M
        self.last_horizontal_error_m = horizontal_error
        self.last_vertical_error_m = vertical_error
        self.last_gripper_open_delta_m = open_delta
        self.last_tilt_rad = tilt
        self.last_linear_speed_mps = math.sqrt(sum(value * value for value in linear))
        self.last_angular_speed_radps = math.sqrt(sum(value * value for value in angular))
        self.on_table = on_table
        self.released = self.released or released

        if not released or not on_table or not upright:
            self.stable_s = 0.0
            self.position_anchor_w = None
            self.quaternion_anchor_xyzw = None
            self.last_position_drift_m = None
            self.last_orientation_drift_rad = None
            return False

        if self.position_anchor_w is None:
            self.position_anchor_w = position
            self.quaternion_anchor_xyzw = quaternion
            position_drift = 0.0
            orientation_drift = 0.0
        else:
            position_drift = math.sqrt(sum(
                (position[index] - self.position_anchor_w[index]) ** 2
                for index in range(3)
            ))
            orientation_drift = _quaternion_angle_difference(
                quaternion, self.quaternion_anchor_xyzw)

        pose_stable = (
            position_drift <= RETURN_POSITION_DRIFT_TOLERANCE_M + 1e-9
            and orientation_drift <= RETURN_ORIENTATION_DRIFT_TOLERANCE_RAD + 1e-9
        )
        if pose_stable:
            self.stable_s += dt
        else:
            self.stable_s = 0.0
            self.position_anchor_w = position
            self.quaternion_anchor_xyzw = quaternion
        self.last_position_drift_m = position_drift
        self.last_orientation_drift_rad = orientation_drift
        return self.stable_s + 1e-9 >= self.hold_s

    def report(self):
        return {
            "required_hold_s": self.hold_s,
            "current_stable_s": self.stable_s,
            "horizontal_error_m": self.last_horizontal_error_m,
            "vertical_error_m": self.last_vertical_error_m,
            "gripper_open_delta_m": self.last_gripper_open_delta_m,
            "position_drift_m": self.last_position_drift_m,
            "position_drift_tolerance_m": RETURN_POSITION_DRIFT_TOLERANCE_M,
            "orientation_drift_rad": self.last_orientation_drift_rad,
            "orientation_drift_tolerance_rad": RETURN_ORIENTATION_DRIFT_TOLERANCE_RAD,
            "tilt_rad": self.last_tilt_rad,
            "tilt_tolerance_rad": RETURN_TILT_TOLERANCE_RAD,
            "linear_speed_mps": self.last_linear_speed_mps,
            "angular_speed_radps": self.last_angular_speed_radps,
            "position_anchor_w": copy.deepcopy(self.position_anchor_w),
            "quaternion_anchor_xyzw": copy.deepcopy(self.quaternion_anchor_xyzw),
            "on_table": self.on_table,
            "released": self.released,
            "passed": self.released and self.stable_s + 1e-9 >= self.hold_s,
        }


def _normalized_quaternion(value, name):
    quaternion = _finite_vector(value, 4, name)
    norm = math.sqrt(sum(component * component for component in quaternion))
    if norm < 1e-12:
        raise ValueError(f"{name} must have non-zero norm")
    return [component / norm for component in quaternion]


def _quaternion_angle_difference(first, second):
    dot = abs(sum(first[index] * second[index] for index in range(4)))
    return 2.0 * math.acos(min(1.0, max(-1.0, dot)))


def postcondition_satisfied(
        capability_id,
        sample,
        command_target,
        parameters,
        *,
        elapsed_sim_s,
        lift_check=None,
        return_check=None,
        grasp_sample=None):
    """Return whether completed primitive actions have met the task postcondition."""
    if capability_id == "pickup_beaker":
        if lift_check is None or not lift_check.report()["passed"]:
            return False
        return grasp_is_closed(sample, command_target, grasp_sample)
    if capability_id == "return_beaker":
        return return_check is not None and bool(return_check.report()["passed"])
    if capability_id == "wait":
        required = float(parameters["duration_s"])
        return elapsed_sim_s + 1e-9 >= required
    raise ValueError(f"Unsupported postcondition capability: {capability_id}")


def grasp_is_closed(sample, command_target, grasp_sample):
    """Require an unchanged closed grasp relative to the pickup grasp sample."""
    if grasp_sample is None:
        return False
    target = _finite_vector(command_target, 8, "control target")
    if target[-1] > -0.5:
        return False
    grasp_position = _finite_vector(
        grasp_sample["robot"]["gripper_position"], 2, "grasp gripper position")
    current_position = _finite_vector(
        sample["robot"]["gripper_position"], 2, "current gripper position")
    return max(
        abs(grasp_position[index] - current_position[index]) for index in range(2)
    ) <= GRASP_STABILITY_TOLERANCE_M


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
