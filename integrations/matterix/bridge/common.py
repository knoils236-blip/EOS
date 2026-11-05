"""Small JSON contract shared by the two Python environments."""

import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TERMINAL = frozenset({"succeeded", "failed", "cancelled", "timed_out", "interrupted"})
WORKFLOW = "pickup_beaker"
SCENE_ID = "franka_beaker"
PIPETTING_SCENE_ID = "pipetting_realtime"
SUPPORTED_SCENE_IDS = frozenset({SCENE_ID, PIPETTING_SCENE_ID})


def now():
    return datetime.now(timezone.utc).isoformat()


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def loads(value):
    def invalid(token):
        raise ValueError(f"Non-finite JSON number: {token}")
    return json.loads(value, parse_constant=invalid)


def read_json(path):
    return loads(Path(path).read_text(encoding="utf-8"))


def read_jsonl_tail(path, *, max_bytes=262144, max_points=500):
    """Read a bounded window of complete samples while the worker appends."""
    path = Path(path)
    if not path.is_file():
        return []
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        start = max(0, stream.tell() - max_bytes)
        stream.seek(start)
        if start:
            stream.readline()  # The first line may start before this byte window.
        data = stream.read(max_bytes)
    lines = data.split(b"\n")[:-1]  # An unfinished final write is not yet a sample.
    return [loads(line.decode("utf-8")) for line in lines[-max_points:] if line.strip()]


def write_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(dumps(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def request_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("request_id must contain 1–64 ASCII letters, digits, '-' or '_'")
    return value


def validate_session(value):
    required = {"session_id", "experiment_id", "scene_id"}
    if (not isinstance(value, dict) or not required <= set(value)
            or set(value) - required - {"record_video", "enable_cameras"}):
        raise ValueError("Expected session_id, experiment_id, scene_id and optional recording settings")
    session_id = request_id(value["session_id"])
    experiment_id = request_id(value["experiment_id"])
    scene_id = value["scene_id"]
    if not isinstance(scene_id, str) or scene_id not in SUPPORTED_SCENE_IDS:
        raise ValueError(f"Only scenes {sorted(SUPPORTED_SCENE_IDS)} are supported")
    record_video = value.get("record_video", False)
    if not isinstance(record_video, bool):
        raise ValueError("record_video must be a JSON boolean")
    request = {"session_id": session_id, "experiment_id": experiment_id, "scene_id": scene_id}
    if record_video:
        request["record_video"] = True
    enable_cameras = value.get("enable_cameras", False)
    if not isinstance(enable_cameras, bool):
        raise ValueError("enable_cameras must be a JSON boolean")
    if enable_cameras:
        request["enable_cameras"] = True
    return request


def validate_request(value):
    if not isinstance(value, dict) or set(value) - {"request_id", "workflow_id", "sample_id", "record_video"}:
        raise ValueError("Expected request_id, workflow_id, optional sample_id and record_video only")
    request_id(value.get("request_id"))
    if value.get("workflow_id") != WORKFLOW:
        raise ValueError("Only the registered pickup_beaker simulation is supported")
    sample = value.get("sample_id")
    if sample is not None:
        request_id(sample)
    video = value.get("record_video", False)
    if not isinstance(video, bool):
        raise ValueError("record_video must be a JSON boolean")
    request = {"request_id": value["request_id"], "workflow_id": WORKFLOW, "sample_id": sample}
    # Keep existing no-video requests byte-for-byte compatible with persisted input.
    if video:
        request["record_video"] = True
    return request


class LiftCheck:
    """Check observed object height at every control step, not only saved samples."""

    def __init__(self, initial_z, minimum_m=0.05, hold_s=1.0):
        self.initial_z = initial_z
        self.minimum_m = minimum_m
        self.hold_s = hold_s
        self.held_s = 0.0
        self.maximum_m = 0.0

    def update(self, z, dt):
        if not all(math.isfinite(v) for v in (z, dt)) or dt <= 0:
            raise ValueError("Invalid lift measurement")
        lift = z - self.initial_z
        self.maximum_m = max(self.maximum_m, lift)
        self.held_s = self.held_s + dt if lift >= self.minimum_m else 0.0
        return self.held_s + 1e-9 >= self.hold_s

    def report(self):
        return {"minimum_lift_m": self.minimum_m, "required_hold_s": self.hold_s,
                "maximum_lift_m": self.maximum_m, "current_hold_s": self.held_s,
                "passed": self.held_s + 1e-9 >= self.hold_s}
