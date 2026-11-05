"""Render-only shell visibility and per-task MP4 recording for the station."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


FPS = 25
VIEW_WIDTH = 960
WIDTH = VIEW_WIDTH * 2
HEIGHT = 540
RENDERER = "isaac_sim_replicator_rgb_annotator"
VIDEO_SOURCE = "Isaac Sim rendered frames"
OVERVIEW_EYE = (1.3, 1.65, 1.7)
OVERVIEW_TARGET = (0.0, 0.0, 1.0)

_SHELL_BODY_ROLES = frozenset(
    {
        "front_door",
        "front_door_cage_left",
        "front_door_cage_right",
        "service_door_left",
        "service_door_right",
    }
)
_SHELL_PART_MARKERS = (
    "CoverPanel",
    "ElectricalPanel",
    "PartitionPanel",
    "AcrylicPanel",
    "封板",
    "盖板",
    "隔板",
    "亚克力板",
)


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a JSON boolean")
    return value


def cameras_enabled(session_request: Mapping[str, object]) -> bool:
    """Return whether this session must boot and warm the renderer."""
    enable_cameras = _boolean(session_request.get("enable_cameras", False), "enable_cameras")
    legacy_record_video = _boolean(session_request.get("record_video", False), "record_video")
    return enable_cameras or legacy_record_video


def task_recording_requested(
    parameters: Mapping[str, object],
    session_request: Mapping[str, object],
) -> bool:
    """Prefer an explicit per-task switch; otherwise honor the legacy session switch."""
    if "record_video" in parameters:
        return _boolean(parameters["record_video"], "record_video")
    return _boolean(session_request.get("record_video", False), "record_video")


def task_hide_shell(parameters: Mapping[str, object], recording: bool) -> bool:
    hide_shell = _boolean(parameters.get("hide_shell", False), "hide_shell")
    return recording and hide_shell


def require_camera_ready(recording: bool, camera_ready: bool) -> None:
    if recording and not camera_ready:
        raise RuntimeError("Video recording requested, but session cameras are not enabled")


def warm_renderer(render: Callable[[], Any], attempts: int = 8) -> None:
    """Advance the renderer until it returns a nonempty frame at the configured size."""
    for _ in range(attempts):
        frame = render()
        if frame is None:
            continue
        shape = tuple(getattr(frame, "shape", ()))
        if shape[:2] != (HEIGHT, WIDTH) or len(shape) != 3 or shape[2] < 3:  # noqa: PLR2004
            continue
        if frame.any():
            return
    raise RuntimeError(f"Isaac Sim renderer did not return a nonempty {WIDTH}x{HEIGHT} frame")


class StationViews:
    """Overview on the left, current tool close-up on the right, at the same physics time."""

    def __init__(self, render, set_view, focus):
        self.render = render
        self.set_view = set_view
        self.focus = focus

    def __call__(self):
        import numpy as np  # noqa: PLC0415

        self.set_view(OVERVIEW_EYE, OVERVIEW_TARGET)
        self.render()  # Flush the previous camera pose without advancing physics.
        overview = self.render()
        if overview is None:
            return None
        overview = overview.copy()
        target = tuple(float(value) for value in self.focus())
        eye = tuple(value + offset for value, offset in zip(target, (0.18, 0.24, 0.16), strict=True))
        try:
            self.set_view(eye, target)
            self.render()
            detail = self.render()
            if detail is None:
                return None
            detail = detail.copy()
            return np.concatenate((overview, detail), axis=1)
        finally:
            self.set_view(OVERVIEW_EYE, OVERVIEW_TARGET)


class StationVideoRecorder:
    """Stream rendered task frames to one MP4 and report measured capture metadata."""

    def __init__(
        self,
        output: str | Path,
        render: Callable[[], Any],
        *,
        hide_shell,
        writer_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.output = Path(output)
        self.partial = self.output.with_name(f"{self.output.stem}.partial{self.output.suffix}")
        self.render = render
        self.hide_shell = hide_shell
        self._writer_factory = writer_factory
        self._writer = None
        self.frames = 0

    def start(self) -> None:
        warm_renderer(self.render)
        if self._writer_factory is None:
            import imageio_ffmpeg  # noqa: PLC0415

            self._writer_factory = imageio_ffmpeg.write_frames
        self._writer = self._writer_factory(
            str(self.partial),
            (WIDTH, HEIGHT),
            fps=FPS,
            codec="libx264",
            macro_block_size=2,
            output_params=["-movflags", "+faststart"],
        )
        self._writer.send(None)

    def capture(self) -> None:
        if self._writer is None:
            raise RuntimeError("Video recorder has not been started")
        frame = self.render()
        if frame is None:
            raise RuntimeError("Isaac Sim renderer returned no frame while recording")
        shape = tuple(getattr(frame, "shape", ()))
        if shape[:2] != (HEIGHT, WIDTH) or len(shape) != 3 or shape[2] < 3:  # noqa: PLR2004
            raise RuntimeError(f"Unexpected video frame shape: {shape}")
        self._writer.send(frame[..., :3].copy())
        self.frames += 1

    def close(self) -> dict[str, object]:
        writer, self._writer = self._writer, None
        if writer is not None:
            writer.close()
        if self.frames == 0:
            self.partial.unlink(missing_ok=True)
            raise RuntimeError("Video recording produced no frames")
        self.partial.replace(self.output)
        return {
            "filename": self.output.name,
            "source": VIDEO_SOURCE,
            "renderer": RENDERER,
            "simulation_mode": "physics",
            "frames": self.frames,
            "framecount": self.frames,
            "fps": FPS,
            "width": WIDTH,
            "height": HEIGHT,
            "hide_shell": self.hide_shell,
            "views": ["station_overview", "active_tool_closeup"],
        }


def shell_visual_paths(
    asset_root: str,
    rig_manifest: str | Path | Mapping[str, object],
) -> list[str]:
    """Map manifest body roles and explicit panel parts to their render-only prims."""
    if isinstance(rig_manifest, (str, Path)):
        rig_manifest = json.loads(Path(rig_manifest).read_text(encoding="utf-8"))
    root = str(asset_root).rstrip("/").removesuffix("/Mechanism")
    paths = set()
    for part in rig_manifest["partAssignments"]:
        body = part["body"]
        searchable = f"{part['name']} {part['englishName']}"
        shell_part = body == "base" and any(marker in searchable for marker in _SHELL_PART_MARKERS)
        if body in _SHELL_BODY_ROLES or shell_part:
            paths.add(f"{root}/Mechanism/Links/{body}/Visuals/{part['primName']}")
    return sorted(paths)


class ShellVisibility:
    """Temporarily hide only shell visual prims and restore their exact prior values."""

    def __init__(
        self,
        stage: Any,
        asset_root: str,
        rig_manifest: str | Path | Mapping[str, object],
    ) -> None:
        self.stage = stage
        self.paths = shell_visual_paths(asset_root, rig_manifest)
        self._original = {}

    def hide(self) -> None:
        if self._original:
            return
        prims = []
        for path in self.paths:
            prim = self.stage.GetPrimAtPath(path)
            if prim.IsValid():
                prims.append((path, prim.GetAttribute("visibility")))
        if not prims:
            raise RuntimeError("Shell hiding requested, but no shell visual prims were found")
        try:
            for path, visibility in prims:
                self._original[path] = (visibility.Get(), visibility.HasAuthoredValueOpinion())
                visibility.Set("invisible")
        except Exception:
            self.restore()
            raise

    def restore(self) -> None:
        first_error = None
        remaining = {}
        for path, original in self._original.items():
            try:
                visibility = self.stage.GetPrimAtPath(path).GetAttribute("visibility")
                value, authored = original
                if authored:
                    visibility.Set(value)
                else:
                    visibility.Clear()
            except Exception as exc:
                remaining[path] = original
                if first_error is None:
                    first_error = exc
        self._original = remaining
        if first_error is not None:
            raise RuntimeError(f"Failed to restore shell visibility: {first_error}") from first_error

    def __enter__(self):
        self.hide()
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.restore()
        return False


__all__ = [
    "FPS",
    "HEIGHT",
    "RENDERER",
    "VIDEO_SOURCE",
    "VIEW_WIDTH",
    "WIDTH",
    "ShellVisibility",
    "StationVideoRecorder",
    "StationViews",
    "cameras_enabled",
    "require_camera_ready",
    "shell_visual_paths",
    "task_hide_shell",
    "task_recording_requested",
    "warm_renderer",
]
