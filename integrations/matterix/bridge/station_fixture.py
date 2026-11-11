"""Align the carried large bottle with the physical CAD clamp, rather than a kinematic carry observation."""

import json
from pathlib import Path

from .station_semantics import BIG_BOTTLE

BOTTLE_FIXTURE_MACROS = {"大开盖机构去大开盖区开盖", "大开盖机构去大开盖区合盖"}


def center_bottle_clamp_frame(frame: dict, asset_dir: Path) -> dict:
    nominal = json.loads((asset_dir / "rig_manifest.json").read_text())["frames"]["bottle_clamp"]
    # The export's measured Z correction is still needed: the CAD frame's Z denotes the bottle mouth.
    for key in ("offset", "worldAtRest"):
        frame[key][:2] = nominal[key][:2]
    return frame


def align_bottle_fixture(actions: list, asset_dir: Path) -> None:
    targets = [action for action in actions if getattr(action, "macro", None) in BOTTLE_FIXTURE_MACROS]
    if not targets:
        return
    from matterix_plc.executor import plan_macro  # noqa: PLC0415 (requires the exported Matterix runtime)

    rig = json.loads((asset_dir / "rig_manifest.json").read_text())
    objects = json.loads((asset_dir / "operational_manifest.json").read_text())["consumables"]
    pick = plan_macro("大开盖机构去大瓶区取瓶", 1, twin_alignment=True).joint_targets
    opening = plan_macro("大开盖机构去大开盖区开盖", 1, twin_alignment=True).joint_targets
    bottle = objects[BIG_BOTTLE]["pose"]
    fixture = rig["frames"]["bottle_clamp"]["offset"]
    # In this station pipette_y drives carriage_x2 (world X), while gantry drives world Y.
    shift = {axis: fixture[index] - (bottle[index] + opening[axis] - pick[axis])
             for index, axis in enumerate(("pipette_y", "gantry"))}
    for action in targets:
        for axis, delta in shift.items():
            if axis in action.joint_targets:
                action.joint_targets[axis] += delta
