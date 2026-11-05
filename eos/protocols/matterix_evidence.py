import asyncio
import json
import math
import shutil
import subprocess
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from eos.database.exceptions import EosFileDbError
from eos.database.file_db_interface import FileDbInterface
from eos.tasks.entities.task import Task
from eos.tasks.task_definition import build_task_output_file_path

CAPABILITY_TASK_TYPES = frozenset({"Matterix Capability", "Matterix Volume Operation"})
PROTOCOL_TASK_NAME = "__protocol__"
PROTOCOL_VIDEO_NAME = "protocol-simulation.mp4"
PROTOCOL_RECORDING_NAME = "recording.json"
TARGET_FPS = 25
TARGET_WIDTH = 1920
TARGET_HEIGHT = 540
MAX_PROTOCOL_SAMPLES = 5000
FFMPEG_TIMEOUT_S = 60 * 60
RECORDING_DISCLAIMER = "协议视频只包含实际已录制的任务步骤，不包含调度等待时间。"
RECORDING_STATUSES = frozenset(
    {"disabled", "pending", "recording", "ready", "partial", "failed", "unavailable"}
)


def protocol_artifact_path(protocol_run_name: str, file_name: str) -> str:
    return build_task_output_file_path(protocol_run_name, PROTOCOL_TASK_NAME, file_name)


def protocol_artifact_prefix(protocol_run_name: str) -> str:
    return build_task_output_file_path(protocol_run_name, PROTOCOL_TASK_NAME, "")


async def list_protocol_artifacts(file_db: FileDbInterface, protocol_run_name: str) -> list[str]:
    return sorted(await file_db.list_files(protocol_artifact_prefix(protocol_run_name)))


async def load_recording_manifest(
    file_db: FileDbInterface,
    protocol_run_name: str,
    artifacts: Iterable[str] | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    path = protocol_artifact_path(protocol_run_name, PROTOCOL_RECORDING_NAME)
    if artifacts is not None and path not in set(artifacts):
        return None, []
    try:
        data = await file_db.get_file(path)
        value = json.loads(data)
    except (EosFileDbError, FileNotFoundError):
        return None, []
    except (TypeError, ValueError, OSError):
        return None, ["recording_manifest_not_readable"]
    if not isinstance(value, dict):
        return None, ["recording_manifest_not_an_object"]
    if value.get("status") not in RECORDING_STATUSES:
        return None, ["recording_manifest_has_invalid_status"]
    return value, []


def actual_task_order(task: Task) -> tuple[bool, float, str]:
    when = task.start_time.timestamp() if task.start_time is not None else math.inf
    return task.start_time is None, when, task.name


def protocol_samples(
    tasks: Sequence[Task],
    observations: Mapping[str, Mapping[str, Any]],
    *,
    fps: int = TARGET_FPS,
    limit: int = MAX_PROTOCOL_SAMPLES,
) -> list[dict[str, Any]]:
    """Place real samples on one protocol timeline without interpolating missing points."""
    session_offsets: dict[Any, float] = {}
    session_indexes: dict[Any, int] = {}
    last_protocol_time = 0.0
    any_timed_sample = False
    timeline = []

    for task in sorted(tasks, key=actual_task_order):
        points = observations.get(task.name, {}).get("samples") or []
        missing_session_key = ("task", task.name)
        for point in points:
            if not isinstance(point, dict):
                continue
            sample = dict(point)
            session_id = sample.get("session_id")
            session_key = session_id if session_id is not None else missing_session_key
            if session_key not in session_offsets:
                session_indexes[session_key] = len(session_indexes)
                sim_time = _finite_number(sample.get("sim_time_s"))
                if not any_timed_sample or sim_time is None:
                    session_offsets[session_key] = 0.0
                else:
                    session_offsets[session_key] = last_protocol_time + (1 / fps) - sim_time

            sim_time = _finite_number(sample.get("sim_time_s"))
            protocol_time = None
            if sim_time is not None:
                protocol_time = sim_time + session_offsets[session_key]
                last_protocol_time = max(last_protocol_time, protocol_time)
                any_timed_sample = True

            sample["task_name"] = task.name
            sample["session_id"] = session_id
            sample["scene_id"] = sample.get("scene_id")
            sample["protocol_time_s"] = protocol_time
            sample["protocol_time_offset_s"] = session_offsets[session_key]
            sample["protocol_session_index"] = session_indexes[session_key]
            timeline.append(sample)
    return _limit_protocol_samples(timeline, limit)


def recording_requested(tasks: Iterable[Task], user_meta: Mapping[str, Any] | None = None) -> bool:
    meta = (user_meta or {}).get("matterix")
    if isinstance(meta, Mapping) and meta.get("record_video") is True:
        return True
    for task in tasks:
        meta = task.meta.get("matterix") if isinstance(task.meta, Mapping) else None
        if isinstance(meta, Mapping) and meta.get("record_video") is True:
            return True
        parameters = task.input_parameters or {}
        if parameters.get("record_video") is True:
            return True
        nested = parameters.get("parameters")
        if isinstance(nested, Mapping) and nested.get("record_video") is True:
            return True
        for value in (task.output_parameters or {}).values():
            if isinstance(value, Mapping) and value.get("record_video") is True:
                return True
    return False


def is_matterix_protocol(  # noqa: PLR0911
    tasks: Iterable[Task],
    user_meta: Mapping[str, Any] | None = None,
    protocol_type: str | None = None,
) -> bool:
    if protocol_type:
        protocol_type_name = protocol_type.lower()
        if "matterix" in protocol_type_name or "mixture" in protocol_type_name:
            return True
    if isinstance((user_meta or {}).get("matterix"), Mapping):
        return True
    for task in tasks:
        if task.type in CAPABILITY_TASK_TYPES:
            return True
        if any(name.endswith("simulation.mp4") for name in task.output_file_names or []):
            return True
        parameters = task.output_parameters or {}
        sample = parameters.get("sample")
        if isinstance(sample, Mapping) and sample.get("backend") in {"matterix", "simulation"}:
            return True
        if any(
            isinstance(value, Mapping) and value.get("backend") in {"matterix", "simulation"}
            for value in parameters.values()
        ):
            return True
    return False


def task_expects_video(task: Task) -> bool:
    if task.type in {"Matterix Begin Experiment", "Matterix End Experiment"}:
        return False
    parameters = task.output_parameters or {}
    output_recording = any(
        isinstance(value, Mapping) and value.get("record_video") is True for value in parameters.values()
    )
    input_recording = (task.input_parameters or {}).get("record_video") is True
    nested = (task.input_parameters or {}).get("parameters")
    input_recording = input_recording or (isinstance(nested, Mapping) and nested.get("record_video") is True)
    meta = task.meta.get("matterix") if isinstance(task.meta, Mapping) else None
    meta_recording = isinstance(meta, Mapping) and meta.get("record_video") is True

    if task.type in CAPABILITY_TASK_TYPES:
        return output_recording or input_recording or meta_recording
    if task.type == "Prepare Mixture":
        sample = parameters.get("sample")
        actual_backend = (isinstance(sample, Mapping) and sample.get("backend") in {"matterix", "simulation"}) or any(
            isinstance(value, Mapping) and value.get("backend") in {"matterix", "simulation"}
            for value in parameters.values()
        )
        return actual_backend and (output_recording or input_recording or meta_recording)
    return output_recording


def recording_object(
    manifest: Mapping[str, Any] | None,
    *,
    protocol_status: str,
    requested: bool,
    artifact_names: Iterable[str] = (),
) -> dict[str, Any]:
    names = set(artifact_names)
    video_exists = False
    if manifest:
        video_path = protocol_artifact_path(str(manifest.get("protocol_run_name")), PROTOCOL_VIDEO_NAME)
        video_exists = video_path in names
    if manifest is None:
        if not requested:
            status = "disabled"
        elif protocol_status == "CREATED":
            status = "pending"
        elif protocol_status == "RUNNING":
            status = "recording"
        else:
            status = "unavailable"
        value: dict[str, Any] = {"status": status, "complete": False, "segments": []}
        if status == "unavailable":
            value["error"] = "recording_manifest_missing"
    else:
        status = str(manifest["status"])
        if status == "ready" and protocol_status != "COMPLETED":
            status = "partial"
        value = {
            "status": status,
            "complete": status == "ready",
            "segments": list(manifest.get("segments") or []),
        }
        if status == "partial" and protocol_status != "COMPLETED":
            value["error"] = f"protocol ended as {protocol_status}"
        elif manifest.get("error"):
            value["error"] = str(manifest["error"])
        if status in {"ready", "partial"} and not video_exists:
            value["status"] = "failed"
            value["complete"] = False
            value["error"] = "protocol_video_missing"
    value["disclaimer"] = RECORDING_DISCLAIMER
    return value


async def finalize_protocol_recording(  # noqa: PLR0911, PLR0912
    protocol_run_name: str,
    tasks: Sequence[Task],
    file_db: FileDbInterface,
    *,
    protocol_status: str = "COMPLETED",
    user_meta: Mapping[str, Any] | None = None,
    protocol_type: str | None = None,
) -> dict[str, Any] | None:
    ordered_tasks = sorted(tasks, key=actual_task_order)
    if not is_matterix_protocol(ordered_tasks, user_meta, protocol_type):
        return None
    requested = recording_requested(tasks, user_meta)
    fragments = [
        (task, name)
        for task in ordered_tasks
        for name in task.output_file_names or []
        if PurePosixPath(name).name == "simulation.mp4" or PurePosixPath(name).name.endswith("-simulation.mp4")
    ]
    requested = requested or bool(fragments)
    if not requested:
        return await _store_manifest(
            protocol_run_name,
            file_db,
            _manifest(protocol_run_name, "disabled", segments=[]),
        )
    warnings: list[str] = []
    if not fragments:
        return await _store_manifest(
            protocol_run_name,
            file_db,
            _manifest(
                protocol_run_name,
                "failed",
                segments=[],
                error="; ".join(
                    ["record_video was requested but no recorded task segments were found", *warnings]
                ),
                warnings=warnings,
            ),
        )
    recorded_tasks = {task.name for task, _ in fragments}
    missing_tasks = sorted(
        task.name
        for task in ordered_tasks
        if task_expects_video(task) and task.name not in recorded_tasks
    )
    if missing_tasks:
        warnings.append(f"recorded_task_missing:{','.join(missing_tasks)}")

    ffmpeg = ffmpeg_executable()
    if ffmpeg is None:
        return await _store_manifest(
            protocol_run_name,
            file_db,
            _manifest(
                protocol_run_name,
                "failed",
                segments=_segments(fragments),
                error="ffmpeg is unavailable in the EOS environment, system PATH, and configured matterix_python",
            ),
        )

    with tempfile.TemporaryDirectory(prefix="matterix-protocol-video-") as directory:
        temporary_dir = Path(directory)
        downloaded: list[tuple[Task, str, Path]] = []
        for index, (task, file_name) in enumerate(fragments):
            input_path = temporary_dir / f"{index:04d}.mp4"
            try:
                with input_path.open("wb") as output:
                    async for chunk in file_db.stream_file(
                        build_task_output_file_path(protocol_run_name, task.name, file_name)
                    ):
                        await asyncio.to_thread(output.write, chunk)
            except Exception as exc:
                input_path.unlink(missing_ok=True)
                warnings.append(f"fragment_unavailable:{task.name}:{file_name}:{type(exc).__name__}")
                continue
            downloaded.append((task, file_name, input_path))
        if not downloaded:
            return await _store_manifest(
                protocol_run_name,
                file_db,
                _manifest(
                    protocol_run_name,
                    "failed",
                    segments=_segments(fragments),
                    error="; ".join(warnings) or "recorded task segments could not be read",
                    warnings=warnings,
                ),
            )
        input_paths = [path for _, _, path in downloaded]
        output_path = temporary_dir / PROTOCOL_VIDEO_NAME
        try:
            await _run_ffmpeg(ffmpeg, input_paths, output_path)
        except Exception as exc:
            return await _store_manifest(
                protocol_run_name,
                file_db,
                _manifest(
                    protocol_run_name,
                    "failed",
                    segments=_segments([(task, name) for task, name, _ in downloaded]),
                    error=f"ffmpeg assembly failed: {exc}",
                ),
            )
        if not output_path.is_file() or output_path.stat().st_size == 0:
            return await _store_manifest(
                protocol_run_name,
                file_db,
                _manifest(
                    protocol_run_name,
                    "failed",
                    segments=_segments([(task, name) for task, name, _ in downloaded]),
                    error="ffmpeg produced no protocol video",
                ),
            )
        try:
            await file_db.store_file(protocol_artifact_path(protocol_run_name, PROTOCOL_VIDEO_NAME), output_path)
        except Exception as exc:
            return await _store_manifest(
                protocol_run_name,
                file_db,
                _manifest(
                    protocol_run_name,
                    "failed",
                    segments=_segments([(task, name) for task, name, _ in downloaded]),
                    error=f"protocol video upload failed: {exc}",
                ),
            )

    status = "partial" if warnings or protocol_status != "COMPLETED" else "ready"
    if protocol_status != "COMPLETED":
        warnings.append(f"protocol_not_completed:{protocol_status}")
    manifest = _manifest(
        protocol_run_name,
        status,
        segments=_segments([(task, name) for task, name, _ in downloaded]),
        warnings=warnings,
    )
    return await _store_manifest(protocol_run_name, file_db, manifest)


def ffmpeg_executable() -> str | None:
    candidates = []
    try:
        import imageio_ffmpeg  # noqa: PLC0415

        candidates.append(imageio_ffmpeg.get_ffmpeg_exe())
    except (ImportError, RuntimeError):
        pass
    system = shutil.which("ffmpeg")
    if system:
        candidates.append(system)
    matterix_python = _matterix_python()
    if matterix_python:
        try:
            result = subprocess.run(
                [
                    matterix_python,
                    "-B",
                    "-c",
                    "import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())",
                ],
                check=False,
                capture_output=True,
                timeout=10,
                text=True,
            )
        except (OSError, subprocess.SubprocessError):
            result = None
        if result is not None and result.returncode == 0:
            candidates.append(result.stdout.strip())
    for candidate in dict.fromkeys(candidates):
        if not candidate or not Path(candidate).is_file():
            continue
        try:
            result = subprocess.run(
                [candidate, "-version"],
                check=False,
                capture_output=True,
                timeout=10,
                text=True,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            return candidate
    return None


def _matterix_python() -> str | None:
    """Read the bridge's own configuration through its existing JSON loader."""
    try:
        from integrations.matterix.bridge.common import ROOT, read_json  # noqa: PLC0415

        config = read_json(ROOT / "config.json")
    except (ImportError, OSError, ValueError):
        return None
    interpreter = config.get("matterix_python")
    return interpreter if isinstance(interpreter, str) and Path(interpreter).is_file() else None


async def _run_ffmpeg(executable: str, input_paths: Sequence[Path], output_path: Path) -> None:
    command = [
        executable,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-filter_threads",
        "1",
        "-y",
    ]
    for path in input_paths:
        command.extend(["-i", str(path)])
    filters = [
        (
            f"[{index}:v:0]scale={TARGET_WIDTH}:{TARGET_HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={TARGET_WIDTH}:{TARGET_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"fps={TARGET_FPS},setsar=1[v{index}]"
        )
        for index in range(len(input_paths))
    ]
    concat_inputs = "".join(f"[v{index}]" for index in range(len(input_paths)))
    filters.append(f"{concat_inputs}concat=n={len(input_paths)}:v=1:a=0[outv]")
    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[outv]",
            "-an",
            "-threads",
            "1",
            "-r",
            str(TARGET_FPS),
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )

    def run() -> None:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            timeout=FFMPEG_TIMEOUT_S,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[-2000:]
            raise RuntimeError(detail or f"ffmpeg exited with status {result.returncode}")

    await asyncio.to_thread(run)


def _manifest(
    protocol_run_name: str,
    status: str,
    *,
    segments: list[dict[str, Any]],
    error: str | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    manifest = {
        "schema_version": 1,
        "protocol_run_name": protocol_run_name,
        "task_name": PROTOCOL_TASK_NAME,
        "status": status,
        "complete": status == "ready",
        "generated_at": datetime.now(UTC).isoformat(),
        "source": "simulation",
        "segments": segments,
        "warnings": warnings or [],
        "target": {
            "fps": TARGET_FPS,
            "width": TARGET_WIDTH,
            "height": TARGET_HEIGHT,
            "video_codec": "libx264",
        },
        "disclaimer": RECORDING_DISCLAIMER,
    }
    if error:
        manifest["error"] = error
    return manifest


def _segments(fragments: Sequence[tuple[Task, str]]) -> list[dict[str, Any]]:
    return [
        {
            "task_name": task.name,
            "file_name": file_name,
            "task_status": task.status.value,
            "start_time": task.start_time.isoformat() if task.start_time else None,
            "end_time": task.end_time.isoformat() if task.end_time else None,
            "key": build_task_output_file_path(task.protocol_run_name, task.name, file_name),
        }
        for task, file_name in fragments
    ]


async def _store_manifest(
    protocol_run_name: str,
    file_db: FileDbInterface,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    data = json.dumps(manifest, ensure_ascii=False, allow_nan=False, sort_keys=True).encode()
    await file_db.store_file(protocol_artifact_path(protocol_run_name, PROTOCOL_RECORDING_NAME), data)
    return manifest


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _limit_protocol_samples(samples: Sequence[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Keep real task boundaries, then distribute the remaining budget across middle points."""
    if len(samples) <= limit:
        return list(samples)
    positions_by_task: dict[str, list[int]] = {}
    for index, sample in enumerate(samples):
        positions_by_task.setdefault(sample["task_name"], []).append(index)

    selected = {
        position
        for positions in positions_by_task.values()
        for position in dict.fromkeys((positions[0], positions[-1]))
    }
    if len(selected) > limit:
        indexes = [round(index * (len(samples) - 1) / (limit - 1)) for index in range(limit)]
        return [samples[index] for index in dict.fromkeys(indexes)]

    middle = {
        task_name: positions[1:-1] if positions[1:-1] else []
        for task_name, positions in positions_by_task.items()
    }
    available = limit - len(selected)
    total_middle = sum(len(positions) for positions in middle.values())
    quotas = {
        task_name: (available * len(positions) // total_middle if total_middle else 0)
        for task_name, positions in middle.items()
    }
    remainders = sorted(
        middle,
        key=lambda task_name: (available * len(middle[task_name]) % total_middle, task_name)
        if total_middle
        else (0, task_name),
        reverse=True,
    )
    for task_name in remainders:
        if sum(quotas.values()) >= available:
            break
        if quotas[task_name] < len(middle[task_name]):
            quotas[task_name] += 1

    for task_name, positions in middle.items():
        selected.update(_evenly_spaced(positions, quotas[task_name]))
    return [samples[index] for index in sorted(selected)]


def _evenly_spaced(positions: Sequence[int], count: int) -> list[int]:
    if count <= 0:
        return []
    if count >= len(positions):
        return list(positions)
    if count == 1:
        return [positions[len(positions) // 2]]
    return [positions[round(index * (len(positions) - 1) / (count - 1))] for index in range(count)]
