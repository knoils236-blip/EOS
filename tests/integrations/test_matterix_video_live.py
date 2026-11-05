"""Optional camera/encoder check, separate from the default no-video experiment acceptance."""

import io
import asyncio
import json
import os
from pathlib import Path
import sys
import subprocess
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "user"))
from matterix_sim.devices.matterix_workstation.device import MatterixWorkstation  # noqa: E402

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not os.environ.get("EOS_TEST_MATTERIX_PORT"), reason="EOS_TEST_MATTERIX_PORT not set"),
]


async def test_optional_video_records_real_scene_frames(tmp_path):
    device = MatterixWorkstation("video", "validation")
    try:
        await device.initialize(
            {
                "address": "127.0.0.1",
                "port": int(os.environ["EOS_TEST_MATTERIX_PORT"]),
                "insecure": True,
                "scene_id": "pipetting_realtime",
                "record_video": True,
            }
        )
        await device.begin_experiment("video-validation", "begin")
        state = await device.run_capability("station_full_station_open_door", {}, "video-validation", "open_door")
        assert state["status"] == "succeeded", state.get("error")
        assert state["result"]["acceptance"]["passed"]
        report = await device.finalize_task("video-validation", "open_door")
        with zipfile.ZipFile(io.BytesIO(report["files"]["matterix-evidence.zip"])) as archive:
            data = archive.read("simulation.mp4")
            result = json.loads(archive.read("result.json"))
        assert result["video"]["frames"] > 100
    finally:
        await device.cleanup()

    evidence = Path(os.environ.get("EOS_MATTERIX_EVIDENCE_DIR", str(tmp_path)))
    path = evidence / "optional-video-open-door.mp4"
    await asyncio.to_thread(path.write_bytes, data)
    config_path = ROOT / "integrations/matterix/config.json"
    config = json.loads(await asyncio.to_thread(config_path.read_text))
    probe = """
import imageio_ffmpeg,numpy as np,json,sys
reader=imageio_ffmpeg.read_frames(sys.argv[1])
try:
    metadata=next(reader)
    frame=next(reader)
    print(json.dumps({'size':metadata['size'],'fps':metadata['fps'],
                      'pixel_std':float(np.frombuffer(frame,dtype=np.uint8).std())}))
finally:
    reader.close()
"""
    completed = await asyncio.to_thread(
        subprocess.run,
        [config["matterix_python"], "-B", "-c", probe, str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    metadata = json.loads(completed.stdout)
    assert metadata["size"] == [1920, 540]
    assert metadata["fps"] == 25
    assert metadata["pixel_std"] > 5
    await asyncio.to_thread(
        (evidence / "optional-video.json").write_text, json.dumps({**result["video"], **metadata}, indent=2)
    )
