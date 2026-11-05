"""Validated Franka beaker workspace calibration."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path


DEFAULT_MAX_LIFT_M = 0.2
DEFAULT_WORKSPACE_PATH = Path(__file__).with_name("beaker_workspace.json")


@dataclass(frozen=True)
class BeakerWorkspace:
    schema_version: int
    scene_id: str
    max_lift_m: float
    calibration_status: str
    method: str
    source_path: str


def load_beaker_workspace(path=DEFAULT_WORKSPACE_PATH):
    """Load the calibrated lift bound, or the conservative legacy bound if absent."""
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        return BeakerWorkspace(
            schema_version=1,
            scene_id="franka_beaker",
            max_lift_m=DEFAULT_MAX_LIFT_M,
            calibration_status="unverified",
            method="legacy conservative fallback; no beaker_workspace.json",
            source_path=str(path),
        )
    value = json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=lambda token: (_ for _ in ()).throw(
            ValueError(f"Non-finite JSON number: {token}")),
    )
    if not isinstance(value, dict):
        raise ValueError("beaker_workspace.json must contain an object")
    required = {"schema_version", "scene_id", "max_lift_m", "calibration_status", "method"}
    if not required <= set(value):
        raise ValueError(f"beaker_workspace.json is missing {sorted(required - set(value))}")
    if value["schema_version"] != 1:
        raise ValueError("beaker_workspace.json schema_version must be 1")
    if value["scene_id"] != "franka_beaker":
        raise ValueError("beaker_workspace.json scene_id must be franka_beaker")
    maximum = value["max_lift_m"]
    if (isinstance(maximum, bool) or not isinstance(maximum, (int, float))
            or not math.isfinite(maximum) or maximum <= 0):
        raise ValueError("beaker_workspace.json max_lift_m must be positive and finite")
    if value["calibration_status"] not in {"verified", "unverified"}:
        raise ValueError("beaker_workspace.json calibration_status must be verified or unverified")
    if not isinstance(value["method"], str) or not value["method"].strip():
        raise ValueError("beaker_workspace.json method must be a non-empty string")
    return BeakerWorkspace(
        schema_version=1,
        scene_id="franka_beaker",
        max_lift_m=float(maximum),
        calibration_status=value["calibration_status"],
        method=value["method"],
        source_path=str(path),
    )
