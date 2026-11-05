"""Validated capability catalog shared by the server and session worker."""

import copy
import hashlib
import math
from pathlib import Path

from .common import PIPETTING_SCENE_ID, ROOT, SCENE_ID, read_json, request_id
from .beaker_workspace import load_beaker_workspace
from .environment import GRID_ASSET_MANIFEST

CAPABILITIES_PATH = ROOT / "bridge/capabilities.json"
WORKFLOWS_PATH = ROOT / "bridge/session_workflows.yaml"
STATION_CATALOG_PATH = ROOT / "bridge/station_catalog.json"
STATION_SEMANTICS_PATH = ROOT / "bridge/station_semantics.py"
# operation: experiment-level step shown in the editor; skill: atomic Matterix action (editor advanced group);
# hidden: registered for protocols but not offered in the editor
TIERS = ("operation", "skill", "hidden")
PIPETTING_SOURCE_ROOT = Path("/home/ry/projects/Matterix/PipettingStation_20261031")

_SCENE_WORKFLOW_FILES = {
    SCENE_ID: WORKFLOWS_PATH,
    PIPETTING_SCENE_ID: STATION_CATALOG_PATH,
}


def _station_catalog():
    value = read_json(STATION_CATALOG_PATH)
    if not isinstance(value, dict):
        raise ValueError("station_catalog.json must contain an object")
    if value.get("scene_id") != PIPETTING_SCENE_ID:
        raise ValueError("Invalid station catalog scene")
    if value.get("task_id") != "Matterix-PipettingStation-Twin-v1":
        raise ValueError("Invalid station catalog task")
    if value.get("control_hz") != 250 or value.get("mode") != "physics":
        raise ValueError("Station catalog must describe the 250 Hz physics twin")
    workflows = value.get("workflows")
    enabled = value.get("enabled_capabilities")
    if not isinstance(workflows, dict) or not isinstance(enabled, dict):
        raise ValueError("Invalid station catalog entries")
    return value


def _load():
    items = read_json(CAPABILITIES_PATH)
    if not isinstance(items, list):
        raise ValueError("capabilities.json must contain a list")
    station = _station_catalog()
    result = {}
    required = {"id", "version", "scene_id", "label", "workflow_yaml", "workflow_name",
                "parameters", "observations", "success_criteria", "tier"}
    for item in items:
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError("Invalid capability entry")
        capability_id = request_id(item["id"])
        if capability_id in result:
            raise ValueError(f"Duplicate capability: {capability_id}")
        if item["tier"] not in TIERS:
            raise ValueError(f"Invalid tier for {capability_id}: {item['tier']}")
        scene_id = item["scene_id"]
        workflow_path = _SCENE_WORKFLOW_FILES.get(scene_id)
        if workflow_path is None or item["workflow_yaml"] != workflow_path.name:
            raise ValueError(f"Invalid scene or workflow for {capability_id}")
        if not isinstance(item["parameters"], dict):
            raise ValueError(f"Invalid parameters for {capability_id}")
        resolved = copy.deepcopy(item)
        if capability_id in {"lift_beaker", "lower_beaker"}:
            resolved["parameters"]["height_m"]["max"] = load_beaker_workspace().max_lift_m
        resolved["workflow_yaml"] = str(workflow_path)
        if scene_id == PIPETTING_SCENE_ID:
            enabled = station["enabled_capabilities"].get(capability_id)
            catalog_key = (
                enabled.get("workflow_catalog_key", item["workflow_name"])
                if isinstance(enabled, dict) else item["workflow_name"]
            )
            workflow = station["workflows"].get(catalog_key)
            if (not isinstance(enabled, dict) or not isinstance(workflow, dict)
                    or enabled.get("workflow_name") != item["workflow_name"]
                    or workflow.get("enabled_capability") != capability_id):
                raise ValueError(f"Station capability is not enabled in station_catalog: {capability_id}")
        result[capability_id] = resolved
    registered_station = {
        value["id"] for value in result.values() if value["scene_id"] == PIPETTING_SCENE_ID}
    if registered_station != set(station["enabled_capabilities"]):
        raise ValueError("Station catalog enabled capabilities do not match capabilities.json")
    return result


def catalog():
    values = _load()
    return [copy.deepcopy(value) for value in values.values()]


def get_capability(identifier):
    values = _load()
    try:
        return copy.deepcopy(values[identifier])
    except (KeyError, TypeError):
        raise ValueError(f"Unknown capability: {identifier}") from None


def config_version():
    if not WORKFLOWS_PATH.is_file():
        raise FileNotFoundError(f"Workflow YAML not found: {WORKFLOWS_PATH}")
    if not STATION_CATALOG_PATH.is_file():
        raise FileNotFoundError(f"Station catalog not found: {STATION_CATALOG_PATH}")
    station = _station_catalog()
    if station.get("source_root") != str(PIPETTING_SOURCE_ROOT):
        raise ValueError("Station catalog source root does not match the fixed export")
    digest = hashlib.sha256()
    trusted_files = [
        ("capabilities", CAPABILITIES_PATH),
        ("capability_contract", Path(__file__)),
        ("environment", ROOT / "bridge/environment.py"),
        ("grid_asset_manifest", GRID_ASSET_MANIFEST),
        ("session_manager", ROOT / "bridge/session_manager.py"),
        ("run_manager", ROOT / "bridge/manager.py"),
        ("sila_server", ROOT / "bridge/server.py"),
        ("sila_contract", ROOT / "bridge/Matterix.sila.xml"),
        ("session_actions", ROOT / "bridge/session_actions.py"),
        ("session_workflow", WORKFLOWS_PATH),
        ("station_catalog", STATION_CATALOG_PATH),
        ("station_semantics", STATION_SEMANTICS_PATH),
        ("station_experiment", ROOT / "bridge/station_experiment.py"),
        ("station_mixture", ROOT / "bridge/station_mixture.py"),
        ("station_initial_state", ROOT / "bridge/station_initial_state.json"),
        ("station_worker", ROOT / "bridge/station_worker.py"),
        ("station_collisions", ROOT / "bridge/station_collisions.py"),
        ("station_skills", ROOT / "bridge/station_skills.py"),
        ("session_worker", ROOT / "bridge/session_worker.py"),
        ("session_contract", ROOT / "bridge/common.py"),
        ("beaker_workspace", ROOT / "bridge/beaker_workspace.json"),
        ("beaker_motion", ROOT / "bridge/beaker_motion.py"),
        ("beaker_ik", ROOT / "bridge/beaker_ik.py"),
        ("beaker_workspace_loader", ROOT / "bridge/beaker_workspace.py"),
        ("recording", ROOT / "bridge/recording.py"),
    ]
    export_files = station.get("export_files")
    if not isinstance(export_files, dict):
        raise ValueError("Station catalog export_files must be an object")
    source_root = PIPETTING_SOURCE_ROOT.resolve()
    for role in sorted(export_files):
        relative = export_files[role]
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise ValueError(f"Invalid station export path: {role}")
        path = (source_root / relative).resolve()
        if not path.is_relative_to(source_root):
            raise ValueError(f"Station export path escapes its root: {role}")
        trusted_files.append((f"export/{role}", path))
    for role, path in trusted_files:
        if not path.is_file():
            raise FileNotFoundError(f"Trusted file not found for {role}: {path}")
        digest.update(role.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def validate_task(payload):
    if not isinstance(payload, dict) or set(payload) != {
            "request_id", "session_id", "capability_id", "parameters"}:
        raise ValueError("Expected request_id, session_id, capability_id and parameters only")
    task_id = request_id(payload["request_id"])
    session_id = request_id(payload["session_id"])
    capability = get_capability(payload["capability_id"])
    parameters = payload["parameters"]
    if not isinstance(parameters, dict):
        raise ValueError("parameters must be a JSON object")
    unknown = set(parameters) - set(capability["parameters"])
    if unknown:
        raise ValueError(f"Unknown capability parameter: {sorted(unknown)[0]}")
    normalized = {}
    for name, spec in capability["parameters"].items():
        value = parameters.get(name, spec["default"])
        if spec["type"] == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"{name} must be a JSON boolean")
        elif spec["type"] == "string":
            if not isinstance(value, str) or value not in spec["choices"]:
                raise ValueError(f"{name} must be one of {spec['choices']}")
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a number")
            try:
                finite = math.isfinite(float(value))
            except OverflowError:
                finite = False
            if not finite:
                raise ValueError(f"{name} must be finite")
            if spec["type"] == "integer":
                if value != int(value):
                    raise ValueError(f"{name} must be an integer")
                value = int(value)
                if "choices" in spec and value not in spec["choices"]:
                    raise ValueError(f"{name} must be one of {spec['choices']}")
            if value < spec["min"] or value > spec["max"]:
                raise ValueError(f"{name} must be between {spec['min']} and {spec['max']}")
        normalized[name] = value
    return {"request_id": task_id, "session_id": session_id,
            "capability_id": capability["id"], "parameters": normalized}
