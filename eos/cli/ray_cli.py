import typer

from eos.cli._common import cli_command, run

ray_app = typer.Typer(help="管理 EOS Ray 集群", no_args_is_help=True)


@ray_app.command()
@cli_command("启动主节点失败")
def head(
    dashboard: bool = typer.Option(False, "--dashboard", help="启用 Ray 控制面板"),
    additional_params: list[str] = typer.Argument(None, help="传给 'ray start' 的附加参数"),
) -> None:
    """启动 Ray 主节点。"""
    cmd = ["ray", "start", "--head", "--resources", '{"eos": 1000}', "--disable-usage-stats"]
    if not dashboard:
        cmd.append("--include-dashboard=false")
    if additional_params:
        cmd.extend(additional_params)
    run(cmd)
    typer.echo("Ray 主节点已启动。")


@ray_app.command()
@cli_command("启动工作节点失败")
def worker(
    address: str = typer.Option(..., "--address", "-a", help="要连接的主节点地址"),
    additional_params: list[str] = typer.Argument(None, help="传给 'ray start' 的附加参数"),
) -> None:
    """启动连接到指定主节点的 Ray 工作节点。"""
    cmd = ["ray", "start", "--address", address, "--disable-usage-stats"]
    if additional_params:
        cmd.extend(additional_params)
    run(cmd)
    typer.echo(f"已启动连接到 {address} 的 Ray 工作节点。")


@ray_app.command()
@cli_command("停止 Ray 失败")
def stop() -> None:
    """停止此节点上的 Ray。"""
    run(["ray", "stop"])


@ray_app.command()
@cli_command("获取状态失败")
def status() -> None:
    """显示 Ray 集群状态。"""
    run(["ray", "status"])
