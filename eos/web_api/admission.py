"""Execution admission shared by every submit endpoint: which submissions must wait for a person to approve them.

Policy: a lab declaring ``approval: always`` needs approval for every submitter. Otherwise automation (an EOS API token,
or an agent calling through the MCP server) needs approval for each lab that is not simulated; signed-in people submit
directly.
"""

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select

from eos.auth.entities.user_role import AuthenticatedUser
from eos.configuration.entities.definition import DefinitionModel
from eos.database.abstract_sql_db_interface import AsyncDbSession
from eos.web_api.exception_handling import APIError


async def definition_data(db: AsyncDbSession, definition_type: str, name: str) -> dict[str, Any]:
    query = select(DefinitionModel).where(DefinitionModel.type == definition_type, DefinitionModel.name == name)
    definition = (await db.execute(query)).scalar_one_or_none()
    if definition is None:
        raise APIError(status_code=404, detail=f"Unknown {definition_type} '{name}'")
    return definition.data


async def protocol_labs(db: AsyncDbSession, protocol_type: str) -> list[str]:
    return list((await definition_data(db, "protocol", protocol_type))["labs"])


async def labs_needing_approval(db: AsyncDbSession, labs: Iterable[str], user: AuthenticatedUser) -> list[str]:
    needing = []
    for lab in labs:
        data = await definition_data(db, "lab", lab)
        if data.get("approval") == "always" or (user.automation and not data.get("simulated", False)):
            needing.append(lab)
    return needing


async def require_no_approval(db: AsyncDbSession, labs: Iterable[str], user: AuthenticatedUser, endpoint: str) -> None:
    """Reject a direct submission that the policy says must be approved first, so no entry point bypasses it."""
    if needing := await labs_needing_approval(db, labs, user):
        raise APIError(
            status_code=403,
            detail=f"Submissions to labs {needing} need approval; submit through POST /approvals/{endpoint} instead",
        )
