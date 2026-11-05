import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from typer import Context

from eos.cli._common import cli_command, config_callback
from eos.configuration.eos_config import EosConfig, DatabaseType
from eos.utils.di.di_container import get_di_container
from eos.database.abstract_sql_db_interface import AbstractSqlDbInterface
from eos.database.postgresql_db_interface import PostgresqlDbInterface
from eos.database.sqlite_db_interface import SqliteDbInterface
from eos.database.alembic_commands import (
    alembic_upgrade,
    alembic_downgrade,
    alembic_revision,
    alembic_stamp,
)

if TYPE_CHECKING:
    from alembic.config import Config

db_app = typer.Typer(help="管理 EOS 数据库", no_args_is_help=True)

# Load the config once into ctx.obj for every db subcommand.
db_app.callback(invoke_without_command=True)(config_callback)

ConfirmOption = typer.Option(False, "--force", "-f", "--yes", help="跳过确认提示")


def create_db_interface(eos_config: EosConfig) -> AbstractSqlDbInterface:
    """Build the configured SQL database interface."""
    return (
        PostgresqlDbInterface(eos_config.db)
        if eos_config.db.type == DatabaseType.POSTGRESQL
        else SqliteDbInterface(eos_config.db)
    )


def initialize_database_core(eos_config: EosConfig) -> None:
    """Create the database and tables and run migrations. Shared by 'db init' and 'eos setup'."""
    di = get_di_container()
    db_interface = create_db_interface(eos_config)
    di.register(AbstractSqlDbInterface, db_interface)
    asyncio.run(db_interface.initialize_database())
    alembic_upgrade("head")


def setup_alembic(eos_config: EosConfig) -> "Config":
    """Register the database interface for Alembic and return its config."""
    from alembic.config import Config

    di = get_di_container()
    di.register(AbstractSqlDbInterface, create_db_interface(eos_config))

    migrations_path = Path(__file__).parent.parent / "database" / "_migrations" / "alembic.ini"
    if not migrations_path.exists():
        raise FileNotFoundError(f"未找到 Alembic 配置： {migrations_path}")

    return Config(str(migrations_path))


@db_app.command("init")
@cli_command("初始化数据库失败")
def initialize_database(ctx: Context) -> None:
    """初始化数据库并创建所有表。"""
    initialize_database_core(ctx.obj)
    typer.secho("数据库初始化成功", fg="green")


@db_app.command()
@cli_command("创建迁移失败")
def migrate(
    ctx: Context,
    message: str = typer.Argument(..., help="迁移说明"),
    autogenerate: bool = typer.Option(True, "--autogenerate", "-a", help="自动检测数据库结构变更"),
) -> None:
    """创建新的数据库迁移。"""
    setup_alembic(ctx.obj)
    alembic_upgrade("head")
    alembic_revision(message=message, autogenerate=autogenerate)
    typer.secho(f"已创建迁移： {message}", fg="green")


@db_app.command()
@cli_command("升级数据库失败")
def upgrade(
    ctx: Context,
    revision: str = typer.Option("head", "--revision", "-r", help="目标版本（默认：head）"),
) -> None:
    """将数据库升级至指定版本。"""
    setup_alembic(ctx.obj)
    alembic_upgrade(revision)
    typer.secho(f"已成功升级至： {revision}", fg="green")


@db_app.command()
@cli_command("降级数据库失败")
def downgrade(
    ctx: Context,
    revision: str = typer.Option("-1", "--revision", "-r", help="目标版本（默认：-1）"),
    force: bool = ConfirmOption,
) -> None:
    """将数据库降级至指定版本。"""
    if not force and not typer.confirm(f"是否降级至 {revision}？"):
        raise typer.Exit()
    setup_alembic(ctx.obj)
    alembic_downgrade(revision)
    typer.secho(f"已成功降级至： {revision}", fg="green")


@db_app.command()
@cli_command("显示历史记录失败")
def history(ctx: Context) -> None:
    """显示迁移历史。"""
    from alembic import command

    command.history(setup_alembic(ctx.obj))


@db_app.command()
@cli_command("显示当前版本失败")
def current(ctx: Context) -> None:
    """显示当前版本。"""
    from alembic import command

    command.current(setup_alembic(ctx.obj))


@db_app.command()
@cli_command("清空数据库失败")
def clear(ctx: Context, force: bool = ConfirmOption) -> None:
    """清空数据库表中的所有数据并保留表结构。"""
    import eos.database.models  # noqa: F401  (register models before clearing)

    if not force and not typer.confirm("警告：此操作将删除全部数据，仅保留表结构。是否继续？"):
        raise typer.Exit()
    asyncio.run(create_db_interface(ctx.obj).clear_db())
    typer.secho("已清空数据库表中的所有数据", fg="green")


@db_app.command()
@cli_command("标记数据库版本失败")
def stamp(
    ctx: Context,
    revision: str = typer.Argument(..., help="要标记的目标版本（例如 'head' 或版本哈希）"),
    force: bool = ConfirmOption,
) -> None:
    """标记数据库版本，不执行迁移。"""
    if not force and not typer.confirm(f"是否将数据库版本标记为 {revision}？"):
        raise typer.Exit()
    setup_alembic(ctx.obj)
    alembic_stamp(revision)
    typer.secho(f"已成功标记为： {revision}", fg="green")


@db_app.command("check")
@cli_command("检查数据库连接失败")
def check_connection(ctx: Context) -> None:
    """测试数据库连接。"""
    if asyncio.run(create_db_interface(ctx.obj).check_connection()):
        typer.secho("数据库连接正常", fg="green")
    else:
        typer.secho("数据库连接失败", fg="red", err=True)
        raise typer.Exit(1)
