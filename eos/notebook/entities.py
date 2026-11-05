from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from eos.database.abstract_sql_db_interface import Base

NotebookKind = Literal["hypothesis", "design", "observation", "interpretation", "decision", "note"]


class NotebookEntryCreate(BaseModel):
    """A lab-notebook entry: the reasoning behind experiments, written by a scientist or an AI agent."""

    kind: NotebookKind
    content: str = Field(min_length=1, max_length=20000)
    campaign_name: str | None = None
    protocol_run_name: str | None = None


class NotebookEntry(NotebookEntryCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    author: str
    created_at: datetime


class NotebookEntryModel(Base):
    """Kept apart from the optimizer journal, which is fed back into the optimizer's own prompt."""

    __tablename__ = "notebook_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Plain names, not foreign keys: an entry may precede the run it plans and outlive a deleted one
    campaign_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    protocol_run_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
