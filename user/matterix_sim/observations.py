"""Pure helpers for preserving and exposing Matterix execution evidence."""

import io
import json
import zipfile
from collections.abc import Iterable, Mapping
from typing import Any

ARCHIVE_FILES = frozenset(
    {
        "request.json",
        "config.json",
        "state.json",
        "result.json",
        "provenance.json",
        "events.jsonl",
        "samples.jsonl",
        "latest.json",
        "cancel.json",
        "simulation.mp4",
    }
)
MAX_DEFAULT_SAMPLES = 5000
MAX_EVIDENCE_FILE_BYTES = 64 * 1024 * 1024
MAX_EVIDENCE_VIDEO_BYTES = 512 * 1024 * 1024
MAX_EVIDENCE_TOTAL_BYTES = 768 * 1024 * 1024


def recording_settings(
    meta: Mapping[str, Any] | None = None,
    parameters: Mapping[str, Any] | None = None,
    defaults: Mapping[str, bool] | None = None,
) -> dict[str, bool]:
    """Merge task meta over capability parameters and device defaults."""
    defaults = defaults or {}
    settings = {
        "record_video": defaults.get("record_video") is True,
        "hide_shell": defaults.get("hide_shell") is True,
    }
    for source in (parameters or {}, meta or {}):
        for name in settings:
            if name in source and not isinstance(source[name], bool):
                raise ValueError(f"{name} must be a boolean")
            if name in source:
                settings[name] = source[name]
    settings["hide_shell"] = settings["hide_shell"] and settings["record_video"]
    return settings


def extract_archive_evidence(data: bytes, include_video: bool = True) -> tuple[dict[str, bytes], list[str]]:
    """Read only the fixed evidence whitelist from an in-memory bridge archive."""
    files: dict[str, bytes] = {}
    warnings: list[str] = []
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = {info.filename: info for info in archive.infolist()}
            for name in sorted(ARCHIVE_FILES):
                if name.endswith(".mp4") and not include_video:
                    if name in members:
                        files[name] = b""
                    continue
                info = members.get(name)
                if info is None or info.is_dir():
                    continue
                limit = MAX_EVIDENCE_VIDEO_BYTES if name.endswith(".mp4") else MAX_EVIDENCE_FILE_BYTES
                if info.flag_bits & 0x1:
                    warnings.append(f"archive_member_encrypted:{name}")
                    continue
                if info.file_size > limit:
                    warnings.append(f"archive_member_too_large:{name}")
                    continue
                with archive.open(info) as stream:
                    content = stream.read(limit + 1)
                if len(content) > limit:
                    warnings.append(f"archive_member_too_large:{name}")
                    continue
                if total + len(content) > MAX_EVIDENCE_TOTAL_BYTES:
                    warnings.append(f"archive_total_too_large:{name}")
                    break
                files[name] = content
                total += len(content)
    except (OSError, ValueError, zipfile.BadZipFile):
        warnings.append("archive_not_readable")
    return files, warnings


def parse_json(data: bytes) -> Any | None:
    try:
        return json.loads(data)
    except (TypeError, ValueError):
        return None


def parse_jsonl(data: bytes) -> list[dict[str, Any]]:
    points = []
    for line in data.splitlines():
        try:
            point = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(point, dict):
            points.append(point)
    return points


def normalize_samples(
    samples: Iterable[Any],
    *,
    request_id: str | None = None,
    session_id: str | None = None,
    limit: int = MAX_DEFAULT_SAMPLES,
) -> list[dict[str, Any]]:
    """Keep real points from one request and enforce the API window."""
    points = []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        point_request = sample.get("request_id")
        point_session = sample.get("session_id")
        if request_id is not None and point_request not in (None, request_id):
            continue
        if session_id is not None and point_session not in (None, session_id):
            continue
        points.append(sample)
    return points[-limit:]


def samples_from_files(files: Mapping[str, bytes]) -> list[dict[str, Any]]:
    points = []
    for name, data in files.items():
        if name.endswith("samples.jsonl"):
            points.extend(parse_jsonl(data))
    return normalize_samples(points)


def results_from_files(files: Mapping[str, bytes]) -> dict[str, dict[str, Any]]:
    results = {}
    for name, data in files.items():
        if not name.endswith("result.json"):
            continue
        result = parse_json(data)
        if isinstance(result, dict):
            stage = name.removesuffix("result.json").rstrip("-") or "run"
            results[stage] = result
    return results


def video_metadata(name: str, result: Mapping[str, Any] | None) -> dict[str, Any]:
    source = result.get("video") if isinstance(result, Mapping) else None
    source = source if isinstance(source, Mapping) else {}
    metadata = {
        "name": name,
        "content_type": "video/mp4",
        "source": result.get("source") if isinstance(result, Mapping) else None,
    }
    for field in ("frames", "framecount", "fps", "duration_s", "width", "height", "hide_shell"):
        if field in source:
            metadata[field] = source[field]
    return metadata
