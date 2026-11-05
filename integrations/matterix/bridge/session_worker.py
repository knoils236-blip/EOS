"""Persistent Matterix simulation session. Run only with the Matterix interpreter."""

from __future__ import annotations

import argparse
import copy
import ctypes
import importlib.metadata
import importlib.util
import inspect
import math
import os
from pathlib import Path
import signal
import sys
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bridge.capabilities import config_version, get_capability, validate_task
from bridge.common import dumps, now, read_json, request_id, validate_session, write_json
from bridge.environment import create_local_environment, grid_asset_provenance
from bridge.beaker_motion import (
    ATOMIC_BEAKER_CAPABILITIES,
    BeakerMotionState,
    BeakerTaskMonitor,
    atomic_ledger_failure,
)
from bridge.beaker_workspace import load_beaker_workspace
from bridge.session_actions import (
    LiftCheck,
    ReturnCheck,
    action_output,
    configure_local_table_asset,
    configure_pickup_cfg,
    control_target,
    grasp_is_closed,
    load_runtime_workflow,
    postcondition_satisfied,
    seed_state_machine,
    sha256_file,
)


STOP_REASON = None
SAMPLE_INTERVAL_S = 0.5
SETTLE_S = 1.0
SEED = 1
INTEGRATION_ROOT = Path(__file__).resolve().parent.parent
TABLE_ASSET_DIR = INTEGRATION_ROOT / "assets/SeattleLabTable"
TABLE_ASSET_USD = TABLE_ASSET_DIR / "table_instanceable.usd"
TABLE_ASSET_MANIFEST = TABLE_ASSET_DIR / "manifest.json"


def stop(signum, _frame):
    global STOP_REASON
    STOP_REASON = signal.Signals(signum).name


def _validate_session_config(session_dir):
    request = validate_session(read_json(session_dir / "request.json"))
    config = read_json(session_dir / "config.json")
    if not isinstance(config, dict) or set(config) != {
            "matterix_root", "matterix_python", "wall_timeout_s"}:
        raise ValueError("Invalid session worker config")
    root = Path(config["matterix_root"]).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Matterix root does not exist: {root}")
    if not isinstance(config["matterix_python"], str) or not config["matterix_python"]:
        raise ValueError("matterix_python must be a non-empty path")
    timeout = config["wall_timeout_s"]
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or not 0 < timeout <= 600):
        raise ValueError("wall_timeout_s must be in (0, 600]")
    config = copy.deepcopy(config)
    config["matterix_root"] = str(root)
    return request, config


def _read_command(path):
    if not path.is_file():
        return None
    command = read_json(path)
    if not isinstance(command, dict):
        raise ValueError("command.json must be an object")
    operation = command.get("operation")
    fields = {"command_id", "operation"}
    if operation == "run":
        fields |= {"request_id", "run_dir"}
    if set(command) != fields:
        raise ValueError(f"Invalid fields for {operation!r} command")
    request_id(command["command_id"])
    if operation not in {"run", "reset", "end"}:
        raise ValueError(f"Unknown session command: {operation}")
    if operation == "run":
        request_id(command["request_id"])
        run_dir = Path(command["run_dir"])
        if not run_dir.is_absolute() or not run_dir.is_dir():
            raise ValueError("run command needs an existing absolute run_dir")
        command["run_dir"] = str(run_dir.resolve())
    return command


def _cancel_reason(run_dir):
    path = run_dir / "cancel.json"
    if not path.is_file():
        return None
    value = read_json(path)
    if not isinstance(value, dict) or value.get("reason") not in {"cancelled", "timed_out"}:
        raise ValueError("cancel.json must contain cancelled or timed_out")
    return value["reason"]


class SessionWorker:
    def __init__(self, session_dir, request, config):
        self.directory = session_dir
        self.request = request
        self.config = config
        self.runtime_path = session_dir / "runtime.json"
        self.command_id = None
        self.session_sim_s = 0.0
        self.generation = 0
        self.latest_sample = None
        self.last_pickup = None
        self.beaker_motion = None
        self.control = None
        self.app = None
        self.env = None
        self.sm = None
        self.torch = None
        self.mdp = None
        self.cfg = None
        self.action_space = None
        self.runtime = {
            "schema_version": 1,
            "status": "starting",
            "session_id": request["session_id"],
            "experiment_id": request["experiment_id"],
            "source": "simulation",
            "sim_time_s": self.session_sim_s,
            "generation": self.generation,
            "command_id": self.command_id,
            "latest_sample": self.latest_sample,
            "control_target": self.control,
            "last_pickup": self.last_pickup,
            "beaker_motion": self.beaker_motion,
            "error": None,
            "updated_at": now(),
        }
        self.publish()

    def publish(self, **changes):
        self.runtime.update(changes)
        self.runtime.update(
            session_id=self.request["session_id"],
            experiment_id=self.request["experiment_id"],
            source="simulation",
            sim_time_s=self.session_sim_s,
            generation=self.generation,
            command_id=self.command_id,
            latest_sample=self.latest_sample,
            control_target=self.control,
            last_pickup=self.last_pickup,
            beaker_motion=(
                None if self.beaker_motion is None else self.beaker_motion.to_dict()),
            updated_at=now(),
        )
        if self.latest_sample is not None:
            self.runtime["scene_summary"] = {
                key: self.latest_sample[key]
                for key in ("scene_id", "sim_time_s", "robot", "beaker", "station", "semantics")
                if key in self.latest_sample
            }
        write_json(self.runtime_path, self.runtime)

    def start(self):
        from isaaclab.app import AppLauncher

        parser = argparse.ArgumentParser()
        AppLauncher.add_app_launcher_args(parser)
        launch_arguments = [
            "--device", "cuda:0",
            "--viz", "none",
            "--enable_cameras",
            f"--kit_args=--/log/file={self.directory / 'kit.log'}",
        ]
        args = parser.parse_args(launch_arguments)
        launcher = AppLauncher(args)
        self.app = launcher.app
        if STOP_REASON:
            raise InterruptedError(STOP_REASON)

        self.torch = __import__("torch")
        from matterix.envs import mdp
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

        self.mdp = mdp
        task_file = (
            Path(self.config["matterix_root"])
            / "source/matterix_tasks/matterix_tasks/test_dev_tasks/test_franka_beaker_lift.py"
        )
        task_spec = importlib.util.spec_from_file_location("session_beaker_task", task_file)
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
        cfg.seed = SEED
        cfg.episode_length_s = 30.0
        cfg.recorders = None
        cfg.record_path = str(self.directory / "dataset.hdf5")
        cfg.ui_window_class_type = None
        cfg.viewer.eye = (1.6, 1.4, 1.4)
        cfg.viewer.lookat = (0.4, 0.0, 0.4)
        cfg.viewer.resolution = (960, 540)
        cfg.sim.render_interval = cfg.decimation
        cfg.events.randomize_beaker_position = None
        cfg.articulated_assets["robot"].event_terms.pop(
            "randomize_franka_joint_state", None)
        cfg.terminations.time_out = None
        active_terminations = {
            name: value for name, value in vars(cfg.terminations).items() if value is not None}
        if active_terminations:
            raise ValueError(
                "Session worker cannot disable all automatic terminations: "
                f"{sorted(active_terminations)}"
            )
        if not TABLE_ASSET_USD.is_file() or not TABLE_ASSET_MANIFEST.is_file():
            raise FileNotFoundError(
                f"SeattleLabTable local asset cache is incomplete: {TABLE_ASSET_DIR}")
        configure_local_table_asset(cfg, TABLE_ASSET_USD)
        self.cfg = cfg

        provenance = self._provenance(task_file)
        write_json(self.directory / "session_provenance.json", provenance)

        self.env = create_local_environment(cfg, render_mode="rgb_array")
        from isaaclab_physx.renderers.kit_viewport_utils import set_kit_renderer_camera_view
        self.env.sim.set_setting("/isaaclab/video/enabled", True)
        set_kit_renderer_camera_view(
            cfg.viewer.eye, cfg.viewer.lookat, cfg.viewer.cam_prim_path)
        self.sm = StateMachine(
            num_envs=1, dt=self.env.step_dt, device=self.env.device,
            status_interval_steps=0)
        self.action_space = FRANKA_IK_ACTION_SPACE
        self._reset_scene(initial=True)

    def _provenance(self, task_file):
        from matterix.envs import mdp
        from matterix_sm import StateMachine
        from isaaclab.scene import InteractiveSceneCfg
        from isaacsim.robot_motion.motion_generation import (
            LulaKinematicsSolver,
            interface_config_loader,
        )

        package_versions = {}
        for name in ("isaacsim", "isaaclab", "torch", "matterix", "matterix_sm"):
            package_versions[name] = importlib.metadata.version(name)
        source_paths = [
            Path(__file__),
            Path(__file__).with_name("beaker_ik.py"),
            Path(__file__).with_name("beaker_motion.py"),
            Path(__file__).with_name("beaker_workspace.py"),
            Path(__file__).with_name("environment.py"),
            Path(__file__).with_name("session_actions.py"),
            Path(inspect.getfile(LulaKinematicsSolver)),
            Path(inspect.getfile(StateMachine)),
            Path(task_file),
            Path(self.config["matterix_root"]) / "source/matterix/matterix/envs/matterix_base_env.py",
            Path(self.config["matterix_root"]) / "scripts/workflow_yaml.py",
        ]
        source_paths.extend(
            Path(value)
            for value in interface_config_loader.load_supported_lula_kinematics_solver_config(
                "Franka").values()
        )
        asset_files = sorted(
            path for path in TABLE_ASSET_DIR.rglob("*") if path.is_file())
        return {
            "schema_version": 1,
            "source": "simulation",
            "python": sys.version,
            "packages": package_versions,
            "matterix_sources": {
                str(path): sha256_file(path) for path in source_paths
            },
            "runtime_assets": {
                "table_usd": str(TABLE_ASSET_USD),
                "manifest": str(TABLE_ASSET_MANIFEST),
                "manifest_sha256": sha256_file(TABLE_ASSET_MANIFEST),
                "files_sha256": {
                    str(path.relative_to(TABLE_ASSET_DIR)): sha256_file(path)
                    for path in asset_files
                },
                "grid": grid_asset_provenance(),
            },
            "settings": {
                "scene_id": self.request["scene_id"],
                "num_envs": 1,
                "seed": SEED,
                "randomization": False,
                "automatic_terminations": False,
                "settle_s": SETTLE_S,
                "lift_orientation_tolerance_rad": 0.1,
                "franka_ik": "Lula with continuous joint commands at 0.5 rad/s",
                "grasp_arm_damping": 80.0,
                "motion_arm_damping": 20.0,
                "camera_boot": True,
                "video_resolution": [960, 540],
            },
            "interactive_scene": inspect.isclass(InteractiveSceneCfg),
            "mdp_module": mdp.__name__,
        }

    def _vector(self, value):
        value = value.torch if hasattr(value, "torch") else value
        values = value[0].detach().cpu().tolist()
        if not all(isinstance(item, (int, float)) and math.isfinite(item) for item in values):
            raise ValueError("Non-finite simulator observation")
        return values

    def _semantics(self):
        manager = self.env.semantic_manager
        states = {}
        for name, semantic in manager.full_semantic_states.items():
            states[name] = self._vector(semantic.get_state())
        transitions = {}
        for name, semantic in manager.full_semantic_state_transitions.items():
            value = semantic.get_value()
            transitions[name] = None if value is None else self._vector(value)
        return {
            "states": states,
            "transitions": transitions,
            "predicates": sorted(manager.full_semantic_predicates),
        }

    def snapshot(self, stage, request_id_value=None, command_target=None):
        robot = self.env.scene["robot"]
        return {
            "schema_version": 1,
            "source": "simulation",
            "request_id": request_id_value,
            "sim_time_s": self.session_sim_s,
            "wall_time_utc": now(),
            "stage": stage,
            "units": {
                "position": "m",
                "orientation": "quaternion_xyzw",
                "linear_velocity": "m/s",
                "angular_velocity": "rad/s",
                "arm_joint_position": "rad",
                "arm_joint_velocity": "rad/s",
                "gripper_joint_position": "m",
                "time": "s",
                "frame": "Isaac world",
            },
            "robot": {
                "joint_names": list(robot.joint_names),
                "joint_position": self._vector(self.mdp.joint_pos(self.env, "robot")),
                "joint_velocity": self._vector(self.mdp.joint_vel(self.env, "robot")),
                "ee_position_w": self._vector(self.mdp.ee_world_pos(self.env, "robot")),
                "ee_quaternion_xyzw": self._vector(self.mdp.ee_world_quat(self.env, "robot")),
                "gripper_position": self._vector(self.mdp.gripper_pos(self.env, "robot")),
                "control_target": copy.deepcopy(command_target),
            },
            "beaker": {
                "position_w": self._vector(self.mdp.object_world_pos(self.env, "beaker")),
                "quaternion_xyzw": self._vector(self.mdp.object_world_quat(self.env, "beaker")),
                "linear_velocity_w": self._vector(self.mdp.object_lin_vel(self.env, "beaker")),
                "angular_velocity_w": self._vector(self.mdp.object_ang_vel(self.env, "beaker")),
            },
            "semantics": self._semantics(),
        }

    def _assert_no_auto_reset(self, terminated, truncated):
        if bool(terminated.any()) or bool(truncated.any()):
            raise RuntimeError("environment_terminated_before_task_completion")

    def _step_control(self, target):
        tensor = self.torch.tensor(
            [target], dtype=self.torch.float32, device=self.env.device)
        observations, _, terminated, truncated, _ = self.env.step(tensor)
        self._assert_no_auto_reset(terminated, truncated)
        self.session_sim_s += self.env.step_dt
        return observations

    def _reset_scene(self, initial=False):
        global STOP_REASON
        if STOP_REASON:
            raise InterruptedError(STOP_REASON)
        observations, _ = self.env.reset(seed=SEED)
        initial_sample = self.snapshot("settling", command_target=None)
        self.control = control_target(initial_sample, 1.0)
        settle_steps = max(1, round(SETTLE_S / self.env.step_dt))
        with self.torch.inference_mode():
            for _ in range(settle_steps):
                if STOP_REASON:
                    raise InterruptedError(STOP_REASON)
                observations = self._step_control(self.control)
        sample = self.snapshot("settled")
        self.latest_sample = sample
        self.control = control_target(sample, 1.0)
        self.sm.update_scene_data_from_obs(observations)
        if not initial:
            self.generation += 1
            self.last_pickup = None
            self.beaker_motion = None
        self.publish(status="idle", latest_sample=sample, error=None)
        return observations

    def reset(self):
        self.publish(status="starting")
        observations = self._reset_scene(initial=False)
        self.publish(status="idle", latest_sample=self.latest_sample, error=None)
        return observations

    def _task_provenance(self, task, workflow_path):
        value = read_json(self.directory / "session_provenance.json")
        value.update(
            request_id=task["request_id"],
            capability_id=task["capability_id"],
            capability_version=task["capability_version"],
            config_version=task["config_version"],
            parameters=task["parameters"],
            workflow_name=task["workflow_name"],
            workflow_yaml=str(workflow_path),
            workflow_sha256=sha256_file(workflow_path),
            worker_sha256=sha256_file(Path(__file__)),
            actions_sha256=sha256_file(Path(__file__).with_name("session_actions.py")),
            beaker_motion_sha256=sha256_file(
                Path(__file__).with_name("beaker_motion.py")),
            beaker_workspace_module_sha256=sha256_file(
                Path(__file__).with_name("beaker_workspace.py")),
        )
        workspace_path = Path(__file__).with_name("beaker_workspace.json")
        value["beaker_workspace"] = {
            "path": str(workspace_path),
            "sha256": sha256_file(workspace_path) if workspace_path.is_file() else None,
        }
        return value

    def _build_actions(self, spec, wait_padding_s=0.0):
        from matterix_sm import (
            CloseGripperCfg,
            MoveRelativeCfg,
            MoveToPoseCfg,
            OpenGripperCfg,
            PickObjectCfg,
            WaitCfg,
        )

        actions = []
        for index, step in enumerate(spec["actions"], start=1):
            kind = step["type"]
            if kind == "pick":
                action = PickObjectCfg(
                    agent_assets="robot",
                    object=step["object"],
                    post_grasp_offset=(0.0, 0.0, 0.0),
                    action_space_info=self.action_space,
                )
                configure_pickup_cfg(
                    action,
                    step["lift"][2],
                    step["timeout"],
                    step.get("orientation_tolerance", 0.1),
                )
                if spec["name"] == "grasp_beaker":
                    if not action.sub_actions or "MoveRelativeCfg" not in type(
                            action.sub_actions[-1]).__name__:
                        raise ValueError("grasp_beaker PickObjectCfg must end in relative lift")
                    action.sub_actions.pop()
                actions.append(action)
            elif kind == "wait":
                actions.append(WaitCfg(duration=float(step["seconds"]) + wait_padding_s))
            elif kind == "move_to_pose":
                position = self.torch.tensor(
                    [step["position_w"]], dtype=self.torch.float32,
                    device=self.env.device).repeat(1, 1)
                w, x, y, z = step["quaternion_wxyz"]
                orientation = self.torch.tensor(
                    [[x, y, z, w]], dtype=self.torch.float32,
                    device=self.env.device).repeat(1, 1)
                actions.append(MoveToPoseCfg(
                    agent_assets="robot",
                    target_positions=position,
                    target_orientations=orientation,
                    timeout=float(step["timeout"]),
                    position_threshold=float(step.get("position_tolerance", 0.01)),
                    orientation_threshold=float(step.get("orientation_tolerance", 0.05)),
                    action_space_info=self.action_space,
                ))
            elif kind == "relative":
                actions.append(MoveRelativeCfg(
                    agent_assets="robot",
                    position_offset=tuple(float(value) for value in step["move"]),
                    timeout=float(step["timeout"]),
                    position_threshold=float(step.get("position_tolerance", 0.01)),
                    orientation_threshold=float(step.get("orientation_tolerance", 0.05)),
                    action_space_info=self.action_space,
                ))
            elif kind in {"open", "close"}:
                config_type = OpenGripperCfg if kind == "open" else CloseGripperCfg
                actions.append(config_type(
                    agent_assets="robot",
                    duration=float(step["seconds"]),
                    timeout=float(step["timeout"]),
                    action_space_info=self.action_space,
                ))
            else:
                raise ValueError(f"Unsupported session action at index {index}: {kind}")
        return actions

    def run_task(self, command):
        run_dir = Path(command["run_dir"])
        task = read_json(run_dir / "request.json")
        task_id = command["request_id"]
        if not isinstance(task, dict) or task.get("request_id") != task_id:
            raise ValueError("run command and task request disagree")
        required = {
            "request_id", "session_id", "experiment_id", "capability_id",
            "capability_version", "config_version", "parameters", "scene_id",
            "workflow_yaml", "workflow_name",
        }
        if set(task) != required:
            raise ValueError("Invalid canonical session task request")
        validated = validate_task({
            name: task[name] for name in
            ("request_id", "session_id", "capability_id", "parameters")
        })
        if (validated["request_id"] != task_id
                or validated["session_id"] != self.request["session_id"]
                or task["experiment_id"] != self.request["experiment_id"]
                or task["scene_id"] != self.request["scene_id"]):
            raise ValueError("Task identity does not match the active session")
        capability = get_capability(validated["capability_id"])
        if task["capability_version"] != capability["version"]:
            raise ValueError("Task capability version is stale")
        if task["config_version"] != config_version():
            raise ValueError("Task configuration version is stale")
        if (task["workflow_name"] != capability["workflow_name"]
                or Path(task["workflow_yaml"]).resolve()
                != Path(capability["workflow_yaml"]).resolve()):
            raise ValueError("Task workflow does not match its capability")

        task.update(parameters=validated["parameters"])
        parameters = validated["parameters"]
        workflow_path = Path(task["workflow_yaml"])
        if workflow_path.parent != Path(capability["workflow_yaml"]).parent:
            raise ValueError("Task workflow is outside the integration bridge")
        write_json(run_dir / "provenance.json", self._task_provenance(task, workflow_path))

        events = (run_dir / "events.jsonl").open("x", encoding="utf-8", buffering=1)
        samples = (run_dir / "samples.jsonl").open("x", encoding="utf-8", buffering=1)
        result = {
            "schema_version": 1,
            "source": "simulation",
            "request_id": task_id,
            "session_id": task["session_id"],
            "experiment_id": task["experiment_id"],
            "capability_id": task["capability_id"],
            "capability_version": task["capability_version"],
            "config_version": task["config_version"],
            "parameters": copy.deepcopy(parameters),
            "status": "running",
            "error": None,
            "started_at": now(),
            "initial": None,
            "final": None,
            "sample_count": 0,
            "steps": 0,
            "elapsed_sim_s": 0.0,
            "elapsed_wall_s": 0.0,
            "final_steps": [],
            "acceptance": None,
            "lift_check": None,
            "return_check": None,
            "beaker_motion_check": None,
        }
        task_started_wall = time.monotonic()
        task_started_sim = self.session_sim_s
        next_sample = SAMPLE_INTERVAL_S
        video_started = False

        def event(kind, **fields):
            value = {
                "event": kind,
                "request_id": task_id,
                "source": "simulation",
                "wall_time_utc": now(),
                "sim_time_s": self.session_sim_s,
                **fields,
            }
            events.write(dumps(value) + "\n")
            write_json(run_dir / "event.json", value)

        def save_sample(sample):
            samples.write(dumps(sample) + "\n")
            write_json(run_dir / "latest.json", sample)
            self.latest_sample = sample
            self.publish(latest_sample=sample)
            result["sample_count"] += 1

        def finish(status, error=None):
            nonlocal video_started
            pending_cancel = _cancel_reason(run_dir)
            if pending_cancel is not None and status != "interrupted":
                status, error = pending_cancel, f"cancel_reason:{pending_cancel}"
            final_sample = self.snapshot(
                "finished", request_id_value=task_id, command_target=self.control)
            result["final"] = final_sample
            save_sample(final_sample)
            if video_started:
                try:
                    event("encoding_video")
                    partial = run_dir / "simulation.partial.mp4"
                    self.env.save_video(str(partial))
                    if not partial.is_file() or partial.stat().st_size == 0:
                        raise RuntimeError("Isaac Sim did not produce video frames")
                    partial.replace(run_dir / "simulation.mp4")
                    result["video"] = {
                        "filename": "simulation.mp4",
                        "source": "Isaac Sim rendered frames",
                        "width": 960,
                        "height": 540,
                        "fps": round(1 / self.env.step_dt),
                    }
                except Exception as exc:
                    result["video_error"] = f"{type(exc).__name__}: {exc}"
                    if status == "succeeded":
                        status, error = "failed", "video_output_failed"
                    traceback.print_exc()
                video_started = False
            result.update(
                status=status,
                error=error,
                finished_at=now(),
                elapsed_sim_s=self.session_sim_s - task_started_sim,
                sim_time_s=self.session_sim_s - task_started_sim,
                session_sim_time_s=self.session_sim_s,
                elapsed_wall_s=time.monotonic() - task_started_wall,
                steps=result["steps"],
            )
            if status != "succeeded" and self.beaker_motion is not None:
                self.beaker_motion = self.beaker_motion.frozen(error or status)
                result["beaker_motion_state"] = self.beaker_motion.to_dict()
            event("finished", status=status, error=error)
            write_json(run_dir / "result.json", result)
            samples.close()
            events.close()
            if status == "succeeded":
                self.publish(
                    status="idle", active_task_id=None, last_task_id=task_id,
                    last_task_status=status, error=None)
            else:
                self.publish(
                    status=status if status == "interrupted" else "frozen",
                    active_task_id=None, last_task_id=task_id,
                    last_task_status=status, error=error,
                    beaker_motion=(
                        None if self.beaker_motion is None else self.beaker_motion.to_dict()))
            return status

        try:
            self.publish(status="running", active_task_id=task_id, error=None)
            event("starting")
            initial = self.snapshot("initial", request_id_value=task_id, command_target=self.control)
            result["initial"] = initial
            save_sample(initial)
            event("running", parameters=parameters)
            capability_id = task["capability_id"]
            beaker_motion_monitor = None
            motion_candidate = self.beaker_motion

            ledger_failure = atomic_ledger_failure(
                capability_id, self.last_pickup, self.beaker_motion)
            if ledger_failure is not None:
                result["precondition_failed"] = ledger_failure
                return finish("failed", ledger_failure)
            if capability_id == "pickup_beaker" and self.last_pickup is not None:
                precondition = "pickup_requires_returned_beaker_or_reset"
                result["precondition_failed"] = precondition
                return finish("failed", precondition)
            if capability_id == "return_beaker" and self.last_pickup is None:
                precondition = "return_requires_previous_successful_pickup"
                result["precondition_failed"] = precondition
                return finish("failed", precondition)
            if capability_id in ATOMIC_BEAKER_CAPABILITIES:
                workspace = load_beaker_workspace()
                result["beaker_workspace"] = {
                    "source_path": workspace.source_path,
                    "max_lift_m": workspace.max_lift_m,
                    "calibration_status": workspace.calibration_status,
                    "method": workspace.method,
                }
                if self.beaker_motion is None:
                    self.beaker_motion = BeakerMotionState.initial(
                        initial,
                        max_lift_m=workspace.max_lift_m,
                        calibration_status=workspace.calibration_status,
                        method=workspace.method,
                    )
                self.beaker_motion.require_action_allowed(capability_id)
                if capability_id == "lift_beaker":
                    motion_candidate = self.beaker_motion.lifted_candidate(
                        float(parameters["height_m"]))
                elif capability_id == "lower_beaker":
                    motion_candidate = self.beaker_motion.lowered_candidate(
                        float(parameters["height_m"]))
                beaker_motion_monitor = BeakerTaskMonitor(
                    capability_id, self.beaker_motion, initial, parameters)
                precondition = beaker_motion_monitor.initial_failure()
                if precondition is not None:
                    result["precondition_failed"] = precondition
                    return finish("failed", precondition)

            damping = 80.0 if capability_id in {"grasp_beaker", "pickup_beaker"} else 20.0
            self.env.scene["robot"].write_joint_damping_to_sim_index(
                damping=self.torch.full(
                    (1, 7), damping, device=self.env.device),
                joint_ids=list(range(7)),
            )

            if parameters.get("record_video", False) or self.request.get("record_video", False):
                for _ in range(10):
                    frame = self.env.render()
                    if frame is not None and frame.any():
                        break
                else:
                    raise RuntimeError("Isaac Sim renderer did not return a frame")
                self.env.start_recording()
                video_started = True

            return_reference = None
            if capability_id == "return_beaker":
                return_reference = self.last_pickup
            spec = load_runtime_workflow(
                workflow_path,
                task["workflow_name"],
                self.config["matterix_root"],
                parameters=parameters,
                return_reference=return_reference,
                beaker_baseline=self.last_pickup["beaker_baseline_position_w"]
                if return_reference else None,
                beaker_current=initial["beaker"]["position_w"] if return_reference else None,
                ee_start_w=(
                    self.beaker_motion.target_ee_position_w(
                        self.beaker_motion.requested_height_m)
                    if capability_id in {"lift_beaker", "lower_beaker"} else None),
                target_ee_w=(
                    motion_candidate.target_ee_position_w(
                        motion_candidate.requested_height_m)
                    if capability_id in {"lift_beaker", "lower_beaker"} else None),
                target_orientation_xyzw=(
                    motion_candidate.grasp_orientation_xyzw
                    if capability_id in {"lift_beaker", "lower_beaker"} else None),
            )
            write_json(run_dir / "workflow.json", spec)
            actions = self._build_actions(
                spec,
                wait_padding_s=(
                    self.env.step_dt
                    if capability_id == "pickup_beaker"
                    or capability_id in ATOMIC_BEAKER_CAPABILITIES
                    else 0.0),
            )
            self.sm.set_action_sequence(actions)
            self.sm.reset()
            seed_state_machine(self.sm, self.control, self.action_space, self.torch)
            observations = self.env.observation_manager.compute()
            self.sm.update_scene_data_from_obs(observations)

            baseline = (
                self.last_pickup["beaker_baseline_position_w"]
                if return_reference
                else initial["beaker"]["position_w"]
            )
            lift_check = (
                LiftCheck(
                    baseline[2], float(parameters["lift_height_m"]), float(parameters["hold_s"]))
                if capability_id == "pickup_beaker" else None
            )
            return_check = (
                ReturnCheck(
                    baseline,
                    self.last_pickup["gripper_position"],
                    float(parameters["hold_s"]),
                )
                if capability_id == "return_beaker" else None
            )
            grasp_sample = None
            last_action_key = None
            action_sequence_complete = False
            release_gate_passed = False
            hold_action = None
            hold_command_target = None
            status = None
            error = None

            with self.torch.inference_mode():
                while self.app.is_running():
                    if STOP_REASON:
                        status, error = "interrupted", f"signal:{STOP_REASON}"
                        break
                    reason = _cancel_reason(run_dir)
                    if reason is not None:
                        status, error = reason, f"cancel_reason:{reason}"
                        break
                    elapsed = self.session_sim_s - task_started_sim
                    wall_elapsed = time.monotonic() - task_started_wall
                    if elapsed + self.env.step_dt > float(parameters.get("timeout_s", 30.0)) + 1e-9:
                        write_json(run_dir / "cancel.json", {"reason": "timed_out"})
                        status, error = "timed_out", "simulation_timeout"
                        break
                    if wall_elapsed >= float(self.config["wall_timeout_s"]):
                        write_json(run_dir / "cancel.json", {"reason": "timed_out"})
                        status, error = "timed_out", "wall_timeout"
                        break

                    if action_sequence_complete:
                        action = hold_action
                        semantics = None
                        command_target = hold_command_target
                    else:
                        status_before = self.sm.get_status()
                        waiting_for_support = False
                        if (capability_id == "release_beaker" and not release_gate_passed and status_before):
                            action_index = status_before[0]["action_idx"]
                            if (action_index < len(self.sm.actions)
                                    and type(self.sm.actions[action_index]).__name__
                                    == "OpenGripper"):
                                if beaker_motion_monitor.release_ready():
                                    release_gate_passed = True
                                    event("release_support_verified", stable_s=beaker_motion_monitor.support_stable_s)
                                else:
                                    waiting_for_support = True
                        if (capability_id == "pickup_beaker" and grasp_sample is None
                                and status_before):
                            action_index = status_before[0]["action_idx"]
                            if (action_index < len(self.sm.actions)
                                    and type(self.sm.actions[action_index]).__name__
                                    == "MoveRelative"):
                                grasp_sample = self.snapshot(
                                    "grasp_closed", request_id_value=task_id,
                                    command_target=self.control)
                        if waiting_for_support:
                            semantics, command_target = None, self.control
                        else:
                            action, semantics = self.sm.step(observations)
                            command_target = action_output(self.sm)
                    observations, _, terminated, truncated, _ = self.env.step(
                        action, semantic_actions=semantics)
                    self._assert_no_auto_reset(terminated, truncated)
                    self.session_sim_s += self.env.step_dt
                    result["steps"] += 1
                    task_elapsed = self.session_sim_s - task_started_sim
                    sample = self.snapshot(
                        "executing", request_id_value=task_id,
                        command_target=command_target)
                    self.control = command_target

                    sm_status = self.sm.get_status()
                    action_key = [
                        (item["action_idx"], item["status"]) for item in sm_status]
                    if action_key != last_action_key:
                        event("step_changed", steps=sm_status)
                        last_action_key = action_key

                    if lift_check is not None:
                        lift_check.update(sample["beaker"]["position_w"][2], self.env.step_dt)
                    if return_check is not None:
                        return_check.update(sample, self.env.step_dt, command_target)
                    if beaker_motion_monitor is not None:
                        beaker_report = beaker_motion_monitor.update(
                            sample, command_target, self.env.step_dt)
                        result["beaker_motion_check"] = beaker_report
                        if beaker_report["hard_failure"] is not None:
                            status, error = "failed", beaker_report["hard_failure"]
                            break

                    if task_elapsed + 1e-9 >= next_sample:
                        save_sample(sample)
                        next_sample += SAMPLE_INTERVAL_S

                    if not action_sequence_complete:
                        if bool(self.sm.action_sequence_failure.any()):
                            status, error = "failed", "primitive_action_failed_or_timed_out"
                            break
                        if bool(self.sm.action_sequence_success.all()):
                            action_sequence_complete = True
                            hold_action = action
                            hold_command_target = command_target
                            event(
                                "postcondition_hold",
                                command_target=command_target,
                                lift_check=lift_check.report() if lift_check else None,
                                return_check=return_check.report() if return_check else None,
                                beaker_motion_check=(
                                    beaker_motion_monitor.report()
                                    if beaker_motion_monitor is not None else None),
                            )

                    passed = False
                    if action_sequence_complete:
                        if beaker_motion_monitor is not None:
                            passed = beaker_motion_monitor.report()["passed"]
                        else:
                            passed = postcondition_satisfied(
                                capability_id,
                                sample,
                                command_target,
                                parameters,
                                elapsed_sim_s=task_elapsed,
                                lift_check=lift_check,
                                return_check=return_check,
                                grasp_sample=grasp_sample,
                            )
                    if passed:
                        status = "succeeded"
                        break
                else:
                    status, error = "interrupted", "simulation_application_closed"

            if lift_check is not None:
                result["lift_check"] = lift_check.report()
            if return_check is not None:
                result["return_check"] = return_check.report()
            if beaker_motion_monitor is not None:
                result["beaker_motion_check"] = beaker_motion_monitor.report()
            if status == "succeeded":
                acceptance = None
                if capability_id in ATOMIC_BEAKER_CAPABILITIES:
                    acceptance = beaker_motion_monitor.report()
                    if not acceptance["passed"]:
                        status, error = "failed", f"{capability_id}_acceptance_failed"
                elif capability_id == "pickup_beaker":
                    lift_report = lift_check.report()
                    closed = grasp_is_closed(sample, action_output(self.sm), grasp_sample)
                    passed = postcondition_satisfied(
                        capability_id,
                        sample,
                        action_output(self.sm),
                        parameters,
                        elapsed_sim_s=self.session_sim_s - task_started_sim,
                        lift_check=lift_check,
                        grasp_sample=grasp_sample,
                    )
                    acceptance = {
                        "lift": lift_report,
                        "grasp_closed": closed,
                        "passed": passed,
                    }
                    result["lift_check"] = lift_report
                    if not acceptance["passed"]:
                        status, error = "failed", "pickup_acceptance_failed"
                elif capability_id == "return_beaker":
                    acceptance = return_check.report()
                    acceptance["passed"] = postcondition_satisfied(
                        capability_id,
                        sample,
                        action_output(self.sm),
                        parameters,
                        elapsed_sim_s=self.session_sim_s - task_started_sim,
                        return_check=return_check,
                    )
                    result["return_check"] = acceptance
                    if not acceptance["passed"]:
                        status, error = "failed", "return_acceptance_failed"
                elif capability_id == "wait":
                    elapsed = self.session_sim_s - task_started_sim
                    requested = float(parameters["duration_s"])
                    acceptance = {
                        "requested_duration_s": requested,
                        "elapsed_sim_s": elapsed,
                        "passed": postcondition_satisfied(
                            capability_id,
                            sample,
                            action_output(self.sm),
                            parameters,
                            elapsed_sim_s=elapsed,
                        ),
                    }
                    if not acceptance["passed"]:
                        status, error = "failed", "wait_duration_not_reached"
                result["acceptance"] = acceptance

                if status == "succeeded" and capability_id == "grasp_beaker":
                    self.beaker_motion = self.beaker_motion.grasped(sample, task_id)
                    self.last_pickup = {
                        "request_id": task_id,
                        "ee_position_w": sample["robot"]["ee_position_w"],
                        "ee_quaternion_xyzw": sample["robot"]["ee_quaternion_xyzw"],
                        "gripper_position": sample["robot"]["gripper_position"],
                        "beaker_baseline_position_w": list(
                            self.beaker_motion.baseline_position_w),
                        "beaker_position_at_grasp_w": sample["beaker"]["position_w"],
                    }
                    self.publish(
                        last_pickup=self.last_pickup,
                        beaker_motion=self.beaker_motion.to_dict())
                elif status == "succeeded" and capability_id == "lift_beaker":
                    self.beaker_motion = self.beaker_motion.lifted(
                        float(parameters["height_m"]), sample, task_id)
                    self.publish(beaker_motion=self.beaker_motion.to_dict())
                elif status == "succeeded" and capability_id == "lower_beaker":
                    self.beaker_motion = self.beaker_motion.lowered(
                        float(parameters["height_m"]), sample, task_id)
                    self.publish(beaker_motion=self.beaker_motion.to_dict())
                elif status == "succeeded" and capability_id == "release_beaker":
                    self.beaker_motion = self.beaker_motion.released(sample, task_id)
                    self.last_pickup = None
                    self.publish(
                        last_pickup=None,
                        beaker_motion=self.beaker_motion.to_dict())
                elif status == "succeeded" and capability_id == "pickup_beaker":
                    grasp = grasp_sample or sample
                    self.last_pickup = {
                        "request_id": task_id,
                        "ee_position_w": sample["robot"]["ee_position_w"],
                        "ee_quaternion_xyzw": sample["robot"]["ee_quaternion_xyzw"],
                        "gripper_position": grasp["robot"]["gripper_position"],
                        "beaker_baseline_position_w": baseline,
                        "beaker_position_at_grasp_w": grasp["beaker"]["position_w"],
                    }
                    workspace = load_beaker_workspace()
                    if float(parameters["lift_height_m"]) <= workspace.max_lift_m + 1e-12:
                        motion = BeakerMotionState.initial(
                            initial,
                            max_lift_m=workspace.max_lift_m,
                            calibration_status=workspace.calibration_status,
                            method=workspace.method,
                        )
                        motion = motion.grasped(grasp, task_id)
                        self.beaker_motion = motion.lifted(
                            float(parameters["lift_height_m"]), sample, task_id)
                    else:
                        self.beaker_motion = None
                    self.publish(
                        last_pickup=self.last_pickup,
                        beaker_motion=(
                            None if self.beaker_motion is None
                            else self.beaker_motion.to_dict()))
                elif status == "succeeded" and capability_id == "return_beaker":
                    self.last_pickup = None
                    if self.beaker_motion is not None:
                        self.beaker_motion = self.beaker_motion.released(sample, task_id)
                    self.publish(
                        last_pickup=None,
                        beaker_motion=(
                            None if self.beaker_motion is None
                            else self.beaker_motion.to_dict()))

            result["final_steps"] = self.sm.get_status()
            return finish(status, error)
        except InterruptedError as exc:
            return finish("interrupted", f"interrupted:{exc}")
        except Exception as exc:
            traceback.print_exc()
            result["exception"] = f"{type(exc).__name__}: {exc}"
            return finish("failed", result["exception"])

    def end(self):
        self.publish(status="ended", active_task_id=None, error=None)

    def close(self):
        env_error = None
        try:
            if self.env is not None:
                self.env.close()
                self.env = None
        except Exception as exc:
            env_error = exc
            traceback.print_exc()
        finally:
            if self.app is not None:
                self.app.close()
                self.app = None
        if env_error is not None:
            raise env_error


def run(session_dir):
    if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Cannot set parent-death signal")
    expected_parent = int(os.environ.get("BRIDGE_PARENT_PID", os.getppid()))
    if os.getppid() != expected_parent:
        return 1
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    request, config = _validate_session_config(session_dir)
    os.environ["MATTERIX_PATH"] = config["matterix_root"]
    for package in ("matterix", "matterix_sm", "matterix_assets", "matterix_tasks"):
        sys.path.insert(0, str(Path(config["matterix_root"]) / "source" / package))

    if request["scene_id"] == "pipetting_realtime":
        # The script and the station adapter must share the signal-stop state.
        sys.modules.setdefault("bridge.session_worker", sys.modules[__name__])
        from bridge.station_worker import StationSessionWorker
        worker = StationSessionWorker(session_dir, request, config)
    else:
        worker = SessionWorker(session_dir, request, config)
    exit_code = 1
    try:
        worker.start()
        while worker.app.is_running():
            if STOP_REASON:
                worker.publish(status="interrupted", error=f"signal:{STOP_REASON}")
                exit_code = 1
                break
            command = _read_command(session_dir / "command.json")
            if command is None or command["command_id"] == worker.command_id:
                time.sleep(0.05)
                continue
            if request["scene_id"] == "franka_beaker":
                worker.control = worker.control or action_output(worker.sm)
            worker.command_id = command["command_id"]
            worker.publish(command_id=worker.command_id)
            if command["operation"] == "end":
                worker.end()
                exit_code = 0
                break
            if command["operation"] == "reset":
                if worker.runtime["status"] not in {"idle", "frozen"}:
                    raise RuntimeError(
                        f"Cannot reset a session while runtime is {worker.runtime['status']}")
                worker.publish(status="starting")
                worker.reset()
                continue
            if worker.runtime["status"] != "idle":
                raise RuntimeError(
                    f"Cannot run a task while session runtime is {worker.runtime['status']}")
            status = worker.run_task(command)
            exit_code = 0 if status in {"succeeded"} else 1
        if STOP_REASON:
            worker.publish(status="interrupted", error=f"signal:{STOP_REASON}")
    except Exception as exc:
        traceback.print_exc()
        worker.publish(status="interrupted", error=f"fatal:{type(exc).__name__}: {exc}")
    finally:
        worker.close()
    return exit_code


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--session-dir", type=Path, required=True)
    arguments = parser.parse_args()
    raise SystemExit(run(arguments.session_dir.resolve()))
