import re
from typing import ClassVar

import yaml as pyyaml
from litestar import get, post, Controller, Request
from pydantic import BaseModel
from sqlalchemy import select

from eos.auth.authorization import get_user_roles, is_auth_disabled, require_role, stamp_owner
from eos.auth.entities.user_role import AuthenticatedUser, Role
from eos.configuration.entities.definition import DefinitionModel
from eos.configuration.exceptions import EosConfigurationError
from eos.configuration.entities.protocol_def import ProtocolDef
from eos.configuration.entities.lab_def import LabDef
from eos.configuration.validation import ProtocolValidator
from eos.database.abstract_sql_db_interface import AsyncDbSession
from eos.protocols.drafting import ProtocolDrafter
from eos.protocols.entities.protocol_run import ProtocolRunSubmission, ProtocolRun
from eos.orchestration.exceptions import EosProtocolInUseError
from eos.orchestration.orchestrator import Orchestrator
from eos.orchestration.services.lab_service import LabService
from eos.web_api.exception_handling import APIError

# Pattern to extract task names from validation error messages
_TASK_NAME_PATTERN = re.compile(r"[Tt]ask '([^']+)'")


def _parse_validation_errors(error_message: str) -> list[dict]:
    """Parse a multi-line validation error into structured per-task errors."""
    errors = []
    for raw_line in error_message.strip().split("\n"):
        cleaned = raw_line.strip().lstrip("- ")
        if not cleaned:
            continue
        match = _TASK_NAME_PATTERN.search(cleaned)
        errors.append({"task": match.group(1) if match else None, "message": cleaned})
    return errors or [{"task": None, "message": error_message}]


class ProtocolTypes(BaseModel):
    protocol_types: list[str]


class ReloadProtocolsRequest(BaseModel):
    protocol_types: list[str]
    if_unused: bool = False


class ReloadProtocolsResponse(BaseModel):
    reloaded: list[str]


class ProtocolTypesResponse(BaseModel):
    protocol_types: list[str]


class ProtocolValidationRequest(BaseModel):
    protocol_yaml: str


class ProtocolSaveRequest(BaseModel):
    package: str
    protocol_yaml: str
    overwrite: bool = False


class ProtocolDraftRequest(BaseModel):
    goal: str
    labs: list[str]
    protocol_type: str = "draft_protocol"
    model: str | None = None  # e.g. "claude-agent-sdk:sonnet"; default follows the server AI settings


async def _definitions(db: AsyncDbSession, definition_type: str, names: list[str] | None = None) -> dict[str, dict]:
    query = select(DefinitionModel).where(DefinitionModel.type == definition_type)
    if names is not None:
        query = query.where(DefinitionModel.name.in_(names))
    return {defn.name: defn.data for defn in (await db.execute(query)).scalars()}


async def _can_load_protocol_labs(request: Request, db: AsyncDbSession, user: AuthenticatedUser) -> bool:
    if is_auth_disabled(request):
        return True
    return any(role.role == Role.SUPERUSER for role in await get_user_roles(db, user.sub))


async def validate_protocol_yaml(protocol_yaml: str, db: AsyncDbSession, labs_service: LabService) -> dict:
    """
    Validate protocol YAML against the stored lab and task specs, then let the assigned devices check the order of
    their steps. Returns per-task errors where possible.
    """
    try:
        protocol_def = ProtocolDef.model_validate(pyyaml.safe_load(protocol_yaml))
    except Exception as e:
        return {"valid": False, "errors": [{"task": None, "message": f"YAML parse error: {e}"}]}

    lab_defs = await _definitions(db, "lab", list(protocol_def.labs))
    missing_labs = [name for name in protocol_def.labs if name not in lab_defs]
    if missing_labs:
        return {"valid": False, "errors": [{"task": None, "message": f"Unknown labs: {missing_labs}"}]}

    labs = [LabDef.model_validate(lab_defs[name]) for name in protocol_def.labs]
    try:
        ProtocolValidator(protocol_def, labs).validate()
    except Exception as e:
        return {"valid": False, "errors": _parse_validation_errors(str(e))}
    errors = labs_service.check_device_plans(protocol_def)
    return {"valid": not errors, "errors": errors}


class ProtocolController(Controller):
    """Controller for protocol-related endpoints."""

    path = "/protocols"
    guards: ClassVar = [require_role(Role.VIEWER)]

    @post("/", guards=[require_role(Role.SUBMITTER)])
    async def submit_protocol_run(
        self, data: ProtocolRunSubmission, db: AsyncDbSession, orchestrator: Orchestrator, user: AuthenticatedUser
    ) -> dict[str, str]:
        """Submit a new protocol run for execution."""
        stamp_owner(data, user)
        await orchestrator.protocols.submit_protocol_run(db, data)
        return {"message": "ProtocolRun submitted"}

    @post("/{protocol_run_name:str}/cancel", guards=[require_role(Role.SUBMITTER)])
    async def cancel_protocol_run(self, protocol_run_name: str, orchestrator: Orchestrator) -> dict[str, str]:
        """Cancel a running protocol run (standalone or part of a campaign)."""
        # First try to cancel as a standalone protocol run
        if protocol_run_name in orchestrator.protocols.submitted_protocol_runs:
            await orchestrator.protocols.cancel_protocol_run(protocol_run_name)
            return {"message": "ProtocolRun cancellation requested"}

        # Try to cancel as a campaign protocol run (queues for cancellation in campaign's main loop)
        queued = await orchestrator.campaigns.cancel_campaign_protocol_run(protocol_run_name)
        if queued:
            return {"message": "Campaign protocol run cancellation queued"}

        raise APIError(status_code=404, detail=f"ProtocolRun '{protocol_run_name}' not found in running protocols")

    @get("/{protocol_run_name:str}")
    async def get_protocol_run(
        self, protocol_run_name: str, db: AsyncDbSession, orchestrator: Orchestrator
    ) -> ProtocolRun:
        """Get a protocol run by name."""
        protocol_run = await orchestrator.protocols.get_protocol_run(db, protocol_run_name)

        if protocol_run is None:
            raise APIError(status_code=404, detail="ProtocolRun not found")

        return protocol_run

    @get("/types")
    async def get_protocol_types(self, orchestrator: Orchestrator) -> dict[str, bool]:
        """List protocol types."""
        return await orchestrator.loading.list_protocols()

    @post("/load", guards=[require_role(Role.LAB_ADMIN)])
    async def load_protocols(
        self,
        data: ProtocolTypes,
        db: AsyncDbSession,
        orchestrator: Orchestrator,
        request: Request,
        user: AuthenticatedUser,
    ) -> dict[str, str]:
        """Load protocol configurations."""
        await orchestrator.loading.load_protocols(
            db, set(data.protocol_types), load_dependencies=await _can_load_protocol_labs(request, db, user)
        )
        return {"message": "Protocol configurations loaded"}

    @post("/unload", guards=[require_role(Role.LAB_ADMIN)])
    async def unload_protocols(
        self, data: ProtocolTypes, db: AsyncDbSession, orchestrator: Orchestrator
    ) -> dict[str, str]:
        """Unload protocol configurations."""
        await orchestrator.loading.unload_protocols(db, set(data.protocol_types))
        return {"message": "Protocol configurations unloaded"}

    @post("/reload", guards=[require_role(Role.LAB_ADMIN)])
    async def reload_protocols(
        self,
        data: ReloadProtocolsRequest,
        db: AsyncDbSession,
        orchestrator: Orchestrator,
        request: Request,
        user: AuthenticatedUser,
    ) -> ReloadProtocolsResponse:
        """Reload protocol configurations."""
        reloaded = await orchestrator.loading.reload_protocols(
            db,
            set(data.protocol_types),
            if_unused=data.if_unused,
            load_dependencies=await _can_load_protocol_labs(request, db, user),
        )
        return ReloadProtocolsResponse(reloaded=sorted(reloaded))

    @post("/validate", guards=[require_role(Role.EDITOR)])
    async def validate_protocol_yaml(
        self, data: ProtocolValidationRequest, db: AsyncDbSession, orchestrator: Orchestrator
    ) -> dict:
        """Validate protocol YAML against lab and task specs and the devices' step-order checks.

        Returns structured errors with per-task attribution when possible.
        """
        return await validate_protocol_yaml(data.protocol_yaml, db, orchestrator.labs)

    @post("/save", guards=[require_role(Role.EDITOR)])
    async def save_protocol(self, data: ProtocolSaveRequest, db: AsyncDbSession, orchestrator: Orchestrator) -> dict:
        """Validate a protocol, write it into a package and load it when its labs are already loaded."""
        check = await validate_protocol_yaml(data.protocol_yaml, db, orchestrator.labs)
        if not check["valid"]:
            return {"saved": False, "loaded": False, **check}
        protocol = ProtocolDef.model_validate(pyyaml.safe_load(data.protocol_yaml))
        try:
            loaded = await orchestrator.loading.save_protocol(
                db, data.package, protocol, data.protocol_yaml, overwrite=data.overwrite
            )
        except (EosConfigurationError, EosProtocolInUseError) as e:
            raise APIError(status_code=409, detail=str(e)) from e
        return {"saved": True, "loaded": loaded, "protocol_type": protocol.type, "valid": True, "errors": []}

    @post("/draft", guards=[require_role(Role.EDITOR)])
    async def draft_protocol(self, data: ProtocolDraftRequest, db: AsyncDbSession, orchestrator: Orchestrator) -> dict:
        """Draft a protocol for a goal with an AI model, validated (and repaired) against the chosen labs."""
        labs = await _definitions(db, "lab", data.labs)
        missing = sorted(set(data.labs) - set(labs))
        if missing:
            raise APIError(400, f"Unknown labs: {missing}")
        task_specs = await _definitions(db, "task")
        scenes = [scene for scene in await orchestrator.labs.get_editor_scenes() if scene["lab_name"] in labs]
        drafter = ProtocolDrafter(model=data.model)
        draft = await drafter.draft(
            data.goal,
            data.protocol_type,
            labs,
            task_specs,
            lambda text: validate_protocol_yaml(text, db, orchestrator.labs),
            scenes,
        )
        return draft.model_dump()
