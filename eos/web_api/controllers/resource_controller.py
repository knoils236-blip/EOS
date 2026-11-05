from typing import ClassVar

from litestar import get, post, Controller
from pydantic import BaseModel

from eos.auth.authorization import require_role
from eos.auth.entities.user_role import Role
from eos.database.abstract_sql_db_interface import AsyncDbSession
from eos.utils.di.di_deps import get_resource_manager
from eos.resources.inventory import InventoryEvent, apply_inventory_event
from eos.resources.exceptions import EosResourceStateError
from eos.web_api.exception_handling import APIError


class ResetResourcesRequest(BaseModel):
    resource_names: list[str]


class ResourceController(Controller):
    """Controller for resource-related endpoints."""

    path = "/resources"
    guards: ClassVar = [require_role(Role.VIEWER)]

    @post("/inventory-events", guards=[require_role(Role.SUBMITTER)])
    async def inventory_event(self, data: InventoryEvent, db: AsyncDbSession) -> dict:
        """Record a manual replenishment or a uniquely identified device consumption event."""
        try:
            resource = await apply_inventory_event(db, data)
        except EosResourceStateError as exc:
            raise APIError(status_code=409, detail=str(exc)) from None
        await db.commit()
        return resource.model_dump()

    @get("/")
    async def get_resources(self, db: AsyncDbSession) -> list[dict]:
        """Get all resources."""
        resources = await get_resource_manager().get_resources(db)
        return [r.model_dump() for r in resources]

    @post("/reset", guards=[require_role(Role.SUBMITTER)])
    async def reset_resources(self, data: ResetResourcesRequest, db: AsyncDbSession) -> list[dict]:
        """Reset resources to their default metadata from lab configuration."""
        resource_manager = get_resource_manager()

        reset = []
        for name in data.resource_names:
            current = await resource_manager.get_resource(db, name)
            if current.meta.get("consumable") is True:
                raise APIError(status_code=409, detail="耗材库存不能重置，请使用补充库存操作。")
            default = resource_manager.get_default_resource(name)
            if default:
                await resource_manager.update_resource(db, default)
                reset.append(default)

        await db.commit()
        return [r.model_dump() for r in reset]
