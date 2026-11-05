"""Persistent physics pipetting-station worker."""

from __future__ import annotations

import argparse
import contextlib
import copy
import dataclasses
import importlib
import importlib.metadata
import importlib.util
import json
import math
import os
import sys
import time
import traceback
import types
from pathlib import Path

from bridge import session_worker as session_worker_module
from bridge import station_skills
from bridge.station_collisions import configure_working_collisions
from bridge.capabilities import config_version, get_capability, validate_task
from bridge.common import dumps, now, read_json, write_json
from bridge.environment import create_local_environment, grid_asset_provenance
from bridge.recording import (
    HEIGHT,
    OVERVIEW_EYE,
    OVERVIEW_TARGET,
    RENDERER,
    VIEW_WIDTH,
    WIDTH,
    VIDEO_SOURCE,
    ShellVisibility,
    StationVideoRecorder,
    StationViews,
    cameras_enabled,
    require_camera_ready,
    task_hide_shell,
    task_recording_requested,
    warm_renderer,
)
from bridge.session_actions import sha256_file
from bridge.session_worker import (
    SAMPLE_INTERVAL_S,
    SEED,
    SessionWorker,
    _cancel_reason,
)
from bridge.station_experiment import PHASES, initial_checks, initial_state
from bridge.station_experiment import WORKFLOWS as FULL_WORKFLOWS
from bridge.station_mixture import WORKFLOWS as MIXTURE_WORKFLOWS

SCENE_ID = "pipetting_realtime"
EXPORT_ROOT = Path("/home/ry/projects/Matterix/PipettingStation_20261031").resolve()
EXPORT_SOURCE_ROOT = EXPORT_ROOT / "source"
EXPORT_ASSET_DIR = EXPORT_ROOT / "assets/PipettingStation_Asset_v4/Asset"
RESET_POLICY = "restore scene and configured simulation ledger; not hardware rollback"
INITIAL_LIQUID_ML = initial_state()["initial_liquid_ml"]
SERVICE_DOOR_JOINTS = frozenset({"service_door_left", "service_door_right"})
WORKFLOW_NAMES = frozenset({
    "take_drop_tip_big_bottle",
    "prepare_small_vial",
    "pipette_xiaoping_to_zhongping1",
    "pipette_zhongping1_tip2",
    "filter_take_syringe",
    "big_bottle_decap_recap",
}) | frozenset(FULL_WORKFLOWS) | frozenset(MIXTURE_WORKFLOWS) | station_skills.WORKFLOWS
CANONICAL_TASK_FIELDS = frozenset({
    "request_id",
    "session_id",
    "experiment_id",
    "capability_id",
    "capability_version",
    "config_version",
    "parameters",
    "scene_id",
    "workflow_yaml",
    "workflow_name",
})


def _installed_version(name):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _package_stub(name, path):
    """Create an import parent without executing the package's aggregate __init__."""
    module = sys.modules.get(name)
    if module is None:
        module = types.ModuleType(name)
        module.__path__ = [str(path)]
        module.__package__ = name
        sys.modules[name] = module
    return module


def _load_station_config() -> type:
    """Load the physics twin, bypassing matterix_tasks.__init__."""
    tasks_root = EXPORT_SOURCE_ROOT / "matterix_tasks/matterix_tasks"
    station_root = tasks_root / "pipetting_station"
    full_name = "matterix_tasks.pipetting_station.pipetting_twin"
    if full_name in sys.modules:
        return sys.modules[full_name].PipettingStationTwinEnvCfg
    _package_stub("matterix_tasks", tasks_root)
    _package_stub("matterix_tasks.pipetting_station", station_root)
    path = station_root / "pipetting_twin.py"
    spec = importlib.util.spec_from_file_location(full_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load pipetting_twin from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(full_name, None)
        raise
    return module.PipettingStationTwinEnvCfg


def _validate_export_layout():
    required = (
        EXPORT_SOURCE_ROOT / "matterix",
        EXPORT_SOURCE_ROOT / "matterix_assets",
        EXPORT_SOURCE_ROOT / "matterix_tasks/matterix_tasks/pipetting_station/pipetting_realtime.py",
        EXPORT_SOURCE_ROOT / "matterix_plc",
        EXPORT_ASSET_DIR / "PipettingStation_Operational.usd",
        EXPORT_ASSET_DIR / "operational_manifest.json",
        EXPORT_ASSET_DIR / "rig_manifest.json",
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Incomplete pipetting export: {missing}")


def _configure_imports(base_root):
    export_paths = [
        EXPORT_SOURCE_ROOT / package
        for package in ("matterix", "matterix_assets", "matterix_tasks", "matterix_plc")
    ]
    base_sm = base_root / "source/matterix_sm"
    if not base_sm.is_dir():
        raise FileNotFoundError(f"Base matterix_sm source is missing: {base_sm}")
    for path in reversed((*export_paths, base_sm)):
        value = str(path)
        if value in sys.path:
            sys.path.remove(value)
        sys.path.insert(0, value)
    os.environ["MATTERIX_PATH"] = str(base_root)
    os.environ["MATTERIX_PIPETTING_BUNDLE"] = str(EXPORT_ASSET_DIR)


def _flags_any(value):
    return bool(value.any()) if hasattr(value, "any") else bool(value)


def _flags_all(value):
    return bool(value.all()) if hasattr(value, "all") else bool(value)


def _json_value(value):
    if hasattr(value, "detach"):
        value = value.detach().cpu().tolist()
    elif hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite station state")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Station state is not JSON serializable: {type(value).__name__}")


# Objects nobody carried stay close to their CAD pose; anything farther moved and is planned where it is
MOVED_FROM_CAD_M = 0.005
# Plan roles Matterix needs before it sends a physical command (executor._resolve_operation_consumables)
_OPERATION_ROLES = {"装小TIP头": {"tip"}, "过滤机构去取过滤头": {"filter"}}
LIQUID_MACRO_SUFFIXES = ("吸液", "吐液")
_OPERATION_ROLE_SUFFIXES = tuple((suffix, {"bottle", "reactor"}) for suffix in LIQUID_MACRO_SUFFIXES)


def _program_steps(node):
    from matterix_plc.actions.program_action import ProgramStepCfg

    if isinstance(node, ProgramStepCfg):
        yield node
    for child in getattr(node, "sub_actions", None) or ():
        yield from _program_steps(child)


class StationSessionWorker(SessionWorker):
    """One Isaac app, one 250 Hz physics station, serial state machines."""

    def __init__(self, session_dir, request, config):
        super().__init__(session_dir, request, config)
        self.station_cfg = None
        self.last_operation_status = None
        self.bottle_clamp_frame = None
        self.completed_phase = 0
        self.held_commands = {}
        self.camera_ready = False
        self.recording_render = None
        self.camera_workflow = ""
        self.camera_frames = read_json(EXPORT_ASSET_DIR / "rig_manifest.json")["frames"]
        self.joint_units = {}
        self.runtime.update(
            scene_id=SCENE_ID,
            execution_mode="physics",
            hardware_calibrated=False,
            reset_policy=RESET_POLICY,
            ledger_reset=False,
        )
        self.publish()

    @staticmethod
    def _validate_station_cfg(cfg) -> None:
        expected_dt = 1.0 / 250.0
        if not math.isclose(
                float(cfg.sim.dt), expected_dt, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError(
                f"Pipetting station requires 250 Hz, got cfg.sim.dt={cfg.sim.dt!r}")
        if cfg.decimation != 1:
            raise ValueError(
                f"Pipetting station requires decimation=1, got {cfg.decimation!r}")
        if cfg.kinematic_mode:
            raise ValueError("Pipetting station requires physics: kinematic_mode=False")
        active_terminations = {
            name: value
            for name, value in vars(cfg.terminations).items()
            if value is not None
        }
        if active_terminations:
            raise ValueError(
                "Pipetting station cannot allow automatic reset terminations: "
                f"{sorted(active_terminations)}")

    @staticmethod
    def _configure_station_actions(cfg) -> tuple[int, ...]:
        from matterix_plc.axes import ACTION_COLUMN_BY_USD_JOINT  # noqa: PLC0415

        terms = cfg.articulated_assets["station"].action_terms
        angular = terms["angular"]
        angular.joint_names = [name for name in angular.joint_names if name not in SERVICE_DOOR_JOINTS]
        # Isaac Lab otherwise lays a term out in articulation order, which differs from these (rig_manifest) lists,
        # so each column below would drive another joint.
        for term in terms.values():
            term.preserve_order = True
        # The upstream PLC keeps its original layout; only workflow joints reach PhysX.
        return tuple(ACTION_COLUMN_BY_USD_JOINT[name] for term in terms.values() for name in term.joint_names)

    @staticmethod
    def _load_joint_units():
        manifest = read_json(EXPORT_ASSET_DIR / "rig_manifest.json")
        operations = read_json(EXPORT_ASSET_DIR / "operational_manifest.json")
        units = {}
        for joint in [*manifest["joints"], *operations["additionalJoints"]]:
            if joint["name"] in SERVICE_DOOR_JOINTS:
                continue
            unit = {"linear": "m", "angular": "rad"}.get(joint["kind"])
            if unit is None:
                raise ValueError(f"Unsupported rig joint kind: {joint['kind']!r}")
            units[joint["name"]] = unit
        return units

    @staticmethod
    def _valid_shell_count(stage, paths):
        return sum(1 for path in paths if stage.GetPrimAtPath(path).IsValid())

    def start(self):
        if self.request["scene_id"] != SCENE_ID:
            raise ValueError(f"StationSessionWorker only supports scene {SCENE_ID}")
        _validate_export_layout()
        self.joint_units = self._load_joint_units()
        base_root = Path(self.config["matterix_root"]).expanduser().resolve()
        _configure_imports(base_root)

        from isaaclab.app import AppLauncher

        parser = argparse.ArgumentParser()
        AppLauncher.add_app_launcher_args(parser)
        args = parser.parse_args([
            "--device", "cuda:0",
            "--viz", "none",
            *(["--enable_cameras"] if cameras_enabled(self.request) else []),
            f"--kit_args=--/log/file={self.directory / 'kit.log'}",
        ])
        self.app = AppLauncher(args).app
        if session_worker_module.STOP_REASON:
            raise InterruptedError(session_worker_module.STOP_REASON)

        self.torch = importlib.import_module("torch")
        StationCfg = _load_station_config()
        from matterix_sm import StateMachine
        from isaaclab.managers import EventTermCfg  # noqa: PLC0415 (requires the initialized Isaac app)

        cfg = StationCfg()
        cfg.sim.device = args.device
        cfg.seed = SEED
        cfg.episode_length_s = 180.0
        cfg.recorders = None
        cfg.ui_window_class_type = None
        cfg.terminations.time_out = None
        if cameras_enabled(self.request):
            # The generic lab camera is too distant to observe this metre-scale station.
            cfg.prepare_for_video_rec(
                fps=25, resolution=(VIEW_WIDTH, HEIGHT), render="default",
                eye=OVERVIEW_EYE, lookat=OVERVIEW_TARGET,
            )
        self._validate_station_cfg(cfg)
        self.station_action_columns = self._configure_station_actions(cfg)
        cfg.articulated_assets["station"].initial_liquid_ml = dict(INITIAL_LIQUID_ML)
        cfg.articulated_assets["station"].event_terms["working_collisions"] = EventTermCfg(
            func=configure_working_collisions, mode="prestartup"
        )
        self.station_cfg = cfg

        write_json(self.directory / "session_provenance.json", self._provenance())
        self.env = create_local_environment(
            cfg, render_mode="rgb_array" if cameras_enabled(self.request) else None)
        self.runtime["collision_profile"] = self.env._station_collision_profile
        if cameras_enabled(self.request):
            from isaaclab_physx.renderers.kit_viewport_utils import set_kit_renderer_camera_view
            self.env.sim.set_setting("/isaaclab/video/enabled", True)
            set_kit_renderer_camera_view(cfg.viewer.eye, cfg.viewer.lookat, cfg.viewer.cam_prim_path)
            self.recording_render = StationViews(
                self.env.render,
                lambda eye, target: set_kit_renderer_camera_view(eye, target, cfg.viewer.cam_prim_path),
                self._camera_focus,
            )
        self.sm = StateMachine(
            num_envs=self.env.num_envs,
            dt=self.env.step_dt,
            device=self.env.device,
            status_interval_steps=0,
        )
        self._reset_scene(initial=True)
        write_json(self.directory / "session_provenance.json", self._provenance())
        if cameras_enabled(self.request):
            warm_renderer(self.recording_render)
            self.camera_ready = True

    def _camera_focus(self) -> tuple[float, float, float]:
        import numpy as np  # noqa: PLC0415

        operations = self._station_driver().operations
        workflow = self.camera_workflow
        if ("syringe" in workflow or "filter" in workflow
                or operations.attached(station_skills.semantics.SYRINGE_BARREL)):
            name = "syringe_tip"
        elif any(mount.get("seated") for mount in operations.tip_mounts.values()):
            name = "pipette_1ml_tip"
        elif (operations.attached(station_skills.semantics.BIG_BOTTLE_CAP)
              or "big_bottle" in workflow or "open_bottle" in workflow or "big_cap" in workflow):
            name = "large_cap_grasp"
        elif (operations.attached(station_skills.semantics.SMALL_VIAL_CAP)
              or "small_vial" in workflow or "small_cap" in workflow or "prepare_vial" in workflow):
            name = "small_cap_grasp"
        elif "tip" in workflow or "aspirate" in workflow or "dispense" in workflow or "transfer" in workflow:
            name = "pipette_1ml_tip"
        else:
            return OVERVIEW_TARGET
        frame = self.camera_frames[name]
        # Link poses are filled by the station driver's first physics tick, which a fresh reset has not run yet
        transform = operations.poses.get(operations.tool_path(frame["body"]))
        if transform is None:
            return OVERVIEW_TARGET
        return tuple((transform @ np.array([*frame["offset"], 1.0]))[:3])

    def _provenance(self):
        source_files = [
            Path(__file__),
            Path(__file__).with_name("station_collisions.py"),
            Path(__file__).with_name("environment.py"),
            EXPORT_SOURCE_ROOT / "matterix/matterix/envs/matterix_base_env.py",
            EXPORT_SOURCE_ROOT / "matterix_tasks/matterix_tasks/pipetting_station/pipetting_realtime.py",
            EXPORT_SOURCE_ROOT / "matterix_tasks/matterix_tasks/pipetting_station/pipetting_twin.py",
            EXPORT_SOURCE_ROOT / "matterix_assets/matterix_assets/equipment/pipetting_station.py",
            EXPORT_SOURCE_ROOT / "matterix_plc/matterix_plc/actions/workflows.py",
            Path(self.config["matterix_root"]) / "source/matterix_sm/matterix_sm/state_machine.py",
        ]
        semantic_helper = Path(__file__).with_name("station_semantics.py")
        if semantic_helper.is_file():
            source_files.append(semantic_helper)
        recording_helper = Path(__file__).with_name("recording.py")
        if recording_helper.is_file():
            source_files.append(recording_helper)
        skills_helper = Path(__file__).with_name("station_skills.py")
        if skills_helper.is_file():
            source_files.append(skills_helper)
        packages = {
            name: _installed_version(name)
            for name in (
                "isaacsim", "isaaclab", "torch", "matterix", "matterix_assets",
                "matterix_tasks", "matterix_plc", "matterix_sm",
            )
        }
        return {
            "schema_version": 1,
            "source": "simulation",
            "python": sys.version,
            "packages": packages,
            "matterix_sources": {
                str(path): sha256_file(path) for path in source_files if path.is_file()
            },
            "runtime_assets": {
                "export_root": str(EXPORT_ROOT),
                "asset_bundle": str(EXPORT_ASSET_DIR),
                "operational_manifest_sha256": sha256_file(
                    EXPORT_ASSET_DIR / "operational_manifest.json"),
                "rig_manifest_sha256": sha256_file(EXPORT_ASSET_DIR / "rig_manifest.json"),
                "grid": grid_asset_provenance(),
                "read_only": True,
            },
            "settings": {
                "scene_id": SCENE_ID,
                "task_id": "Matterix-PipettingStation-Twin-v1",
                "simulation_mode": "physics",
                "hardware_calibrated": False,
                "collision_profile": self.runtime.get("collision_profile", {"mode": "working_area"}),
                "dt": 1.0 / 250.0,
                "decimation": 1,
                "initial_liquid_ml": dict(INITIAL_LIQUID_ML),
                "workflows": sorted(WORKFLOW_NAMES),
                "sample_interval_s": SAMPLE_INTERVAL_S,
                "reset_policy": RESET_POLICY,
                "enable_cameras": cameras_enabled(self.request),
                "record_video": self.request.get("record_video", False),
                "video": {
                    "renderer": RENDERER,
                    "source": VIDEO_SOURCE,
                    "fps": 25,
                    "width": WIDTH,
                    "height": HEIGHT,
                    "views": ["station_overview", "active_tool_closeup"],
                },
            },
        }

    def _semantics(self):
        helper = importlib.import_module("bridge.station_semantics")
        value = helper.snapshot_semantics(self.env)
        if not isinstance(value, dict):
            raise TypeError("snapshot_semantics(env) must return a dict")
        asset = self.env.scene.articulations["station"]
        joint_names = [name for name in asset.joint_names if name not in SERVICE_DOOR_JOINTS]
        missing_units = sorted(set(joint_names) - set(self.joint_units))
        if missing_units:
            raise ValueError(f"rig_manifest.json has no units for joints: {missing_units}")
        joint_positions = dict(zip(
            joint_names,
            asset.data.joint_pos[0, [asset.joint_names.index(name) for name in joint_names]].detach().cpu().tolist(),
            strict=True,
        ))
        value["device_state"]["joint_positions"] = joint_positions
        value["jointsSI"] = joint_positions
        value["sim_time_s"] = self.session_sim_s
        value["experiment_phase"] = self.completed_phase
        operations = self._station_driver().operations
        value["liquid_balance"] = {"air_ml": operations.air_ml, "spilled_ml": operations.spilled_ml}
        value["initial_state_id"] = initial_state()["id"]
        return _json_value(value)

    def _station_state(self):
        cache = getattr(self.env, "_pipetting_operations", {}) or {}
        drivers = cache.get("station") or []
        if not drivers:
            return {}
        driver = drivers[0]
        operations = driver.operations
        return {
            "driver_index": 0,
            "operation_status": self._operation_status(driver),
            "volume_ml": _json_value(dict(operations.volume)),
            "grips": _json_value(dict(operations.grips)),
            "execution_mode": "physics",
            "hardware_calibrated": False,
        }

    @staticmethod
    def _operation_status(driver=None):
        if driver is None:
            return None
        try:
            prim = driver.stage.GetPrimAtPath(driver.root)
            return prim.GetAttribute("operation:status").Get() or ""
        except Exception as exc:
            raise RuntimeError(f"Cannot read station operation status: {exc}") from exc

    def _station_driver(self):
        cache = getattr(self.env, "_pipetting_operations", {}) or {}
        drivers = cache.get("station") or []
        return drivers[0] if drivers else None

    def _track_liquid(self, extrema):
        """Fold the asset ledger of this control step into per-object [min, max] in mL."""
        driver = self._station_driver()
        volumes = getattr(getattr(driver, "operations", None), "volume", None)
        if not isinstance(volumes, dict):
            raise RuntimeError("Station liquid ledger is unavailable")
        for name, value in volumes.items():
            value = float(value)
            low, high = extrema.get(name, (value, value))
            extrema[name] = (min(low, value), max(high, value))

    def _read_bottle_clamp_frame(self):
        driver = self._station_driver()
        if driver is None:
            return None
        operations = driver.operations
        attribute = operations.prim.GetAttribute("operation:rig")
        raw = attribute.Get() if attribute else None
        if not raw:
            return None
        return copy.deepcopy(json.loads(raw)["frames"]["bottle_clamp"])

    def _restore_bottle_clamp_frame(self, frame):
        if frame is None:
            return
        operations = self._station_driver().operations
        attribute = operations.prim.GetAttribute("operation:rig")
        rig = json.loads(attribute.Get())
        rig["frames"]["bottle_clamp"] = copy.deepcopy(frame)
        attribute.Set(json.dumps(rig))

    @staticmethod
    def _rejected_operation(status):
        if status is None or status in ("", "idle"):
            return False
        return not all(
            part == "accepted" or part.partition(": ")[2] == "accepted"
            for part in status.split(" | ")
        )

    def snapshot(self, stage, request_id_value=None, command_target=None):
        station = self._station_state()
        return {
            "schema_version": 1,
            "source": "simulation",
            "scene_id": SCENE_ID,
            "request_id": request_id_value,
            "sim_time_s": self.session_sim_s,
            "wall_time_utc": now(),
            "stage": stage,
            "units": {"time": "s", "liquid_volume": "mL", "joints": self.joint_units},
            "semantics": self._semantics(),
            "station": station,
        }

    def _reset_semantic_ledger(self):
        manager = getattr(self.env, "semantic_manager", None)
        if manager is None:
            raise RuntimeError("Station environment has no semantic_manager")
        for semantic in manager.full_semantic_states.values():
            semantic_type = type(semantic).__name__
            if semantic_type in {"PipetteVolume", "SyringeVolume"}:
                semantic.redeclare(float(semantic.cfg.initial_state))
                semantic.rate_of_change.zero_()
            elif semantic_type == "VialVolumes":
                volumes = getattr(semantic, "volumes", None)
                if isinstance(volumes, dict):
                    volumes.clear()
                rates = getattr(semantic, "_rates", None)
                if isinstance(rates, dict):
                    rates.clear()
        for predicate in manager.full_semantic_predicates.values():
            value = getattr(predicate, "value", None)
            if isinstance(value, dict):
                value.clear()
            elif type(predicate).__name__ == "StationOperation":
                predicate.value = None
                self._clear_station_operation_state(predicate)
            elif hasattr(predicate, "value"):
                predicate.value = False

    def _clear_station_operation_history(self):
        manager = getattr(self.env, "semantic_manager", None)
        if manager is None:
            raise RuntimeError("Station environment has no semantic_manager")
        for predicate in manager.full_semantic_predicates.values():
            if type(predicate).__name__ == "StationOperation":
                history = getattr(predicate, "history", None)
                if isinstance(history, list):
                    history.clear()

    @staticmethod
    def _clear_station_operation_state(predicate):
        history = getattr(predicate, "history", None)
        if isinstance(history, list):
            history.clear()
        last_status = getattr(predicate, "_last_status", None)
        if isinstance(last_status, dict):
            last_status.clear()

    def _reset_scene(self, initial=False):
        if session_worker_module.STOP_REASON:
            raise InterruptedError(session_worker_module.STOP_REASON)
        with self._inference_context():
            bottle_clamp_before_reset = self._read_bottle_clamp_frame()
            observations, _ = self.env.reset(seed=SEED)
            if self.bottle_clamp_frame is None:
                self.bottle_clamp_frame = (
                    bottle_clamp_before_reset or self._read_bottle_clamp_frame())
            # shortcut: preserve calibration until the export reset stops accumulating its offset.
            self._restore_bottle_clamp_frame(self.bottle_clamp_frame)
            self._reset_semantic_ledger()
            # Reset-time rack latch events belong to initialization, not the next experiment.
            self._station_driver().operations.history.clear()
            self._station_driver().operations.prim.GetAttribute("operation:command").Set("")
            self._station_driver().operations.prim.GetAttribute("operation:status").Set("idle")
            self.completed_phase = 0
            self.held_commands = {}
            self.camera_workflow = ""
            self.last_operation_status = None
            if self.sm is not None:
                self.sm.set_action_sequence([])
                self.sm.reset()
            sample = self.snapshot("settled")
            reset_acceptance = initial_checks(sample["semantics"])
            if not reset_acceptance["passed"]:
                write_json(self.directory / "reset_acceptance.json", reset_acceptance)
                raise RuntimeError("Station initial-state verification failed")
            self.latest_sample = sample
            if not initial:
                self.generation += 1
        self.publish(
            status="idle",
            latest_sample=sample,
            error=None,
            generation=self.generation,
            ledger_reset=True,
            reset_policy=RESET_POLICY,
            initial_state=initial_state(),
            reset_acceptance=reset_acceptance,
        )
        return observations

    def reset(self):
        self.publish(status="starting", ledger_reset=False)
        try:
            observations = self._reset_scene(initial=False)
        except Exception as exc:
            self.publish(status="frozen", error=f"reset_failed:{type(exc).__name__}: {exc}")
            raise
        self.publish(status="idle", latest_sample=self.latest_sample, error=None)
        return observations

    def _load_workflow(self, workflow_name, parameters=None):
        workflow = self._build_workflow(workflow_name, parameters)
        # Matterix plans every step when the workflow loads, finding targets by their CAD slots; liquid steps must
        # find a container that earlier tasks carried elsewhere (a bottle at an opening station) where it is now
        self._use_live_positions(workflow)
        return workflow

    def _seed_station_commands(self) -> None:
        from matterix_plc.axes import ACTION_COLUMN_BY_USD_JOINT  # noqa: PLC0415

        asset = self.env.scene.articulations["station"]
        names = sorted(ACTION_COLUMN_BY_USD_JOINT, key=ACTION_COLUMN_BY_USD_JOINT.get)
        indices = [asset.joint_names.index(name) for name in names]
        # Wait has no agent output; hold measured positions until an action supplies a target.
        self.sm.action_dict["station"] = asset.data.joint_pos[:, indices].clone()
        self.sm.action_dict.update(self.held_commands)

    def _step_station(self, action, *, semantic_actions) -> tuple:
        action = action[:, self.station_action_columns]
        self.control = _json_value(action)
        return self.env.step(action, semantic_actions=semantic_actions)

    def _build_workflow(self, workflow_name, parameters=None):
        if workflow_name in station_skills.WORKFLOWS:
            return station_skills.build_workflow(workflow_name, parameters)
        workflows = importlib.import_module("matterix_plc.actions.workflows")
        if workflow_name == "mixture_close":
            return workflows.full_station().sub_actions[6]
        if workflow_name in {"mixture_stock", "mixture_diluent"}:
            stock = workflow_name == "mixture_stock"
            transfer = workflows.PipetteTransferCfg(
                source="小瓶区" if stock else "大开盖区", target="中瓶区1",
                volume=parameters["volume_ul"], tip_index=1 if stock else 2)
            suffix = [] if stock else list(workflows.big_bottle_decap_recap.sub_actions[3:])
            return workflows.workflow(workflow_name, workflows.SettleCfg(), transfer, *suffix)
        if workflow_name in FULL_WORKFLOWS:
            phase = FULL_WORKFLOWS[workflow_name]
            workflow = workflows.full_station().sub_actions[phase - 1]
            if workflow.description != PHASES[phase - 1][1]:
                raise RuntimeError("Exported full-station phase changed")
            return station_skills.apply_workflow_parameters(workflow, parameters)
        if workflow_name == "pipette_xiaoping_to_zhongping1":
            exported = workflows.WORKFLOWS[workflow_name]
            settle, transfer = exported.sub_actions
            if (type(transfer).__name__ != "PipetteTransferCfg"
                    or (transfer.source, transfer.target, transfer.volume, transfer.tip_index)
                    != ("小瓶区", "中瓶区1", 100.0, 1)):
                raise RuntimeError("Exported pipette_xiaoping_to_zhongping1 changed")
            return workflows.workflow(
                exported.description,
                settle,
                dataclasses.replace(transfer, volume=float(parameters["volume_ul"])),
            )
        if workflow_name == "prepare_small_vial":
            return workflows.full_station().sub_actions[1]
        if workflow_name == "big_bottle_decap_recap":
            return workflows.big_bottle_decap_recap
        if workflow_name == "pipette_zhongping1_tip2":
            return self._zhongping1_tip2(workflows, float(parameters["volume_ul"]))
        return station_skills.apply_workflow_parameters(workflows.WORKFLOWS[workflow_name], parameters)

    def _live_consumables(self):
        """Consumables at their current poses: carried objects where they are, all others exactly as in CAD."""
        from matterix_plc.object_frames import ConsumableIndex

        ops = self._station_driver().operations
        items = dict(ConsumableIndex.from_bundle()._by_name)  # the export offers no public iterator
        for key in ops.objects:
            if key in items:
                try:
                    pose = ops.pose(key)
                except KeyError:
                    # Only bodies the stage simulates have measured poses (none before the first step); the
                    # planner keeps using the CAD slot for the rest, as it always did
                    continue
                x, y, z = (float(value) for value in pose[:3, 3])
                item = items[key]
                if math.dist((x, y, z), (item.x, item.y, item.z)) > MOVED_FROM_CAD_M:
                    items[key] = dataclasses.replace(item, x=x, y=y, z=z)
        return ConsumableIndex(items)

    def _use_live_positions(self, workflow):
        """
        Only aspirate/dispense steps target a container where it is. Pick, place, decap and TIP steps target a slot,
        which only the CAD index knows once its bottle was carried away (placing it back must find the home slot).
        """
        steps = [node for node in _program_steps(workflow) if node.macro.endswith(LIQUID_MACRO_SUFFIXES)]
        if steps:
            live = self._live_consumables()
            for node in steps:
                node.consumables = live

    @staticmethod
    def _unresolved_targets(workflow):
        """
        Failed checks for steps whose target the planner cannot find. Matterix then only books a ledger event and
        moves anyway, so the task would end in a postcondition failure with no liquid moved; refuse it up front.
        """
        from matterix_plc.executor import plan_macro

        checks, seen = [], set()
        for node in _program_steps(workflow):
            roles = _OPERATION_ROLES.get(node.macro) or next(
                (roles for suffix, roles in _OPERATION_ROLE_SUFFIXES if node.macro.endswith(suffix)), None)
            if roles is None or (node.macro, node.index) in seen:
                continue
            seen.add((node.macro, node.index))
            plan = plan_macro(
                node.macro, node.index, node.runtime_vars, resolve_operations=True,
                twin_alignment=node.twin_alignment, twin_settle_offsets=node.twin_settle_offsets,
                consumables=node.consumables,
            )
            if not roles & plan.consumables.keys():
                reason = next((note for note in plan.notes if "没认出来" in note), "规划器没有找到目标耗材")
                checks.append({"name": f"{node.macro} 的目标耗材", "actual": reason,
                               "expected": "工位上有可操作的耗材", "unit": "", "passed": False})
        return checks

    @staticmethod
    def _zhongping1_tip2(workflows, volume):
        """Exported pipette_zhongping1 with only the TIP slot moved from 1 to 2.

        Slot 1 is consumed by the xiaoping transfer that fills 中瓶区1 first; an ejected TIP does
        not return to its slot. Every other field comes from the export, and drift is refused.
        """
        exported = workflows.WORKFLOWS["pipette_zhongping1"]
        settle, transfer = exported.sub_actions
        if (type(transfer).__name__ != "PipetteTransferCfg"
                or (transfer.source, transfer.target, transfer.volume, transfer.tip_index)
                != ("中瓶区1", "中瓶区1", 100.0, 1)):
            raise RuntimeError("Exported pipette_zhongping1 changed; refusing the TIP slot 2 variant")
        return workflows.workflow(
            exported.description + "（TIP 2 号位）",
            settle,
            dataclasses.replace(transfer, tip_index=2, volume=volume),
        )

    def _task_provenance(self, task):
        value = read_json(self.directory / "session_provenance.json")
        workflow_path = Path(task["workflow_yaml"])
        value.update(
            request=copy.deepcopy(task),
            request_id=task["request_id"],
            capability_id=task["capability_id"],
            capability_version=task["capability_version"],
            config_version=task["config_version"],
            parameters=copy.deepcopy(task["parameters"]),
            workflow_name=task["workflow_name"],
            workflow_phase=(
                2 if task["workflow_name"] == "prepare_small_vial" else None),
            workflow_detail={
                "prepare_small_vial":
                    "full_station().sub_actions[1]: small_vial_decap_recap + SmallCapHomeCfg()",
                "pipette_zhongping1_tip2":
                    "WORKFLOWS['pipette_zhongping1'] with PipetteTransferCfg.tip_index 1 -> 2",
            }.get(task["workflow_name"]),
            workflow_path=str(workflow_path),
            workflow_sha256=sha256_file(workflow_path) if workflow_path.is_file() else None,
            worker_sha256=sha256_file(Path(__file__)),
        )
        return value

    def _validate_station_task(self, task, task_id):
        if not isinstance(task, dict) or set(task) != CANONICAL_TASK_FIELDS:
            raise ValueError("Invalid canonical station task request")
        validated = validate_task({
            name: task[name]
            for name in ("request_id", "session_id", "capability_id", "parameters")
        })
        if (
            validated["request_id"] != task_id
            or validated["session_id"] != self.request["session_id"]
            or task["experiment_id"] != self.request["experiment_id"]
            or task["scene_id"] != SCENE_ID
            or self.request["scene_id"] != SCENE_ID
        ):
            raise ValueError("Task identity does not match the active station session")
        capability = get_capability(validated["capability_id"])
        if (
            capability["scene_id"] != SCENE_ID
            or capability.get("workflow_name") not in WORKFLOW_NAMES
            or task["workflow_name"] != capability["workflow_name"]
            or task["capability_version"] != capability["version"]
            or task["config_version"] != config_version()
            or Path(task["workflow_yaml"]).resolve()
            != Path(capability["workflow_yaml"]).resolve()
        ):
            raise ValueError("Task capability or workflow identity is stale")
        task["parameters"] = validated["parameters"]
        return task

    def _inference_context(self):
        if self.torch is None or not hasattr(self.torch, "inference_mode"):
            return contextlib.nullcontext()
        return self.torch.inference_mode()

    def _semantic_acceptance(self, task, initial, final, history, extrema=None):
        helper = importlib.import_module("bridge.station_semantics")
        value = helper.evaluate_postconditions(
            task["workflow_name"],
            copy.deepcopy(initial),
            copy.deepcopy(final),
            copy.deepcopy(task["parameters"]),
            history=copy.deepcopy(history),
            extrema=copy.deepcopy(extrema),
        )
        if not isinstance(value, dict) or not {"passed", "checks"} <= set(value):
            raise TypeError("evaluate_postconditions must return {passed, checks}")
        if not isinstance(value["passed"], bool) or not isinstance(value["checks"], list):
            raise TypeError("Invalid semantic acceptance result")
        return _json_value(value)

    def run_task(self, command):
        run_dir = Path(command["run_dir"])
        raw_task = read_json(run_dir / "request.json")
        requested_parameters = raw_task.get("parameters") if isinstance(raw_task, dict) else {}
        if not isinstance(requested_parameters, dict):
            requested_parameters = {}
        record_video = task_recording_requested(requested_parameters, self.request)
        hide_shell = task_hide_shell(requested_parameters, record_video)
        task = self._validate_station_task(raw_task, command["request_id"])
        task_id = task["request_id"]
        self.camera_workflow = task["workflow_name"]
        workflow = self._load_workflow(task["workflow_name"], task["parameters"])
        write_json(run_dir / "provenance.json", self._task_provenance(task))

        events = (run_dir / "events.jsonl").open("x", encoding="utf-8", buffering=1)
        samples = (run_dir / "samples.jsonl").open("x", encoding="utf-8", buffering=1)
        result = {
            "schema_version": 1,
            "source": "simulation",
            "scene_id": SCENE_ID,
            "request_id": task_id,
            "session_id": task["session_id"],
            "experiment_id": task["experiment_id"],
            "capability_id": task["capability_id"],
            "capability_version": task["capability_version"],
            "config_version": task["config_version"],
            "workflow_name": task["workflow_name"],
            "parameters": copy.deepcopy(task["parameters"]),
            "simulation_mode": "physics",
            "hardware_calibrated": False,
            "reset_policy": RESET_POLICY,
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
            "semantic_history": [],
            "operation_history": [],
        }
        task_started_wall = time.monotonic()
        task_started_sim = self.session_sim_s
        next_sample = SAMPLE_INTERVAL_S
        initial_semantics = None
        semantic_history = []
        liquid_extrema = {}
        last_semantic = None
        finished = False
        video = None
        shell_visibility = None
        hidden_shell_count = 0
        next_video_sim_s = 0.0

        def capture_video():
            nonlocal next_video_sim_s
            elapsed = self.session_sim_s - task_started_sim
            if video is not None and elapsed + 1e-9 >= next_video_sim_s:
                video.capture()
                next_video_sim_s = elapsed + 1.0 / 25.0

        def event(kind, **fields):
            value = {
                "event": kind,
                "request_id": task_id,
                "scene_id": SCENE_ID,
                "source": "simulation",
                "wall_time_utc": now(),
                "sim_time_s": self.session_sim_s,
                **fields,
            }
            events.write(dumps(value) + "\n")
            write_json(run_dir / "event.json", value)

        def remember_sample(sample):
            nonlocal last_semantic
            sample = _json_value(sample)
            semantic = sample["semantics"]
            if semantic != last_semantic:
                semantic_history.append(copy.deepcopy(semantic))
                last_semantic = copy.deepcopy(semantic)
            station = sample.get("station") or {}
            operation = station.get("operation_status")
            if operation != self.last_operation_status:
                self.last_operation_status = operation
                point = {
                    "sim_time_s": self.session_sim_s,
                    "operation_status": operation,
                    "volume_ml": copy.deepcopy(station.get("volume_ml", {})),
                    "grips": copy.deepcopy(station.get("grips", {})),
                }
                result["operation_history"].append(point)
                if operation:
                    event("operation_status", **point)
            return sample

        def save_sample(sample):
            sample = remember_sample(sample)
            samples.write(dumps(sample) + "\n")
            write_json(run_dir / "latest.json", sample)
            self.latest_sample = sample
            self.publish(latest_sample=sample)
            result["sample_count"] += 1
            return sample

        def finish(status, error=None):
            nonlocal finished
            if finished:
                return status
            finished = True
            if video is not None:
                try:
                    result["video"] = video.close()
                    if hide_shell:
                        result["video"]["hidden_shell_count"] = hidden_shell_count
                except Exception as exc:
                    result["video_error"] = f"{type(exc).__name__}: {exc}"
                    if status == "succeeded":
                        status, error = "failed", "video_output_failed"
            if shell_visibility is not None:
                try:
                    shell_visibility.restore()
                    result["shell_visibility_restored"] = True
                except Exception as exc:
                    result["shell_visibility_restore_error"] = f"{type(exc).__name__}: {exc}"
                    if status == "succeeded":
                        status, error = "failed", "shell_visibility_restore_failed"
            pending_cancel = _cancel_reason(run_dir)
            if pending_cancel is not None and status != "interrupted":
                status, error = pending_cancel, f"cancel_reason:{pending_cancel}"
            if status == "succeeded" and task["workflow_name"] in FULL_WORKFLOWS:
                self.completed_phase = FULL_WORKFLOWS[task["workflow_name"]]
            if status == "succeeded" and task["workflow_name"] in MIXTURE_WORKFLOWS:
                self.completed_phase = MIXTURE_WORKFLOWS[task["workflow_name"]]
            self.held_commands = {key: value.clone() for key, value in self.sm.action_dict.items()}
            try:
                final = save_sample(self.snapshot(
                    "finished", request_id_value=task_id, command_target=self.control))
            except Exception as exc:
                final = {
                    "schema_version": 1,
                    "source": "simulation",
                    "scene_id": SCENE_ID,
                    "request_id": task_id,
                    "stage": "finished",
                    "semantics": {},
                    "station": {},
                    "snapshot_error": f"{type(exc).__name__}: {exc}",
                }
                result["snapshot_error"] = final["snapshot_error"]
            result.update(
                status=status,
                error=error,
                finished_at=now(),
                elapsed_sim_s=self.session_sim_s - task_started_sim,
                sim_time_s=self.session_sim_s - task_started_sim,
                session_sim_time_s=self.session_sim_s,
                elapsed_wall_s=time.monotonic() - task_started_wall,
                final=final,
                semantic_history=semantic_history,
                liquid_extrema_ml={name: list(pair) for name, pair in liquid_extrema.items()},
            )
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
                    last_task_status=status, error=error)
            return status

        try:
            self.publish(status="running", active_task_id=task_id, error=None)
            event("starting", workflow_name=task["workflow_name"])
            require_camera_ready(record_video, self.camera_ready)
            if hide_shell:
                station = self.env.scene.articulations["station"]
                asset_root = station.root_view.prim_paths[0]
                shell_visibility = ShellVisibility(
                    self.env.sim.stage, asset_root, EXPORT_ASSET_DIR / "rig_manifest.json")
                shell_visibility.hide()
                hidden_shell_count = self._valid_shell_count(
                    self.env.sim.stage, shell_visibility.paths)
            self._clear_station_operation_history()
            self.last_operation_status = None
            initial = save_sample(self.snapshot("initial", request_id_value=task_id))
            result["initial"] = initial
            initial_semantics = copy.deepcopy(initial["semantics"])
            self._track_liquid(liquid_extrema)
            semantics_helper = importlib.import_module("bridge.station_semantics")
            failed_preconditions = semantics_helper.check_preconditions(
                task["workflow_name"], initial["semantics"], task["parameters"])
            failed_preconditions += self._unresolved_targets(workflow)
            result["preconditions"] = failed_preconditions
            if failed_preconditions:
                detail = "；".join(
                    f"{item['name']} 实际 {item['actual']}，要求 {item['expected']}"
                    for item in failed_preconditions)
                raise RuntimeError(f"Station task precondition failed: {detail}")

            if record_video:
                video = StationVideoRecorder(
                    run_dir / "simulation.mp4", self.recording_render, hide_shell=hide_shell)
                video.start()

            self.sm.set_action_sequence([workflow])
            self.sm.reset()
            observations = self.env.observation_manager.compute()
            self.sm.update_scene_data_from_obs(observations)
            self._seed_station_commands()
            event("running", parameters=task["parameters"])
            status = None
            error = None
            action = None

            with self._inference_context():
                while self.app.is_running():
                    if session_worker_module.STOP_REASON:
                        status, error = (
                            "interrupted", f"signal:{session_worker_module.STOP_REASON}")
                        break
                    reason = _cancel_reason(run_dir)
                    if reason is not None:
                        status, error = reason, f"cancel_reason:{reason}"
                        break
                    elapsed = self.session_sim_s - task_started_sim
                    if elapsed + self.env.step_dt > float(task["parameters"]["timeout_s"]) + 1e-9:
                        write_json(run_dir / "cancel.json", {"reason": "timed_out"})
                        status, error = "timed_out", "simulation_timeout"
                        break
                    if time.monotonic() - task_started_wall >= float(
                            self.config["wall_timeout_s"]):
                        write_json(run_dir / "cancel.json", {"reason": "timed_out"})
                        status, error = "timed_out", "wall_timeout"
                        break

                    sm_failure = _flags_any(self.sm.action_sequence_failure)
                    sm_success = _flags_all(self.sm.action_sequence_success)
                    if sm_failure:
                        status, error = "failed", "primitive_action_failed_or_timed_out"
                        break
                    if sm_success:
                        if action is None:
                            raise RuntimeError("State machine completed without an action")
                        observations, _, terminated, truncated, _ = self._step_station(
                            action, semantic_actions=None)
                        self._assert_no_auto_reset(terminated, truncated)
                        self.session_sim_s += self.env.step_dt
                        capture_video()
                        result["steps"] += 1
                        self._track_liquid(liquid_extrema)
                        final = self.snapshot(
                            "executing", request_id_value=task_id,
                            command_target=self.control)
                        save_sample(final)
                        station = final.get("station") or {}
                        operation = station.get("operation_status")
                        if self._rejected_operation(operation):
                            status, error = "failed", f"operation_rejected:{operation}"
                            break
                        acceptance = self._semantic_acceptance(
                            task, initial_semantics, final["semantics"],
                            semantic_history, liquid_extrema)
                        result["acceptance"] = acceptance
                        if acceptance["passed"]:
                            status = "succeeded"
                        else:
                            status, error = "failed", "semantic_postcondition_failed"
                        break

                    action, semantics = self.sm.step(observations)
                    observations, _, terminated, truncated, _ = self._step_station(
                        action, semantic_actions=semantics)
                    self._assert_no_auto_reset(terminated, truncated)
                    self.session_sim_s += self.env.step_dt
                    capture_video()
                    result["steps"] += 1
                    self._track_liquid(liquid_extrema)
                    task_elapsed = self.session_sim_s - task_started_sim
                    operation = self._operation_status(self._station_driver())
                    if self._rejected_operation(operation):
                        sample = self.snapshot(
                            "executing", request_id_value=task_id,
                            command_target=self.control)
                        save_sample(sample)
                        status, error = "failed", f"operation_rejected:{operation}"
                        break
                    status_changed = operation != self.last_operation_status
                    if status_changed or task_elapsed + 1e-9 >= next_sample:
                        sample = self.snapshot(
                            "executing", request_id_value=task_id,
                            command_target=self.control)
                        save_sample(sample)
                        if task_elapsed + 1e-9 >= next_sample:
                            next_sample += SAMPLE_INTERVAL_S
                    reason = _cancel_reason(run_dir)
                    if reason is not None:
                        if not status_changed and task_elapsed + 1e-9 < next_sample:
                            save_sample(self.snapshot(
                                "executing", request_id_value=task_id,
                                command_target=self.control))
                        status, error = reason, f"cancel_reason:{reason}"
                        break
                else:
                    status, error = "interrupted", "simulation_application_closed"

            result["final_steps"] = self.sm.get_status()
            if status is None:
                status, error = "failed", "task_loop_exited_without_terminal_state"
            return finish(status, error)
        except InterruptedError as exc:
            return finish("interrupted", f"interrupted:{exc}")
        except Exception as exc:
            traceback.print_exc()
            result["exception"] = f"{type(exc).__name__}: {exc}"
            return finish("failed", result["exception"])


__all__ = ["SCENE_ID", "StationSessionWorker"]
