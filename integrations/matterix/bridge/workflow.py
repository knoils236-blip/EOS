"""One durable, local simulation workflow at a time; reuse demo/laserworkflow.py."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid

from . import workflow_db
from .client import MatterixClient
from .common import ROOT, TERMINAL, now, read_json, request_id, write_json
from .manager import process_start
from .web_data import directory, file_path

RUNS = ROOT / "workflows"
DEMO = ROOT.parent / "demo"
STAGES = {"atlas": "Atlas 推荐", "matterix": "Matterix 抓取仿真", "synthesis": "Chemspeed 模拟合成",
          "filtration": "过滤", "hplc": "HPLC 与协同注入", "optics": "光学模拟表征", "feedback": "更新 Atlas campaign"}


def new_id():
    return datetime.now(timezone.utc).strftime("wf-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]


def ensure_simulation():
    if "SIMULATION = True" not in (DEMO / "sila-chemspeed/start_server.py").read_text().splitlines():
        raise RuntimeError("集成工作流仅支持本机模拟服务，请使用 start_web.sh 启动")


def get_state(identifier):
    path = file_path(identifier, "state.json", RUNS)
    state = read_json(path)
    if state.get("request_id") != identifier:
        raise ValueError("工作流编号不一致")
    if state["status"] not in TERMINAL:
        pid = state.get("worker_pid")
        starting = not pid and time.time() - path.stat().st_mtime < 10
        if not starting and (not pid or process_start(pid) != state.get("worker_start")):
            state.update(status="interrupted", error="工作流进程已退出，未自动重试", updated_at=now())
            for step in state["steps"].values():
                if step["status"] == "running":
                    step["status"] = "interrupted"
                elif step["status"] == "pending":
                    step["status"] = "skipped"
            write_json(path, state)
            workflow_db.save(state, state["error"])
    return state


def history():
    records = []
    if RUNS.is_dir():
        for folder in RUNS.iterdir():
            if folder.is_dir() and not folder.is_symlink() and (folder / "state.json").is_file():
                try:
                    records.append(get_state(folder.name))
                except (OSError, ValueError):
                    continue
    return sorted(records, key=lambda value: value["created_at"], reverse=True)


def submit(identifier, name, record_video=False):
    request_id(identifier)
    if len(identifier) > 61:
        raise ValueError("工作流编号最长为 61 个字符，需为 Matterix 子任务保留后缀")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 64:
        raise ValueError("任务名称需为 1–64 个字符")
    if not isinstance(record_video, bool):
        raise ValueError("视频选项必须为布尔值")
    request = {"name": name.strip(), "record_video": record_video}
    folder = directory(identifier, RUNS)
    if folder.exists():
        if read_json(folder / "request.json") != request:
            raise ValueError("同一工作流编号已对应另一份输入，请准备新任务")
        return get_state(identifier)
    ensure_simulation()
    RUNS.mkdir(exist_ok=True)
    with (RUNS / ".lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("已有集成工作流正在运行，请等待它结束") from None
        folder.mkdir()
        write_json(folder / "request.json", request)
        state = {"request_id": identifier, "kind": "integrated_workflow", "source": "simulation",
                 "request": request, "status": "starting", "created_at": now(), "updated_at": now(),
                 "error": None, "matterix_request_id": identifier + "-mx",
                 "steps": {key: {"status": "pending", "job_ids": []} for key in STAGES}}
        try:
            state["job_id"] = workflow_db.create_job(name.strip(), "integrated_workflow", state)
            write_json(folder / "state.json", state)
            environment = {**os.environ, "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
                           "OLYMPUS_SCRATCH": str(ROOT.parent / ".state/olympus"),
                           "ATLAS_SCRATCH": str(ROOT.parent / ".state/atlas"), "MPLBACKEND": "Agg",
                           "PGHOST": "127.0.0.1", "PGPORT": "5432", "PGUSER": "chemos", "PGDATABASE": "chemos"}
            with (folder / "workflow.log").open("wb") as log:
                process = subprocess.Popen([sys.executable, "-B", "-u", "-m", "bridge.workflow", "--worker", identifier,
                                            "--lock-fd", str(lock.fileno())], cwd=ROOT, env=environment,
                                           pass_fds=(lock.fileno(),), stdout=log, stderr=subprocess.STDOUT,
                                           start_new_session=True)
            state.update(worker_pid=process.pid, worker_start=process_start(process.pid))
            write_json(folder / "state.json", state)
            threading.Thread(target=process.wait, daemon=True).start()
        except Exception as exc:
            state.update(status="failed", error=str(exc), updated_at=now())
            write_json(folder / "state.json", state)
            workflow_db.save(state, "启动失败：" + str(exc))
            raise
    return state


class Workflow:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.state = read_json(self.folder / "state.json")
        self.current = None
        self.lock = threading.RLock()

    def record(self, **fields):
        with self.lock:
            self.state.update(fields, updated_at=now())
            write_json(self.folder / "state.json", self.state)
            workflow_db.save(self.state, f"{self.current or 'workflow'}: {self.state['status']}")

    @contextmanager
    def step(self, key):
        self.current = key
        self.state["steps"][key].update(status="running", started_at=now())
        self.record(status="running", current_step=key)
        try:
            yield
        except BaseException as exc:
            self.state["steps"][key].update(status="failed", error=str(exc), finished_at=now())
            raise
        else:
            self.state["steps"][key].update(status="succeeded", finished_at=now())
            self.record()

    def link_job(self, job_id):
        with self.lock:
            workflow_db.link(job_id, self.state, self.current)
            self.state["steps"][self.current]["job_ids"].append(job_id)
            self.record()

    def run_matterix(self):
        job_id = workflow_db.create_job("Matterix 抓取仿真", "matterix_simulation",
                                        {"matterix_request_id": self.state["matterix_request_id"]})
        self.link_job(job_id)
        with MatterixClient() as client:
            response = client.submit({"request_id": self.state["matterix_request_id"], "workflow_id": "pickup_beaker",
                                      "sample_id": self.state["sample_id"],
                                      "record_video": self.state["request"]["record_video"]}, wait=True)
        self.record(matterix_status=response["status"])
        if response["status"] != "succeeded" or not response.get("result", {}).get("lift_check", {}).get("passed"):
            raise RuntimeError(f"Matterix 验收未通过：{response['status']} / {response.get('error')}")


def worker(identifier, lock_fd):
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    folder = directory(identifier, RUNS)
    deadline = time.monotonic() + 10
    while read_json(folder / "state.json").get("worker_pid") != os.getpid():
        if time.monotonic() > deadline:
            return 1
        time.sleep(0.02)
    workflow = Workflow(folder)

    def interrupted(signum, _):
        if signum == signal.SIGALRM:
            raise TimeoutError("集成工作流超过 20 分钟上限")
        raise InterruptedError("工作流进程被停止")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGALRM, interrupted)
    signal.alarm(1200)
    try:
        ensure_simulation()
        sys.path.insert(0, str(DEMO))
        os.chdir(DEMO)
        from laserworkflow import run
        run(steps=1, run_dir=folder, observer=workflow)
        workflow.record(status="succeeded", finished_at=now())
        return 0
    except BaseException as exc:
        traceback.print_exc()
        status = "timed_out" if isinstance(exc, TimeoutError) else "interrupted" if isinstance(exc, InterruptedError) else "failed"
        for step in workflow.state["steps"].values():
            if step["status"] == "pending":
                step["status"] = "skipped"
            elif step["status"] == "running":
                step.update(status="failed", error=str(exc), finished_at=now())
        workflow.state.update(status=status, error=str(exc), finished_at=now())
        try:
            with MatterixClient() as client:
                state = client.get(workflow.state["matterix_request_id"])
                if state["status"] not in TERMINAL and state["status"] != "rejected":
                    client.cancel(workflow.state["matterix_request_id"])
        except Exception:
            pass
        try:
            workflow.record()
        except Exception as sync_error:
            workflow.state["database_sync_error"] = str(sync_error)
            write_json(folder / "state.json", workflow.state)
            traceback.print_exc()
        return 1
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", required=True)
    parser.add_argument("--lock-fd", required=True, type=int)
    args = parser.parse_args()
    # Native SiLA helpers retain subscription threads; no work remains after the final state is saved.
    os._exit(worker(args.worker, args.lock_fd))
