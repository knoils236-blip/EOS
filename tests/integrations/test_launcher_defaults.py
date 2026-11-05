"""Check launcher selection without starting services or devices."""

import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize(
    ("args", "overrides", "expected"),
    [
        ([], {}, "matterix_pipetting_lab,mixture_mock_lab|matterix_full_station,mixture_mock"),
        (["--no-matterix"], {}, "mixture_mock_lab|mixture_mock"),
        ([], {"EOS_LABS": "custom_lab", "EOS_PROTOCOLS": "custom_protocol"}, "custom_lab|custom_protocol"),
        (
            ["--no-matterix"],
            {"EOS_LABS": "custom_lab", "EOS_PROTOCOLS": "custom_protocol"},
            "custom_lab|custom_protocol",
        ),
    ],
)
def test_launcher_selects_labs_and_protocols(args, overrides, expected):
    launcher = Path(__file__).resolve().parents[2] / "start_eos.sh"
    selection = launcher.read_text().split("port_open()", 1)[0]
    env = {key: value for key, value in os.environ.items() if key not in {"EOS_LABS", "EOS_PROTOCOLS"}}
    result = subprocess.run(
        ["bash", "-c", selection + '\nprintf "%s|%s" "$LABS" "$PROTOCOLS"', str(launcher), *args],
        env=env | overrides,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout == expected
