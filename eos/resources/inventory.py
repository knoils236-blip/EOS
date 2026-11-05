"""Transactional consumable counts and idempotent device/manual events."""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import DateTime, Integer, JSON, String, select, update
from sqlalchemy.orm import Mapped, mapped_column

from eos.database.abstract_sql_db_interface import AsyncDbSession, Base
from eos.resources.entities.resource import Resource, ResourceModel
from eos.resources.exceptions import EosResourceStateError


class InventoryEvent(BaseModel):
    resource_name: str = Field(min_length=1, max_length=255)
    event_id: str = Field(min_length=1, max_length=255)
    delta: int = Field(strict=True, ge=-1_000_000, le=1_000_000)
    source: Literal["simulation", "hardware", "manual"]
    note: str = Field("", max_length=1000)


class InventoryEventModel(Base):
    __tablename__ = "inventory_events"

    resource_name: Mapped[str] = mapped_column(String(255), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    delta: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16))
    note: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict] = mapped_column(JSON)


async def apply_inventory_events(db: AsyncDbSession, events: list[dict]) -> None:
    # Use one lock order when a device reports multiple stock items.
    for item in sorted(events, key=lambda item: item["resource_name"]):
        await apply_inventory_event(db, InventoryEvent.model_validate(item))


async def apply_inventory_event(db: AsyncDbSession, event: InventoryEvent) -> Resource:
    # Acquire a write lock before reading, including on SQLite (which ignores FOR UPDATE).
    await db.execute(
        update(ResourceModel).where(ResourceModel.name == event.resource_name).values(meta=ResourceModel.meta)
    )
    row = await db.scalar(
        select(ResourceModel).where(ResourceModel.name == event.resource_name).execution_options(populate_existing=True)
    )
    if row is None or row.meta.get("consumable") is not True:
        raise EosResourceStateError("Resource is not a registered consumable")
    previous = await db.get(InventoryEventModel, (event.resource_name, event.event_id))
    if previous is not None:
        if (previous.delta, previous.source, previous.note) != (event.delta, event.source, event.note):
            raise EosResourceStateError("Inventory event ID already belongs to a different change")
        return Resource.model_validate(row)
    quantity = row.meta.get("quantity")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 0:
        raise EosResourceStateError("Consumable quantity must be a non-negative integer")
    if quantity + event.delta < 0:
        raise EosResourceStateError(f"Insufficient stock for '{row.name}': {quantity} available")
    now = datetime.now(UTC)
    row.meta = {**row.meta, "quantity": quantity + event.delta, "updated_at": now.isoformat()}
    db.add(InventoryEventModel(**event.model_dump(), created_at=now, details={"remaining": quantity + event.delta}))
    await db.flush()
    return Resource.model_validate(row)
