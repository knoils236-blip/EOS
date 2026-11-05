"""Single-shot Matterix execution. Run only with the Matterix interpreter."""

import argparse
import copy
import ctypes
import hashlib
import importlib.metadata
import importlib.util
import math
import os
from pathlib import Path
import signal
import sys
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bridge.common import LiftCheck, dumps, now, read_json, validate_request, write_json

STOP = False


def stop(*_):
    global STOP
    STOP = True


def run(directory):
    # A killed manager must not leave a GPU worker running after its lock is released.
    if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Cannot set parent-death signal")
    expected_parent = int(os.environ.get("BRIDGE_PARENT_PID", os.getppid()))
    if os.getppid() != expected_parent:
        return 1
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    config = read_json(directory / "config.json")
    request = validate_request(read_json(directory / "request.json"))
    record_video = request.get("record_video", False)
    os.environ["MATTERIX_PATH"] = config["matterix_root"]
    for package in ("matterix", "matterix_sm", "matterix_assets", "matterix_tasks"):
        sys.path.insert(0, str(Path(config["matterix_root"]) / "source" / package))
    result = {"schema_version": 1, "source": "simulation", "request_id": request["request_id"],
              "status": "failed", "error": None, "started_at": now(),
              "task": "Matterix-Test-Beaker-Lift-Franka-v1", "workflow_id": request["workflow_id"],
              "sample_id": request["sample_id"], "sample_count": 0}
    env = app = None
    video_started = False
    began = time.monotonic()
    samples = (directory / "samples.jsonl").open("a", encoding="utf-8", buffering=1)
    events = (directory / "events.jsonl").open("a", encoding="utf-8", buffering=1)

    def event(kind, **fields):
        value = {"event": kind, "request_id": request["request_id"], "wall_time_utc": now(), **fields}
        events.write(dumps(value) + "\n")
        write_json(directory / "event.json", value)

    def cancelled():
        return STOP or (directory / "cancel.json").exists()

    try:
        event("starting")
        from isaaclab.app import AppLauncher
        parser = argparse.ArgumentParser()
        AppLauncher.add_app_launcher_args(parser)
        launch_arguments = ["--device", "cuda:0", "--viz", "none",
                            f"--kit_args=--/log/file={directory / 'kit.log'}"]
        if record_video:
            launch_arguments.append("--enable_cameras")
        args = parser.parse_args(launch_arguments)
        launcher = AppLauncher(args)
        app = launcher.app
        if cancelled():
            result.update(status="cancelled", error="cancelled_during_startup")
            return 1
        import torch
        from matterix.envs import MatterixBaseEnv, mdp
        # Import just this task: package discovery also loads unrelated, missing lab scenes.
        task_file = Path(config["matterix_root"]) / "source/matterix_tasks/matterix_tasks/test_dev_tasks/test_franka_beaker_lift.py"
        spec = importlib.util.spec_from_file_location("bridge_beaker_task", task_file)
        task_module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = task_module
        spec.loader.exec_module(task_module)
        FrankaBeakerLiftEnvTestCfg = task_module.FrankaBeakerLiftEnvTestCfg
        from matterix_sm import StateMachine, WaitCfg
        from isaaclab.scene import InteractiveSceneCfg

        cfg = copy.deepcopy(FrankaBeakerLiftEnvTestCfg())
        cfg.scene = InteractiveSceneCfg(num_envs=1, env_spacing=cfg.env_spacing, replicate_physics=False)
        cfg.sim.device = args.device
        cfg.seed = 1
        cfg.episode_length_s = 30.0
        cfg.recorders = None
        cfg.record_path = str(directory / "dataset.hdf5")
        cfg.ui_window_class_type = None
        if record_video:
            cfg.viewer.eye = (1.2, 1.0, 0.8)
            cfg.viewer.lookat = (0.4, 0.0, 0.12)
            cfg.viewer.resolution = (960, 540)
            cfg.sim.render_interval = cfg.decimation
        cfg.events.randomize_beaker_position = None
        cfg.articulated_assets["robot"].event_terms.pop("randomize_franka_joint_state", None)
        # Loaded cup causes small wrist oscillations; retain the independent object-lift check.
        cfg.workflows["pickup_beaker"].sub_actions[-1].orientation_threshold = 0.1
        provenance = {"python": sys.version, "packages": {}, "matterix_sources": {},
                      "settings": {"num_envs": 1, "seed": 1, "randomization": False,
                                   "episode_limit_s": 30, "sample_interval_sim_s": 0.5,
                                   "settle_before_grasp_s": 1.0, "post_lift_hold_s": 1.2}}
        provenance["settings"]["lift_orientation_tolerance_rad"] = 0.1
        provenance["settings"]["record_video"] = record_video
        for name in ("isaacsim", "isaaclab", "torch", "matterix", "matterix_sm"):
            provenance["packages"][name] = importlib.metadata.version(name)
        paths = [Path(__file__), Path(FrankaBeakerLiftEnvTestCfg.__module__.replace(".", "/"))]
        import inspect
        paths[1] = Path(inspect.getfile(FrankaBeakerLiftEnvTestCfg))
        paths.append(Path(inspect.getfile(StateMachine)))
        for path in paths:
            provenance["matterix_sources"][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        write_json(directory / "provenance.json", provenance)
        env = MatterixBaseEnv(cfg, render_mode="rgb_array" if record_video else None)
        if record_video:
            from isaaclab_physx.renderers.kit_viewport_utils import set_kit_renderer_camera_view
            env.sim.set_setting("/isaaclab/video/enabled", True)
            set_kit_renderer_camera_view(cfg.viewer.eye, cfg.viewer.lookat, cfg.viewer.cam_prim_path)
        obs, _ = env.reset()
        if record_video:
            # Replicator's first reads can be empty while the render product warms up.
            for _ in range(10):
                frame = env.render()
                if frame is not None and frame.any():
                    break
            else:
                raise RuntimeError("Isaac Sim 渲染器未返回画面")
            env.start_recording()
            video_started = True
        sm = StateMachine(num_envs=1, dt=env.step_dt, device=env.device, status_interval_steps=0)
        sm.set_action_sequence([WaitCfg(duration=1.0), cfg.workflows["pickup_beaker"], WaitCfg(duration=1.2)])
        sm.reset()
        units = {"position": "m", "orientation": "quaternion_xyzw", "arm_joint_position": "rad",
                 "arm_joint_velocity": "rad/s", "finger_joint_position": "m",
                 "finger_joint_velocity": "m/s", "frame": "Isaac world", "time": "s"}

        def vector(value):
            value = value.torch if hasattr(value, "torch") else value
            values = value[0].detach().cpu().tolist()
            if not all(math.isfinite(v) for v in values):
                raise ValueError("Non-finite simulator observation")
            return values

        def snapshot(sim_s, stage):
            return {"schema_version": 1, "source": "simulation", "request_id": request["request_id"],
                    "sample_id": request["sample_id"], "sim_time_s": sim_s, "wall_time_utc": now(),
                    "elapsed_wall_s": time.monotonic() - began, "units": units, "stage": stage,
                    "robot": {"joint_names": list(env.scene["robot"].joint_names),
                              "joint_position": vector(mdp.joint_pos(env, "robot")),
                              "joint_velocity": vector(mdp.joint_vel(env, "robot")),
                              "ee_position_w": vector(mdp.ee_world_pos(env, "robot")),
                              "ee_quaternion_xyzw": vector(mdp.ee_world_quat(env, "robot"))},
                    "beaker": {"position_w": vector(mdp.object_world_pos(env, "beaker")),
                               "quaternion_xyzw": vector(mdp.object_world_quat(env, "beaker"))}}

        def save_sample(value):
            samples.write(dumps(value) + "\n")
            write_json(directory / "latest.json", value)
            result["sample_count"] += 1

        initial = snapshot(0.0, "settling")
        save_sample(initial)
        result["initial"] = initial
        event("running", sim_time_s=0.0)
        check = None
        sim_s, step, next_sample = 0.0, 0, 0.5
        last_action = None
        last = initial
        with torch.inference_mode():
            while app.is_running():
                if cancelled():
                    result.update(status="cancelled", error="cancel_requested")
                    break
                if sim_s + env.step_dt > 30.0:
                    result.update(status="timed_out", error="simulation_time_limit")
                    break
                action, semantics = sm.step(obs)
                if bool(sm.action_sequence_failure.any()):
                    result.update(status="failed", error="primitive_action_failed_or_timed_out")
                    break
                status = sm.get_status()
                action_key = [(s["action_idx"], s["status"]) for s in status]
                if action_key != last_action:
                    event("step_changed", sim_time_s=sim_s, steps=status)
                    last_action = action_key
                obs, _, terminated, truncated, _ = env.step(action, semantic_actions=semantics)
                step += 1
                sim_s = step * env.step_dt
                # Matterix auto-resets on termination: never accept that reset as task success.
                if bool((terminated | truncated).any()):
                    result.update(status="timed_out" if bool(truncated.any()) else "failed",
                                  error="environment_terminated_before_completion")
                    break
                stage = "settling" if sim_s < 1.0 else "executing"
                last = snapshot(sim_s, stage)
                if check is None and sim_s + 1e-9 >= 1.0:
                    check = LiftCheck(last["beaker"]["position_w"][2])
                    result["settled_initial"] = last
                    event("baseline_measured", sim_time_s=sim_s, beaker=last["beaker"])
                elif check is not None:
                    check.update(last["beaker"]["position_w"][2], env.step_dt)
                if sim_s + 1e-9 >= next_sample:
                    save_sample(last)
                    next_sample += 0.5
                if bool(sm.action_sequence_success.all()):
                    passed = check is not None and check.report()["passed"]
                    result.update(status="succeeded" if passed else "failed",
                                  error=None if passed else "beaker_did_not_pass_lift_check")
                    break
            else:
                result.update(status="failed", error="simulation_application_closed")
        result.update(final=last, elapsed_sim_s=sim_s, state_machine_success=bool(sm.action_sequence_success.all()),
                      lift_check=check.report() if check else None, final_steps=sm.get_status())
        save_sample(last)
    except BaseException as exc:
        result.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    finally:
        if video_started:
            try:
                event("encoding_video")
                temporary_video = directory / "simulation.partial.mp4"
                env.save_video(str(temporary_video))
                if not temporary_video.is_file() or temporary_video.stat().st_size == 0:
                    raise RuntimeError("仿真没有生成可用的视频帧")
                temporary_video.replace(directory / "simulation.mp4")
                result["video"] = {"filename": "simulation.mp4", "source": "Isaac Sim rendered frames",
                                   "width": 960, "height": 540, "fps": round(1 / env.step_dt)}
            except Exception as exc:
                result["video_error"] = str(exc)
                if result["status"] == "succeeded":
                    result.update(status="failed", error="video_output_failed")
                traceback.print_exc()
        result.update(finished_at=now(), elapsed_wall_s=time.monotonic() - began)
        event("finished", status=result["status"], error=result["error"])
        write_json(directory / "result.json", result)
        samples.close()
        events.close()
        if env is not None:
            env.close()
        if app is not None:
            app.close()
    return 0 if result["status"] == "succeeded" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    arguments = parser.parse_args()
    raise SystemExit(run(arguments.run_dir.resolve()))
