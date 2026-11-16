"""Run manifests: the immutable, content-addressed plan of a protocol run.

A manifest records everything that decides what a run does: the resolved protocol, its parameters, the labs with
their device configuration, and the version of every task and device plugin it uses. Approval, preflight, resume and
task dispatch compare manifest hashes, so a plan that changed after it was approved or checked cannot run unnoticed.
"""

import hashlib
import importlib.metadata
import json
import platform
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from pydantic import BaseModel

from eos.configuration.configuration_manager import ConfigurationManager
from eos.configuration.packages import EntityType, source_fingerprint
from eos.configuration.entities.protocol_def import ProtocolDef
from eos.database.abstract_sql_db_interface import AsyncDbSession, Base
from eos.protocols.entities.protocol_run import ProtocolRunSubmission
from eos.protocols.exceptions import EosProtocolRunExecutionError
from eos.protocols.preflight import LAB_MAP_KEY, remap_protocol_labs

MANIFEST_SCHEMA = 1
REDACTED = "<redacted>"
_SECRET_KEY = re.compile(r"pass(word)?|secret|token|api[_-]?key|credential|private[_-]?key", re.IGNORECASE)
_URL_CREDENTIALS = re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/@\s]+@", re.IGNORECASE)


@dataclass(frozen=True)
class RunManifest:
    content: dict[str, Any]
    hash: str


class RunManifestModel(Base):
    __tablename__ = "run_manifests"

    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


def redact(value: Any) -> Any:
    """Replace values under secret-looking keys and credentials embedded in URLs, so manifests can be shared."""
    if isinstance(value, dict):
        return {k: REDACTED if _SECRET_KEY.search(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _URL_CREDENTIALS.sub(rf"\1{REDACTED}@", value)
    return value


def content_hash(content: Any) -> str:
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def resolve_protocol(configuration_manager: ConfigurationManager, submission: ProtocolRunSubmission) -> ProtocolDef:
    """The protocol as this submission runs it: the loaded definition, moved to twin labs for a preflight."""
    protocol = configuration_manager.protocols.get(submission.type)
    if protocol is None:
        raise EosProtocolRunExecutionError(f"Protocol '{submission.type}' is not loaded.")
    meta = submission.meta or {}
    if LAB_MAP_KEY in meta:
        protocol = remap_protocol_labs(protocol, meta[LAB_MAP_KEY], configuration_manager.labs)
    return protocol


def build_manifest(
    configuration_manager: ConfigurationManager, protocol: ProtocolDef, parameters: dict[str, Any]
) -> RunManifest:
    labs = {name: configuration_manager.labs[name] for name in protocol.labs}
    task_types = sorted({task.type for task in protocol.tasks})
    device_types = sorted({device.type for lab in labs.values() for device in lab.devices.values()})
    content = redact(
        {
            "schema": MANIFEST_SCHEMA,
            "protocol": protocol.model_dump(mode="json"),
            "parameters": parameters,
            "labs": {name: lab.model_dump(mode="json") for name, lab in labs.items()},
            "task_plugins": {t: configuration_manager.tasks.fingerprints[t] for t in task_types},
            "device_plugins": {t: configuration_manager.devices.fingerprints[t] for t in device_types},
            "environment": {"eos": importlib.metadata.version("eos"), "python": platform.python_version()},
        }
    )
    return RunManifest(content, content_hash(content))


def manifest_for(configuration_manager: ConfigurationManager, submission: ProtocolRunSubmission) -> RunManifest:
    return build_manifest(
        configuration_manager, resolve_protocol(configuration_manager, submission), submission.parameters
    )


def plan_hash(configuration_manager: ConfigurationManager, kind: str, submission: BaseModel) -> str:
    """Hash of everything an approval request approves: its submission and the definitions and code it would run
    with. If it differs when the request is decided, the plan changed after it was requested."""
    cm = configuration_manager
    if kind == "protocol_run":
        return manifest_for(cm, submission).hash
    if kind == "campaign":
        protocol = resolve_protocol(cm, ProtocolRunSubmission(name="", type=submission.protocol))
        package = cm.package_manager.find_package_for_entity(submission.protocol, EntityType.PROTOCOL)
        return content_hash(
            {
                "protocol": build_manifest(cm, protocol, {}).hash,  # each run's parameters come from the campaign
                "protocol_package": source_fingerprint(package.path),  # includes its optimizer.py
                "campaign": redact(submission.model_dump(mode="json", exclude={"name", "owner"})),
            }
        )
    devices = {}
    for slot, device in submission.devices.items():
        if device.lab_name not in cm.labs:
            raise EosProtocolRunExecutionError(f"Lab '{device.lab_name}' is not loaded.")
        lab_device = cm.labs[device.lab_name].devices[device.name]
        devices[slot] = {
            "device": lab_device.model_dump(mode="json"),
            "plugin": cm.devices.fingerprints[lab_device.type],
        }
    return content_hash(
        redact(
            {
                "task_plugin": cm.tasks.fingerprints[submission.type],
                "devices": devices,
                "task": submission.model_dump(mode="json", exclude={"name", "meta"}),
            }
        )
    )


def differences(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Name the parts of two manifests that differ, one level deep (e.g. 'labs.lab_a', 'task_plugins.Mix')."""
    changed = []
    for key in sorted(old.keys() | new.keys()):
        before, after = old.get(key), new.get(key)
        if before == after:
            continue
        if isinstance(before, dict) and isinstance(after, dict) and key != "protocol":
            changed += [f"{key}.{k}" for k in sorted(before.keys() | after.keys()) if before.get(k) != after.get(k)]
        else:
            changed.append(key)
    return changed


async def save_manifest(db: AsyncDbSession, manifest: RunManifest) -> None:
    """Store a manifest once; identical plans share one row because the hash is the content's address."""
    if await db.get(RunManifestModel, manifest.hash) is None:
        db.add(RunManifestModel(hash=manifest.hash, content=manifest.content))
        await db.flush()


async def load_manifest(db: AsyncDbSession, manifest_hash: str) -> dict[str, Any]:
    return (await db.get(RunManifestModel, manifest_hash)).content
