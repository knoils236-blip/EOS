from typing import Any, ClassVar

from litestar import Controller, get, post
from pydantic import BaseModel, Field
from sqlalchemy import select

from eos.auth.authorization import require_role
from eos.auth.entities.user_role import AuthenticatedUser, Role
from eos.database.abstract_sql_db_interface import AsyncDbSession
from eos.resources.exceptions import EosResourceStateError
from eos.resources.materials import (
    ContainerModel,
    MaterialEvent,
    MaterialEventModel,
    SampleModel,
    apply_material_event,
    lineage,
    reserve_position,
)
from eos.web_api.exception_handling import APIError


class ContainerRegistration(BaseModel):
    id: str = Field(min_length=1, max_length=255)
    type: str = Field(min_length=1, max_length=255)
    capacity_ul: float | None = Field(None, gt=0)
    position: str | None = None


class SampleRegistration(BaseModel):
    id: str = Field(min_length=1, max_length=255)
    container_id: str
    volume_ul: float | None = Field(None, ge=0)
    details: dict[str, Any] = Field(default_factory=dict)


class Reservation(BaseModel):
    position: str = Field(min_length=1, max_length=255)


def _container(row: ContainerModel) -> dict[str, Any]:
    return {
        c: getattr(row, c) for c in ("id", "type", "capacity_ul", "position", "position_known", "reserved_position")
    }


def _sample(row: SampleModel) -> dict[str, Any]:
    return {c: getattr(row, c) for c in ("id", "container_id", "parent_id", "volume_ul", "removed", "details")}


class MaterialController(Controller):
    """Containers, samples and their positions. Changes go through idempotent material events."""

    path = "/materials"
    guards: ClassVar = [require_role(Role.VIEWER)]

    @get("/containers")
    async def containers(self, db: AsyncDbSession) -> list[dict[str, Any]]:
        return [_container(row) for row in await db.scalars(select(ContainerModel).order_by(ContainerModel.id))]

    @get("/samples")
    async def samples(self, db: AsyncDbSession) -> list[dict[str, Any]]:
        return [_sample(row) for row in await db.scalars(select(SampleModel).order_by(SampleModel.id))]

    @get("/samples/{sample_id:str}/lineage")
    async def sample_lineage(self, sample_id: str, db: AsyncDbSession) -> list[dict[str, Any]]:
        chain = await lineage(db, sample_id)
        if not chain:
            raise APIError(status_code=404, detail="Sample not found")
        return [_sample(row) for row in chain]

    @get("/events")
    async def events(self, db: AsyncDbSession, container_id: str | None = None) -> list[dict[str, Any]]:
        rows = await db.scalars(select(MaterialEventModel).order_by(MaterialEventModel.created_at.desc()).limit(500))
        return [
            {
                "event_id": r.event_id,
                "kind": r.kind,
                "payload": r.payload,
                "attempt_id": r.attempt_id,
                "actor": r.actor,
                "created_at": r.created_at,
                "details": r.details,
            }
            for r in rows
            if container_id is None or r.payload.get("container_id") == container_id
        ]

    @post("/containers", guards=[require_role(Role.SUBMITTER)])
    async def register_container(self, data: ContainerRegistration, db: AsyncDbSession) -> dict[str, Any]:
        if await db.get(ContainerModel, data.id) is not None:
            raise APIError(status_code=409, detail=f"Container '{data.id}' already exists")
        if data.position and await db.scalar(select(ContainerModel.id).where(ContainerModel.position == data.position)):
            raise APIError(status_code=409, detail=f"Position '{data.position}' is occupied")
        row = ContainerModel(**data.model_dump(), position_known=True)
        db.add(row)
        await db.commit()
        return _container(row)

    @post("/samples", guards=[require_role(Role.SUBMITTER)])
    async def register_sample(self, data: SampleRegistration, db: AsyncDbSession) -> dict[str, Any]:
        if await db.get(ContainerModel, data.container_id) is None:
            raise APIError(status_code=404, detail=f"Unknown container '{data.container_id}'")
        row = SampleModel(**data.model_dump(), removed=False)
        db.add(row)
        await db.commit()
        return _sample(row)

    @post("/containers/{container_id:str}/reservation", guards=[require_role(Role.SUBMITTER)])
    async def reserve(self, container_id: str, data: Reservation, db: AsyncDbSession) -> dict[str, Any]:
        try:
            await reserve_position(db, container_id, data.position)
        except EosResourceStateError as e:
            raise APIError(status_code=409, detail=str(e)) from None
        await db.commit()
        return _container(await db.get(ContainerModel, container_id))

    @post("/events", guards=[require_role(Role.LAB_ADMIN)])
    async def record_event(self, data: MaterialEvent, db: AsyncDbSession, user: AuthenticatedUser) -> dict[str, str]:
        """A person's record of a material change, e.g. moving a container by hand or checking where it is."""
        try:
            await apply_material_event(db, data, actor=f"user:{user.sub}")
        except EosResourceStateError as e:
            raise APIError(status_code=409, detail=str(e)) from None
        await db.commit()
        return {"event_id": data.event_id}
