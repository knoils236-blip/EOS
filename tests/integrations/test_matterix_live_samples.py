"""The live endpoint reads complete actual samples without scanning the full log."""

import json

import pytest

from integrations.matterix.bridge.common import read_jsonl_tail, validate_session


def test_tail_keeps_complete_samples_and_ignores_inflight_write(tmp_path):
    path = tmp_path / "samples.jsonl"
    path.write_text(''.join(json.dumps({"sim_time_s": i}) + '\n' for i in range(20)) + '{"sim_')
    assert read_jsonl_tail(path, max_points=3) == [{"sim_time_s": i} for i in (17, 18, 19)]
    bounded = read_jsonl_tail(path, max_bytes=70)
    assert 0 < len(bounded) < 4
    assert bounded[-1] == {"sim_time_s": 19}


def test_tail_does_not_hide_corrupt_committed_samples(tmp_path):
    path = tmp_path / "samples.jsonl"
    assert read_jsonl_tail(path) == []
    path.write_text('{broken}\n')
    with pytest.raises(ValueError):
        read_jsonl_tail(path)


def test_camera_provisioning_is_distinct_from_default_recording():
    value = {"session_id": "s", "experiment_id": "e", "scene_id": "pipetting_realtime"}
    assert validate_session(value) == value
    assert validate_session({**value, "enable_cameras": True}) == {**value, "enable_cameras": True}
    with pytest.raises(ValueError, match="enable_cameras"):
        validate_session({**value, "enable_cameras": 1})
