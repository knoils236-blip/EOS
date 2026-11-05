"""CPU state and physical acceptance checks for Franka beaker motion skills."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math


ATOMIC_BEAKER_CAPABILITIES = frozenset({
    "grasp_beaker",
    "lift_beaker",
    "lower_beaker",
    "release_beaker",
})
HEIGHT_TOLERANCE_M = 0.02
SUPPORT_HEIGHT_TOLERANCE_M = 0.015
FLOOR_TOLERANCE_M = 0.0005  # Contact settling varies by tens of micrometres in this scene.
UPPER_BOUND_TOLERANCE_M = 1e-6
HORIZONTAL_DRIFT_TOLERANCE_M = 0.03
GRASP_OFFSET_TOLERANCE_M = 0.015
GRIPPER_POSITION_TOLERANCE_M = 0.01
HOLD_POSITION_DRIFT_TOLERANCE_M = 0.003
HOLD_ORIENTATION_DRIFT_TOLERANCE_RAD = 0.03
TILT_TOLERANCE_RAD = 0.1
LINEAR_SPEED_TOLERANCE_MPS = 0.03
ANGULAR_SPEED_TOLERANCE_RADPS = 0.15
GRIPPER_CLOSE_DELTA_M = 0.006
GRIPPER_OPEN_DELTA_M = 0.006


def _finite_vector(value, length, name):
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise ValueError(f"{name} must contain {length} numbers")
    result = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
            raise ValueError(f"{name} must contain finite numbers")
        result.append(float(item))
    return result


def _normalized_quaternion(value):
    quaternion = _finite_vector(value, 4, "beaker quaternion_xyzw")
    norm = math.sqrt(sum(component * component for component in quaternion))
    if norm < 1e-12:
        raise ValueError("beaker quaternion_xyzw must have non-zero norm")
    return tuple(component / norm for component in quaternion)


def _quaternion_angle_difference(first, second):
    dot = abs(sum(first[index] * second[index] for index in range(4)))
    return 2.0 * math.acos(min(1.0, max(-1.0, dot)))


def _tilt_rad(quaternion):
    x, y, _z, _w = quaternion
    local_z_in_world_z = 1.0 - 2.0 * (x * x + y * y)
    return math.acos(min(1.0, max(-1.0, local_z_in_world_z)))


def _speed(vector):
    return math.sqrt(sum(component * component for component in vector))


def _sample_metrics(sample, baseline):
    beaker = sample["beaker"]
    position = _finite_vector(beaker["position_w"], 3, "beaker position_w")
    quaternion = _normalized_quaternion(beaker["quaternion_xyzw"])
    linear = _finite_vector(beaker["linear_velocity_w"], 3, "beaker linear_velocity_w")
    angular = _finite_vector(beaker["angular_velocity_w"], 3, "beaker angular_velocity_w")
    ee_position = _finite_vector(
        sample["robot"]["ee_position_w"], 3, "robot ee_position_w")
    ee_quaternion = _normalized_quaternion(
        sample["robot"]["ee_quaternion_xyzw"])
    gripper = _finite_vector(
        sample["robot"]["gripper_position"], 2, "robot gripper_position")
    return {
        "position_w": tuple(position),
        "quaternion_xyzw": quaternion,
        "height_m": position[2] - baseline[2],
        "horizontal_m": math.hypot(position[0] - baseline[0], position[1] - baseline[1]),
        "tilt_rad": _tilt_rad(quaternion),
        "linear_speed_mps": _speed(linear),
        "angular_speed_radps": _speed(angular),
        "ee_position_w": tuple(ee_position),
        "grasp_offset_w": tuple(position[index] - ee_position[index] for index in range(3)),
        "gripper_position": tuple(gripper),
        "gripper_spacing_m": abs(gripper[0] - gripper[1]),
        "ee_quaternion_xyzw": ee_quaternion,
    }


@dataclass(frozen=True)
class BeakerMotionState:
    """Successful cross-task ledger; candidate increments never enter this object early."""

    baseline_position_w: tuple[float, float, float]
    max_lift_m: float
    calibration_status: str
    method: str
    held: bool = False
    requested_height_m: float = 0.0
    actual_height_m: float = 0.0
    accumulated_requested_lift_m: float = 0.0
    grasp_offset_w: tuple[float, float, float] | None = None
    grasp_orientation_xyzw: tuple[float, float, float, float] | None = None
    closed_gripper_position: tuple[float, float] | None = None
    last_request_id: str | None = None
    uncertain: bool = False
    uncertainty_reason: str | None = None

    @classmethod
    def initial(cls, sample, *, max_lift_m, calibration_status, method):
        baseline = _finite_vector(sample["beaker"]["position_w"], 3, "beaker position_w")
        maximum = float(max_lift_m)
        if not math.isfinite(maximum) or maximum <= 0:
            raise ValueError("max_lift_m must be positive and finite")
        return cls(
            baseline_position_w=tuple(baseline),
            max_lift_m=maximum,
            calibration_status=calibration_status,
            method=method,
        )

    def require_action_allowed(self, capability_id):
        if self.uncertain:
            raise ValueError(
                f"beaker_motion_uncertain: {self.uncertainty_reason or 'previous failure'}")
        if capability_id == "grasp_beaker" and self.held:
            raise ValueError("grasp_requires_released_beaker")
        if capability_id in {"lift_beaker", "lower_beaker", "release_beaker"} and not self.held:
            raise ValueError(f"{capability_id}_requires_grasp_beaker")

    def lifted_candidate(self, increment_m):
        increment = _positive_finite(increment_m, "height_m")
        self.require_action_allowed("lift_beaker")
        requested = self.requested_height_m + increment
        accumulated = self.accumulated_requested_lift_m + increment
        if requested > self.max_lift_m + 1e-12:
            raise ValueError("lift_would_exceed_calibrated_height")
        if accumulated > self.max_lift_m + 1e-12:
            raise ValueError("lift_cumulative_travel_exceeds_calibrated_limit")
        return replace(
            self,
            requested_height_m=requested,
            accumulated_requested_lift_m=accumulated,
        )

    def lowered_candidate(self, decrement_m):
        decrement = _positive_finite(decrement_m, "height_m")
        self.require_action_allowed("lower_beaker")
        requested = self.requested_height_m - decrement
        if requested < -1e-12:
            raise ValueError("lower_would_go_below_original_support")
        return replace(self, requested_height_m=max(0.0, requested))

    def grasped(self, sample, request_id):
        self.require_action_allowed("grasp_beaker")
        metrics = _sample_metrics(sample, self.baseline_position_w)
        gripper = metrics["gripper_position"]
        return replace(
            self,
            held=True,
            requested_height_m=0.0,
            actual_height_m=metrics["height_m"],
            accumulated_requested_lift_m=0.0,
            grasp_offset_w=metrics["grasp_offset_w"],
            grasp_orientation_xyzw=metrics["ee_quaternion_xyzw"],
            closed_gripper_position=gripper,
            last_request_id=request_id,
        )

    def lifted(self, increment_m, sample, request_id):
        value = self.lifted_candidate(increment_m)
        metrics = _sample_metrics(sample, self.baseline_position_w)
        return replace(
            value,
            actual_height_m=metrics["height_m"],
            last_request_id=request_id,
        )

    def lowered(self, decrement_m, sample, request_id):
        value = self.lowered_candidate(decrement_m)
        metrics = _sample_metrics(sample, self.baseline_position_w)
        return replace(
            value,
            actual_height_m=metrics["height_m"],
            last_request_id=request_id,
        )

    def released(self, sample, request_id):
        self.require_action_allowed("release_beaker")
        metrics = _sample_metrics(sample, self.baseline_position_w)
        return replace(
            self,
            held=False,
            requested_height_m=0.0,
            actual_height_m=metrics["height_m"],
            accumulated_requested_lift_m=0.0,
            grasp_offset_w=None,
            grasp_orientation_xyzw=None,
            closed_gripper_position=None,
            last_request_id=request_id,
        )

    def target_ee_position_w(self, requested_height_m):
        """Return the absolute EE target implied by the grasp offset and lift ledger."""
        if not self.held or self.grasp_offset_w is None:
            raise ValueError("target pose requires a successful grasp")
        height = float(requested_height_m)
        if not math.isfinite(height) or height < 0:
            raise ValueError("requested height must be non-negative and finite")
        target_beaker = (
            self.baseline_position_w[0],
            self.baseline_position_w[1],
            self.baseline_position_w[2] + height,
        )
        return tuple(
            target_beaker[index] - self.grasp_offset_w[index]
            for index in range(3)
        )

    def frozen(self, reason):
        return replace(
            self,
            uncertain=True,
            uncertainty_reason=str(reason),
        )

    def to_dict(self):
        return {
            "baseline_position_w": list(self.baseline_position_w),
            "max_lift_m": self.max_lift_m,
            "calibration_status": self.calibration_status,
            "method": self.method,
            "held": self.held,
            "requested_height_m": self.requested_height_m,
            "actual_height_m": self.actual_height_m,
            "accumulated_requested_lift_m": self.accumulated_requested_lift_m,
            "grasp_offset_w": (
                None if self.grasp_offset_w is None else list(self.grasp_offset_w)),
            "grasp_orientation_xyzw": (
                None if self.grasp_orientation_xyzw is None
                else list(self.grasp_orientation_xyzw)),
            "closed_gripper_position": (
                None if self.closed_gripper_position is None
                else list(self.closed_gripper_position)),
            "last_request_id": self.last_request_id,
            "uncertain": self.uncertain,
            "uncertainty_reason": self.uncertainty_reason,
        }


def _positive_finite(value, name):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0):
        raise ValueError(f"{name} must be positive and finite")
    return float(value)


def atomic_ledger_failure(capability_id, last_pickup, beaker_motion):
    """Reject atomic skills when a legacy grasp has no trustworthy motion ledger."""
    if (capability_id in ATOMIC_BEAKER_CAPABILITIES
            and last_pickup is not None
            and beaker_motion is None):
        return "beaker_motion_ledger_unavailable_for_legacy_held_beaker"
    return None


class BeakerTaskMonitor:
    """Track continuous physical state and evaluate one atomic skill postcondition."""

    def __init__(self, capability_id, state, initial_sample, parameters):
        if capability_id not in ATOMIC_BEAKER_CAPABILITIES:
            raise ValueError(f"Unsupported atomic beaker capability: {capability_id}")
        self.capability_id = capability_id
        self.state = state
        self.hold_s = float(parameters.get("hold_s", 0.0))
        if not math.isfinite(self.hold_s) or self.hold_s < 0:
            raise ValueError("hold_s must be non-negative and finite")
        self.initial = _sample_metrics(initial_sample, state.baseline_position_w)
        self.minimum_height_m = self.initial["height_m"]
        self.maximum_height_m = self.initial["height_m"]
        self.expected_height_m = state.requested_height_m
        self.requested_delta_m = 0.0
        if capability_id == "lift_beaker":
            self.requested_delta_m = _positive_finite(
                parameters.get("height_m"), "height_m")
            self.expected_height_m = state.requested_height_m + self.requested_delta_m
        elif capability_id == "lower_beaker":
            self.requested_delta_m = -_positive_finite(
                parameters.get("height_m"), "height_m")
            self.expected_height_m = state.requested_height_m + self.requested_delta_m
        self.stable_s = 0.0
        self.support_stable_s = 0.0
        self.last = dict(self.initial)
        self.last_height_error_m = None
        self.last_grasp_offset_drift_m = None
        self.last_gripper_drift_m = None
        self.last_position_drift_m = None
        self.last_orientation_drift_rad = None
        self.position_anchor_w = None
        self.quaternion_anchor_xyzw = None
        self.support_anchor_w = None
        self.support_anchor_quaternion_xyzw = None
        self.hard_failure = None
        self.release_opened = False
        self.grasp_anchor_w = None
        self.gripper_anchor = None
        self.maximum_open_gripper_spacing_m = self.initial["gripper_spacing_m"]

    def initial_failure(self):
        """Return a precondition failure based only on the untouched first sample."""
        height = self.initial["height_m"]
        if height < -FLOOR_TOLERANCE_M:
            return "beaker_below_original_support"
        if height > self.state.max_lift_m + UPPER_BOUND_TOLERANCE_M:
            return "beaker_above_calibrated_limit"
        if (self.capability_id in {"grasp_beaker", "release_beaker"}
                and not self._is_on_support_surface(self.initial)):
            return (
                "grasp_requires_supported_beaker"
                if self.capability_id == "grasp_beaker"
                else "release_requires_supported_beaker")
        return None

    def _grasp_offset_drift(self, metrics):
        reference = (
            self.grasp_anchor_w
            if self.capability_id == "grasp_beaker" else self.state.grasp_offset_w)
        if reference is None:
            return math.inf
        return math.sqrt(sum(
            (metrics["grasp_offset_w"][index] - reference[index]) ** 2
            for index in range(3)
        ))

    def _gripper_drift(self, metrics):
        reference = (
            self.gripper_anchor
            if self.capability_id == "grasp_beaker"
            else self.state.closed_gripper_position)
        if reference is None:
            return math.inf
        return max(
            abs(metrics["gripper_position"][index] - reference[index])
            for index in range(2)
        )

    def _is_on_support_surface(self, metrics):
        return (
            -FLOOR_TOLERANCE_M <= metrics["height_m"] <= SUPPORT_HEIGHT_TOLERANCE_M
            and metrics["tilt_rad"] <= TILT_TOLERANCE_RAD
            and metrics["horizontal_m"] <= HORIZONTAL_DRIFT_TOLERANCE_M
        )

    def _is_supported(self, metrics):
        return (
            self._is_on_support_surface(metrics)
            and metrics["linear_speed_mps"] <= LINEAR_SPEED_TOLERANCE_MPS
            and metrics["angular_speed_radps"] <= ANGULAR_SPEED_TOLERANCE_RADPS
        )

    def _pose_stable(self, metrics, dt):
        if self.position_anchor_w is None:
            self.position_anchor_w = metrics["position_w"]
            self.quaternion_anchor_xyzw = metrics["quaternion_xyzw"]
            position_drift = 0.0
            orientation_drift = 0.0
        else:
            position_drift = math.sqrt(sum(
                (metrics["position_w"][index] - self.position_anchor_w[index]) ** 2
                for index in range(3)
            ))
            orientation_drift = _quaternion_angle_difference(
                metrics["quaternion_xyzw"], self.quaternion_anchor_xyzw)
        stable = (
            position_drift <= HOLD_POSITION_DRIFT_TOLERANCE_M + 1e-9
            and orientation_drift <= HOLD_ORIENTATION_DRIFT_TOLERANCE_RAD + 1e-9
        )
        if stable:
            self.stable_s += dt
        else:
            self.stable_s = 0.0
            self.position_anchor_w = metrics["position_w"]
            self.quaternion_anchor_xyzw = metrics["quaternion_xyzw"]
        self.last_position_drift_m = position_drift
        self.last_orientation_drift_rad = orientation_drift
        return stable, position_drift, orientation_drift

    def _support_stable(self, metrics, dt, supported):
        if not supported:
            self.support_stable_s = 0.0
            self.support_anchor_w = None
            self.support_anchor_quaternion_xyzw = None
            return
        if self.support_anchor_w is None:
            self.support_anchor_w = metrics["position_w"]
            self.support_anchor_quaternion_xyzw = metrics["quaternion_xyzw"]
            position_drift = 0.0
            orientation_drift = 0.0
        else:
            position_drift = math.sqrt(sum(
                (metrics["position_w"][index] - self.support_anchor_w[index]) ** 2
                for index in range(3)
            ))
            orientation_drift = _quaternion_angle_difference(
                metrics["quaternion_xyzw"], self.support_anchor_quaternion_xyzw)
        stable = (
            position_drift <= HOLD_POSITION_DRIFT_TOLERANCE_M + 1e-9
            and orientation_drift <= HOLD_ORIENTATION_DRIFT_TOLERANCE_RAD + 1e-9
        )
        if stable:
            self.support_stable_s += dt
        else:
            self.support_stable_s = 0.0
            self.support_anchor_w = metrics["position_w"]
            self.support_anchor_quaternion_xyzw = metrics["quaternion_xyzw"]

    def update(self, sample, command_target, dt):
        if (isinstance(dt, bool) or not isinstance(dt, (int, float))
                or not math.isfinite(dt) or dt <= 0):
            raise ValueError("beaker motion time step must be positive and finite")
        target = _finite_vector(command_target, 8, "control target")
        metrics = _sample_metrics(sample, self.state.baseline_position_w)
        # TGS contact velocities are noisy; use observed pose changes at the control cadence.
        metrics["raw_linear_speed_mps"] = metrics["linear_speed_mps"]
        metrics["raw_angular_speed_radps"] = metrics["angular_speed_radps"]
        metrics["linear_speed_mps"] = math.dist(metrics["position_w"], self.last["position_w"]) / dt
        metrics["angular_speed_radps"] = _quaternion_angle_difference(
            metrics["quaternion_xyzw"], self.last["quaternion_xyzw"]) / dt
        self.last = metrics
        height = metrics["height_m"]
        self.minimum_height_m = min(self.minimum_height_m, height)
        self.maximum_height_m = max(self.maximum_height_m, height)
        supported = self._is_supported(metrics)
        if self.hard_failure is None and height < -FLOOR_TOLERANCE_M:
            self.hard_failure = "beaker_below_original_support"
        elif (self.hard_failure is None
              and height > self.state.max_lift_m + UPPER_BOUND_TOLERANCE_M):
            self.hard_failure = "beaker_above_calibrated_limit"

        closed = target[-1] <= -0.5
        opened = target[-1] >= 0.5
        if opened:
            self.maximum_open_gripper_spacing_m = max(
                self.maximum_open_gripper_spacing_m,
                metrics["gripper_spacing_m"],
            )
        if (self.capability_id == "grasp_beaker"
                and closed and self.grasp_anchor_w is None):
            self.grasp_anchor_w = metrics["grasp_offset_w"]
            self.gripper_anchor = metrics["gripper_position"]
        grasp_drift = self._grasp_offset_drift(metrics)
        gripper_drift = self._gripper_drift(metrics)
        self.last_grasp_offset_drift_m = grasp_drift
        self.last_gripper_drift_m = gripper_drift
        upright = metrics["tilt_rad"] <= TILT_TOLERANCE_RAD
        slow = (
            metrics["linear_speed_mps"] <= LINEAR_SPEED_TOLERANCE_MPS
            and metrics["angular_speed_radps"] <= ANGULAR_SPEED_TOLERANCE_RADPS
        )

        if self.capability_id == "grasp_beaker":
            gripper_close_delta = (
                self.maximum_open_gripper_spacing_m
                - metrics["gripper_spacing_m"]
            )
            requested_ok = (
                closed
                and gripper_close_delta >= GRIPPER_CLOSE_DELTA_M - 1e-9
                and grasp_drift <= GRASP_OFFSET_TOLERANCE_M + 1e-9
                and gripper_drift <= GRIPPER_POSITION_TOLERANCE_M + 1e-9
                and abs(height) <= SUPPORT_HEIGHT_TOLERANCE_M
                and upright
                and slow
            )
            if requested_ok:
                self._pose_stable(metrics, dt)
            else:
                self.stable_s = 0.0
                self.position_anchor_w = None
                self.quaternion_anchor_xyzw = None
        elif self.capability_id in {"lift_beaker", "lower_beaker"}:
            if (self.hard_failure is None and (
                    not closed
                    or grasp_drift > GRASP_OFFSET_TOLERANCE_M + 1e-9
                    or gripper_drift > GRIPPER_POSITION_TOLERANCE_M + 1e-9)):
                self.hard_failure = "beaker_grasp_lost"
            height_error = abs(height - self.expected_height_m)
            self.last_height_error_m = height_error
            target_ok = (
                height_error <= HEIGHT_TOLERANCE_M + 1e-9
                and upright
                and slow
                and metrics["horizontal_m"] <= HORIZONTAL_DRIFT_TOLERANCE_M
            )
            if target_ok:
                self._pose_stable(metrics, dt)
            else:
                self.stable_s = 0.0
                self.position_anchor_w = None
                self.quaternion_anchor_xyzw = None
        else:
            if opened and not self.release_opened:
                self.release_opened = True
                self.stable_s = 0.0
                self.position_anchor_w = None
                self.quaternion_anchor_xyzw = None
            self._support_stable(metrics, dt, supported)
            if self.release_opened:
                if not self._is_on_support_surface(metrics) and self.hard_failure is None:
                    self.hard_failure = "released_beaker_lost_stable_support"
                if supported and upright and slow:
                    self._pose_stable(metrics, dt)
                else:
                    self.stable_s = 0.0
                    self.position_anchor_w = None
                    self.quaternion_anchor_xyzw = None

        return self.report()

    def _gripper_open_delta(self):
        if self.state.closed_gripper_position is None:
            return None
        closed = self.state.closed_gripper_position
        current = self.last["gripper_position"]
        return abs(current[0] - current[1]) - abs(closed[0] - closed[1])

    def release_ready(self):
        return (
            self.capability_id == "release_beaker"
            and self._is_supported(self.last)
            and self.support_stable_s + 1e-9 >= self.hold_s
        )

    def passed(self):
        if self.hard_failure is not None:
            return False
        if self.stable_s + 1e-9 < self.hold_s:
            return False
        if self.capability_id == "release_beaker":
            open_delta = self._gripper_open_delta()
            return (
                self.release_opened
                and open_delta is not None
                and open_delta >= GRIPPER_OPEN_DELTA_M
                and self._is_supported(self.last)
            )
        return True

    def report(self):
        return {
            "capability_id": self.capability_id,
            "baseline_z_m": self.state.baseline_position_w[2],
            "calibrated_max_lift_m": self.state.max_lift_m,
            "calibration_status": self.state.calibration_status,
            "current_requested_height_m": self.state.requested_height_m,
            "expected_height_m": self.expected_height_m,
            "requested_delta_m": self.requested_delta_m,
            "actual_height_m": self.last["height_m"],
            "minimum_height_m": self.minimum_height_m,
            "maximum_height_m": self.maximum_height_m,
            "measured_delta_m": self.last["height_m"] - self.initial["height_m"],
            "height_error_m": self.last_height_error_m,
            "height_tolerance_m": HEIGHT_TOLERANCE_M,
            "horizontal_drift_m": self.last["horizontal_m"],
            "horizontal_drift_tolerance_m": HORIZONTAL_DRIFT_TOLERANCE_M,
            "grasp_offset_drift_m": self.last_grasp_offset_drift_m,
            "grasp_offset_tolerance_m": GRASP_OFFSET_TOLERANCE_M,
            "gripper_drift_m": self.last_gripper_drift_m,
            "gripper_drift_tolerance_m": GRIPPER_POSITION_TOLERANCE_M,
            "gripper_spacing_m": self.last["gripper_spacing_m"],
            "maximum_open_gripper_spacing_m": self.maximum_open_gripper_spacing_m,
            "gripper_close_delta_m": (
                self.maximum_open_gripper_spacing_m
                - self.last["gripper_spacing_m"]
            ),
            "gripper_close_delta_tolerance_m": GRIPPER_CLOSE_DELTA_M,
            "tilt_rad": self.last["tilt_rad"],
            "tilt_tolerance_rad": TILT_TOLERANCE_RAD,
            "linear_speed_mps": self.last["linear_speed_mps"],
            "angular_speed_radps": self.last["angular_speed_radps"],
            "position_drift_m": self.last_position_drift_m,
            "position_drift_tolerance_m": HOLD_POSITION_DRIFT_TOLERANCE_M,
            "orientation_drift_rad": self.last_orientation_drift_rad,
            "orientation_drift_tolerance_rad": HOLD_ORIENTATION_DRIFT_TOLERANCE_RAD,
            "current_stable_s": self.stable_s,
            "support_stable_s": self.support_stable_s,
            "required_hold_s": self.hold_s,
            "supported": self._is_supported(self.last),
            "release_ready": self.release_ready(),
            "released": self.release_opened and (
                (self._gripper_open_delta() or 0.0) >= GRIPPER_OPEN_DELTA_M),
            "hard_failure": self.hard_failure,
            "passed": self.passed(),
        }
