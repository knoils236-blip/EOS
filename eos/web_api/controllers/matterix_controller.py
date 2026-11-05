import asyncio
import json
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any, ClassVar
from urllib.parse import quote

from litestar import Controller, get

from eos.auth.authorization import require_role
from eos.auth.entities.user_role import Role
from eos.database.abstract_sql_db_interface import AsyncDbSession
from eos.database.file_db_interface import FileDbInterface
from eos.orchestration.orchestrator import Orchestrator
from eos.protocols.entities.protocol_run import ProtocolRun
from eos.protocols.matterix_evidence import (
    PROTOCOL_TASK_NAME,
    PROTOCOL_VIDEO_NAME,
    actual_task_order,
    list_protocol_artifacts,
    load_recording_manifest,
    protocol_artifact_path,
    protocol_samples,
    recording_object,
    recording_requested,
)
from eos.tasks.entities.task import Task, TaskStatus
from eos.tasks.task_definition import build_task_output_file_path
from eos.utils.di.di_deps import get_device_manager, get_file_db_interface, get_task_manager
from eos.web_api.exception_handling import APIError

PROGRESS_TIMEOUT_S = 1.5
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_JSONL_BYTES = 16 * 1024 * 1024
MAX_LEGACY_ARCHIVE_BYTES = 96 * 1024 * 1024
MAX_PROTOCOL_TASKS_CONCURRENCY = 4
MAX_CACHED_TASK_OBSERVATIONS = 256
MAX_CACHED_TASK_OBSERVATION_BYTES = 8 * 1024 * 1024
MAX_TASK_OBSERVATION_CACHE_BYTES = 32 * 1024 * 1024
_TASK_OBSERVATION_CACHE: OrderedDict[
    tuple[str, str, str, str, str],
    tuple[tuple[Any, ...], dict[str, Any], int],
] = OrderedDict()


class MatterixController(Controller):
    """Read Matterix execution observations and archived evidence."""

    path = "/matterix"
    guards: ClassVar = [require_role(Role.VIEWER)]

    @get("/{protocol_run_name:str}")
    async def get_protocol_observation(
        self,
        protocol_run_name: str,
        db: AsyncDbSession,
        orchestrator: Orchestrator,
    ) -> dict[str, Any]:
        protocol_run = await orchestrator.protocols.get_protocol_run(db, protocol_run_name)
        if protocol_run is None:
            raise APIError(status_code=404, detail="Protocol run not found")
        task_manager = get_task_manager()
        if task_manager is None:
            raise APIError(status_code=503, detail="Task manager is unavailable")
        tasks = await task_manager.get_tasks(db, protocol_run_name=protocol_run_name)
        file_db = get_file_db_interface()
        if file_db is None:
            raise APIError(status_code=503, detail="File storage is unavailable")
        return await _build_protocol_observation(orchestrator, protocol_run, tasks, file_db)

    @get("/{protocol_run_name:str}/{task_name:str}")
    async def get_observation(
        self,
        protocol_run_name: str,
        task_name: str,
        db: AsyncDbSession,
        orchestrator: Orchestrator,
    ) -> dict[str, Any]:
        task = await orchestrator.tasks.get_task(db, protocol_run_name, task_name)
        protocol_run = await orchestrator.protocols.get_protocol_run(db, protocol_run_name)
        if task is None or protocol_run is None:
            raise APIError(status_code=404, detail="Matterix task not found")
        return await _build_observation(orchestrator, task)


async def _build_protocol_observation(
    orchestrator: Orchestrator,
    protocol_run: ProtocolRun,
    tasks: Sequence[Task],
    file_db: FileDbInterface,
) -> dict[str, Any]:
    ordered_tasks = sorted(tasks, key=actual_task_order)
    cache_scope = (
        str(id(orchestrator)),
        str(id(orchestrator.results)),
        str(id(file_db)),
    )
    observations = await _task_observations(orchestrator, ordered_tasks, cache_scope)
    samples = protocol_samples(ordered_tasks, observations)
    warnings = [
        f"task:{task.name}:{warning}"
        for task in ordered_tasks
        for warning in observations[task.name].get("warnings", [])
    ]
    source, source_warnings = _protocol_source(observations)
    warnings.extend(source_warnings)

    artifact_names: list[str] = []
    try:
        artifact_names = await list_protocol_artifacts(file_db, protocol_run.name)
    except Exception as exc:
        warnings.append(f"protocol_artifacts_unavailable:{type(exc).__name__}")
    manifest, manifest_warnings = await load_recording_manifest(
        file_db,
        protocol_run.name,
        artifacts=artifact_names,
    )
    warnings.extend(manifest_warnings)
    if manifest:
        warnings.extend(f"protocol_recording:{warning}" for warning in manifest.get("warnings", []))
    status = protocol_run.status.value
    recording = recording_object(
        manifest,
        protocol_status=status,
        requested=recording_requested(ordered_tasks, protocol_run.meta),
        artifact_names=artifact_names,
    )
    warnings.extend(recording.get("warnings", []))
    if error := recording.get("error"):
        warnings.append(f"protocol_recording:{error}")

    videos = []
    if recording["status"] in {"ready", "partial"}:
        video_path = protocol_artifact_path(protocol_run.name, PROTOCOL_VIDEO_NAME)
        if video_path in artifact_names:
            videos.append(
                {
                    "name": PROTOCOL_VIDEO_NAME,
                    "content_type": "video/mp4",
                    "source": "simulation",
                    "key": video_path,
                    "download_url": (
                        f"/api/files/download/{quote(protocol_run.name, safe='')}/"
                        f"{quote(PROTOCOL_TASK_NAME, safe='')}/{quote(PROTOCOL_VIDEO_NAME, safe='')}"
                    ),
                    **(manifest.get("target") if manifest else {}),
                }
            )

    submitted_parameters = {
        task.name: dict(task.input_parameters or {})
        for task in ordered_tasks
    }
    effective_parameters = {
        task.name: _protocol_effective_parameters(observations[task.name])
        for task in ordered_tasks
    }
    return {
        "schema_version": 1,
        "protocol_run_name": protocol_run.name,
        "task_name": PROTOCOL_TASK_NAME,
        "scope": "protocol",
        "status": status,
        "source": source,
        "snapshot": {
            "submitted": {
                "parameters": submitted_parameters,
                "meta": dict(protocol_run.meta or {}),
            },
            "effective": {
                "parameters": effective_parameters,
                "meta": dict(protocol_run.meta or {}),
            },
        },
        "samples": samples,
        "latest_sample": samples[-1] if samples else None,
        "acceptance": _protocol_acceptance(protocol_run, ordered_tasks, observations),
        "videos": videos,
        "recording": recording,
        "files": _protocol_files(ordered_tasks, observations, artifact_names),
        "result": _protocol_result(protocol_run, ordered_tasks),
        "warnings": list(dict.fromkeys(warnings)),
    }


async def _task_observations(
    orchestrator: Orchestrator,
    tasks: Sequence[Task],
    cache_scope: tuple[str, str, str],
) -> dict[str, dict[str, Any]]:
    semaphore = asyncio.Semaphore(MAX_PROTOCOL_TASKS_CONCURRENCY)

    async def build(task: Task) -> dict[str, Any]:
        fingerprint = _task_fingerprint(task)
        cache_key = (*cache_scope, task.protocol_run_name or "", task.name)
        if cached := _TASK_OBSERVATION_CACHE.get(cache_key):
            cached_fingerprint, payload, _ = cached
            if cached_fingerprint == fingerprint:
                _TASK_OBSERVATION_CACHE.move_to_end(cache_key)
                return payload
        async with semaphore:
            payload = await _build_observation(orchestrator, task)
        if task.status in {
            TaskStatus.COMPLETED,
            TaskStatus.SKIPPED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }:
            size = len(json.dumps(payload, ensure_ascii=False).encode())
            if size > MAX_CACHED_TASK_OBSERVATION_BYTES:
                return payload
            _TASK_OBSERVATION_CACHE.pop(cache_key, None)
            _TASK_OBSERVATION_CACHE[cache_key] = (fingerprint, payload, size)
            while len(_TASK_OBSERVATION_CACHE) > MAX_CACHED_TASK_OBSERVATIONS:
                _TASK_OBSERVATION_CACHE.popitem(last=False)
            while sum(entry[2] for entry in _TASK_OBSERVATION_CACHE.values()) > MAX_TASK_OBSERVATION_CACHE_BYTES:
                _TASK_OBSERVATION_CACHE.popitem(last=False)
        return payload

    built = await asyncio.gather(*(build(task) for task in tasks))
    return {task.name: payload for task, payload in zip(tasks, built, strict=True)}


def _task_fingerprint(task: Task) -> tuple[Any, ...]:
    return (
        task.status.value,
        task.start_time.isoformat() if task.start_time else None,
        task.end_time.isoformat() if task.end_time else None,
        tuple(task.output_file_names or []),
    )


def _protocol_source(observations: Mapping[str, Mapping[str, Any]]) -> tuple[str | None, list[str]]:
    sources = {observation.get("source") for observation in observations.values()}
    sources.discard(None)
    if len(sources) == 1:
        return str(sources.pop()), []
    if sources:
        return None, [f"mixed_sources:{','.join(sorted(str(source) for source in sources))}"]
    return None, []


def _protocol_acceptance(
    protocol_run: ProtocolRun,
    tasks: Sequence[Task],
    observations: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    stages = {}
    for task in tasks:
        stages[task.name] = {
            "acceptance": observations[task.name].get("acceptance"),
            "status": task.status.value,
            "task_type": task.type,
        }
    passed = (
        protocol_run.status.value == "COMPLETED"
        and bool(stages)
        and all(
            isinstance(stage["acceptance"], Mapping) and stage["acceptance"].get("passed") is True
            for stage in stages.values()
        )
    )
    return {"passed": passed, "stages": stages}


def _protocol_effective_parameters(observation: Mapping[str, Any]) -> dict[str, Any]:
    parameters = observation["snapshot"]["effective"]["parameters"]
    stages = parameters.get("stages")
    if isinstance(stages, Mapping) and stages:
        return {stage: dict(request) for stage, request in stages.items() if isinstance(request, Mapping)}
    return dict(parameters)


def _protocol_files(
    tasks: Sequence[Task],
    observations: Mapping[str, Mapping[str, Any]],
    artifact_names: Sequence[str],
) -> list[dict[str, Any]]:
    files = []
    for task in tasks:
        for file in observations[task.name].get("files", []):
            files.append({**file, "task_name": task.name})
    for key in artifact_names:
        name = PurePosixPath(key).name
        if not name:
            continue
        suffix = PurePosixPath(name).suffix.lower()
        files.append(
            {
                "name": name,
                "key": key,
                "kind": {".mp4": "video", ".json": "json"}.get(suffix, "binary"),
                "task_name": PROTOCOL_TASK_NAME,
            }
        )
    return files


def _protocol_result(protocol_run: ProtocolRun, tasks: Sequence[Task]) -> dict[str, Any]:
    return {
        "protocol_run": {
            "name": protocol_run.name,
            "status": protocol_run.status.value,
            "start_time": protocol_run.start_time.isoformat() if protocol_run.start_time else None,
            "end_time": protocol_run.end_time.isoformat() if protocol_run.end_time else None,
            "error": protocol_run.error_message,
            "parameters": dict(protocol_run.parameters or {}),
        },
        "tasks": [
            {
                "name": task.name,
                "type": task.type,
                "status": task.status.value,
                "start_time": task.start_time.isoformat() if task.start_time else None,
                "end_time": task.end_time.isoformat() if task.end_time else None,
                "error": task.error_message,
            }
            for task in tasks
        ],
    }


async def _build_observation(orchestrator: Orchestrator, task: Task) -> dict[str, Any]:
    from matterix_sim.observations import (  # noqa: PLC0415
        MAX_DEFAULT_SAMPLES,
        extract_archive_evidence,
        normalize_samples,
        parse_json,
        results_from_files,
        samples_from_files,
        video_metadata,
    )

    warnings: list[str] = []
    active = task.status == TaskStatus.RUNNING
    progress = await _progress(orchestrator, task, active, warnings) if active else []
    file_names, metadata = await _metadata_files(orchestrator, task, warnings, extract_archive_evidence)
    results = results_from_files(metadata)
    results.update(
        {
            f"request:{index}": state["result"]
            for index, state in enumerate(progress)
            if isinstance(state.get("result"), dict)
        }
    )
    samples = _active_samples(progress, normalize_samples) if active else samples_from_files(metadata)
    samples = [
        point for point in normalize_samples(samples, limit=MAX_DEFAULT_SAMPLES) if point.get("source") == "simulation"
    ]
    requests = [parse_json(data) for name, data in metadata.items() if name.endswith("request.json")]
    requests.extend(
        {"request_id": state.get("request_id"), **state["request"]}
        for state in progress if isinstance(state.get("request"), dict)
    )
    identities = {
        request["request_id"]: {key: request[key] for key in ("session_id", "scene_id") if key in request}
        for request in requests if isinstance(request, dict) and request.get("request_id")
    }
    samples = [{**identities.get(point.get("request_id"), {}), **point} for point in samples]
    source = _source(task, results, metadata, progress, parse_json)
    if source is None and (results or progress):
        warnings.append("source_not_recorded")
    return {
        "schema_version": 1,
        "protocol_run_name": task.protocol_run_name,
        "task_name": task.name,
        "status": task.status.value,
        "source": source,
        "snapshot": _snapshot(task, metadata, parse_json, progress),
        "samples": samples,
        "latest_sample": samples[-1] if samples else None,
        "acceptance": _acceptance(task, results),
        "videos": _videos(task, file_names, results, video_metadata),
        "files": _file_list(task, file_names),
        "result": (next(iter(results.values())) if len(results) == 1 else results) or task.output_parameters or None,
        "warnings": warnings,
    }


async def _progress(orchestrator: Orchestrator, task: Task, active: bool, warnings: list[str]) -> list[dict[str, Any]]:
    if not active:
        return []
    states = []
    for assignment in task.devices.values():
        try:
            actor = get_device_manager().get_device_actor(assignment.lab_name, assignment.name)
            value = await asyncio.wait_for(
                actor.get_execution_progress.remote(task.protocol_run_name, task.name),
                PROGRESS_TIMEOUT_S,
            )
        except Exception as exc:
            warnings.append(f"progress_unavailable:{type(exc).__name__}")
            continue
        if isinstance(value, dict):
            states.append(value)
        elif isinstance(value, list):
            states.extend(item for item in value if isinstance(item, dict))
    return states


async def _metadata_files(
    orchestrator: Orchestrator,
    task: Task,
    warnings: list[str],
    extract,
) -> tuple[list[str], dict[str, bytes]]:
    names = _output_names(task, warnings)
    metadata = {}
    for name in names:
        if not name.endswith((".json", ".jsonl")):
            continue
        limit = MAX_JSONL_BYTES if name.endswith(".jsonl") else MAX_JSON_BYTES
        try:
            metadata[name] = await _read_output(orchestrator, task, name, limit)
        except Exception as exc:
            warnings.append(f"file_unavailable:{name}:{type(exc).__name__}")

    if not any(name.endswith(("result.json", "samples.jsonl")) for name in names):
        await _legacy_metadata(orchestrator, task, names, metadata, warnings, extract)
    return list(dict.fromkeys(names)), metadata


def _output_names(task: Task, warnings: list[str]) -> list[str]:
    names = []
    for raw_name in task.output_file_names or []:
        name = PurePosixPath(raw_name).name
        if name == raw_name and name:
            names.append(name)
        else:
            warnings.append("unsafe_output_file_name")
    return list(dict.fromkeys(names))


async def _legacy_metadata(
    orchestrator: Orchestrator,
    task: Task,
    names: list[str],
    metadata: dict[str, bytes],
    warnings: list[str],
    extract,
) -> None:
    for archive_name in (name for name in names if name.endswith("evidence.zip")):
        try:
            archive = await _read_output(orchestrator, task, archive_name, MAX_LEGACY_ARCHIVE_BYTES)
        except Exception as exc:
            warnings.append(f"file_unavailable:{archive_name}:{type(exc).__name__}")
            continue
        extracted, archive_warnings = extract(archive, include_video=False)
        warnings.extend(archive_warnings)
        warnings.append("历史证据仅保存在 ZIP 中，内部视频和原始文件请下载证据包查看。")
        if archive_name.endswith("-matterix-evidence.zip"):
            prefix = archive_name.removesuffix("-matterix-evidence.zip")
        elif archive_name == "matterix-evidence.zip":
            prefix = ""
        else:
            prefix = archive_name.removesuffix("evidence.zip").rstrip("-")
        for member, data in extracted.items():
            name = f"{prefix}-{member}" if prefix else member
            if member.endswith((".json", ".jsonl")):
                metadata[name] = data


async def _read_output(orchestrator: Orchestrator, task: Task, name: str, limit: int) -> bytes:
    chunks = []
    size = 0
    async for chunk in orchestrator.results.download_task_output_file(task.protocol_run_name, task.name, name):
        size += len(chunk)
        if size > limit:
            raise ValueError("output file exceeds metadata read limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _active_samples(progress: list[dict[str, Any]], normalize_samples) -> list[dict[str, Any]]:
    samples = []
    for state in progress:
        request = state.get("request") if isinstance(state.get("request"), dict) else {}
        identity = {"request_id": state.get("request_id"), "session_id": request.get("session_id")}
        points = list(state.get("recent_samples") or [])
        if isinstance(state.get("latest_sample"), dict):
            points.append(state["latest_sample"])
        samples.extend(normalize_samples(points, **identity))
    return samples


def _source(task, results, metadata, progress, parse_json) -> str | None:
    sources = {result["source"] for result in results.values() if result.get("source") in {"simulation", "mock"}}
    sources.update(state["source"] for state in progress if state.get("source") in {"simulation", "mock"})
    for parameters in (task.output_parameters or {}, task.input_parameters or {}):
        for name in ("sample", "observation"):
            sample = parameters.get(name)
            if isinstance(sample, dict) and sample.get("backend") in {"simulation", "mock"}:
                sources.add(sample["backend"])
    for name, data in metadata.items():
        if name.endswith("preparation-context.json"):
            context = parse_json(data)
            if isinstance(context, dict) and context.get("backend") in {"simulation", "mock"}:
                sources.add(context["backend"])
    return sources.pop() if len(sources) == 1 else None


def _acceptance(task: Task, results: dict[str, dict[str, Any]]) -> dict[str, Any] | None:  # noqa: PLR0911
    output = task.output_parameters or {}
    state = output.get("state")
    if task.type == "Matterix Begin Experiment":
        return state.get("reset_acceptance") if isinstance(state, dict) and isinstance(
            state.get("reset_acceptance"), dict
        ) else None
    if task.type == "Matterix End Experiment":
        return state.get("experiment_acceptance") if isinstance(state, dict) and isinstance(
            state.get("experiment_acceptance"), dict
        ) else None
    stages = {
        name: result["acceptance"] for name, result in results.items() if isinstance(result.get("acceptance"), dict)
    }
    if len(stages) == 1:
        return next(iter(stages.values()))
    if stages:
        return {"passed": all(item.get("passed") is True for item in stages.values()), "stages": stages}
    execution = output.get("matterix_execution")
    if isinstance(output.get("acceptance"), dict):
        return output["acceptance"]
    if isinstance(execution, dict) and isinstance(execution.get("acceptance"), dict):
        return execution["acceptance"]
    return None


def _file_list(task: Task, names: list[str]) -> list[dict[str, str]]:
    result = []
    for name in sorted(names):
        suffix = PurePosixPath(name).suffix.lower()
        kinds = {".mp4": "video", ".jsonl": "jsonl", ".json": "json", ".zip": "zip"}
        kind = kinds.get(suffix, "binary")
        result.append(
            {
                "name": name,
                "key": build_task_output_file_path(task.protocol_run_name, task.name, name),
                "kind": kind,
            }
        )
    return result


def _videos(
    task: Task,
    names: list[str],
    results: dict[str, dict[str, Any]],
    video_metadata,
) -> list[dict[str, Any]]:
    videos = []
    for name in names:
        if not name.endswith("simulation.mp4"):
            continue
        stage = name.removesuffix("simulation.mp4").rstrip("-")
        result = results.get(stage) if stage else next(iter(results.values()), None)
        metadata = video_metadata(name, result)
        metadata.update(
            {
                "key": build_task_output_file_path(task.protocol_run_name, task.name, name),
                "download_url": (
                    f"/api/files/download/{quote(task.protocol_run_name, safe='')}/"
                    f"{quote(task.name, safe='')}/{quote(name, safe='')}"
                ),
            }
        )
        videos.append(metadata)
    return videos


def _snapshot(task: Task, metadata: dict[str, bytes], parse_json, progress=()) -> dict[str, dict[str, Any]]:
    submitted_parameters = dict(task.input_parameters or {})
    effective_parameters = dict(submitted_parameters)
    requests = {}
    for name, data in metadata.items():
        if name.endswith("request.json"):
            request = parse_json(data)
            if isinstance(request, dict):
                requests[name.removesuffix("request.json").rstrip("-") or "run"] = request
    for state in progress:
        if isinstance(state.get("request"), dict):
            requests[state.get("request_id", "run")] = state["request"]
    for name, request in requests.items():
        parameters = request.get("parameters")
        if isinstance(parameters, dict):
            if task.type == "Matterix Capability":
                effective_parameters["parameters"] = parameters
            elif task.type == "Matterix Volume Operation":
                effective_parameters.update(parameters)
            else:
                effective_parameters.setdefault("stages", {})[name] = parameters
    meta = dict(task.meta or {})
    return {
        "submitted": {"parameters": submitted_parameters, "meta": meta},
        "effective": {"parameters": effective_parameters, "meta": meta},
    }
