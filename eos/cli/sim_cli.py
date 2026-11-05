"""CLI entry point for the EOS scheduling simulator."""

import json
import logging
import sys
from dataclasses import asdict
from typing import Annotated

import typer

from eos.scheduling.simulation import compute_sim_stats, run_simulation


def simulate(
    config: Annotated[str, typer.Argument(help="仿真 YAML 配置文件路径")],
    user_dir: Annotated[str, typer.Option("--user-dir", "-u", help="包含 EOS 软件包的用户目录")] = "./user",
    verbose: Annotated[bool, typer.Option("--verbose", "-v", help="显示调度决策")] = False,
    jitter: Annotated[float, typer.Option("--jitter", help="耗时抖动比例（例如 0.1 表示 +/-10%%）")] = 0.0,
    seed: Annotated[int | None, typer.Option("--seed", help="用于复现结果的随机种子")] = None,
    scheduler: Annotated[
        str, typer.Option("--scheduler", "-s", help="调度器类型：greedy、heuristic 或 cpsat")
    ] = "greedy",
    output_json: Annotated[bool, typer.Option("--json", help="以 JSON 输出结果（不显示文本输出）")] = False,
) -> None:
    """运行 EOS 调度器的离散事件仿真。"""
    if output_json:
        logging.getLogger("rich").setLevel(logging.CRITICAL)

    timeline, deadlock, scheduler_ms = run_simulation(
        config_path=config,
        user_dir=user_dir,
        verbose=verbose if not output_json else False,
        jitter=jitter,
        seed=seed,
        scheduler_type=scheduler,
        quiet=output_json,
    )

    if not output_json:
        return

    starts = [e for e in timeline if e.event_type == "START"]
    completions = [e for e in timeline if e.event_type == "DONE"]
    stats = compute_sim_stats(starts, completions, scheduler)
    stats["scheduler_overhead_ms"] = scheduler_ms
    task_records = [
        {
            "protocol_run": ev.protocol_run_name,
            "task": ev.task_name,
            "start": ev.time,
            "duration": ev.duration,
            "end": ev.time + ev.duration,
            "devices": [{"slot": slot, "lab": d.lab_name, "name": d.name} for slot, d in ev.devices.items()],
            "resources": dict(ev.resources.items()),
        }
        for ev in starts
    ]
    payload: dict = {"timeline": task_records, "stats": stats}
    if deadlock is not None:
        payload["deadlock"] = asdict(deadlock)
    sys.stdout.write(json.dumps(payload))
