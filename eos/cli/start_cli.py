import typer

from eos.cli.orchestrator_cli import start_orchestrator
from eos.cli.services_cli import services_up
from eos.cli.web_cli import start_web_ui

start_app = typer.Typer(
    help="启动 EOS 组件",
    invoke_without_command=True,
)

# Bare `eos start` runs the orchestrator; subcommands start the other components.
start_app.callback(invoke_without_command=True)(start_orchestrator)
start_app.command(name="ui", help="启动 EOS Web 界面")(start_web_ui)
start_app.command(name="services", help="启动基础服务（'eos services up' 的别名）")(services_up)
