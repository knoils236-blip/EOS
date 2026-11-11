"""Export the pipetting station USD as the web twin shown in the EOS UI.

Writes web_ui/public/twin/pipetting_station.glb and pipetting_station.json (joint axes, consumable rest poses and
initial tool grips). The GLB has one node per link / shell part / consumable and USD material, named
`<link|shell|obj>_<name>__<material>`, baked in the base-link frame at the USD rest pose (Z up), with the USD
materials and crease-split normals. Triangles are only removed where that moves the surface less than TOLERANCE_M.

Run with the Matterix python, which has pxr and trimesh; simplification also needs `pip install meshoptimizer`:
    /home/ry/anaconda3/envs/matterix/bin/python integrations/matterix/tools/export_station_twin.py
"""

from __future__ import annotations

import ast
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

import meshoptimizer
import numpy as np
import trimesh
from pxr import Gf, Usd, UsdGeom, UsdPhysics, UsdShade

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "integrations/matterix"))
from bridge.recording import shell_visual_paths  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Iterator

ASSET_DIR = Path("/home/ry/projects/Matterix/PipettingStation_20261031/assets/PipettingStation_Asset_v4/Asset")
STATION_CFG = Path(
    "/home/ry/projects/Matterix/PipettingStation_20261031/source/matterix_assets/matterix_assets/equipment"
    "/pipetting_station.py"
)
OUT_DIR = REPO_ROOT / "web_ui/public/twin"
ROOT = "/PipettingStation"
LINKS = f"{ROOT}/Mechanism/Links/"
CONSUMABLES = f"{ROOT}/Consumables/"
TOLERANCE_M = 2e-4  # a fifth of a millimetre: below a pixel even zoomed in on a single tool
CREASE = np.radians(35)  # edges sharper than this stay hard, the rest shade smooth
AXES = {"X": Gf.Vec3d(1, 0, 0), "Y": Gf.Vec3d(0, 1, 0), "Z": Gf.Vec3d(0, 0, 1)}


def _discarded_parts() -> set[str]:
    """Prims the simulation removes before startup, so the twin must not draw them.

    The USD is a CAD snapshot taken mid-operation: demo bottles, vials and a tip sit in the tools and at the
    decapping stations, exactly where real consumables are later carried. The station config lists them; they
    are read from its source because importing it needs a running Isaac Sim.
    """
    wanted = {"_DISCARDED_CAD_DEMO_PARTS", "_DISCARDED_INITIAL_SYRINGE"}
    found = {
        node.targets[0].id: set(ast.literal_eval(node.value))
        for node in ast.parse(STATION_CFG.read_text(encoding="utf-8")).body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in wanted
    }
    if found.keys() != wanted:
        raise RuntimeError(f"{STATION_CFG} no longer defines {sorted(wanted - found.keys())}")
    return set().union(*found.values())


def _material(prim) -> UsdShade.Material:
    return UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()[0]


def _gltf_material(material: UsdShade.Material) -> trimesh.visual.material.PBRMaterial:
    shader = material.ComputeSurfaceSource()[0]
    values = {usd_input.GetBaseName(): usd_input.Get() for usd_input in shader.GetInputs()}
    return trimesh.visual.material.PBRMaterial(
        name=material.GetPrim().GetName(),
        baseColorFactor=[*values["diffuseColor"], values["opacity"]],
        metallicFactor=values["metallic"],
        roughnessFactor=values["roughness"],
        emissiveFactor=list(values.get("emissiveColor", (0, 0, 0))),
        alphaMode="BLEND" if values["opacity"] < 1 else "OPAQUE",
        doubleSided=True,
    )


def _simplified(points: np.ndarray, faces: np.ndarray) -> trimesh.Trimesh:
    mesh = trimesh.Trimesh(points, faces, process=True)  # welds the duplicated CAD vertices
    vertices = np.ascontiguousarray(mesh.vertices, dtype=np.float32)
    indices = np.ascontiguousarray(mesh.faces, dtype=np.uint32).ravel()
    kept = np.empty_like(indices)
    count = meshoptimizer.simplify(
        kept, indices, vertices,
        target_index_count=0, target_error=TOLERANCE_M, options=meshoptimizer.SIMPLIFY_ERROR_ABSOLUTE,
    )
    mesh = trimesh.Trimesh(vertices, kept[:count].reshape(-1, 3), process=False)
    mesh.remove_unreferenced_vertices()
    return trimesh.graph.smooth_shade(mesh, angle=CREASE)


def _parts(prim, to_base) -> Iterator[tuple[UsdShade.Material, trimesh.Trimesh]]:
    """Yield (material, mesh) for a USD mesh, one per material where GeomSubsets split its faces."""
    usd_mesh = UsdGeom.Mesh(prim)
    counts = np.asarray(usd_mesh.GetFaceVertexCountsAttr().Get())
    indices = np.asarray(usd_mesh.GetFaceVertexIndicesAttr().Get())
    starts = np.cumsum(counts) - counts
    # fan triangulation, remembering which USD face every triangle came from
    triangles = np.array([
        (face, indices[start], indices[start + i], indices[start + i + 1])
        for face, (start, count) in enumerate(zip(starts, counts, strict=True))
        for i in range(1, count - 1)
    ])
    face_of, faces = triangles[:, 0], triangles[:, 1:]
    matrix = np.array(to_base)  # Gf matrices act on row vectors
    points = np.asarray(usd_mesh.GetPointsAttr().Get(), dtype=np.float64) @ matrix[:3, :3] + matrix[3, :3]
    flipped = usd_mesh.GetOrientationAttr().Get() == UsdGeom.Tokens.leftHanded
    if flipped != (np.linalg.det(matrix[:3, :3]) < 0):
        faces = faces[:, ::-1]

    unassigned = np.ones(len(counts), dtype=bool)
    for subset in UsdGeom.Subset.GetAllGeomSubsets(usd_mesh):
        subset_faces = np.asarray(subset.GetIndicesAttr().Get())
        unassigned[subset_faces] = False
        yield _material(subset.GetPrim()), _simplified(points, faces[np.isin(face_of, subset_faces)])
    if unassigned.any():
        yield _material(prim), _simplified(points, faces[unassigned[face_of]])


def _node(path: str, shell: tuple[str, ...]) -> str | None:
    if path.startswith(LINKS):
        link = path[len(LINKS):].split("/", 1)[0]
        return ("shell_" if path.startswith(shell) else "link_") + link
    if path.startswith(CONSUMABLES):
        return "obj_" + path[len(CONSUMABLES):].split("/", 1)[0]
    return None


def main() -> None:
    stage = Usd.Stage.Open(str(ASSET_DIR / "PipettingStation_Operational.usd"))
    cache = UsdGeom.XformCache()
    from_world = cache.GetLocalToWorldTransform(stage.GetPrimAtPath(LINKS + "base")).GetInverse()
    shell = tuple(path + "/" for path in shell_visual_paths(ROOT, ASSET_DIR / "rig_manifest.json"))

    discarded = _discarded_parts()
    source_faces = 0
    groups = defaultdict(list)
    materials = {}
    for prim in Usd.PrimRange(stage.GetPrimAtPath(ROOT), Usd.TraverseInstanceProxies()):
        if not prim.IsA(UsdGeom.Mesh) or UsdGeom.Imageable(prim).ComputeVisibility() == UsdGeom.Tokens.invisible:
            continue
        node = _node(str(prim.GetPath()), shell)
        if not node or discarded.intersection(str(prim.GetPath()).split("/")):
            continue
        source_faces += sum(count - 2 for count in UsdGeom.Mesh(prim).GetFaceVertexCountsAttr().Get())
        for material, mesh in _parts(prim, cache.GetLocalToWorldTransform(prim) * from_world):
            name = material.GetPrim().GetName()
            materials.setdefault(name, _gltf_material(material))
            groups[f"{node}__{name}"].append(mesh)
    scene = trimesh.Scene()
    for node, meshes in groups.items():
        merged = trimesh.Trimesh(
            np.concatenate([mesh.vertices for mesh in meshes]),
            np.concatenate([
                mesh.faces + offset
                for mesh, offset in zip(meshes, np.cumsum([0, *(len(m.vertices) for m in meshes[:-1])]), strict=True)
            ]),
            vertex_normals=np.concatenate([mesh.vertex_normals for mesh in meshes]),
            visual=trimesh.visual.TextureVisuals(material=materials[node.rsplit("__", 1)[1]]),
            process=False,
        )
        scene.add_geometry(merged, node_name=node, geom_name=node)

    joints, fixed, rest, attached = [], [], {}, {}
    for prim in stage.Traverse():
        if prim.IsA(UsdPhysics.Joint):
            joint = UsdPhysics.Joint(prim)
            bodies = [str(t) for rel in (joint.GetBody0Rel(), joint.GetBody1Rel()) for t in rel.GetTargets()]
            # skip the one-body world anchor and consumable-to-consumable closures
            if len(bodies) == 1 or not bodies[0].startswith(LINKS):
                continue
            if bodies[1].startswith(CONSUMABLES):
                if bodies[1][len(CONSUMABLES):] in discarded:
                    continue
                attached[bodies[1][len(CONSUMABLES):]] = bodies[0][len(LINKS):]  # held by a tool in the initial state
                continue
            parent, child = (body[len(LINKS):] for body in bodies)
            if prim.IsA(UsdPhysics.FixedJoint):
                fixed.append({"parent": parent, "child": child})
                continue
            frame = Gf.Matrix4d(Gf.Rotation(Gf.Quatd(joint.GetLocalRot0Attr().Get())), Gf.Vec3d(0))
            frame = frame * cache.GetLocalToWorldTransform(stage.GetPrimAtPath(bodies[0])) * from_world
            anchor = (cache.GetLocalToWorldTransform(stage.GetPrimAtPath(bodies[0])) * from_world).Transform(
                Gf.Vec3d(joint.GetLocalPos0Attr().Get()))
            joints.append({
                "name": prim.GetName(),
                "type": "revolute" if prim.IsA(UsdPhysics.RevoluteJoint) else "prismatic",
                "parent": parent,
                "child": child,
                "axis": list(frame.TransformDir(AXES[prim.GetAttribute("physics:axis").Get()]).GetNormalized()),
                "anchor": list(anchor),
            })
        elif (
            str(prim.GetPath()).startswith(CONSUMABLES)
            and prim.HasAPI(UsdPhysics.RigidBodyAPI)
            and prim.GetName() not in discarded
        ):
            pose = (cache.GetLocalToWorldTransform(prim) * from_world).RemoveScaleShear()
            quat = pose.ExtractRotationQuat()
            rest[prim.GetName()] = [*pose.ExtractTranslation(), *quat.GetImaginary(), quat.GetReal()]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    scene.export(OUT_DIR / "pipetting_station.glb")
    meta = {"joints": joints, "fixed": fixed, "attached": attached, "object_rest_xyz_qxyzw": rest}
    (OUT_DIR / "pipetting_station.json").write_text(json.dumps(meta), encoding="utf-8")
    faces = sum(len(geometry.faces) for geometry in scene.geometry.values())
    print(  # noqa: T201
        f"{len(groups)} nodes, {faces} of {source_faces} triangles, {len(joints)} joints, "
        f"{len(rest)} consumables -> {OUT_DIR}"
    )


if __name__ == "__main__":
    main()
