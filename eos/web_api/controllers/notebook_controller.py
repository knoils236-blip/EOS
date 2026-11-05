from typing import ClassVar

from litestar import Controller, get, post
from sqlalchemy import select

from eos.auth.authorization import require_role
from eos.auth.entities.user_role import AuthenticatedUser, Role
from eos.database.abstract_sql_db_interface import AsyncDbSession
from eos.notebook.entities import NotebookEntry, NotebookEntryCreate, NotebookEntryModel, NotebookKind

MAX_ENTRIES = 200


class NotebookController(Controller):
    """The lab notebook: hypotheses, designs and interpretations behind experiments, in writing order."""

    path = "/notebook"
    guards: ClassVar = [require_role(Role.VIEWER)]

    @get("/")
    async def list_entries(
        self,
        db: AsyncDbSession,
        campaign_name: str | None = None,
        protocol_run_name: str | None = None,
        kind: NotebookKind | None = None,
        limit: int = 50,
    ) -> list[NotebookEntry]:
        query = select(NotebookEntryModel)
        if campaign_name is not None:
            query = query.where(NotebookEntryModel.campaign_name == campaign_name)
        if protocol_run_name is not None:
            query = query.where(NotebookEntryModel.protocol_run_name == protocol_run_name)
        if kind is not None:
            query = query.where(NotebookEntryModel.kind == kind)
        # The newest entries, returned oldest first so they read as a log
        query = query.order_by(NotebookEntryModel.id.desc()).limit(min(max(limit, 1), MAX_ENTRIES))
        rows = (await db.execute(query)).scalars().all()
        return [NotebookEntry.model_validate(row) for row in reversed(rows)]

    @post("/", guards=[require_role(Role.SUBMITTER)])
    async def add_entry(self, data: NotebookEntryCreate, db: AsyncDbSession, user: AuthenticatedUser) -> NotebookEntry:
        row = NotebookEntryModel(**data.model_dump(), author=user.sub)
        db.add(row)
        await db.flush()
        return NotebookEntry.model_validate(row)
