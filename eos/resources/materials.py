"""Physical material state: containers at positions, samples in containers, and the events that changed them.

Positions are plain names (e.g. ``lab.rack_A1``) holding at most one container, enforced by unique constraints.
A reservation of a target position is kept apart from where a container is: only a confirmed event moves it.
Events carry stable IDs, so a device or person reporting the same change twice changes state once.
"""

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, String, func, select
from sqlalchemy.orm import Mapped, mapped_column

from eos.database.abstract_sql_db_interface import AsyncDbSession, Base
from eos.resources.exceptions import EosResourceStateError


class ContainerModel(Base):
    __tablename__ = "material_containers"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    type: Mapped[str] = mapped_column(String(255), nullable=False)
    capacity_ul: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Last confirmed position, kept when the position becomes unknown so people know where to look
    # shortcut: one container per position; add a positions table with capacities when a rack slot holds several
    position: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    position_known: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    reserved_position: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)


class SampleModel(Base):
    __tablename__ = "material_samples"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    container_id: Mapped[str | None] = mapped_column(ForeignKey("material_containers.id"), nullable=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("material_samples.id"), nullable=True)
    volume_ul: Mapped[float | None] = mapped_column(Float, nullable=True)
    removed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default={})


class MaterialEventModel(Base):
    __tablename__ = "material_events"

    event_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=255)


class Move(_Event):
    """A device or person confirms the container arrived at `target`."""

    kind: Literal["move"] = "move"
    container_id: str
    target: str


class PositionUnknown(_Event):
    """A move was interrupted or the position cannot be confirmed; work needing its position is blocked."""

    kind: Literal["position_unknown"] = "position_unknown"
    container_id: str
    note: str = ""


class PositionCheck(_Event):
    """A person verified where the container actually is."""

    kind: Literal["position_check"] = "position_check"
    container_id: str
    position: str


class Split(_Event):
    """Part of a sample was transferred into another container as a new child sample."""

    kind: Literal["split"] = "split"
    sample_id: str
    new_sample_id: str
    container_id: str
    volume_ul: float = Field(gt=0)


class Remove(_Event):
    kind: Literal["remove"] = "remove"
    sample_id: str


MaterialEvent = Annotated[Move | PositionUnknown | PositionCheck | Split | Remove, Field(discriminator="kind")]
_events = TypeAdapter(MaterialEvent)


async def _container(db: AsyncDbSession, container_id: str) -> ContainerModel:
    container = await db.get(ContainerModel, container_id, with_for_update=True)
    if container is None:
        raise EosResourceStateError(f"Unknown container '{container_id}'")
    return container


async def _sample(db: AsyncDbSession, sample_id: str) -> SampleModel:
    sample = await db.get(SampleModel, sample_id, with_for_update=True)
    if sample is None or sample.removed:
        raise EosResourceStateError(f"Unknown or removed sample '{sample_id}'")
    return sample


async def _occupant(db: AsyncDbSession, position: str, column) -> str | None:
    return await db.scalar(select(ContainerModel.id).where(column == position))


async def reserve_position(db: AsyncDbSession, container_id: str, target: str) -> None:
    """Hold `target` for a planned move. The container stays where it is until a move is confirmed."""
    container = await _container(db, container_id)
    if not container.position_known:
        raise EosResourceStateError(f"Container '{container_id}' has an unknown position; check it first")
    for column in (ContainerModel.position, ContainerModel.reserved_position):
        holder = await _occupant(db, target, column)
        if holder not in (None, container_id):
            raise EosResourceStateError(f"Position '{target}' is already held by container '{holder}'")
    container.reserved_position = target
    await db.flush()


async def apply_material_events(
    db: AsyncDbSession, events: list[dict[str, Any]], actor: str, attempt_id: str | None = None
) -> None:
    for item in events:
        await apply_material_event(db, _events.validate_python(item), actor, attempt_id)


async def apply_material_event(db: AsyncDbSession, event: _Event, actor: str, attempt_id: str | None = None) -> None:
    payload = event.model_dump(mode="json")
    previous = await db.get(MaterialEventModel, event.event_id)
    if previous is not None:
        if previous.payload != payload:
            raise EosResourceStateError(f"Material event ID '{event.event_id}' already belongs to a different change")
        return  # Reported again, e.g. a resent device result: already applied

    details: dict[str, Any] = {}
    if isinstance(event, Move | PositionCheck):
        target = event.target if isinstance(event, Move) else event.position
        container = await _container(db, event.container_id)
        holder = await _occupant(db, target, ContainerModel.position)
        if holder not in (None, container.id):
            raise EosResourceStateError(f"Position '{target}' is occupied by container '{holder}'")
        details["source"] = container.position
        container.position, container.position_known = target, True
        if container.reserved_position == target:
            container.reserved_position = None
    elif isinstance(event, PositionUnknown):
        container = await _container(db, event.container_id)
        container.position_known = False
        details["last_confirmed_position"] = container.position
    elif isinstance(event, Split):
        parent = await _sample(db, event.sample_id)
        if parent.volume_ul is not None and parent.volume_ul < event.volume_ul:
            raise EosResourceStateError(
                f"Sample '{parent.id}' holds {parent.volume_ul} uL, less than {event.volume_ul}"
            )
        target = await _container(db, event.container_id)
        held = await db.scalar(
            select(func.coalesce(func.sum(SampleModel.volume_ul), 0.0)).where(
                SampleModel.container_id == target.id, SampleModel.removed.is_(False)
            )
        )
        if target.capacity_ul is not None and held + event.volume_ul > target.capacity_ul:
            raise EosResourceStateError(
                f"Container '{target.id}' holds {held} of {target.capacity_ul} uL; {event.volume_ul} uL does not fit"
            )
        if parent.volume_ul is not None:
            parent.volume_ul -= event.volume_ul
        db.add(
            SampleModel(id=event.new_sample_id, container_id=target.id, parent_id=parent.id, volume_ul=event.volume_ul)
        )
    else:
        sample = await _sample(db, event.sample_id)
        details["container_id"] = sample.container_id
        sample.removed, sample.container_id = True, None

    db.add(
        MaterialEventModel(
            event_id=event.event_id,
            kind=event.kind,
            payload=payload,
            attempt_id=attempt_id,
            actor=actor,
            created_at=datetime.now(UTC),
            details=details,
        )
    )
    await db.flush()


async def lineage(db: AsyncDbSession, sample_id: str) -> list[SampleModel]:
    """The sample followed by its ancestors, nearest first."""
    chain = []
    current = await db.get(SampleModel, sample_id)
    while current is not None:
        chain.append(current)
        current = await db.get(SampleModel, current.parent_id) if current.parent_id else None
    return chain
