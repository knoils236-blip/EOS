import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


BRIDGE_DIR = Path(__file__).resolve().parents[2] / "integrations/matterix"
sys.path.insert(0, str(BRIDGE_DIR))

from bridge.station_collisions import (  # noqa: E402
    configure_working_collisions,
    shell_collision_paths,
)
from bridge.station_skills import TIP_BY_INDEX  # noqa: E402


STATION_USD = Path(
    "/home/ry/projects/Matterix/PipettingStation_20261031/"
    "assets/PipettingStation_Asset_v4/Asset/PipettingStation_Operational.usd"
)


def test_shell_collision_paths_include_static_and_door_cad_only():
    root = "/World/envs/env_0/Articulations_PipettingStation_station"
    rig = {
        "partAssignments": [
            {
                "body": "base",
                "name": "方案D-封板2",
                "englishName": "DesignD-CoverPanel2",
                "primName": "cover",
            },
            {
                "body": "front_door",
                "name": "方案DD-亚克力板1",
                "englishName": "DesignDD-AcrylicPanel1",
                "primName": "front_door_visual",
            },
            {
                "body": "gantry_y",
                "name": "导轨",
                "englishName": "LinearGuideRail",
                "primName": "rail",
            },
            {
                "body": "base",
                "name": "TIP承载结构",
                "englishName": "TipRackCarrier",
                "primName": "tip_carrier",
            },
        ]
    }

    assert set(shell_collision_paths(root, rig)) == {
        f"{root}/Mechanism/StaticColliders/cover",
        f"{root}/Mechanism/StaticColliders/CAD_cover",
        f"{root}/Mechanism/Links/base/Colliders/cover",
        f"{root}/Mechanism/Links/base/Colliders/CAD_cover",
        f"{root}/Mechanism/StaticColliders/front_door_visual",
        f"{root}/Mechanism/StaticColliders/CAD_front_door_visual",
        f"{root}/Mechanism/Links/front_door/Colliders/front_door_visual",
        f"{root}/Mechanism/Links/front_door/Colliders/CAD_front_door_visual",
    }


def _collision_prims(prim, usd, usd_physics):
    return [child for child in usd.PrimRange(prim) if child.HasAPI(usd_physics.CollisionAPI)]


def _collision_enabled(prim, usd_physics):
    assert prim.HasAPI(usd_physics.CollisionAPI)
    return usd_physics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is True


def _collision_disabled(prim, usd_physics):
    assert prim.HasAPI(usd_physics.CollisionAPI)
    return usd_physics.CollisionAPI(prim).GetCollisionEnabledAttr().Get() is False


def _physics_properties(stage, usd_physics):
    properties = {}
    for prim in stage.Traverse():
        if prim.IsA(usd_physics.Joint):
            attributes = {attribute.GetName(): attribute.Get() for attribute in prim.GetAttributes()}
            relationships = {
                relationship.GetName(): tuple(str(target) for target in relationship.GetTargets())
                for relationship in prim.GetRelationships()
            }
            properties[str(prim.GetPath())] = (attributes, relationships)
        elif prim.HasAPI(usd_physics.MassAPI):
            mass = usd_physics.MassAPI(prim)
            properties[str(prim.GetPath())] = {
                attribute.GetName(): attribute.Get()
                for attribute in (
                    mass.GetMassAttr(),
                    mass.GetDensityAttr(),
                    mass.GetCenterOfMassAttr(),
                    mass.GetDiagonalInertiaAttr(),
                    mass.GetPrincipalAxesAttr(),
                )
                if attribute
            }
    return properties


def test_working_collision_profile_preserves_active_contacts_and_physics_contract():
    usd = pytest.importorskip("pxr.Usd")
    usd_physics = pytest.importorskip("pxr.UsdPhysics")

    source_stat_before = STATION_USD.stat()
    stage = usd.Stage.Open(str(STATION_USD))
    roots = [prim for prim in stage.Traverse() if prim.HasAttribute("operation:manifest")]
    assert len(roots) == 1
    root = roots[0]
    root_path = str(root.GetPath())
    rig = json.loads(root.GetAttribute("operation:rig").Get())
    manifest = json.loads(root.GetAttribute("operation:manifest").Get())
    objects = manifest["consumables"]
    active_tip_names = set(TIP_BY_INDEX.values())
    active_holes = {objects[name]["rackTipIndex"] - 1 for name in active_tip_names}
    idle_tip_names = {name for name, data in objects.items() if "rackTipIndex" in data and name not in active_tip_names}
    assert active_tip_names == {
        "_1mlTipBox_222_Tip_85",
        "_1mlTipBox_222_Tip_73",
    }
    assert len(idle_tip_names) == 94

    mechanism = stage.GetPrimAtPath(f"{root_path}/Mechanism")
    mechanism_colliders = _collision_prims(mechanism, usd, usd_physics)
    assert mechanism_colliders
    assert all(_collision_enabled(prim, usd_physics) for prim in mechanism_colliders)

    shell_disabled_paths = set()
    for collision_path in shell_collision_paths(root_path, rig):
        prim = stage.GetPrimAtPath(collision_path)
        if prim.IsValid():
            shell_disabled_paths.update(
                str(collider.GetPath()) for collider in _collision_prims(prim, usd, usd_physics)
            )

    non_shell_mechanism_paths = {
        str(prim.GetPath()) for prim in mechanism_colliders if str(prim.GetPath()) not in shell_disabled_paths
    }
    assert non_shell_mechanism_paths

    rack = stage.GetPrimAtPath(f"{root_path}/Consumables/_1mlTipBox_222/Colliders")
    hole_colliders = {}
    for child in rack.GetChildren():
        name = child.GetName()
        if name.startswith("Hole_"):
            hole_index = int(name.split("_")[1])
            hole_colliders.setdefault(hole_index, []).append(child)
    assert len(hole_colliders) == 96
    assert all(len(colliders) == 24 for colliders in hole_colliders.values())

    active_tip_colliders = set()
    for name in active_tip_names:
        tip = stage.GetPrimAtPath(f"{root_path}/Consumables/{name}")
        colliders = _collision_prims(tip, usd, usd_physics)
        assert len(colliders) == 24
        active_tip_colliders.update(str(prim.GetPath()) for prim in colliders)

    idle_tip_colliders = {}
    for name in idle_tip_names:
        tip = stage.GetPrimAtPath(f"{root_path}/Consumables/{name}")
        colliders = _collision_prims(tip, usd, usd_physics)
        assert len(colliders) == 24
        idle_tip_colliders[name] = colliders

    active_hole_colliders = set()
    for tip_index in active_holes:
        active_hole_colliders.update(str(collider.GetPath()) for collider in hole_colliders[tip_index])

    physics_properties_before = _physics_properties(stage, usd_physics)
    assert physics_properties_before
    root_layer = stage.GetRootLayer()
    session_layer = stage.GetSessionLayer()
    assert not root_layer.dirty
    stage.SetEditTarget(usd.EditTarget(session_layer))

    env = SimpleNamespace(sim=SimpleNamespace(stage=stage))
    configure_working_collisions(env, None)

    assert stage.GetEditTarget().GetLayer().identifier == session_layer.identifier
    assert env._station_collision_profile["mode"] == "working_area"
    assert env._station_collision_profile["disabled_colliders"] == {
        "shell": 49,
        "idle_tip_slots": 2256,
        "idle_tips": 2256,
    }
    assert env._station_collision_profile["idle_tips_kinematic"] == 94

    assert all(_collision_disabled(stage.GetPrimAtPath(path), usd_physics) for path in shell_disabled_paths)
    assert all(_collision_enabled(stage.GetPrimAtPath(path), usd_physics) for path in non_shell_mechanism_paths)

    for name in active_tip_names:
        tip = stage.GetPrimAtPath(f"{root_path}/Consumables/{name}")
        assert all(_collision_enabled(prim, usd_physics) for prim in _collision_prims(tip, usd, usd_physics))
    for tip_index in active_holes:
        assert all(_collision_enabled(prim, usd_physics) for prim in hole_colliders[tip_index])
    assert all(
        _collision_enabled(stage.GetPrimAtPath(path), usd_physics)
        for path in active_tip_colliders | active_hole_colliders
    )

    for name, colliders in idle_tip_colliders.items():
        assert all(_collision_disabled(prim, usd_physics) for prim in colliders)
        tip = stage.GetPrimAtPath(f"{root_path}/Consumables/{name}")
        assert usd_physics.RigidBodyAPI(tip).GetKinematicEnabledAttr().Get() is True

    idle_holes = {index: colliders for index, colliders in hole_colliders.items() if index not in active_holes}
    assert len(idle_holes) == 94
    assert all(
        _collision_disabled(collider, usd_physics) for colliders in idle_holes.values() for collider in colliders
    )

    cad_support = [child for child in rack.GetChildren() if child.GetName().startswith(("CADRack", "CADLedge"))]
    assert any(child.GetName().startswith("CADRack") for child in cad_support)
    assert any(child.GetName().startswith("CADLedge") for child in cad_support)
    assert all(_collision_enabled(child, usd_physics) for child in cad_support)

    for joint_path in (
        f"{root_path}/Mechanism/Joints/tip_tray_fixed",
        f"{root_path}/Connections/TipRackElasticLatch",
    ):
        joint = usd_physics.FixedJoint(stage.GetPrimAtPath(joint_path))
        assert joint.GetJointEnabledAttr().Get() is True
        assert joint.GetExcludeFromArticulationAttr().Get() is False

    assert _physics_properties(stage, usd_physics) == physics_properties_before
    assert not root_layer.dirty
    source_stat_after = STATION_USD.stat()
    assert (source_stat_after.st_size, source_stat_after.st_mtime_ns) == (
        source_stat_before.st_size,
        source_stat_before.st_mtime_ns,
    )


def test_working_collision_profile_rejects_a_missing_station_root():
    usd = pytest.importorskip("pxr.Usd")
    stage = usd.Stage.CreateInMemory()
    env = SimpleNamespace(sim=SimpleNamespace(stage=stage))

    with pytest.raises(ValueError, match="Expected one pipetting station collision root"):
        configure_working_collisions(env, None)
