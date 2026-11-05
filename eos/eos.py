#!/usr/bin/env python3

import importlib
from typing import NamedTuple

import click
import typer
from typer.core import TyperGroup


class _LazyCommand(NamedTuple):
    module: str
    attribute: str
    help: str
    hidden: bool = False


# Sub-commands are imported only when invoked, so help and unrelated commands start fast.
_COMMANDS = {
    "setup": _LazyCommand("eos.cli.setup_cli", "run_setup", "交互式配置 EOS"),
    "sim": _LazyCommand("eos.cli.sim_cli", "simulate", "运行调度仿真"),
    "ui": _LazyCommand("eos.cli.web_cli", "start_web_ui", "启动 EOS Web 界面", hidden=True),
    "update": _LazyCommand("eos.cli.update_cli", "update", "更新 EOS"),
    "start": _LazyCommand("eos.cli.start_cli", "start_app", "启动 EOS 组件"),
    "services": _LazyCommand("eos.cli.services_cli", "services_app", "管理基础服务"),
    "auth": _LazyCommand("eos.cli.auth_cli", "auth_app", "管理账户和角色"),
    "db": _LazyCommand("eos.cli.db_cli", "db_app", "管理 EOS 数据库"),
    "pkg": _LazyCommand("eos.cli.pkg_cli", "pkg_app", "管理 EOS 软件包"),
    "ray": _LazyCommand("eos.cli.ray_cli", "ray_app", "管理 EOS Ray 集群"),
}


def _load_command(name: str, spec: _LazyCommand) -> click.Command:
    """Build the sub-command exactly as eager registration on the root app would."""
    target = getattr(importlib.import_module(spec.module), spec.attribute)
    parent = typer.Typer()
    if isinstance(target, typer.Typer):
        parent.add_typer(target, name=name)
    else:
        parent.command(name=name, help=spec.help, hidden=spec.hidden)(target)
    return typer.main.get_group(parent).commands[name]


class _LazyGroup(TyperGroup):
    """Resolves sub-commands on first use. Help listings use the static help text instead."""

    _listing = False

    def list_commands(self, ctx: click.Context) -> list[str]:
        return list(_COMMANDS)

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        spec = _COMMANDS.get(cmd_name)
        if spec is None:
            return None
        if self._listing:
            return click.Command(cmd_name, help=spec.help, hidden=spec.hidden)
        if cmd_name not in self.commands:
            self.add_command(_load_command(cmd_name, spec))
        return self.commands[cmd_name]

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        self._listing = True
        try:
            super().format_help(ctx, formatter)
        finally:
            self._listing = False


eos_app = typer.Typer(cls=_LazyGroup, pretty_exceptions_show_locals=False, no_args_is_help=True)


@eos_app.callback()
def _main() -> None:
    pass


if __name__ == "__main__":
    eos_app()
