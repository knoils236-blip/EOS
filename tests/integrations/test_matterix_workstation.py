"""Runs against a live Matterix SiLA 2 bridge and Isaac Sim. Set EOS_TEST_MATTERIX_PORT (e.g. 65200) to enable."""

import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("sila2")
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not os.environ.get("EOS_TEST_MATTERIX_PORT"), reason="EOS_TEST_MATTERIX_PORT not set"),
]

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "user"))
from matterix_sim.devices.matterix_workstation.device import MatterixWorkstation  # noqa: E402


async def test_pick_and_return_beaker():
    device = MatterixWorkstation("matterix", "test_lab")
    await device.initialize(
        {
            "address": "127.0.0.1",
            "port": int(os.environ["EOS_TEST_MATTERIX_PORT"]),
            "insecure": True,
            "scene_id": "franka_beaker",
        }
    )
    try:
        for capability, parameters in [("pickup_beaker", {"lift_height_m": 0.1}), ("return_beaker", {})]:
            state = await device.run_capability(capability, parameters)
            assert state["status"] == "succeeded", state.get("error")
            assert state["result"]["acceptance"]["passed"]
    finally:
        await device.cleanup()


async def test_unknown_capability_is_rejected():
    device = MatterixWorkstation("matterix", "test_lab")
    await device.initialize(
        {
            "address": "127.0.0.1",
            "port": int(os.environ["EOS_TEST_MATTERIX_PORT"]),
            "insecure": True,
            "scene_id": "franka_beaker",
        }
    )
    try:
        with pytest.raises(RuntimeError, match="rejected"):
            await device.run_capability("no_such_capability", {})
    finally:
        await device.cleanup()
