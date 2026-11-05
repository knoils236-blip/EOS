"""Durable, serial simulator sessions and their task lifecycle."""

import os
from pathlib import Path
import signal
import subprocess
import threading
import time
import uuid

from .capabilities import config_version, get_capability, validate_task
from .common import ROOT, TERMINAL, now, read_json, read_jsonl_tail, request_id, validate_session, write_json
from .station_semantics import PRECONDITION_WORKFLOWS, check_preconditions

SESSION_ACTIVE = frozenset({"starting", "idle", "running", "resetting", "ending", "frozen"})
CANCEL_GRACE_S = 10
END_GRACE_S = 5


def process_start(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (FileNotFoundError, ProcessLookupError):
        return None


def _task_directory(run_root, identifier):
    root = Path(run_root).resolve()
    target = root / request_id(identifier)
    if target.is_symlink() or target.resolve().parent != root:
        raise ValueError("Run directory is outside the configured root")
    return target


class SessionManager:
    def __init__(self, manager, *, session_root=None, worker_command=None):
        self.manager = manager
        self.config_version = config_version()
        self.root = Path(session_root or manager.root.parent / "sessions").resolve()
        self.worker_command = worker_command
        self.active = None
        self.thread = None
        self.closed = False
        self._recover()

    def _session_directory(self, identifier):
        target = self.root / request_id(identifier)
        if target.is_symlink() or target.resolve().parent != self.root:
            raise ValueError("Session directory is outside the configured root")
        return target

    def _read_session(self, identifier):
        path = self._session_directory(identifier) / "session.json"
        if not path.is_file():
            raise ValueError(f"Unknown session: {identifier}")
        return read_json(path)

    def _read_runtime(self, directory):
        path = directory / "runtime.json"
        if not path.is_file():
            return {}
        return read_json(path)

    def _write_session(self, directory, state):
        state["updated_at"] = now()
        write_json(directory / "session.json", state)

    def _persisted_sessions(self):
        if not self.root.is_dir():
            return []
        sessions = []
        for folder in self.root.iterdir():
            if not folder.is_dir() or folder.is_symlink():
                continue
            path = folder / "session.json"
            if not path.is_file():
                continue
            try:
                sessions.append(read_json(path))
            except (OSError, ValueError) as exc:
                raise ValueError(f"Invalid session record {path}: {exc}") from exc
        return sorted(sessions, key=lambda value: value.get("created_at", ""), reverse=True)

    def _recover(self):
        for state in self._persisted_sessions():
            if state.get("status") not in SESSION_ACTIVE:
                continue
            directory = self._session_directory(state["session_id"])
            self._stop_verified_worker(state, directory)
            state.update(status="interrupted", error="server_restart", active_task_id=None, updated_at=now())
            self._write_session(directory, state)
            self._interrupt_unfinished_tasks(state["session_id"])

    def _owned_worker(self, state, directory):
        pid = state.get("worker_pid")
        start = state.get("worker_start")
        if not isinstance(pid, int) or not start or process_start(pid) != start:
            return False
        try:
            command = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            return str(directory).encode() in command and os.getpgid(pid) == pid
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            return False

    def _stop_verified_worker(self, state, directory):
        if not self._owned_worker(state, directory):
            return
        pid = state["worker_pid"]
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 5
        while process_start(pid) is not None and time.monotonic() < deadline:
            try:
                if Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z":
                    break
            except FileNotFoundError:
                break
            time.sleep(0.05)

    def _interrupt_unfinished_tasks(self, session_id):
        for path in self.manager.root.glob("*/state.json"):
            try:
                task = read_json(path)
            except (OSError, ValueError):
                continue
            if task.get("session_id") == session_id and task.get("status") not in TERMINAL:
                task.update(status="interrupted", error="server_restart", updated_at=now())
                write_json(path, task)

    def create(self, payload):
        request = validate_session(payload)
        if config_version() != self.config_version:
            raise RuntimeError("Bridge configuration changed; restart the idle bridge before creating a session")
        with self.manager.lock:
            if self.closed or self.manager.closed:
                raise RuntimeError("Bridge is shutting down")
            directory = self._session_directory(request["session_id"])
            if directory.exists():
                existing = read_json(directory / "request.json")
                if existing != request:
                    raise ValueError("session_id already belongs to different input")
                return self.get(request["session_id"])
            if self.active is not None or self.manager.active is not None:
                raise RuntimeError("workstation_busy")
            directory.mkdir(parents=True)
            write_json(directory / "request.json", request)
            write_json(directory / "config.json", self.manager.config)
            state = {
                "schema_version": 1,
                "session_id": request["session_id"],
                "experiment_id": request["experiment_id"],
                "scene_id": request["scene_id"],
                "request": request,
                "config_version": config_version(),
                "status": "starting",
                "generation": 0,
                "active_task_id": None,
                "scene_released": False,
                "error": None,
                "created_at": now(),
                "updated_at": now(),
            }
            write_json(directory / "session.json", state)
            command = self.worker_command or [
                self.manager.config["matterix_python"], "-B", str(ROOT / "bridge/session_worker.py")]
            environment = os.environ.copy()
            for name in ("PYTHONPATH", "PYTHONHOME"):
                environment.pop(name, None)
            environment.update(
                PYTHONDONTWRITEBYTECODE="1",
                PYTHONNOUSERSITE="1",
                MATTERIX_PATH=self.manager.config["matterix_root"],
                BRIDGE_PARENT_PID=str(os.getpid()),
            )
            process = None
            try:
                with (directory / "simulator.log").open("wb") as log:
                    process = subprocess.Popen(
                        [*command, "--session-dir", str(directory)],
                        cwd=directory,
                        env=environment,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                state.update(worker_pid=process.pid, worker_start=process_start(process.pid))
                self._write_session(directory, state)
            except Exception as exc:
                if process is not None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                state.update(status="interrupted", error=f"worker_start_failed: {exc}")
                self._write_session(directory, state)
                return self.get(request["session_id"])
            self.active = {
                "process": process,
                "directory": directory,
                "state": state,
                "task": None,
                "task_command_id": None,
                "task_started": None,
                "sim_time_baseline": None,
                "stop_requested": False,
                "stop_started": None,
                "operation_started": time.monotonic(),
                "end_started": None,
            }
            self.thread = threading.Thread(target=self._monitor, daemon=True)
            self.thread.start()
            return self.get(request["session_id"])

    def get(self, identifier):
        with self.manager.lock:
            state = self._read_session(identifier)
            directory = self._session_directory(identifier)
            try:
                runtime = self._read_runtime(directory)
            except (OSError, ValueError) as exc:
                runtime = {"status": "interrupted", "error": f"invalid_runtime: {exc}"}
            state["runtime"] = runtime
            if runtime:
                state["runtime_status"] = runtime.get("status")
                for name in ("sim_time_s", "generation", "latest_sample"):
                    if name in runtime:
                        state[name] = runtime[name]
            return state

    def list(self):
        with self.manager.lock:
            return [self.get(state["session_id"]) for state in self._persisted_sessions()]

    @staticmethod
    def _task_semantics(task):
        return {name: task.get(name) for name in
                ("request_id", "session_id", "capability_id", "parameters")}

    def submit_task(self, payload):
        request = validate_task(payload)
        with self.manager.lock:
            if self.closed or self.manager.closed:
                raise RuntimeError("Bridge is shutting down")
            session = self._read_session(request["session_id"])
            if ("record_video" not in payload["parameters"]
                    and "record_video" in request["parameters"] and session["request"].get("record_video")):
                request["parameters"]["record_video"] = True
            capability = get_capability(request["capability_id"])
            if capability["scene_id"] != session["scene_id"]:
                raise ValueError("Capability does not apply to this session scene")
            directory = _task_directory(self.manager.root, request["request_id"])
            if directory.exists():
                existing = read_json(directory / "request.json")
                if self._task_semantics(existing) != request:
                    raise ValueError("request_id already belongs to different input")
                return self.get_task(request["request_id"])
            if session.get("active_task_id"):
                raise RuntimeError("session_busy")
            if session["status"] not in {"starting", "idle"}:
                raise RuntimeError("session_not_idle")
            active = self.active
            if active is None or active["state"]["session_id"] != request["session_id"]:
                raise RuntimeError("Session worker is not available")
            self._check_preconditions(active, capability, request["parameters"])
            version = config_version()
            if version != self.config_version:
                raise RuntimeError("Bridge configuration changed; restart the idle bridge before submitting a task")
            canonical = {
                **request,
                "scene_id": session["scene_id"],
                "experiment_id": session["experiment_id"],
                "capability_version": capability["version"],
                "config_version": version,
                "workflow_yaml": capability["workflow_yaml"],
                "workflow_name": capability["workflow_name"],
            }
            directory.mkdir()
            write_json(directory / "request.json", canonical)
            write_json(directory / "config.json", self.manager.config)
            task = {
                "schema_version": 1,
                "source": "simulation",
                "request_id": request["request_id"],
                "session_id": request["session_id"],
                "experiment_id": session["experiment_id"],
                "capability_id": request["capability_id"],
                "request": canonical,
                "status": "starting",
                "error": None,
                "run_dir": str(directory),
                "worker_pid": active["process"].pid,
                "created_at": now(),
                "updated_at": now(),
            }
            write_json(directory / "state.json", task)
            session["status"] = "starting" if session["status"] == "starting" else "running"
            session["active_task_id"] = request["request_id"]
            session["error"] = None
            self._write_session(active["directory"], session)
            active["task"] = task
            active["task_command_id"] = None
            active["task_started"] = None
            active["sim_time_baseline"] = None
            active["stop_requested"] = False
            return self.get_task(request["request_id"])

    def _check_preconditions(self, active, capability, parameters=None):
        """Reject a task whose required carried-over state is absent, before any simulation runs.

        Between tasks the worker is paused, so its last published sample is the current scene.
        """
        if (capability["workflow_name"] not in PRECONDITION_WORKFLOWS
                and not capability["workflow_name"].startswith("skill_")):
            return
        runtime = self._read_runtime(active["directory"])
        semantics = (runtime.get("latest_sample") or {}).get("semantics")
        if runtime.get("status") != "idle" or not isinstance(semantics, dict):
            raise ValueError("precondition_unknown: 会话尚未就绪，无法确认执行前状态")
        failed = check_preconditions(capability["workflow_name"], semantics, parameters)
        if failed:
            detail = "；".join(f"{item['name']} 实际 {item['actual']}，要求 {item['expected']}" for item in failed)
            raise ValueError(f"precondition_failed: {detail}")

    def get_task(self, identifier):
        with self.manager.lock:
            path = _task_directory(self.manager.root, identifier) / "state.json"
            if not path.is_file():
                raise ValueError(f"Unknown request: {identifier}")
            state = read_json(path)
            directory = path.parent
            state["recent_samples"] = read_jsonl_tail(directory / "samples.jsonl")
            if (directory / "latest.json").is_file():
                state["latest_sample"] = read_json(directory / "latest.json")
            if (directory / "event.json").is_file():
                state["latest_event"] = read_json(directory / "event.json")
            if state.get("status") in TERMINAL and (directory / "result.json").is_file():
                try:
                    state["result"] = read_json(directory / "result.json")
                except (OSError, ValueError) as exc:
                    state["result_read_error"] = str(exc)
            return state

    def _update_task(self, task, **changes):
        task.update(changes, updated_at=now())
        write_json(Path(task["run_dir"]) / "state.json", task)

    def _command_acknowledged(self, active):
        command = active["directory"] / "command.json"
        if not command.is_file():
            return True
        runtime = self._read_runtime(active["directory"])
        return runtime.get("command_id") == read_json(command).get("command_id")

    def _send_command(self, operation, *, request_id=None, run_dir=None):
        active = self.active
        if active is None or not self._command_acknowledged(active):
            raise RuntimeError("Previous session command is not acknowledged")
        command = {"command_id": uuid.uuid4().hex, "operation": operation}
        if request_id is not None:
            command.update(request_id=request_id, run_dir=str(run_dir))
        write_json(active["directory"] / "command.json", command)
        session = self._read_session(active["state"]["session_id"])
        session["command_id"] = command["command_id"]
        self._write_session(active["directory"], session)
        active["state"] = session
        return command

    def cancel(self, identifier):
        with self.manager.lock:
            task = self.get_task(identifier)
            if task["status"] in TERMINAL:
                return task
            active = self.active
            if active is None or not active["task"] or active["task"]["request_id"] != identifier:
                raise RuntimeError("No active process for this request")
            if not active["stop_requested"]:
                write_json(Path(task["run_dir"]) / "cancel.json", {"reason": "cancelled"})
                active["stop_requested"] = True
                active["stop_started"] = time.monotonic()
            self._update_task(task, status="cancelling")
            return self.get_task(identifier)

    def reset(self, identifier):
        with self.manager.lock:
            state = self._read_session(identifier)
            if state["status"] in {"ended", "interrupted"}:
                raise RuntimeError("Interrupted or ended sessions cannot be reset")
            if state.get("active_task_id"):
                raise RuntimeError("session_busy")
            if state["status"] not in {"idle", "frozen"}:
                raise RuntimeError("session_not_idle")
            if not self.active or self.active["state"]["session_id"] != identifier:
                raise RuntimeError("Session worker is not available")
            command = self._send_command("reset")
            state = self._read_session(identifier)
            state.update(status="resetting", generation_before=state.get("generation", 0))
            self._write_session(self.active["directory"], state)
            self.active["state"] = state
            state["command_id"] = command["command_id"]
            self.active["operation_started"] = time.monotonic()
            return self.get(identifier)

    def end(self, identifier):
        with self.manager.lock:
            state = self._read_session(identifier)
            if state["status"] in {"ended", "interrupted"}:
                return self.get(identifier)
            if state.get("active_task_id"):
                raise RuntimeError("session_busy")
            if not self.active or self.active["state"]["session_id"] != identifier:
                state.update(status="interrupted", error="worker_unavailable")
                self._write_session(self._session_directory(identifier), state)
                return self.get(identifier)
            self._send_command("end")
            state = self._read_session(identifier)
            state["status"] = "ending"
            self._write_session(self.active["directory"], state)
            self.active["state"] = state
            self.active["end_started"] = time.monotonic()
            return self.get(identifier)

    def _start_pending_task(self, active, runtime):
        task = active["task"]
        if task is None or active["task_command_id"] is not None or runtime.get("status") != "idle":
            return
        command = self._send_command(
            "run", request_id=task["request_id"], run_dir=Path(task["run_dir"]))
        active["task_command_id"] = command["command_id"]
        active["task_started"] = time.monotonic()
        session = self._read_session(active["state"]["session_id"])
        session["status"] = "running"
        self._write_session(active["directory"], session)
        active["state"] = session

    def _finish_task(self, active, task, result):
        status = result["status"]
        self._update_task(
            task,
            status=status,
            error=result.get("error") or (active["state"].get("error") if status != "succeeded" else None),
            completed_at=now(),
            elapsed_wall_s=(time.monotonic() - active["task_started"]) if active["task_started"] else None,
        )
        session = self._read_session(active["state"]["session_id"])
        session["active_task_id"] = None
        session["status"] = (
            "idle" if status == "succeeded"
            else "interrupted" if status == "interrupted"
            else "frozen"
        )
        session["error"] = None if status == "succeeded" else result.get("error") or status
        session["latest_task_id"] = task["request_id"]
        self._write_session(active["directory"], session)
        active["state"] = session
        active["task"] = None
        active["task_command_id"] = None
        active["task_started"] = None
        active["sim_time_baseline"] = None
        active["stop_requested"] = False

    def _check_task(self, active, runtime):
        task = active["task"]
        if task is None:
            return
        task_runtime_status = runtime.get("status")
        if active["task_command_id"] is not None and task["status"] == "starting":
            self._update_task(task, status="running")
        if task_runtime_status == "running" and active["task_started"] is None:
            active["task_started"] = time.monotonic()
            self._update_task(task, status="running")
        sim_time = runtime.get("sim_time_s")
        if isinstance(sim_time, (int, float)) and not isinstance(sim_time, bool):
            if active["sim_time_baseline"] is None:
                active["sim_time_baseline"] = sim_time
            timeout = task["request"]["parameters"]["timeout_s"]
            if sim_time - active["sim_time_baseline"] >= timeout and not active["stop_requested"]:
                write_json(Path(task["run_dir"]) / "cancel.json", {"reason": "timed_out"})
                active["stop_requested"] = True
                active["stop_started"] = time.monotonic()
                self._update_task(task, status="cancelling")
        if (active["task_started"] is not None and
                time.monotonic() - active["task_started"] >= self.manager.config["wall_timeout_s"]):
            self._interrupt_active("wall_timeout")
            return
        result_path = Path(task["run_dir"]) / "result.json"
        if not result_path.is_file():
            if (active["stop_requested"] and active["stop_started"] is not None and
                    time.monotonic() - active["stop_started"] >= CANCEL_GRACE_S):
                self._interrupt_active("cancel_grace_expired")
            return
        try:
            result = read_json(result_path)
            if result.get("request_id") != task["request_id"] or result.get("status") not in TERMINAL:
                raise ValueError("Invalid worker result")
        except (OSError, ValueError, AttributeError) as exc:
            self._interrupt_active(f"invalid_task_result: {exc}")
            return
        self._finish_task(active, task, result)

    def _mark_interrupted(self, error):
        active = self.active
        if active is None:
            return
        if active["task"] is not None:
            self._update_task(active["task"], status="interrupted", error=error, completed_at=now())
        state = self._read_session(active["state"]["session_id"])
        state.update(status="interrupted", error=error, active_task_id=None)
        self._write_session(active["directory"], state)
        active["state"] = state
        active["task"] = None

    def _interrupt_active(self, error):
        active = self.active
        if active is None:
            return
        process = active["process"]
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
        try:
            self._mark_interrupted(error)
        finally:
            self.active = None

    def _monitor(self):
        try:
            self._monitor_loop()
        except Exception as exc:
            with self.manager.lock:
                if self.active is not None:
                    self._interrupt_active(f"supervisor_error: {exc}")

    def _monitor_loop(self):
        while True:
            with self.manager.lock:
                active = self.active
                if active is None:
                    return
                process = active["process"]
                try:
                    runtime = self._read_runtime(active["directory"])
                except (OSError, ValueError) as exc:
                    self._interrupt_active(f"invalid_runtime: {exc}")
                    return
                state = self._read_session(active["state"]["session_id"])
                if (state["status"] in {"starting", "resetting"} and
                        time.monotonic() - active["operation_started"] >=
                        self.manager.config["wall_timeout_s"]):
                    self._interrupt_active(f"{state['status']}_timeout")
                    return
                self._start_pending_task(active, runtime)
                self._check_task(active, runtime)
                if self.active is None:
                    return
                state = self._read_session(active["state"]["session_id"])
                if state["status"] == "starting" and active["task"] is None and runtime.get("status") == "idle":
                    state.update(status="idle", generation=runtime.get("generation", 0), error=None)
                    self._write_session(active["directory"], state)
                    active["state"] = state
                if state["status"] == "resetting":
                    generation = runtime.get("generation")
                    if (runtime.get("command_id") == state.get("command_id") and
                            runtime.get("status") == "idle" and
                            isinstance(generation, int) and generation > state.get("generation_before", -1)):
                        state.update(status="idle", generation=generation, error=None)
                        state.pop("generation_before", None)
                        self._write_session(active["directory"], state)
                        active["state"] = state
                if state["status"] == "ending" and runtime.get("status") == "ended":
                    state.update(
                        status="ended",
                        termination_reason="completed",
                        scene_released=True,
                        ended_at=now(),
                        active_task_id=None,
                    )
                    self._write_session(active["directory"], state)
                    active["state"] = state
                if state["status"] in {"ending", "ended"} and active["end_started"]:
                    if (time.monotonic() - active["end_started"] >= END_GRACE_S and
                            process.poll() is None):
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait()
                        state.update(
                            status="ended",
                            termination_reason="forced_end",
                            scene_released=True,
                            exit_code=process.returncode,
                            ended_at=now(),
                            active_task_id=None,
                            error=None,
                        )
                        self._write_session(active["directory"], state)
                        active["state"] = state
                        self.active = None
                        return
                code = process.poll()
                if code is not None:
                    if active["task"] is not None:
                        result_path = Path(active["task"]["run_dir"]) / "result.json"
                        if result_path.is_file():
                            try:
                                result = read_json(result_path)
                                if (result.get("request_id") == active["task"]["request_id"] and
                                        result.get("status") in TERMINAL):
                                    self._finish_task(active, active["task"], result)
                            except (OSError, ValueError):
                                pass
                    state = self._read_session(active["state"]["session_id"])
                    runtime = self._read_runtime(active["directory"])
                    if state["status"] == "ended":
                        pass
                    elif state["status"] == "ending" and (runtime.get("status") == "ended" or code == 0):
                        state.update(
                            status="ended",
                            termination_reason="completed",
                            scene_released=True,
                            exit_code=code,
                            ended_at=now(),
                            active_task_id=None,
                        )
                    else:
                        error = runtime.get("error") or f"worker_exited: {code}"
                        if active["task"] is not None:
                            self._update_task(
                                active["task"], status="interrupted",
                                error=error, exit_code=code, completed_at=now())
                        state.update(
                            status="interrupted",
                            error=error, exit_code=code,
                            active_task_id=None,
                        )
                    self._write_session(active["directory"], state)
                    active["state"] = state
                    self.active = None
                    return
            time.sleep(0.05)

    def close(self):
        with self.manager.lock:
            self.closed = True
            active = self.active
            if active is None:
                return
            state = self._read_session(active["state"]["session_id"])
            if state["status"] not in {"ending", "ended"}:
                self._interrupt_active("server_shutdown")
                ending = False
            else:
                ending = True
                deadline = active["end_started"] + END_GRACE_S + 1
        if ending:
            while self.active is not None and time.monotonic() < deadline:
                time.sleep(0.05)
        with self.manager.lock:
            if self.active is not None:
                self._interrupt_active("server_shutdown")
        if self.thread is not None:
            self.thread.join(timeout=5)
