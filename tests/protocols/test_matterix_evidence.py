import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from eos.protocols import matterix_evidence
from eos.protocols.entities.protocol_run import ProtocolRun, ProtocolRunStatus
from eos.protocols.matterix_evidence import (
    MAX_PROTOCOL_SAMPLES,
    PROTOCOL_VIDEO_NAME,
    finalize_protocol_recording,
    protocol_artifact_path,
    protocol_samples,
    recording_object,
)
from eos.protocols.protocol_executor import ProtocolExecutor
from eos.tasks.entities.task import Task, TaskStatus


class _FileDb:
    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.stored = {}
        self.list_calls = 0
        self.get_calls = 0
        self.stream_calls = 0

    async def list_files(self, prefix: str = "") -> list[str]:
        self.list_calls += 1
        return sorted(path for path in self.files if path.startswith(prefix))

    async def get_file(self, path: str) -> bytes:
        self.get_calls += 1
        return self.files[path]

    async def stream_file(self, path: str, chunk_size: int = 3 * 1024 * 1024):
        self.stream_calls += 1
        data = self.files[path]
        for index in range(0, len(data), chunk_size):
            yield data[index : index + chunk_size]

    async def store_file(self, path: str, data: bytes | Path) -> None:
        value = await asyncio.to_thread(Path(data).read_bytes) if isinstance(data, Path) else data
        self.files[path] = value
        self.stored[path] = value


def _task(
    name: str,
    start_s: int,
    *,
    status: TaskStatus = TaskStatus.COMPLETED,
    files: list[str] | None = None,
    record_video: bool = True,
    task_type: str = "Matterix Capability",
) -> Task:
    start = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=start_s)
    return Task(
        name=name,
        type=task_type,
        protocol_run_name="run",
        status=status,
        start_time=start,
        end_time=start + timedelta(seconds=2),
        output_file_names=files or [],
        meta={"matterix": {"record_video": record_video}},
    )


@pytest.mark.parametrize("task_type", ["Matterix Begin Experiment", "Matterix End Experiment"])
def test_lifecycle_boundary_does_not_require_a_motion_video(task_type):
    task = _task("boundary", 0, task_type=task_type)
    task.output_parameters = {"state": {"record_video": True}}
    assert not matterix_evidence.task_expects_video(task)


def test_protocol_samples_keep_real_session_time_and_offset_missing_sessions():
    tasks = [_task("first", 1), _task("second", 2), _task("third", 3)]
    observations = {
        "first": {
            "samples": [
                {"session_id": "session-a", "sim_time_s": 1.0},
                {"session_id": "session-a", "sim_time_s": 1.5},
            ]
        },
        "second": {"samples": [{"sim_time_s": 0.5}]},
        "third": {"samples": [{"session_id": "session-a", "sim_time_s": 2.0}]},
    }

    samples = protocol_samples(tasks, observations)

    assert [sample["task_name"] for sample in samples] == ["first", "first", "second", "third"]
    assert [sample["protocol_time_s"] for sample in samples[:2]] == [1.0, 1.5]
    assert samples[2]["protocol_time_s"] > samples[1]["protocol_time_s"]
    assert samples[2]["session_id"] is None
    assert samples[2]["protocol_session_index"] != samples[0]["protocol_session_index"]
    assert samples[3]["protocol_session_index"] == samples[0]["protocol_session_index"]
    assert samples[3]["protocol_time_s"] == 2.0


def test_protocol_samples_use_one_global_budget_and_keep_run_boundaries():
    tasks = [_task("first", 1), _task("second", 2)]
    observations = {
        "first": {"samples": [{"session_id": "a", "sim_time_s": value} for value in range(4000)]},
        "second": {"samples": [{"sim_time_s": value} for value in range(4000)]},
    }

    samples = protocol_samples(tasks, observations)

    assert len(samples) == MAX_PROTOCOL_SAMPLES
    assert samples[0]["task_name"] == "first"
    assert samples[0]["sim_time_s"] == 0
    assert any(sample["task_name"] == "second" for sample in samples)
    assert samples[-1]["task_name"] == "second"
    assert samples[-1]["sim_time_s"] == 3999


async def test_protocol_recording_reencodes_all_real_segments(monkeypatch):
    tasks = [
        _task("begin", 0, task_type="Matterix Begin Experiment"),
        _task("prepare", 1, files=["simulation.mp4"]),
        _task("measure", 2, files=["stock-simulation.mp4"]),
    ]
    tasks[0].output_parameters = {"state": {"record_video": True}}
    files = {
        "run/prepare/simulation.mp4": b"prepare-video",
        "run/measure/stock-simulation.mp4": b"measure-video",
    }
    file_db = _FileDb(files)
    captured = {}

    async def run_ffmpeg(executable, input_paths, output_path):
        captured["executable"] = executable
        captured["inputs"] = [path.read_bytes() for path in input_paths]
        output_path.write_bytes(b"protocol-video")

    monkeypatch.setattr(matterix_evidence, "ffmpeg_executable", lambda: "/configured/ffmpeg")
    monkeypatch.setattr(matterix_evidence, "_run_ffmpeg", run_ffmpeg)

    manifest = await finalize_protocol_recording("run", tasks, file_db)

    video_path = protocol_artifact_path("run", PROTOCOL_VIDEO_NAME)
    manifest_path = protocol_artifact_path("run", "recording.json")
    assert manifest["status"] == "ready"
    assert manifest["complete"] is True
    assert [segment["task_name"] for segment in manifest["segments"]] == ["prepare", "measure"]
    assert captured == {"executable": "/configured/ffmpeg", "inputs": [b"prepare-video", b"measure-video"]}
    assert file_db.files[video_path] == b"protocol-video"
    assert json.loads(file_db.files[manifest_path])["status"] == "ready"
    assert file_db.stream_calls == 2
    assert file_db.get_calls == 0


async def test_ffmpeg_filter_unifies_fps_and_resolution(monkeypatch):
    command = []

    def capture_run(command_value, **kwargs):
        command.extend(command_value)
        return type("Result", (), {"returncode": 0, "stderr": "", "stdout": ""})()

    monkeypatch.setattr(matterix_evidence.subprocess, "run", capture_run)
    await matterix_evidence._run_ffmpeg("/ffmpeg", [Path("a.mp4"), Path("b.mp4")], Path("out.mp4"))

    command_text = " ".join(command)
    assert command.count("-i") == 2
    assert "scale=1920:540" in command_text
    assert "fps=25" in command_text
    assert "concat=n=2:v=1:a=0" in command_text
    assert "-c:v libx264" in command_text


async def test_missing_ffmpeg_writes_failed_manifest(monkeypatch):
    task = _task("prepare", 1, files=["simulation.mp4"])
    file_db = _FileDb({"run/prepare/simulation.mp4": b"recorded-video"})
    monkeypatch.setattr(matterix_evidence, "ffmpeg_executable", lambda: None)

    manifest = await finalize_protocol_recording("run", [task], file_db)

    assert manifest["status"] == "failed"
    assert manifest["complete"] is False
    assert "ffmpeg is unavailable" in manifest["error"]
    assert protocol_artifact_path("run", PROTOCOL_VIDEO_NAME) not in file_db.files
    stored = json.loads(file_db.files[protocol_artifact_path("run", "recording.json")])
    assert stored["status"] == "failed"


async def test_non_matterix_protocol_does_not_touch_file_storage():
    task = _task("multiply", 1, files=[], record_video=False, task_type="Multiplication")
    file_db = _FileDb({})

    result = await finalize_protocol_recording("run", [task], file_db, protocol_type="multiplication")

    assert result is None
    assert file_db.list_calls == 0
    assert file_db.stored == {}


@pytest.mark.parametrize("protocol_type", ["matterix_protocol", "mixture_protocol"])
async def test_disabled_recording_skips_task_file_listing(protocol_type):
    task = _task("prepare", 1, files=[], record_video=False)
    file_db = _FileDb({})

    manifest = await finalize_protocol_recording("run", [task], file_db, protocol_type=protocol_type)

    assert manifest["status"] == "disabled"
    assert manifest["complete"] is False
    assert file_db.list_calls == 0
    assert set(file_db.stored) == {protocol_artifact_path("run", "recording.json")}


def test_recording_object_only_reports_protocol_video_status():
    assert recording_object(None, protocol_status="RUNNING", requested=True)["status"] == "recording"
    disabled = recording_object(None, protocol_status="RUNNING", requested=False)
    assert disabled["status"] == "disabled"
    assert disabled["complete"] is False
    manifest = {
        "protocol_run_name": "run",
        "status": "ready",
        "segments": [{"task_name": "step"}],
    }
    without_video = recording_object(manifest, protocol_status="COMPLETED", requested=True)
    assert without_video["status"] == "failed"
    assert without_video["error"] == "protocol_video_missing"
    with_video = recording_object(
        manifest,
        protocol_status="COMPLETED",
        requested=True,
        artifact_names=[protocol_artifact_path("run", PROTOCOL_VIDEO_NAME)],
    )
    assert with_video["status"] == "ready"
    assert with_video["complete"] is True


async def test_cancelled_protocol_video_is_partial_and_incomplete(monkeypatch):
    task = _task("prepare", 1, files=["simulation.mp4"], status=TaskStatus.CANCELLED)
    file_db = _FileDb({"run/prepare/simulation.mp4": b"recorded-video"})

    async def run_ffmpeg(executable, input_paths, output_path):
        output_path.write_bytes(b"partial-video")

    monkeypatch.setattr(matterix_evidence, "ffmpeg_executable", lambda: "/configured/ffmpeg")
    monkeypatch.setattr(matterix_evidence, "_run_ffmpeg", run_ffmpeg)

    manifest = await finalize_protocol_recording(
        "run",
        [task],
        file_db,
        protocol_status="CANCELLED",
    )

    assert manifest["status"] == "partial"
    assert manifest["complete"] is False
    assert "protocol_not_completed:CANCELLED" in manifest["warnings"]


async def test_completed_protocol_with_a_missing_expected_segment_is_partial(monkeypatch):
    recorded = _task("recorded", 1, files=["simulation.mp4"])
    missing = _task("missing", 2, files=[])
    file_db = _FileDb({"run/recorded/simulation.mp4": b"recorded-video"})

    async def run_ffmpeg(executable, input_paths, output_path):
        output_path.write_bytes(b"partial-video")

    monkeypatch.setattr(matterix_evidence, "ffmpeg_executable", lambda: "/configured/ffmpeg")
    monkeypatch.setattr(matterix_evidence, "_run_ffmpeg", run_ffmpeg)

    manifest = await finalize_protocol_recording(
        "run",
        [recorded, missing],
        file_db,
        protocol_status="COMPLETED",
    )

    assert manifest["status"] == "partial"
    assert manifest["complete"] is False
    assert "recorded_task_missing:missing" in manifest["warnings"]


@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled"])
async def test_every_protocol_terminal_path_finalizes_matterix_evidence(outcome):
    events = []

    class Session:
        async def commit(self):
            return None

    class Db:
        @asynccontextmanager
        async def get_async_session(self):
            yield Session()

    class ProtocolRuns:
        async def get_protocol_run(self, db, name):
            return ProtocolRun(name=name, type="protocol", status=ProtocolRunStatus.RUNNING)

        async def complete_protocol_run(self, db, name):
            events.append("completed")

        async def fail_protocol_run(self, db, name, error_message=None):
            events.append("failed")

        async def cancel_protocol_run(self, db, name):
            events.append("cancelled")

    class Scheduler:
        async def unregister_protocol_run(self, db, name):
            return None

    executor = object.__new__(ProtocolExecutor)
    executor._protocol_run_name = "run"
    executor._protocol_run_status = ProtocolRunStatus.RUNNING
    executor._db_interface = Db()
    executor._protocol_run_manager = ProtocolRuns()
    executor._scheduler = Scheduler()
    executor._current_task_submissions = {}

    async def archive(db, status):
        events.append("archive")

    executor._finalize_matterix_evidence = archive
    if outcome == "completed":
        await executor._complete_protocol_run(Session())
    elif outcome == "failed":
        await executor._fail_protocol_run(Session(), "boom")
    else:
        await executor.cancel_protocol_run()

    assert events == [outcome, "archive"]
