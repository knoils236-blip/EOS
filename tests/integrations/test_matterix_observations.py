import asyncio
import io
import json
import sys
import zipfile
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "user"))

from eos.configuration.entities.task_def import DeviceAssignmentDef  # noqa: E402
from eos.integrations.sila import SilaClientContext  # noqa: E402
from eos.protocols.entities.protocol_run import (  # noqa: E402
    ProtocolRun,
    ProtocolRunStatus,
)
from eos.tasks.entities.task import Task, TaskStatus  # noqa: E402
from eos.tasks.task_definition import Context, TaskDefinition  # noqa: E402
from eos.tasks.task_runner import run_task  # noqa: E402
from eos.web_api.controllers import matterix_controller  # noqa: E402
from matterix_sim.devices.matterix_workstation.device import MatterixWorkstation  # noqa: E402
from matterix_sim.devices.mixture_workstation.device import MixtureWorkstation  # noqa: E402
from matterix_sim.observations import extract_archive_evidence, recording_settings  # noqa: E402
from matterix_sim.tasks.matterix_capability.task import matterix_capability  # noqa: E402


def _archive(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _task(
    *,
    name: str = "phase",
    task_type: str = "Matterix Capability",
    status: TaskStatus = TaskStatus.COMPLETED,
    input_parameters: dict | None = None,
    meta: dict | None = None,
    output_parameters: dict | None = None,
    output_files: list[str] | None = None,
    devices: dict | None = None,
    start_time: datetime | None = None,
) -> Task:
    return Task(
        name=name,
        type=task_type,
        protocol_run_name="run",
        status=status,
        input_parameters=input_parameters,
        meta=meta or {},
        output_parameters=output_parameters,
        output_file_names=output_files,
        devices=devices or {},
        start_time=start_time,
    )


class _Outputs:
    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.accessed = []

    async def download_task_output_file(self, protocol_run_name, task_name, file_name):
        self.accessed.append(file_name)
        yield self.files[file_name]


class _ProtocolOutputs:
    def __init__(self, files: dict[tuple[str, str], bytes]):
        self.files = files

    async def download_task_output_file(self, protocol_run_name, task_name, file_name):
        yield self.files[(task_name, file_name)]


class _ProtocolFileDb:
    def __init__(self, files: dict[str, bytes]):
        self.files = files

    async def list_files(self, prefix=""):
        return sorted(path for path in self.files if path.startswith(prefix))

    async def get_file(self, path):
        return self.files[path]


async def test_context_meta_is_optional_and_task_runner_keeps_old_calls_compatible():
    assert Context("phase", "run").meta == {}
    captured = {}

    async def capture(context: Context) -> None:
        captured.update(context.meta)

    definition = TaskDefinition("capture", capture)
    await asyncio.to_thread(
        run_task._function,
        definition,
        None,
        "run",
        "phase",
        {},
        {},
        {},
        {},
    )
    assert captured == {}
    await asyncio.to_thread(
        run_task._function,
        definition,
        None,
        "run",
        "phase",
        {},
        {},
        {},
        {},
        {"matterix": {"record_video": True}},
    )
    assert captured == {"matterix": {"record_video": True}}


async def test_capability_meta_overrides_parameter_only_when_explicit():
    captured = {}

    def run_capability(capability_id, parameters, protocol_run_name, task_name):
        captured.update(parameters)
        return {"status": "succeeded", "result": {}}

    fake = SimpleNamespace(run_capability=run_capability)
    with pytest.raises(Exception, match="did not pass acceptance"):
        await matterix_capability.execute(
            Context("phase", "run", {"matterix": {"record_video": True}}),
            {"matterix": fake},
            {"capability_id": "transfer", "parameters": {"record_video": False, "hide_shell": False}},
            {},
            {},
        )
    assert captured == {"record_video": True, "hide_shell": False}
    captured.clear()
    with pytest.raises(Exception, match="did not pass acceptance"):
        await matterix_capability.execute(
            Context("phase", "run", {"matterix": {"hide_shell": True}}),
            {"matterix": fake},
            {"capability_id": "transfer", "parameters": {"record_video": False, "hide_shell": False}},
            {},
            {},
        )
    assert captured == {"record_video": False, "hide_shell": True}


async def test_progress_uses_registered_request_and_unlocked_client_is_closed(monkeypatch):
    workstation = MatterixWorkstation("station", "lab")
    workstation._requests = {
        ("run", "phase"): {"request_id": "request-1"},
        ("other", "phase"): {"request_id": "request-2"},
    }
    calls, closed = [], []

    class Client:
        class Matterix:
            @staticmethod
            def GetRunState(request_id):  # noqa: N802
                calls.append(request_id)
                return (json.dumps({"request_id": request_id, "status": "running"}),)

        def close(self):
            closed.append(True)

    async def create_client(*args, **kwargs):
        return Client()

    async def no_lock(*args, **kwargs):
        raise AssertionError("progress must not use a locked client")

    monkeypatch.setattr(SilaClientContext, "create_client", create_client)
    monkeypatch.setattr(SilaClientContext, "connect", no_lock)
    state = await workstation.get_execution_progress("run", "phase")
    assert state == {"request_id": "request-1", "status": "running"}
    assert calls == ["request-1"]
    assert closed == [True]
    assert await workstation.get_execution_progress("missing", "phase") is None


@pytest.mark.parametrize("initial_status", ["idle", "starting"])
async def test_idle_station_session_is_replaced_once_when_recording_enables_cameras(initial_status):
    workstation = MatterixWorkstation("station", "lab")
    workstation._session_id = "old-session"
    workstation._scene_id = "pipetting_realtime"
    workstation._experiment_id = "eos"
    workstation._experiment_owner = None
    workstation._blocked = False
    workstation._camera_enabled = False
    workstation._generation = 3
    workstation._requests = {("old", "task"): {"request_id": "old"}}
    workstation._results = {("old", "task"): {"status": "succeeded"}}
    workstation._recording_settings = {("old", "task"): {"record_video": False, "hide_shell": False}}
    workstation._stopped = {("old", "task")}
    calls, sessions = (
        [],
        iter(
            [
                {"status": initial_status, "active_task_id": None},
                {"status": "ended", "active_task_id": None},
            ]
        ),
    )

    async def call(command, argument):
        calls.append((command, argument))
        if command == "GetSession":
            return next(sessions)
        if command == "EndSession":
            return {"status": "ending"}
        if command == "CreateSession":
            return {"status": "starting"}
        raise AssertionError(command)

    workstation._call = call
    async def ready():
        return {"status": "idle", "active_task_id": None}

    workstation._wait_ready = ready
    await workstation._ensure_camera_session(True)
    create = next(argument for command, argument in calls if command == "CreateSession")
    assert workstation._session_id != "old-session"
    assert create["enable_cameras"] is True
    assert create["record_video"] is False
    assert workstation._requests == {}
    assert workstation._stopped == set()
    assert workstation._generation == 0


async def test_recording_never_replaces_an_active_or_uncertain_session():
    workstation = MatterixWorkstation("station", "lab")
    workstation._session_id = "active-session"
    workstation._scene_id = "pipetting_realtime"
    workstation._experiment_owner = None
    workstation._blocked = False
    workstation._camera_enabled = False
    calls = []

    async def call(command, argument):
        calls.append(command)
        return {"status": "idle", "active_task_id": "running-request"}

    workstation._call = call
    with pytest.raises(RuntimeError, match="active or uncertain"):
        await workstation._ensure_camera_session(True)
    assert calls == ["GetSession"]


async def test_finalize_extracts_fixed_zip_members_and_never_fabricates_video(monkeypatch):
    workstation = MatterixWorkstation("station", "lab")
    workstation._session_id = "session"
    workstation._requests = {("run", "phase"): {"request_id": "request-1", "parameters": {}}}
    workstation._results = {
        ("run", "phase"): {
            "request_id": "request-1",
            "status": "succeeded",
            "result": {"acceptance": {"passed": True}},
        }
    }
    workstation._stopped = set()
    workstation._blocked = False
    workstation._resource_map = {}
    workstation._consumable_map = {"tips": ["tip1"]}
    workstation._recording_settings = {}
    archive = _archive(
        {
            "request.json": b'{"parameters":{"record_video":false,"hide_shell":false}}',
            "result.json": b'{"source":"simulation","acceptance":{"passed":true}}',
            "samples.jsonl": b'{"source":"simulation","sim_time_s":1.0,"units":{"time":"s"}}\n',
            "secret.txt": b"must not escape",
            "../escape.json": b"must not escape",
        }
    )

    async def call(command, argument):
        assert command == "GetSession"
        return {
            "status": "idle",
            "generation": 2,
            "runtime": {
                "latest_sample": {
                    "semantics": {"device_state": {"operation_events": [{"event": "grasp", "object": "tip1"}]}}
                }
            },
        }

    @asynccontextmanager
    async def connect(*args, **kwargs):
        yield SimpleNamespace(Matterix=SimpleNamespace(GetRunArchive=lambda _: (archive,)))

    monkeypatch.setattr(workstation, "_call", call)
    monkeypatch.setattr(SilaClientContext, "connect", connect)
    report = await workstation.finalize_task("run", "phase")
    assert {"request.json", "result.json", "samples.jsonl", "matterix-evidence.zip"} <= set(report["files"])
    assert "secret.txt" not in report["files"]
    assert "../escape.json" not in report["files"]
    assert not any(name.endswith("simulation.mp4") for name in report["files"])
    assert len(report["inventory_events"]) == 1
    assert report["inventory_events"][0]["resource_name"] == "tips"
    assert report["inventory_events"][0]["delta"] == -1
    assert report["inventory_events"][0]["source"] == "simulation"


async def test_mixture_multistage_files_have_stable_prefixes(monkeypatch):
    workstation = MixtureWorkstation("mixture", "lab")
    workstation._backend = "matterix"
    workstation._blocked = False
    workstation._driver = None
    workstation._runs = {
        ("run", "prepare"): {
            "volume_ul": 74,
            "settings": {"record_video": True, "hide_shell": False},
            "active": None,
            "operations": [],
            "reports": [
                ("stock", {"files": {"simulation.mp4": b"stock-video"}, "resource_updates": {}}),
                ("diluent", {"files": {"simulation.mp4": b"diluent-video"}, "resource_updates": {}}),
            ],
        }
    }

    async def persist(key):
        return None

    monkeypatch.setattr(workstation, "_persist", persist)
    report = await workstation.finalize_task("run", "prepare")
    assert report["files"]["stock-simulation.mp4"] == b"stock-video"
    assert report["files"]["diluent-simulation.mp4"] == b"diluent-video"


async def test_archive_reader_ignores_non_whitelisted_members():
    files, warnings = extract_archive_evidence(
        _archive({"request.json": b"{}", "../../secret": b"no", "samples.jsonl": b'{"x":1}\n'})
    )
    assert files == {"request.json": b"{}", "samples.jsonl": b'{"x":1}\n'}
    assert warnings == []


def test_recording_settings_reject_non_boolean_boundary_values():
    with pytest.raises(ValueError, match="record_video must be a boolean"):
        recording_settings(meta={"record_video": "true"})


async def test_api_mock_has_no_synthetic_curve():
    task = _task(
        output_parameters={"sample": {"backend": "mock"}},
        output_files=["sample.json", "preparation-context.json"],
        input_parameters={"stock_volume_ul": 74},
    )
    orchestrator = SimpleNamespace(
        results=_Outputs(
            {
                "sample.json": b'{"backend":"mock","stock_ul":74}',
                "preparation-context.json": b'{"backend":"mock","record_video":false,"hide_shell":false}',
            }
        )
    )
    payload = await matterix_controller._build_observation(orchestrator, task)
    assert payload["source"] == "mock"
    assert payload["samples"] == []
    assert payload["latest_sample"] is None
    assert payload["videos"] == []
    assert payload["result"] == task.output_parameters


async def test_archived_samples_take_missing_identity_from_their_actual_request():
    task = _task(output_files=["request.json", "samples.jsonl"])
    outputs = _Outputs({
        "request.json": json.dumps({"request_id": "r", "session_id": "s", "scene_id": "pipetting_realtime"}).encode(),
        "samples.jsonl": b'{"request_id":"r","source":"simulation","sim_time_s":1}\n',
    })
    payload = await matterix_controller._build_observation(SimpleNamespace(results=outputs), task)
    assert payload["samples"][0]["session_id"] == "s"
    assert payload["samples"][0]["scene_id"] == "pipetting_realtime"


async def test_api_measurement_uses_saved_outputs_and_input_backend_without_archives():
    output = {"observation": {"source": "synthetic_model", "value_mm": 0.37}}
    task = _task(input_parameters={"sample": {"backend": "mock"}}, output_parameters=output)
    payload = await matterix_controller._build_observation(SimpleNamespace(results=_Outputs({})), task)
    assert payload["source"] == "mock"
    assert payload["result"] == output
    assert payload["samples"] == []
    assert payload["acceptance"] is None


async def test_api_simulation_preserves_units_result_acceptance_video_and_snapshot():
    sample = {
        "schema_version": 1,
        "source": "simulation",
        "request_id": "request-1",
        "session_id": "session",
        "sim_time_s": 4.5,
        "units": {"time": "s", "liquid_volume": "mL"},
        "semantics": {"liquid_volumes_ml": {"vial": 1.25}},
    }
    result = {
        "source": "simulation",
        "request_id": "request-1",
        "acceptance": {"passed": True, "checks": []},
        "video": {"frames": 100, "fps": 25, "duration_s": 4, "width": 960, "height": 540},
    }
    task = _task(
        input_parameters={"capability_id": "transfer", "record_video": False},
        meta={"matterix": {"record_video": True, "hide_shell": True}},
        output_files=["request.json", "result.json", "samples.jsonl", "simulation.mp4", "matterix-evidence.zip"],
    )
    outputs = _Outputs(
        {
            "request.json": b'{"parameters":{"record_video":true,"hide_shell":true}}',
            "result.json": json.dumps(result).encode(),
            "samples.jsonl": (json.dumps(sample) + "\n").encode(),
            "simulation.mp4": b"real-video-bytes",
            "matterix-evidence.zip": b"must not be read",
        }
    )
    orchestrator = SimpleNamespace(results=outputs)
    payload = await matterix_controller._build_observation(orchestrator, task)
    assert payload["source"] == "simulation"
    assert payload["samples"] == [sample]
    assert payload["latest_sample"]["units"] == {"time": "s", "liquid_volume": "mL"}
    assert payload["result"]["request_id"] == "request-1"
    assert payload["acceptance"]["passed"] is True
    assert payload["snapshot"]["submitted"]["parameters"]["record_video"] is False
    assert payload["snapshot"]["submitted"]["meta"]["matterix"]["record_video"] is True
    assert payload["snapshot"]["effective"]["parameters"]["parameters"]["record_video"] is True
    assert payload["videos"][0]["name"] == "simulation.mp4"
    assert payload["videos"][0]["frames"] == 100
    assert "simulation.mp4" not in outputs.accessed
    assert "matterix-evidence.zip" not in outputs.accessed


async def test_api_active_observations_come_from_the_assigned_device(monkeypatch):
    calls = []
    progress = {
        "request_id": "request-1",
        "source": "simulation",
        "request": {"session_id": "session"},
        "recent_samples": [
            {
                "source": "simulation",
                "request_id": "request-1",
                "session_id": "session",
                "sim_time_s": 1.0,
                "units": {"time": "s"},
            }
        ],
        "latest_sample": {
            "source": "simulation",
            "request_id": "request-1",
            "session_id": "session",
            "sim_time_s": 1.5,
            "units": {"time": "s"},
        },
    }

    class Remote:
        async def remote(self, protocol_run_name, task_name):
            calls.append((protocol_run_name, task_name))
            return progress

    manager = SimpleNamespace(get_device_actor=lambda lab, name: SimpleNamespace(get_execution_progress=Remote()))
    monkeypatch.setattr(matterix_controller, "get_device_manager", lambda: manager)
    task = _task(
        status=TaskStatus.RUNNING,
        devices={"matterix": DeviceAssignmentDef(lab_name="lab", name="station")},
        output_files=[],
    )
    orchestrator = SimpleNamespace(results=_Outputs({}))
    payload = await matterix_controller._build_observation(orchestrator, task)
    assert calls == [("run", "phase")]
    assert [point["sim_time_s"] for point in payload["samples"]] == [1.0, 1.5]
    assert payload["source"] == "simulation"


async def test_api_uses_bounded_zip_only_for_legacy_tasks_without_extracted_evidence():
    sample = {"source": "simulation", "request_id": "old", "sim_time_s": 2.0, "units": {"time": "s"}}
    archive = _archive(
        {
            "request.json": b'{"parameters":{"record_video":true,"hide_shell":false}}',
            "result.json": b'{"source":"simulation","acceptance":{"passed":true}}',
            "samples.jsonl": (json.dumps(sample) + "\n").encode(),
            "simulation.mp4": b"legacy-video-is-not-read",
        }
    )
    outputs = _Outputs({"matterix-evidence.zip": archive})
    task = _task(output_files=["matterix-evidence.zip"])
    payload = await matterix_controller._build_observation(SimpleNamespace(results=outputs), task)
    assert outputs.accessed == ["matterix-evidence.zip"]
    assert payload["samples"] == [sample]
    assert payload["videos"] == []  # ZIP members are not independently downloadable S3 objects.
    assert [file["name"] for file in payload["files"]] == ["matterix-evidence.zip"]


async def test_protocol_api_aggregates_five_tabs_and_only_the_protocol_video():
    matterix_controller._TASK_OBSERVATION_CACHE.clear()
    start = datetime(2026, 1, 1, tzinfo=UTC)
    prepare_inputs = {
        "capability_id": "transfer",
        "parameters": {"record_video": True, "stock_ul": 74},
    }
    measure_inputs = {
        "capability_id": "measure",
        "parameters": {"record_video": True, "sample_id": "sample-1"},
    }
    first = _task(
        name="prepare",
        input_parameters=prepare_inputs,
        meta={"matterix": {"record_video": True}},
        output_files=["request.json", "result.json", "samples.jsonl", "simulation.mp4"],
        start_time=start,
    )
    second = _task(
        name="measure",
        input_parameters=measure_inputs,
        meta={"matterix": {"record_video": True}},
        output_files=["request.json", "result.json", "samples.jsonl", "stock-simulation.mp4"],
        start_time=start + timedelta(seconds=10),
    )
    sample_a = {"source": "simulation", "session_id": "session-a", "sim_time_s": 1.0}
    sample_b = {"source": "simulation", "sim_time_s": 0.5}
    acceptance = {"passed": True, "checks": [{"name": "real", "passed": True}]}
    outputs = _ProtocolOutputs(
        {
            ("prepare", "request.json"): json.dumps(
                {"parameters": {"record_video": True, "stock_ul": 74}}
            ).encode(),
            ("prepare", "result.json"): json.dumps({"source": "simulation", "acceptance": acceptance}).encode(),
            ("prepare", "samples.jsonl"): (json.dumps(sample_a) + "\n").encode(),
            ("measure", "request.json"): json.dumps(
                {"parameters": {"record_video": True, "sample_id": "sample-1"}}
            ).encode(),
            ("measure", "result.json"): json.dumps({"source": "simulation", "acceptance": acceptance}).encode(),
            ("measure", "samples.jsonl"): (json.dumps(sample_b) + "\n").encode(),
        }
    )
    manifest = {
        "schema_version": 1,
        "protocol_run_name": "run",
        "task_name": "__protocol__",
        "status": "ready",
        "complete": True,
        "segments": [
            {"task_name": "prepare", "file_name": "simulation.mp4"},
            {"task_name": "measure", "file_name": "stock-simulation.mp4"},
        ],
        "target": {"fps": 25, "width": 960, "height": 540},
        "disclaimer": "协议视频只包含实际已录制的任务步骤，不包含调度等待时间。",
    }
    file_db = _ProtocolFileDb(
        {
            "run/__protocol__/protocol-simulation.mp4": b"video",
            "run/__protocol__/recording.json": json.dumps(manifest).encode(),
        }
    )
    protocol_run = ProtocolRun(
        name="run",
        type="matterix_full_station",
        status=ProtocolRunStatus.COMPLETED,
        parameters={},
        meta={"matterix": {"record_video": True}},
    )

    payload = await matterix_controller._build_protocol_observation(
        SimpleNamespace(results=outputs),
        protocol_run,
        [second, first],
        file_db,
    )

    assert payload["schema_version"] == 1
    assert payload["protocol_run_name"] == "run"
    assert payload["task_name"] == "__protocol__"
    assert payload["scope"] == "protocol"
    assert payload["status"] == "COMPLETED"
    assert payload["source"] == "simulation"
    assert payload["snapshot"]["submitted"]["parameters"] == {
        "prepare": prepare_inputs,
        "measure": measure_inputs,
    }
    assert payload["snapshot"]["effective"]["parameters"] == {
        "prepare": prepare_inputs,
        "measure": measure_inputs,
    }
    assert payload["result"]["protocol_run"]["parameters"] == {}
    assert payload["result"]["tasks"] == [
        {
            "name": "prepare",
            "type": "Matterix Capability",
            "status": "COMPLETED",
            "start_time": start.isoformat(),
            "end_time": None,
            "error": None,
        },
        {
            "name": "measure",
            "type": "Matterix Capability",
            "status": "COMPLETED",
            "start_time": (start + timedelta(seconds=10)).isoformat(),
            "end_time": None,
            "error": None,
        },
    ]
    assert [sample["task_name"] for sample in payload["samples"]] == ["prepare", "measure"]
    assert payload["samples"][0]["protocol_time_s"] == 1.0
    assert payload["samples"][1]["protocol_time_s"] > payload["samples"][0]["protocol_time_s"]
    assert payload["latest_sample"]["task_name"] == "measure"
    assert payload["acceptance"]["passed"] is True
    assert payload["acceptance"]["stages"]["prepare"]["task_type"] == "Matterix Capability"
    assert payload["recording"]["status"] == "ready"
    assert payload["recording"]["complete"] is True
    assert "不包含调度等待" in payload["recording"]["disclaimer"]
    assert [video["name"] for video in payload["videos"]] == ["protocol-simulation.mp4"]
    assert {file["task_name"] for file in payload["files"]} == {"prepare", "measure", "__protocol__"}
    assert "semantic_history" not in json.dumps(payload["result"])


async def test_protocol_api_never_labels_mixed_mock_and_simulation_as_simulation():
    matterix_controller._TASK_OBSERVATION_CACHE.clear()
    simulation = _task(
        name="simulated",
        output_files=["result.json"],
        output_parameters={"acceptance": {"passed": True}},
    )
    mock = _task(
        name="measured",
        output_files=["result.json"],
        output_parameters={"acceptance": {"passed": True}},
    )
    outputs = _ProtocolOutputs(
        {
            ("simulated", "result.json"): b'{"source":"simulation","acceptance":{"passed":true}}',
            ("measured", "result.json"): b'{"source":"mock","acceptance":{"passed":true}}',
        }
    )
    manifest = {
        "schema_version": 1,
        "protocol_run_name": "run",
        "status": "failed",
        "complete": True,
        "error": "ffmpeg assembly failed",
        "segments": [],
    }
    file_db = _ProtocolFileDb(
        {"run/__protocol__/recording.json": json.dumps(manifest).encode()}
    )
    protocol_run = ProtocolRun(name="run", type="protocol", status=ProtocolRunStatus.FAILED)

    payload = await matterix_controller._build_protocol_observation(
        SimpleNamespace(results=outputs),
        protocol_run,
        [simulation, mock],
        file_db,
    )

    assert payload["source"] is None
    assert "mixed_sources:mock,simulation" in payload["warnings"]
    assert payload["acceptance"]["passed"] is False
    assert payload["recording"]["status"] == "failed"
    assert payload["recording"]["complete"] is False
    assert payload["videos"] == []


async def test_created_protocol_uses_submitted_recording_meta_before_tasks_exist():
    protocol_run = ProtocolRun(
        name="run",
        type="protocol",
        status=ProtocolRunStatus.CREATED,
        meta={"matterix": {"record_video": True}},
    )

    payload = await matterix_controller._build_protocol_observation(
        SimpleNamespace(results=SimpleNamespace()),
        protocol_run,
        [],
        _ProtocolFileDb({}),
    )

    assert payload["recording"]["status"] == "pending"
    assert payload["recording"]["complete"] is False


async def test_protocol_acceptance_uses_begin_reset_and_end_experiment_only():
    begin = _task(
        name="begin",
        task_type="Matterix Begin Experiment",
        output_parameters={
            "state": {
                "reset_acceptance": {"passed": True, "checks": [{"name": "initial_state"}]},
                "experiment_acceptance": {"passed": False},
            }
        },
    )
    end = _task(
        name="end",
        task_type="Matterix End Experiment",
        output_parameters={
            "state": {
                "reset_acceptance": {"passed": False},
                "experiment_acceptance": {"passed": True, "checks": [{"name": "owner_idle"}]},
            }
        },
    )
    analysis = _task(
        name="measure",
        task_type="Measure Mixture",
        output_parameters={"state": {"reset_acceptance": {"passed": True}}},
    )
    protocol_run = ProtocolRun(name="run", type="protocol", status=ProtocolRunStatus.COMPLETED)

    payload = await matterix_controller._build_protocol_observation(
        SimpleNamespace(results=SimpleNamespace()),
        protocol_run,
        [begin, end, analysis],
        _ProtocolFileDb({}),
    )

    stages = payload["acceptance"]["stages"]
    assert stages["begin"]["acceptance"]["checks"][0]["name"] == "initial_state"
    assert stages["end"]["acceptance"]["checks"][0]["name"] == "owner_idle"
    assert stages["measure"]["acceptance"] is None
    assert payload["acceptance"]["passed"] is False


async def test_protocol_api_bounds_concurrency_and_caches_terminal_tasks(monkeypatch):
    matterix_controller._TASK_OBSERVATION_CACHE.clear()
    tasks = [_task(name=f"task-{index}") for index in range(8)]
    active = 0
    maximum = 0
    calls = 0

    async def build(orchestrator, task):
        nonlocal active, maximum, calls
        calls += 1
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {
            "samples": [],
            "warnings": [],
            "source": None,
            "snapshot": {"effective": {"parameters": {}}},
            "acceptance": None,
            "files": [],
        }

    monkeypatch.setattr(matterix_controller, "_build_observation", build)
    protocol_run = ProtocolRun(name="run", type="protocol", status=ProtocolRunStatus.COMPLETED)
    file_db = _ProtocolFileDb({})
    orchestrator = SimpleNamespace(results=SimpleNamespace())

    await matterix_controller._build_protocol_observation(
        orchestrator, protocol_run, tasks, file_db
    )
    await matterix_controller._build_protocol_observation(
        orchestrator, protocol_run, tasks, file_db
    )

    assert maximum == 4
    assert calls == 8


async def test_protocol_observation_cache_is_store_isolated(monkeypatch):
    matterix_controller._TASK_OBSERVATION_CACHE.clear()
    calls = 0

    async def build(orchestrator, task):
        nonlocal calls
        calls += 1
        return {
            "samples": [],
            "warnings": [],
            "source": "simulation",
            "snapshot": {"effective": {"parameters": {"store": id(orchestrator.results)}}},
            "acceptance": {"passed": True},
            "files": [],
        }

    monkeypatch.setattr(matterix_controller, "_build_observation", build)
    task = _task(name="phase")
    protocol_run = ProtocolRun(name="run", type="protocol", status=ProtocolRunStatus.COMPLETED)
    first_orchestrator = SimpleNamespace(results=SimpleNamespace())
    second_orchestrator = SimpleNamespace(results=SimpleNamespace())
    first_file_db = _ProtocolFileDb({})
    second_file_db = _ProtocolFileDb({})

    first = await matterix_controller._build_protocol_observation(
        first_orchestrator, protocol_run, [task], first_file_db
    )
    second = await matterix_controller._build_protocol_observation(
        second_orchestrator, protocol_run, [task], second_file_db
    )
    await matterix_controller._build_protocol_observation(
        first_orchestrator, protocol_run, [task], first_file_db
    )

    assert calls == 2
    first_store = first["snapshot"]["effective"]["parameters"]["phase"]["store"]
    second_store = second["snapshot"]["effective"]["parameters"]["phase"]["store"]
    assert first_store != second_store


async def test_protocol_observation_cache_skips_oversized_entry(monkeypatch):
    matterix_controller._TASK_OBSERVATION_CACHE.clear()
    calls = 0

    async def build(orchestrator, task):
        nonlocal calls
        calls += 1
        return {
            "samples": [],
            "warnings": [],
            "source": "simulation",
            "snapshot": {"effective": {"parameters": {}}},
            "acceptance": {"passed": True},
            "files": [],
            "large": "x" * 256,
        }

    monkeypatch.setattr(matterix_controller, "MAX_CACHED_TASK_OBSERVATION_BYTES", 64)
    monkeypatch.setattr(matterix_controller, "_build_observation", build)
    orchestrator = SimpleNamespace(results=SimpleNamespace())
    protocol_run = ProtocolRun(name="run", type="protocol", status=ProtocolRunStatus.COMPLETED)
    file_db = _ProtocolFileDb({})

    await matterix_controller._build_protocol_observation(orchestrator, protocol_run, [_task()], file_db)
    await matterix_controller._build_protocol_observation(orchestrator, protocol_run, [_task()], file_db)

    assert calls == 2
    assert not matterix_controller._TASK_OBSERVATION_CACHE


async def test_protocol_observation_cache_enforces_global_byte_budget(monkeypatch):
    matterix_controller._TASK_OBSERVATION_CACHE.clear()
    calls = 0

    async def build(orchestrator, task):
        nonlocal calls
        calls += 1
        return {
            "samples": [],
            "warnings": [],
            "source": "simulation",
            "snapshot": {"effective": {"parameters": {}}},
            "acceptance": {"passed": True},
            "files": [],
            "payload": "x" * 200,
        }

    monkeypatch.setattr(matterix_controller, "_build_observation", build)
    orchestrator = SimpleNamespace(results=SimpleNamespace())
    file_db = _ProtocolFileDb({})
    protocol_run = ProtocolRun(name="run", type="protocol", status=ProtocolRunStatus.COMPLETED)
    tasks = [_task(name=name) for name in ("a", "b", "c")]

    await matterix_controller._build_protocol_observation(orchestrator, protocol_run, [tasks[0]], file_db)
    entry_size = next(iter(matterix_controller._TASK_OBSERVATION_CACHE.values()))[2]
    monkeypatch.setattr(matterix_controller, "MAX_TASK_OBSERVATION_CACHE_BYTES", entry_size + 16)
    await matterix_controller._build_protocol_observation(orchestrator, protocol_run, [tasks[1]], file_db)
    await matterix_controller._build_protocol_observation(orchestrator, protocol_run, [tasks[2]], file_db)
    await matterix_controller._build_protocol_observation(orchestrator, protocol_run, [tasks[0]], file_db)

    assert calls == 4
    assert sum(entry[2] for entry in matterix_controller._TASK_OBSERVATION_CACHE.values()) <= entry_size + 16
