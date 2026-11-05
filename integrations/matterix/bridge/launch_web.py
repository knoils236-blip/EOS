"""Launch the original ChemOS web app and the bridge; stop only owned processes."""

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

from .client import MatterixClient
from .common import ROOT

URL = "http://127.0.0.1:8501/Matterix"
WEB_DIR = ROOT.parent / "demo/streamlit"


def web_command():
    return [
        sys.executable,
        "-B",
        "-m",
        "streamlit",
        "run",
        "Hello.py",
        "--server.address",
        "127.0.0.1",
        "--server.port",
        "8501",
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]


def listening(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


def bridge_ready():
    if not listening(65200):
        return False
    with MatterixClient() as client:
        client.get("launcher-health-check")
    return True


def web_ready():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8501/_stcore/health", timeout=0.5) as response:
            return response.status == 200
    except OSError:
        return False


def stop_owned(processes):
    for process, _, _ in reversed(processes):
        if process.poll() is None:
            process.terminate()
    for process, _, _ in reversed(processes):
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-browser", action="store_true", help="Prepare services without opening a browser")
    args = parser.parse_args()
    owned = []
    logs = ROOT / "runs" / ("web-session-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + f"-{os.getpid()}")
    logs.mkdir(parents=True)
    environment = os.environ.copy()
    environment.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    def start(command, name, cwd):
        with (logs / f"{name}.log").open("wb") as stream:
            process = subprocess.Popen(command, cwd=cwd, env=environment, stdout=stream,
                                       stderr=subprocess.STDOUT, start_new_session=True)
        owned.append((process, name, command))
        return process

    def wait_ready(probe, process, name):
        deadline = time.monotonic() + 150
        while not stopped and time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"{name} 启动失败，请查看 {logs}")
            if probe():
                return
            time.sleep(0.2)
        raise RuntimeError(f"{name} 尚未就绪或启动已取消，请查看 {logs}")

    try:
        if listening(65200):
            if not bridge_ready():
                raise RuntimeError("65200 端口已占用，但不是可用的 Matterix 服务")
            print("复用已有 Matterix 服务。", flush=True)
        else:
            process = start([sys.executable, "-B", "-m", "bridge.server"], "bridge", ROOT)
            wait_ready(bridge_ready, process, "Matterix 服务")
        if listening(8501):
            if not web_ready():
                raise RuntimeError("8501 端口已占用，但网页健康检查未通过")
            print("复用已有 ChemOS 网页。", flush=True)
        else:
            process = start(web_command(), "web", WEB_DIR)
            wait_ready(web_ready, process, "ChemOS 网页")
        print(
            f"Matterix 页面：{URL}\n"
            "Matterix 与网页已独立启动；论文 demo 仪器服务未启动。\n"
            f"本次启动日志：{logs}\n按 Ctrl+C 只停止本次启动的服务。",
            flush=True,
        )
        if not args.no_browser:
            webbrowser.open(URL)
        reported = set()
        while not stopped:
            for process, name, _ in owned:
                if process.poll() is not None and name not in reported:
                    reported.add(name)
                    if name == "web":
                        raise RuntimeError("ChemOS 网页进程已退出")
                    print("Matterix 服务已退出；网页保留，可查看离线历史。", flush=True)
            time.sleep(0.2)
    finally:
        stop_owned(owned)


if __name__ == "__main__":
    main()
