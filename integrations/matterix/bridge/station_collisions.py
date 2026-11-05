"""Collision scope for the station's two registered working TIP slots."""

import json

from bridge.recording import shell_visual_paths


def shell_collision_paths(root, rig) -> list[str]:
    paths = set()
    for visual in shell_visual_paths(root, rig):
        body, _, part = visual.rsplit("/", 3)[-3:]
        for name in (part, "CAD_" + part):
            paths.add(f"{root}/Mechanism/Links/{body}/Colliders/{name}")
            paths.add(f"{root}/Mechanism/StaticColliders/{name}")
    return sorted(paths)


def configure_working_collisions(env, env_ids, asset_cfg=None) -> None:
    """Keep working contacts; idle TIPs remain visible at their CAD positions."""
    from pxr import Usd, UsdPhysics  # noqa: PLC0415
    from bridge.station_skills import TIP_BY_INDEX  # noqa: PLC0415

    stage = env.sim.stage
    roots = [prim for prim in stage.Traverse() if prim.HasAttribute("operation:manifest")]
    if len(roots) != 1:
        raise ValueError("Expected one pipetting station collision root")
    root = roots[0]
    path = str(root.GetPath())
    rig = json.loads(root.GetAttribute("operation:rig").Get())
    manifest = json.loads(root.GetAttribute("operation:manifest").Get())
    objects = manifest["consumables"]
    active_tips = set(TIP_BY_INDEX.values())
    active_holes = {objects[name]["rackTipIndex"] - 1 for name in active_tips}
    counts = {"shell": 0, "idle_tip_slots": 0, "idle_tips": 0}

    def disable(prim, category) -> None:
        if not prim.IsValid():
            return
        for child in Usd.PrimRange(prim):
            if child.HasAPI(UsdPhysics.CollisionAPI):
                attribute = UsdPhysics.CollisionAPI(child).GetCollisionEnabledAttr()
                if attribute.Get() is not False:
                    attribute.Set(False)
                    counts[category] += 1

    for collision_path in shell_collision_paths(path, rig):
        disable(stage.GetPrimAtPath(collision_path), "shell")
    rack = stage.GetPrimAtPath(f"{path}/Consumables/_1mlTipBox_222/Colliders")
    if not rack.IsValid():
        raise ValueError("TIP rack collision geometry is missing")
    for collider in rack.GetChildren():
        name = collider.GetName()
        if name.startswith("Hole_") and int(name.split("_")[1]) not in active_holes:
            disable(collider, "idle_tip_slots")
    idle_tips = [name for name, data in objects.items() if "rackTipIndex" in data and name not in active_tips]
    for name in idle_tips:
        tip = stage.GetPrimAtPath(f"{path}/Consumables/{name}")
        disable(tip, "idle_tips")
        UsdPhysics.RigidBodyAPI(tip).CreateKinematicEnabledAttr(True)
    env._station_collision_profile = {
        "mode": "working_area",
        "active_tips": sorted(active_tips),
        "disabled_colliders": counts,
        "idle_tips_kinematic": len(idle_tips),
        "limitation": "Shell and unused TIP slots are excluded from collision validation.",
    }
