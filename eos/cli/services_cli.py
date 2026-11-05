from pathlib import Path
from typing import Annotated

import typer

from eos.cli._common import run

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = REPO_ROOT / ".env"

services_app = typer.Typer(
    help="管理基础服务",
    no_args_is_help=True,
)


def _auth_enabled() -> bool:
    """Whether the Zitadel auth services are active, per EOS_AUTH_ENABLED in .env."""
    if not ENV_PATH.exists():
        return False
    for line in ENV_PATH.read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key == "EOS_AUTH_ENABLED":
            return value.strip().lower() == "true"
    return False


def _compose(*args: str, auth: bool | None = None) -> None:
    """Run `docker compose` from the repo root, adding the auth profile when enabled."""
    include_auth = _auth_enabled() if auth is None else auth
    profile = ["--profile", "auth"] if include_auth else []
    try:
        result = run(["docker", "compose", *profile, *args], cwd=REPO_ROOT, check=False)
    except FileNotFoundError:
        typer.echo("错误：在 PATH 中未找到 docker。", err=True)
        raise typer.Exit(1) from None
    if result.returncode != 0:
        raise typer.Exit(result.returncode)


@services_app.command(name="up")
def services_up(
    auth: Annotated[
        bool | None,
        typer.Option("--auth/--no-auth", help="包含 Zitadel 身份验证服务（默认取自 EOS_AUTH_ENABLED）"),
    ] = None,
) -> None:
    """在后台启动基础服务。"""
    _compose("up", "-d", auth=auth)


@services_app.command(name="down")
def services_down(
    volumes: Annotated[bool, typer.Option("--volumes", "-v", help="同时移除数据卷（永久删除数据）")] = False,
) -> None:
    """停止并移除基础服务。"""
    _compose("down", *(["--volumes"] if volumes else []))


@services_app.command(name="restart")
def services_restart(
    service: Annotated[str | None, typer.Argument(help="要重启的服务（默认：全部）")] = None,
) -> None:
    """重启基础服务。"""
    _compose("restart", *([service] if service else []))


@services_app.command(name="logs")
def services_logs(
    service: Annotated[str | None, typer.Argument(help="要查看日志的服务（默认：全部）")] = None,
    follow: Annotated[bool, typer.Option("--follow/--no-follow", "-f", help="持续显示日志输出")] = True,
) -> None:
    """显示基础服务日志。"""
    _compose("logs", *(["-f"] if follow else []), *([service] if service else []))


@services_app.command(name="ps")
def services_ps() -> None:
    """列出基础服务及其状态。"""
    _compose("ps")
