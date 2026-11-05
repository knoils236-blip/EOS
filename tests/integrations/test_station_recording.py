# ruff: noqa: N802

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "integrations/matterix"))
from bridge.recording import (
    HEIGHT,
    RENDERER,
    VIDEO_SOURCE,
    WIDTH,
    VIEW_WIDTH,
    OVERVIEW_EYE,
    OVERVIEW_TARGET,
    ShellVisibility,
    StationVideoRecorder,
    StationViews,
    shell_visual_paths,
    cameras_enabled,
    require_camera_ready,
    task_hide_shell,
    task_recording_requested,
    warm_renderer,
)


class FakeVisibility:
    def __init__(self, value="inherited"):
        self.value = value

    def Get(self):
        return self.value

    def HasAuthoredValueOpinion(self):
        return self.value is not None

    def Set(self, value):
        self.value = value

    def Clear(self):
        self.value = None


class FakePrim:
    def __init__(self, visibility=None):
        self.visibility = FakeVisibility() if visibility is None else FakeVisibility(visibility)

    def IsValid(self):
        return True

    def GetAttribute(self, name):
        assert name == "visibility"
        return self.visibility


class FakeStage:
    def __init__(self, paths):
        self.prims = {path: FakePrim() for path in paths}

    def GetPrimAtPath(self, path):
        return self.prims.get(path, FakePrim())


def test_shell_hiding_selects_body_roles_and_named_panels_only():
    root = "/World/envs/env_0/Articulations_PipettingStation_station"
    manifest = {
        "partAssignments": [
            {
                "body": "base",
                "name": "方案D-封板2",
                "englishName": "DesignD-CoverPanel2",
                "primName": "cover",
            },
            {
                "body": "base",
                "name": "方案D-方案C-电气板",
                "englishName": "DesignD-DesignC-ElectricalPanel",
                "primName": "electrical",
            },
            {
                "body": "front_door",
                "name": "方案DD-亚克力板1",
                "englishName": "DesignDD-DesignC-AcrylicPanel1",
                "primName": "front_door_visual",
            },
            {
                "body": "front_door_cage_left",
                "name": "方案DD-SR3622_s",
                "englishName": "DesignDD-SR3622_s",
                "primName": "left_cage",
            },
            {
                "body": "front_door_cage_right",
                "name": "方案DD-SR3622_s",
                "englishName": "DesignDD-SR3622_s",
                "primName": "right_cage",
            },
            {
                "body": "service_door_left",
                "name": "方案DD-钣金双开门",
                "englishName": "DesignDD-SheetMetalDoubleDoor",
                "primName": "left_service_door",
            },
            {
                "body": "service_door_right",
                "name": "方案DD-钣金双开门2",
                "englishName": "DesignDD-SheetMetalDoubleDoor2",
                "primName": "right_service_door",
            },
            {
                "body": "base",
                "name": "方案D-方案C-拖链钣金2",
                "englishName": "DesignD-DesignC-CableCarrierSheetMetal2",
                "primName": "cable_carrier",
            },
            {
                "body": "gantry_y",
                "name": "方案D-封板2",
                "englishName": "DesignD-CoverPanel2",
                "primName": "moving_cover_name",
            },
            {
                "body": "base",
                "name": "主体",
                "englishName": "MainBody",
                "primName": "main_body",
            },
        ]
    }
    expected = {
        f"{root}/Mechanism/Links/base/Visuals/cover",
        f"{root}/Mechanism/Links/base/Visuals/electrical",
        f"{root}/Mechanism/Links/front_door/Visuals/front_door_visual",
        f"{root}/Mechanism/Links/front_door_cage_left/Visuals/left_cage",
        f"{root}/Mechanism/Links/front_door_cage_right/Visuals/right_cage",
        f"{root}/Mechanism/Links/service_door_left/Visuals/left_service_door",
        f"{root}/Mechanism/Links/service_door_right/Visuals/right_service_door",
    }
    untouched = f"{root}/Mechanism/Links/base/Visuals/main_body"
    collision = f"{root}/Mechanism/StaticColliders/cover"
    stage = FakeStage(expected | {untouched, collision})
    assert set(shell_visual_paths(root + "/Mechanism", manifest)) == expected
    original_visibility = {path: stage.prims[path].visibility.value for path in expected}
    original_visibility[next(iter(expected))] = "invisible"
    stage.prims[next(iter(expected))].visibility.value = "invisible"
    stage.prims[collision].visibility.value = "invisible"
    visibility = ShellVisibility(stage, root, manifest)

    visibility.hide()
    assert all(stage.prims[path].visibility.value == "invisible" for path in expected)
    assert stage.prims[untouched].visibility.value == "inherited"

    def stop_while_hidden():
        with visibility:
            raise ValueError("task stopped")

    with pytest.raises(ValueError, match="task stopped"):
        stop_while_hidden()
    assert all(stage.prims[path].visibility.value == original_visibility[path] for path in expected)
    assert stage.prims[untouched].visibility.value == "inherited"
    assert stage.prims[collision].visibility.value == "invisible"


def test_no_recording_means_hide_shell_is_a_noop_and_task_switch_wins():
    legacy = {"record_video": True}
    assert task_recording_requested({"record_video": False}, legacy) is False
    assert task_hide_shell({"record_video": False, "hide_shell": True}, False) is False
    assert task_recording_requested({}, legacy) is True
    assert task_recording_requested({}, {"enable_cameras": True}) is False


def test_camera_session_flags_and_missing_camera_failure_are_explicit():
    assert cameras_enabled({"enable_cameras": True}) is True
    assert cameras_enabled({"record_video": True}) is True
    assert cameras_enabled({}) is False
    require_camera_ready(False, False)
    with pytest.raises(RuntimeError, match="session cameras are not enabled"):
        require_camera_ready(True, False)


def test_recorder_counts_frames_and_records_real_output_metadata(tmp_path):
    frames = []
    calls = {}

    class FakeWriter:
        def __init__(self, path):
            self.path = Path(path)

        def send(self, frame):
            if frame is not None:
                frames.append(frame)

        def close(self):
            self.path.write_bytes(b"mp4")

    def factory(path, size, **kwargs):
        calls.update(path=path, size=size, **kwargs)
        return FakeWriter(path)

    def render():
        return np.ones((HEIGHT, WIDTH, 3), dtype=np.uint8)

    output = tmp_path / "simulation.mp4"
    recorder = StationVideoRecorder(output, render, hide_shell=True, writer_factory=factory)
    recorder.start()
    recorder.capture()
    recorder.capture()
    metadata = recorder.close()

    assert output.is_file()
    assert not recorder.partial.exists()
    assert len(frames) == 2
    assert calls["size"] == (WIDTH, HEIGHT)
    assert metadata["framecount"] == 2
    assert metadata["fps"] == 25
    assert metadata["renderer"] == RENDERER
    assert metadata["source"] == VIDEO_SOURCE
    assert metadata["hide_shell"] is True


def test_renderer_warmup_rejects_blank_frames():
    with pytest.raises(RuntimeError, match=f"nonempty {WIDTH}x{HEIGHT}"):
        warm_renderer(lambda: np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8), attempts=2)


def test_two_views_preserve_resolution_and_restore_camera_after_failure():
    views = []

    def set_view(eye, target):
        views.append((eye, target))

    def render():
        value = 1 if views[-1][0] == OVERVIEW_EYE else 2
        return np.full((HEIGHT, VIEW_WIDTH, 3), value, dtype=np.uint8)

    renderer = StationViews(render, set_view, lambda: (0.1, 0.2, 0.9))
    frame = renderer()
    assert frame.shape == (HEIGHT, WIDTH, 3)
    assert (frame[:, :VIEW_WIDTH] == 1).all()
    assert (frame[:, VIEW_WIDTH:] == 2).all()
    assert views[-1] == (OVERVIEW_EYE, OVERVIEW_TARGET)

    def broken_render():
        if views[-1][0] != OVERVIEW_EYE:
            raise RuntimeError("camera failed")
        return render()

    renderer.render = broken_render
    with pytest.raises(RuntimeError, match="camera failed"):
        renderer()
    assert views[-1] == (OVERVIEW_EYE, OVERVIEW_TARGET)
