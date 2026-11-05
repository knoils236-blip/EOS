"""One local simulator process at a time; durable requests and bounded shutdown."""

import fcntl
import os
from pathlib import Path
import signal
import subprocess
import threading
import time

from .common import ROOT, TERMINAL, now, read_json, read_jsonl_tail, request_id, validate_request, write_json
from .session_manager import SessionManager


def process_start(pid):
    try:
        # /proc comm may contain spaces; field 22 is starttime.
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (FileNotFoundError, ProcessLookupError):
        return None


class RunManager:
    def __init__(self, config_path=None, *, run_root=None, worker_command=None,
                 session_root=None, session_worker_command=None):
        self.config = read_json(config_path or ROOT / "config.json")
        if set(self.config) != {"matterix_root", "matterix_python", "wall_timeout_s"}:
            raise ValueError("Unexpected bridge configuration fields")
        timeout = self.config["wall_timeout_s"]
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 600:
            raise ValueError("wall_timeout_s must be in (0, 600]")
        self.root = Path(run_root or ROOT / "runs").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock_file = (self.root / ".manager.lock").open("a")
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock_file.close()
            raise RuntimeError("Another bridge manager owns this run directory") from None
        self.lock = threading.RLock()
        self.active = None
        self.thread = None
        self.closed = False
        self.worker_command = worker_command
        try:
            self._recover()
            self.sessions = SessionManager(
                self, session_root=session_root, worker_command=session_worker_command)
        except BaseException:
            self.lock_file.close()
            raise

    def _recover(self):
        for file in self.root.glob("*/state.json"):
            state = read_json(file)
            if state.get("session_id"):
                continue
            if state["status"] not in TERMINAL:
                pid = state.get("worker_pid")
                birth = process_start(pid) if pid else None
                if birth is not None and birth == state.get("worker_start"):
                    command = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
                    if str(file.parent).encode() not in command or os.getpgid(pid) != pid:
                        raise RuntimeError(f"Cannot verify interrupted worker {pid}")
                    os.killpg(pid, signal.SIGKILL)
                    deadline = time.monotonic() + 5
                    while process_start(pid) is not None and time.monotonic() < deadline:
                        try:
                            zombie = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z"
                        except FileNotFoundError:
                            break
                        if zombie:
                            break
                        time.sleep(0.05)
                    else:
                        if process_start(pid) is not None:
                            raise RuntimeError(f"Interrupted worker {pid} has not stopped")
                elif birth is not None and state.get("worker_start") is None:
                    raise RuntimeError(f"Cannot verify interrupted worker {pid} without its start time")
                state.update(status="interrupted", error="server_restart", updated_at=now())
                write_json(file, state)

    def _state(self, identifier):
        path = self.root / request_id(identifier) / "state.json"
        if not path.is_file():
            return self.sessions.get_task(identifier)
        return read_json(path)

    def get(self, identifier):
        with self.lock:
            state = self._state(identifier)
            directory = self.root / identifier
            state["recent_samples"] = read_jsonl_tail(directory / "samples.jsonl")
            latest = directory / "latest.json"
            if latest.is_file():
                state["latest_sample"] = read_json(latest)
            if (directory / "event.json").is_file():
                state["latest_event"] = read_json(directory / "event.json")
            if state["status"] in TERMINAL and (directory / "result.json").is_file():
                try:
                    state["result"] = read_json(directory / "result.json")
                except (ValueError, OSError) as exc:
                    state["result_read_error"] = str(exc)
            return state

    def submit(self, payload):
        request = validate_request(payload)
        with self.lock:
            if self.closed:
                raise RuntimeError("Bridge is shutting down")
            directory = self.root / request["request_id"]
            if directory.exists():
                if read_json(directory / "request.json") != request:
                    raise ValueError("request_id already belongs to different input")
                return self.get(request["request_id"])
            if self.active is not None or self.sessions.active is not None:
                raise RuntimeError("workstation_busy")
            directory.mkdir()
            write_json(directory / "request.json", request)
            state = {"schema_version": 1, "source": "simulation", "request_id": request["request_id"],
                     "request": request, "status": "starting", "error": None,
                     "created_at": now(), "updated_at": now(), "run_dir": str(directory)}
            write_json(directory / "state.json", state)
            write_json(directory / "config.json", self.config)
            command = self.worker_command or [self.config["matterix_python"], "-B", str(ROOT / "bridge/worker.py")]
            environment = os.environ.copy()
            for name in ("PYTHONPATH", "PYTHONHOME"):
                environment.pop(name, None)
            environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1",
                               MATTERIX_PATH=self.config["matterix_root"],
                               BRIDGE_PARENT_PID=str(os.getpid()))
            process = None
            try:
                with (directory / "simulator.log").open("wb") as log:
                    process = subprocess.Popen([*command, "--run-dir", str(directory)], cwd=directory,
                                               env=environment, stdout=log, stderr=subprocess.STDOUT,
                                               start_new_session=True)
                state.update(worker_pid=process.pid, worker_start=process_start(process.pid))
                write_json(directory / "state.json", state)
            except Exception as exc:
                if process is not None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                state.update(status="failed", error=f"worker_start_failed: {exc}", updated_at=now())
                write_json(directory / "state.json", state)
                return self.get(request["request_id"])
            self.active = {"process": process, "directory": directory, "state": state,
                           "started": time.monotonic(), "stop_reason": None, "stop_started": None}
            self.thread = threading.Thread(target=self._monitor, daemon=True)
            self.thread.start()
            return self.get(request["request_id"])

    def _request_stop(self, reason):
        active = self.active
        if active and active["stop_reason"] is None:
            active["stop_reason"] = reason
            active["stop_started"] = time.monotonic()
            write_json(active["directory"] / "cancel.json", {"reason": reason})
            active["state"].update(status="cancelling", updated_at=now())
            write_json(active["directory"] / "state.json", active["state"])

    def cancel(self, identifier):
        with self.lock:
            path = self.root / request_id(identifier) / "state.json"
            if not path.is_file():
                return self.sessions.cancel(identifier)
            state = read_json(path)
            if state.get("session_id"):
                return self.sessions.cancel(identifier)
            if state["status"] not in TERMINAL:
                if not self.active or self.active["state"]["request_id"] != identifier:
                    raise RuntimeError("No active process for this request")
                self._request_stop("cancelled")
            return self.get(identifier)

    def _monitor(self):
        try:
            self._monitor_loop()
        except Exception as exc:
            with self.lock:
                active = self.active
                if active:
                    process = active["process"]
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    active["state"].update(status="failed", error=f"supervisor_error: {exc}", updated_at=now())
                    write_json(active["directory"] / "state.json", active["state"])
                    self.active = None

    def _monitor_loop(self):
        while True:
            with self.lock:
                active = self.active
                process, directory, state = active["process"], active["directory"], active["state"]
                elapsed = time.monotonic() - active["started"]
                code = process.poll()
                if code is not None:
                    result_file = directory / "result.json"
                    result = None
                    try:
                        if result_file.is_file():
                            result = read_json(result_file)
                            if result.get("request_id") != state["request_id"] or result.get("status") not in TERMINAL:
                                raise ValueError("Invalid worker result")
                    except (ValueError, OSError, AttributeError) as exc:
                        state["error"] = f"invalid_worker_result: {exc}"
                        result = None
                    reason = active["stop_reason"]
                    status = reason or (result["status"] if result else "failed")
                    if code != 0 and status == "succeeded":
                        status = "failed"
                        state["error"] = "worker_exited_with_error_after_report"
                    state.update(status=status, exit_code=code, elapsed_wall_s=elapsed, updated_at=now())
                    state["error"] = (reason if reason else state["error"] or
                                      (result.get("error") if result else "worker_exited_without_result"))
                    write_json(directory / "state.json", state)
                    self.active = None
                    return
                if elapsed >= self.config["wall_timeout_s"]:
                    self._request_stop("timed_out")
                if active["stop_reason"]:
                    stopping = time.monotonic() - active["stop_started"]
                    if stopping >= 5:
                        try:
                            os.killpg(process.pid, signal.SIGKILL if stopping >= 10 else signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                elif state["status"] == "starting" and (directory / "latest.json").is_file():
                    state.update(status="running", updated_at=now())
                    write_json(directory / "state.json", state)
            time.sleep(0.05)

    def close(self):
        with self.lock:
            if self.lock_file.closed:
                return
            self.closed = True
        self.sessions.close()
        with self.lock:
            self._request_stop("interrupted")
        if self.thread:
            self.thread.join(timeout=15)
            if self.thread.is_alive():
                raise RuntimeError("Simulator did not stop; keep the manager lock held")
        fcntl.flock(self.lock_file, fcntl.LOCK_UN)
        self.lock_file.close()
