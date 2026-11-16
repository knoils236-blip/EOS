from eos.configuration.eos_config import DbConfig

from pathlib import Path
import sqlite3
import sys
from sqlite3 import Connection
from typing import IO

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from eos.logging.logger import log
from eos.database.abstract_sql_db_interface import AbstractSqlDbInterface
from eos.database.exceptions import EosControlLockError

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl


def _open_locked(path: Path) -> IO:
    """Open ``path`` holding a non-blocking exclusive OS lock; raises OSError if another process holds it."""
    handle = path.open("a+")
    try:
        if sys.platform == "win32":
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise
    return handle


class SqliteDbInterface(AbstractSqlDbInterface):
    """SQLite-specific database interface implementation."""

    DB_FILE_EXTENSION = ".db"
    _control_lock_file: IO | None = None
    IN_MEMORY_PATH = ":memory:"

    def __init__(self, db_config: DbConfig):
        """Initialize database interface with configuration."""
        self._is_in_memory = db_config.sqlite.in_memory
        super().__init__(db_config)

    def _get_connection_args(self) -> dict:
        return {
            "connect_args": {
                "check_same_thread": False,  # Allow multi-threading
                "timeout": 30,  # Connection timeout in seconds
            },
            "creator": self._configure_connection,
        }

    def _configure_connection(self, _: any) -> Connection:
        """
        Configure SQLite connection with optimized settings and WAL mode.

        Args:
            _: Ignored connection record from SQLAlchemy pool

        Returns:
            Configured SQLite connection
        """
        conn = sqlite3.connect(self.IN_MEMORY_PATH if self._is_in_memory else str(self._get_db_path()))

        # Only use WAL mode for file-based databases
        if not self._is_in_memory:
            conn.execute("PRAGMA journal_mode=WAL")

        conn.execute("PRAGMA foreign_keys=ON")  # Enable FK constraint enforcement including cascades
        conn.execute("PRAGMA synchronous=NORMAL")  # Balance between safety and speed
        conn.execute("PRAGMA cache_size=-64000")  # 64MB cache size
        conn.execute("PRAGMA busy_timeout=60000")  # 60 second busy timeout

        return conn

    def _get_db_path(self) -> Path:
        """Construct the complete database file path."""
        if self._is_in_memory:
            return Path(self.IN_MEMORY_PATH)
        return (
            Path(self._db_config.sqlite.db_dir) / f"{self._db_config.sqlite.db_name}{self.DB_FILE_EXTENSION}"
        ).resolve()

    def build_db_url(self, use_system_db: bool = False) -> str:
        """Build the synchronous database URL."""
        if self._is_in_memory:
            return "sqlite:///:memory:"
        return f"sqlite:///{self._get_db_path()}"

    def build_async_db_url(self, use_system_db: bool = False) -> str:
        """Build the asynchronous database URL."""
        if self._is_in_memory:
            return "sqlite+aiosqlite:///:memory:"
        return f"sqlite+aiosqlite:///{self._get_db_path()}"

    def _create_async_engine(self) -> AsyncEngine:
        """Create asynchronous database engine."""
        engine = create_async_engine(
            self.build_async_db_url(),
            echo=self._db_config.echo,
            pool_pre_ping=True,
            connect_args={
                "check_same_thread": False,
                "timeout": 30,
            },
        )

        @event.listens_for(engine.sync_engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record) -> None:
            cursor = dbapi_connection.cursor()
            if not self._is_in_memory:
                cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA cache_size=-64000")
            cursor.execute("PRAGMA busy_timeout=60000")
            cursor.close()

        return engine

    async def _create_database(self) -> None:
        if not self._is_in_memory:
            db_path = self._get_db_path()
            db_path.parent.mkdir(parents=True, exist_ok=True)
            db_path.touch(exist_ok=True)
            log.debug(f"Ensured SQLite database exists at '{db_path.parent}'")
        else:
            log.debug("Using in-memory SQLite database")

    async def _drop_database(self) -> None:
        """Drop the SQLite database by deleting the file or clearing memory."""
        try:
            await self.close()

            if not self._is_in_memory:
                db_path = self._get_db_path()
                if db_path.exists():
                    db_path.unlink()
                    log.info(f"Deleted SQLite database file '{db_path}'")
            else:
                log.info("Dropped in-memory SQLite database")

        except Exception as e:
            log.error(f"Failed to drop SQLite database: {e!s}")
            raise

    async def acquire_control_lock(self) -> None:
        if self._is_in_memory:
            return  # No other process can open this database
        try:
            self._control_lock_file = _open_locked(Path(f"{self._get_db_path()}.control.lock"))
        except OSError as e:
            raise EosControlLockError(
                f"Another EOS orchestrator controls '{self._get_db_path()}'. Stop it before starting this one."
            ) from e

    async def check_control_lock(self) -> None:
        """The OS keeps the file lock for as long as this process holds the file open, so it cannot be lost."""

    async def release_control_lock(self) -> None:
        if self._control_lock_file is not None:
            self._control_lock_file.close()
            self._control_lock_file = None

    async def _database_exists(self) -> bool:
        if self._is_in_memory:
            return False  # Always create tables for in-memory database
        db_path = Path(self._db_config.sqlite.db_dir) / Path(self._db_config.sqlite.db_name + ".db")
        return db_path.exists()
