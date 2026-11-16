"""
Simulation-first preflight: run a protocol on twin labs (e.g. a Matterix digital twin of a real lab) before the
real run. The real run starts only if the preflight completes.

A submission requests it with ``meta["preflight"] = {"real_lab": "twin_lab", ...}``. The preflight run is the same
protocol with the same parameters, its devices remapped by ``meta["lab_map"]``.
"""

from typing import Any

from eos.configuration.entities.lab_def import LabDef
from eos.configuration.entities.protocol_def import ProtocolDef
from eos.configuration.entities.task_def import DeviceAssignmentDef, DeviceIdentifier, DynamicDeviceAssignmentDef
from eos.protocols.entities.protocol_run import ProtocolRunSubmission
from eos.protocols.exceptions import EosProtocolRunExecutionError

PREFLIGHT_KEY = "preflight"  # on the real run: {real lab: twin lab}
LAB_MAP_KEY = "lab_map"  # on the preflight run: the same mapping, applied to its devices
PREFLIGHT_FOR_KEY = "preflight_for"  # on the preflight run: the real run it guards
PREFLIGHT_RUN_KEY = "preflight_run"  # on the real run, once its preflight passed
PREFLIGHT_EVIDENCE_KEY = "preflight_evidence"  # on the real run: what its passed preflight checked, and at which level
# Only EOS sets these; a client sending one could make a run look preflighted
RESERVED_META_KEYS = (PREFLIGHT_FOR_KEY, PREFLIGHT_RUN_KEY, PREFLIGHT_EVIDENCE_KEY)


def reject_reserved_meta(submission: ProtocolRunSubmission) -> None:
    if reserved := sorted(set(submission.meta or {}) & set(RESERVED_META_KEYS)):
        raise EosProtocolRunExecutionError(f"Metadata keys {reserved} are set by EOS and cannot be submitted")


def preflight_evidence(
    preflight_run: str, plan_hash: str, lab_map: dict[str, str], labs: dict[str, LabDef]
) -> dict[str, Any]:
    """What a passed preflight shows about the real run's plan. Levels come from the twin labs' declared fidelity;
    an undeclared fidelity stays undeclared rather than being read as physics, and a twin never counts as hardware."""
    checks = [{"name": "definitions_validated_at_load", "level": "L0", "result": "passed"}]
    checks += [
        {
            "name": f"twin_run:{twin}",
            "level": labs[twin].fidelity or "undeclared",
            "result": "passed",
            "run": preflight_run,
        }
        for twin in sorted(set(lab_map.values()))
    ]
    return {"run": preflight_run, "plan": plan_hash, "checks": checks, "levels": sorted({c["level"] for c in checks})}


def preflight_name(protocol_run_name: str) -> str:
    return f"{protocol_run_name}__preflight"


def split_preflight(submission: ProtocolRunSubmission) -> tuple[ProtocolRunSubmission, ProtocolRunSubmission]:
    """Return (preflight run, real run to start after it). The real run no longer asks for a preflight."""
    meta = dict(submission.meta or {})
    lab_map = meta.pop(PREFLIGHT_KEY)
    name = preflight_name(submission.name)
    preflight = submission.model_copy(
        update={"name": name, "meta": {**meta, LAB_MAP_KEY: lab_map, PREFLIGHT_FOR_KEY: submission.name}}
    )
    real = submission.model_copy(update={"meta": {**meta, PREFLIGHT_RUN_KEY: name}})
    return preflight, real


def remap_protocol_labs(protocol: ProtocolDef, lab_map: dict[str, str], labs: dict[str, LabDef]) -> ProtocolDef:
    """
    Return the protocol with every device of a mapped lab moved to its twin lab. Every resulting lab must be
    loaded and simulated; twin devices must have the same names and types.
    """
    if (
        not isinstance(lab_map, dict)
        or not lab_map
        or any(not isinstance(source, str) or not isinstance(target, str) for source, target in lab_map.items())
    ):
        raise EosProtocolRunExecutionError("Preflight requires a non-empty mapping of lab names to twin lab names")
    unknown = set(lab_map) - set(protocol.labs)
    if unknown:
        raise EosProtocolRunExecutionError(f"Preflight maps labs the protocol does not use: {sorted(unknown)}")
    protocol_labs = [lab_map.get(lab, lab) for lab in protocol.labs]
    missing = [lab for lab in protocol_labs if lab not in labs]
    if missing:
        raise EosProtocolRunExecutionError(f"Preflight twin labs are not loaded: {missing}")
    real_labs = [lab for lab in protocol_labs if not labs[lab].simulated]
    if real_labs:
        raise EosProtocolRunExecutionError(f"Preflight labs must be marked simulated: {real_labs}")

    def twin_device(lab_name: str, device_name: str) -> str:
        twin = lab_map.get(lab_name, lab_name)
        if twin != lab_name:
            real = labs[lab_name].devices[device_name] if lab_name in labs else None
            twin_dev = labs[twin].devices.get(device_name)
            if twin_dev is None or (real is not None and real.type != twin_dev.type):
                raise EosProtocolRunExecutionError(
                    f"Twin lab '{twin}' has no device '{device_name}' of the type used in lab '{lab_name}'"
                )
        return twin

    tasks = []
    for task in protocol.tasks:
        devices: dict[str, Any] = {}
        for slot, assignment in task.devices.items():
            device = assignment
            if isinstance(assignment, DeviceAssignmentDef):
                device = assignment.model_copy(update={"lab_name": twin_device(assignment.lab_name, assignment.name)})
            elif isinstance(assignment, DynamicDeviceAssignmentDef):
                device = assignment.model_copy(
                    update={
                        # Unrestricted dynamic devices could otherwise land on real equipment
                        "allowed_labs": [lab_map.get(lab, lab) for lab in (assignment.allowed_labs or protocol.labs)],
                        "allowed_devices": [
                            DeviceIdentifier(lab_name=twin_device(d.lab_name, d.name), name=d.name)
                            for d in assignment.allowed_devices
                        ]
                        if assignment.allowed_devices
                        else None,
                    }
                )
            devices[slot] = device  # References to earlier tasks' devices ("task.slot") follow those tasks
        tasks.append(task.model_copy(update={"devices": devices}))
    return protocol.model_copy(update={"labs": protocol_labs, "tasks": tasks})
