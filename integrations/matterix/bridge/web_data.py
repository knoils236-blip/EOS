"""Read-only views of local simulation records, also usable while SiLA is offline."""

from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from .common import ROOT, dumps, loads, read_json, request_id


def new_request_id():
    return datetime.now(timezone.utc).strftime("web-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


def directory(identifier, run_root=ROOT / "runs"):
    request_id(identifier)
    root = Path(run_root).resolve()
    target = root / identifier
    if target.is_symlink() or target.resolve().parent != root:
        raise ValueError("拒绝读取记录目录之外的路径")
    return target


def file_path(identifier, name, run_root):
    folder = directory(identifier, run_root)
    path = folder / name
    if path.is_symlink() or path.resolve().parent != folder.resolve():
        raise ValueError("拒绝读取指向其他位置的文件")
    return path


def load_run(identifier, run_root=ROOT / "runs"):
    state = read_json(file_path(identifier, "state.json", run_root))
    if not isinstance(state, dict) or state.get("request_id") != identifier or not isinstance(state.get("status"), str):
        raise ValueError("任务记录与编号不一致")
    for name, key in (("latest.json", "latest_sample"), ("event.json", "latest_event"), ("result.json", "result")):
        path = file_path(identifier, name, run_root)
        if path.is_file():
            try:
                value = read_json(path)
                if not isinstance(value, dict):
                    raise ValueError("结果必须是 JSON 对象")
                state[key] = value
            except (OSError, ValueError) as exc:
                state.setdefault("read_errors", []).append(f"{name}: {exc}")
    return state


def load_session(identifier, session_root=ROOT / "sessions"):
    state = read_json(file_path(identifier, "session.json", session_root))
    if not isinstance(state, dict) or state.get("session_id") != identifier or not isinstance(state.get("status"), str):
        raise ValueError("会话记录与编号不一致")
    try:
        runtime_path = file_path(identifier, "runtime.json", session_root)
        runtime = read_json(runtime_path) if runtime_path.is_file() else None
        if runtime is not None and not isinstance(runtime, dict):
            raise ValueError("runtime.json 必须是 JSON 对象")
    except (OSError, ValueError) as exc:
        runtime = {"status": "interrupted", "error": f"invalid_runtime: {exc}"}
    if runtime is not None:
        state["runtime"] = runtime
        state["runtime_status"] = runtime.get("status")
        for name in ("sim_time_s", "generation", "latest_sample"):
            if name in runtime:
                state[name] = runtime[name]
    return state


def session_history(session_root=ROOT / "sessions"):
    root = Path(session_root)
    if not root.is_dir():
        return []
    records = []
    for folder in root.iterdir():
        if not folder.is_dir() or folder.is_symlink():
            continue
        if not (folder / "session.json").exists():
            continue
        try:
            records.append(load_session(folder.name, root))
        except (OSError, ValueError, AttributeError) as exc:
            records.append({"session_id": folder.name, "status": "unreadable", "error": str(exc)})
    return sorted(records, key=lambda row: row.get("updated_at", row.get("created_at", "")), reverse=True)


def history(run_root=ROOT / "runs"):
    root = Path(run_root)
    if not root.is_dir():
        return []
    records = []
    for folder in root.iterdir():
        if not folder.is_dir() or folder.is_symlink():
            continue
        if not (folder / "state.json").exists():
            continue
        try:
            state = read_json(file_path(folder.name, "state.json", root))
            if state.get("request_id") != folder.name or not isinstance(state.get("status"), str):
                raise ValueError("任务编号不一致")
            records.append(state)
        except (OSError, ValueError, AttributeError) as exc:
            records.append({"request_id": folder.name, "status": "unreadable", "error": str(exc)})
    return sorted(records, key=lambda row: row.get("created_at", ""), reverse=True)


def load_samples(identifier, run_root=ROOT / "runs"):
    path = file_path(identifier, "samples.jsonl", run_root)
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    records = []
    for index, line in enumerate(lines):
        try:
            value = loads(line)
        except json.JSONDecodeError:
            # A worker may be appending its final line while the browser reads.
            if index == len(lines) - 1 and not line.endswith("\n"):
                break
            raise ValueError(f"观测文件第 {index + 1} 行损坏") from None
        if not isinstance(value, dict) or value.get("request_id") != identifier:
            raise ValueError(f"观测文件第 {index + 1} 行的任务编号不一致")
        records.append(value)
    return records


def download(identifier, kind, run_root=ROOT / "runs"):
    if kind == "samples":
        return "".join(dumps(row) + "\n" for row in load_samples(identifier, run_root)).encode("utf-8")
    if kind == "result":
        return (dumps(read_json(file_path(identifier, "result.json", run_root))) + "\n").encode("utf-8")
    raise ValueError("只允许下载结果或观测数据")


def video_path(identifier, run_root=ROOT / "runs"):
    path = file_path(identifier, "simulation.mp4", run_root)
    return path if path.is_file() and path.stat().st_size > 0 else None
