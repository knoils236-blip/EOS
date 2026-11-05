import asyncio
from typing import TYPE_CHECKING

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import delete as sa_delete, func, or_, select
from typer import Context

from eos.auth.entities.user_identity import UserIdentityModel
from eos.auth.entities.user_role import Role, UserRoleModel
from eos.auth.zitadel_client import ZitadelClient, generate_complexity_password
from eos.cli._common import cli_command, config_callback
from eos.cli.db_cli import create_db_interface

if TYPE_CHECKING:
    from eos.configuration.eos_config import EosConfig

auth_app = typer.Typer(help="管理账户和角色", no_args_is_help=True)
console = Console()

CLI_GRANTED_BY = "cli"

# Load the config once into ctx.obj for every auth subcommand.
auth_app.callback(invoke_without_command=True)(config_callback)


def _parse_role(role: str) -> Role:
    try:
        return Role(role.upper())
    except ValueError as e:
        valid = ", ".join(r.value.lower() for r in Role)
        typer.secho(f"角色 '{role}' 无效，可用角色：{valid}", fg="red", err=True)
        raise typer.Exit(1) from e


async def _insert_role(eos_config: "EosConfig", sub: str, role: Role, lab: str | None) -> bool:
    """Insert a role assignment for a user in this instance. Returns False if it already exists."""
    async with create_db_interface(eos_config).get_async_session() as db:
        existing = await db.execute(
            select(UserRoleModel).where(
                UserRoleModel.sub == sub, UserRoleModel.role == role, UserRoleModel.lab_name == lab
            )
        )
        if existing.scalar_one_or_none():
            return False
        db.add(UserRoleModel(sub=sub, role=role, lab_name=lab, granted_by=CLI_GRANTED_BY))
        return True


def _match_user_id(users: list[dict], identifier: str) -> str:
    """Find a user's Zitadel ID by username, email, or ID among already-fetched users. Exits if no match."""
    wanted = identifier.lower()
    for user in users:
        email = user.get("human", {}).get("email", {}).get("email", "")
        if identifier == user.get("userId") or wanted in (user.get("username", "").lower(), email.lower()):
            return user["userId"]
    typer.secho(f"未找到匹配 '{identifier}' 的用户，请运行 'eos auth list-users' 查看账户。", fg="red", err=True)
    raise typer.Exit(1)


async def _find_identities(eos_config: "EosConfig", identifier: str) -> list[UserIdentityModel]:
    """Find users EOS has already seen, matching a sub, email, or name."""
    wanted = identifier.strip().lower()
    async with create_db_interface(eos_config).get_async_session() as db:
        result = await db.execute(
            select(UserIdentityModel).where(
                or_(
                    UserIdentityModel.sub == identifier,
                    func.lower(UserIdentityModel.email) == wanted,
                    func.lower(UserIdentityModel.name) == wanted,
                )
            )
        )
        return list(result.scalars().all())


async def _identity_names(eos_config: "EosConfig") -> dict[str, str]:
    """Map each known sub to something readable."""
    async with create_db_interface(eos_config).get_async_session() as db:
        result = await db.execute(select(UserIdentityModel))
        return {identity.sub: identity.email or identity.name or "" for identity in result.scalars().all()}


def _resolve_sub(eos_config: "EosConfig", identifier: str) -> str:
    """Resolve a sub, email, or name to a user's sub. Prefers users EOS has already seen, so
    assigning roles needs no credential against the identity provider."""
    matches = asyncio.run(_find_identities(eos_config, identifier))
    if len(matches) == 1:
        return matches[0].sub
    if len(matches) > 1:
        listed = ", ".join(f"{m.sub} ({m.email or '无邮箱'})" for m in matches)
        typer.secho(f"'{identifier}' 匹配多个用户：{listed}。请改用 sub 指定用户。", fg="red", err=True)
        raise typer.Exit(1)
    if eos_config.auth.can_manage_accounts:
        with ZitadelClient(eos_config.auth) as client:
            return _match_user_id(client.list_users(), identifier)
    typer.secho(f"未找到匹配 '{identifier}' 的用户，该用户需要先登录一次 EOS。", fg="red", err=True)
    raise typer.Exit(1)


@auth_app.command("create-user")
@cli_command("创建用户失败")
def create_user(
    ctx: Context,
    username: str = typer.Argument(..., help="用户名（登录名）"),
    email: str = typer.Argument(..., help="邮箱地址"),
    given_name: str = typer.Option(None, "--given", help="名（默认为用户名）"),
    family_name: str = typer.Option(None, "--family", help="姓（默认为用户名）"),
    superuser: bool = typer.Option(False, "--superuser", help="同时授予此实例的超级用户角色"),
) -> None:
    """创建 Zitadel 账户并生成临时密码，首次登录时必须修改密码。"""
    eos_config: EosConfig = ctx.obj
    password = generate_complexity_password()
    with ZitadelClient(eos_config.auth) as client:
        user_id = client.create_user(username, email, given_name or username, family_name or username, password)
    if superuser:
        asyncio.run(_insert_role(eos_config, user_id, Role.SUPERUSER, None))

    typer.secho(f"已创建用户 '{username}'（ID：{user_id}）{'，已授予超级用户角色' if superuser else ''}", fg="green")
    typer.echo(f"临时密码（首次登录时必须修改）： {password}")


@auth_app.command("list-users")
@cli_command("列出用户失败")
def list_users(ctx: Context) -> None:
    """列出组织中的 Zitadel 账户。"""
    with ZitadelClient(ctx.obj.auth) as client:
        users = client.list_users()

    table = Table("ID", "用户名", "邮箱", "状态")
    for user in users:
        human = user.get("human", {})
        table.add_row(
            user.get("userId", ""),
            user.get("username", ""),
            human.get("email", {}).get("email", ""),
            user.get("state", "").removeprefix("USER_STATE_"),
        )
    console.print(table)


@auth_app.command("deactivate-user")
@cli_command("停用用户失败")
def deactivate_user(ctx: Context, user: str = typer.Argument(..., help="用户名、邮箱或 Zitadel 用户 ID")) -> None:
    """停用 Zitadel 账户。"""
    with ZitadelClient(ctx.obj.auth) as client:
        client.deactivate_user(_match_user_id(client.list_users(), user))
    typer.secho(f"已停用用户 {user}", fg="green")


@auth_app.command("reactivate-user")
@cli_command("重新启用用户失败")
def reactivate_user(ctx: Context, user: str = typer.Argument(..., help="用户名、邮箱或 Zitadel 用户 ID")) -> None:
    """重新启用已停用的 Zitadel 账户。"""
    with ZitadelClient(ctx.obj.auth) as client:
        client.reactivate_user(_match_user_id(client.list_users(), user))
    typer.secho(f"已重新启用用户 {user}", fg="green")


@auth_app.command("assign-role")
@cli_command("分配角色失败")
def assign_role(
    ctx: Context,
    user: str = typer.Argument(..., help="用户名、邮箱或 Zitadel 用户 ID"),
    role: str = typer.Argument(..., help="角色：superuser、lab_admin、editor、submitter 或 viewer"),
    lab: str = typer.Option(None, "--lab", help="实验室名称（lab_admin 角色必填）"),
) -> None:
    """为用户分配此 EOS 实例的本地角色。"""
    parsed_role = _parse_role(role)
    if (parsed_role == Role.LAB_ADMIN) != (lab is not None):
        typer.secho("lab_admin 角色必须指定 --lab，其他角色不允许使用此选项", fg="red", err=True)
        raise typer.Exit(1)

    sub = _resolve_sub(ctx.obj, user)
    if asyncio.run(_insert_role(ctx.obj, sub, parsed_role, lab)):
        typer.secho(f"已为 {user} 分配 {role}" + (f"，实验室为 '{lab}'" if lab else ""), fg="green")
    else:
        typer.secho("该角色已分配", fg="yellow")


@auth_app.command("revoke-role")
@cli_command("撤销角色失败")
def revoke_role(
    ctx: Context,
    user: str = typer.Argument(..., help="用户名、邮箱或 Zitadel 用户 ID"),
    role: str = typer.Argument(..., help="角色：superuser、lab_admin、editor、submitter 或 viewer"),
    lab: str = typer.Option(None, "--lab", help="实验室名称（用于 lab_admin）"),
) -> None:
    """撤销用户在此 EOS 实例中的本地角色。"""
    parsed_role = _parse_role(role)
    sub = _resolve_sub(ctx.obj, user)

    async def _revoke() -> None:
        async with create_db_interface(ctx.obj).get_async_session() as db:
            await db.execute(
                sa_delete(UserRoleModel).where(
                    UserRoleModel.sub == sub, UserRoleModel.role == parsed_role, UserRoleModel.lab_name == lab
                )
            )

    asyncio.run(_revoke())
    typer.secho(f"已撤销用户 {user} 的 {role} 角色", fg="green")


@auth_app.command("list-roles")
@cli_command("列出角色失败")
def list_roles(
    ctx: Context,
    user: str = typer.Argument(None, help="按用户名、邮箱或 Zitadel 用户 ID 筛选"),
) -> None:
    """列出此 EOS 实例中的本地角色分配。"""
    names = asyncio.run(_identity_names(ctx.obj))
    sub_filter = _resolve_sub(ctx.obj, user) if user else None

    async def _list() -> list[UserRoleModel]:
        async with create_db_interface(ctx.obj).get_async_session() as db:
            query = select(UserRoleModel)
            if sub_filter:
                query = query.where(UserRoleModel.sub == sub_filter)
            result = await db.execute(query)
            return list(result.scalars().all())

    roles = asyncio.run(_list())
    table = Table("ID", "用户", "角色", "实验室", "授权者", "创建时间")
    for role in roles:
        table.add_row(
            str(role.id),
            names.get(role.sub) or role.sub,
            role.role.value.lower(),
            role.lab_name or "-",
            role.granted_by,
            role.created_at.strftime("%Y-%m-%d %H:%M"),
        )
    console.print(table)
