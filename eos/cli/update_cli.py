import subprocess
from typing import Annotated

import typer

from eos.cli._common import DEFAULT_CONFIG_PATH, load_config
from eos.cli.db_cli import setup_alembic
from eos.database.alembic_commands import alembic_upgrade

REQUIRED_BRANCH = "master"


def _run(cmd: list[str], *, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=capture, text=True)


def _ensure_git_repo() -> None:
    try:
        _run(["git", "rev-parse", "--git-dir"], capture=True)
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        typer.echo("当前目录不是 Git 仓库，或尚未安装 git。", err=True)
        raise typer.Exit(1) from e


def _fetch_master_and_count_behind() -> int:
    """Fetch origin/master and return how many commits local master is behind."""
    typer.echo("正在检查更新…")
    try:
        _run(["git", "fetch", "origin", REQUIRED_BRANCH, "--quiet"])
    except subprocess.CalledProcessError as e:
        typer.echo(f"git fetch 失败： {e}", err=True)
        raise typer.Exit(1) from e
    try:
        behind = _run(
            ["git", "rev-list", "--count", f"{REQUIRED_BRANCH}..origin/{REQUIRED_BRANCH}"],
            capture=True,
        ).stdout.strip()
        return int(behind)
    except subprocess.CalledProcessError as e:
        typer.echo(f"无法比较本地 '{REQUIRED_BRANCH}' 与 origin： {e}", err=True)
        raise typer.Exit(1) from e


def _check_pull_preconditions() -> None:
    """Abort if the repo is not on master, is dirty, or has no upstream."""
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture=True).stdout.strip()
    if branch != REQUIRED_BRANCH:
        typer.echo(
            f"当前分支为 '{branch}'，`eos update` 仅在 '{REQUIRED_BRANCH}' 分支执行拉取。"
            f"请切换到 master 或使用 --skip-pull。",
            err=True,
        )
        raise typer.Exit(1)

    dirty = _run(["git", "status", "--porcelain"], capture=True).stdout.strip()
    if dirty:
        typer.echo(
            "工作区存在未提交的更改，请先提交或暂存，或使用 --skip-pull。",
            err=True,
        )
        raise typer.Exit(1)

    try:
        _run(["git", "rev-parse", "--abbrev-ref", "@{upstream}"], capture=True)
    except subprocess.CalledProcessError as e:
        typer.echo(f"分支 '{branch}' 未配置上游跟踪。", err=True)
        raise typer.Exit(1) from e


def _do_pull() -> None:
    typer.echo("正在拉取最新更改（git pull --ff-only）…")
    try:
        _run(["git", "pull", "--ff-only"])
    except subprocess.CalledProcessError as e:
        typer.echo("git pull 失败，当前分支可能已与上游分叉。", err=True)
        raise typer.Exit(1) from e


def _do_sync() -> None:
    typer.echo("正在同步依赖（uv sync --inexact）…")
    try:
        _run(["uv", "sync", "--inexact"])
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        typer.echo(f"uv sync 失败： {e}", err=True)
        raise typer.Exit(1) from e


def _do_migrate(config_path: str) -> None:
    typer.echo("正在执行数据库迁移（alembic upgrade head）…")
    try:
        eos_config = load_config(config_path)
        setup_alembic(eos_config)
        alembic_upgrade("head")
    except Exception as e:
        typer.echo(f"数据库迁移失败： {e}", err=True)
        raise typer.Exit(1) from e


def update(
    skip_pull: Annotated[bool, typer.Option("--skip-pull", help="跳过 git pull")] = False,
    skip_sync: Annotated[bool, typer.Option("--skip-sync", help="跳过 uv sync")] = False,
    skip_migrate: Annotated[bool, typer.Option("--skip-migrate", help="跳过数据库迁移")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="跳过确认提示")] = False,
    config: Annotated[str, typer.Option("--config", "-c", help="EOS YAML 配置文件路径")] = DEFAULT_CONFIG_PATH,
) -> None:
    """更新 EOS：拉取 master、同步依赖并执行数据库迁移。

    user/ 中被 Git 忽略的用户软件包代码保持不变。
    保留通过 eos pkg install 安装的依赖（uv sync --inexact）。
    任一步骤失败后，将停止后续步骤。
    """
    if not skip_pull:
        _ensure_git_repo()
        behind = _fetch_master_and_count_behind()
        if behind == 0:
            typer.secho("EOS 已是最新版本。", fg="green")
            raise typer.Exit()
        typer.echo(f"origin/{REQUIRED_BRANCH} 有 {behind} 个新提交。")
        _check_pull_preconditions()

    steps = []
    if not skip_pull:
        steps.append(f"git pull --ff-only on '{REQUIRED_BRANCH}'")
    if not skip_sync:
        steps.append("uv sync --inexact")
    if not skip_migrate:
        steps.append("alembic upgrade head")

    if not steps:
        typer.echo("所有步骤均已跳过，无需执行操作。")
        raise typer.Exit()

    typer.echo("执行计划：")
    for s in steps:
        typer.echo(f"  - {s}")

    if not yes and not typer.confirm("是否继续？", default=True):
        raise typer.Exit()

    if not skip_pull:
        _do_pull()
    if not skip_sync:
        _do_sync()
    if not skip_migrate:
        _do_migrate(config)

    typer.secho("EOS 更新完成。", fg="green")
