import os
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from eos.utils.net import is_port_in_use

WEB_UI_DIR = Path(__file__).resolve().parents[2] / "web_ui"
WEB_UI_ENV = WEB_UI_DIR / ".env"
IS_WINDOWS = os.name == "nt"
DEFAULT_UI_PORT = 3000


def _run_npm(*args: str, cwd: Path, env: dict[str, str] | None = None) -> int:
    return subprocess.call(["npm", *args], cwd=cwd, env=env, shell=IS_WINDOWS)  # noqa: S607


def _node_ca_from_env_file() -> str | None:
    """Read NODE_EXTRA_CA_CERTS from web_ui/.env.

    Node configures its TLS trust store at process startup, before Next.js loads .env, so the value must
    be in the process environment from the start. A shell export wins; otherwise we inject the .env value.
    """
    if "NODE_EXTRA_CA_CERTS" in os.environ or not WEB_UI_ENV.exists():
        return None
    for line in WEB_UI_ENV.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "NODE_EXTRA_CA_CERTS":
            return value.strip().strip("\"'") or None
    return None


def start_web_ui(
    dev: Annotated[bool, typer.Option("--dev", "-d", help="启动开发服务器而非生产服务器")] = False,
    build: Annotated[bool, typer.Option("--build", "-b", help="即使已有构建也强制重新构建")] = False,
    host: Annotated[str, typer.Option("--host", "-H", help="绑定地址（默认：127.0.0.1）")] = "127.0.0.1",
) -> None:
    """启动 EOS Web 界面。"""
    if not WEB_UI_DIR.is_dir():
        typer.echo(f"错误：未找到 Web 界面目录： {WEB_UI_DIR}", err=True)
        raise typer.Exit(1)

    if not (WEB_UI_DIR / "node_modules").is_dir():
        typer.echo("错误：未找到 node_modules，请先在 web_ui/ 目录运行 'npm install'。", err=True)
        raise typer.Exit(1)

    port = int(os.environ.get("PORT", DEFAULT_UI_PORT))
    if is_port_in_use(host, port):
        typer.echo(
            f"错误：端口 {host}:{port} 已被占用，Web 界面可能已在运行。",
            err=True,
        )
        raise typer.Exit(1)

    env = {**os.environ, "HOST": host}
    node_ca = _node_ca_from_env_file()
    if node_ca:
        env["NODE_EXTRA_CA_CERTS"] = node_ca

    try:
        if dev:
            typer.echo("正在以开发模式启动 Web 界面…")
            sys.exit(_run_npm("run", "dev", cwd=WEB_UI_DIR, env=env))
        else:
            needs_build = build or not (WEB_UI_DIR / ".next").is_dir()
            if needs_build:
                typer.echo("正在构建 Web 界面…")
                result = _run_npm("run", "build", cwd=WEB_UI_DIR)
                if result != 0:
                    typer.echo("错误：构建失败。", err=True)
                    raise typer.Exit(result)

            typer.echo("正在启动 Web 界面…")
            sys.exit(_run_npm("start", cwd=WEB_UI_DIR, env=env))
    except FileNotFoundError:
        typer.echo("错误：在 PATH 中未找到 npm。", err=True)
        raise typer.Exit(1) from None
