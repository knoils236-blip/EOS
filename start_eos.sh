#!/usr/bin/env bash
# Start the whole EOS stack with one command:
#   infrastructure services (Docker) -> Matterix SiLA 2 bridge -> EOS web UI -> EOS orchestrator (foreground).
# Ctrl+C stops the orchestrator and every process this script started; services already running are reused.
#
# Usage: ./start_eos.sh [--no-matterix] [--no-ui] [--rebuild-ui]
# Environment overrides:
#   EOS_LABS, EOS_PROTOCOLS       comma-separated lists (default: Matterix station and mixture Mock;
#                                --no-matterix: mixture Mock only)
#   MATTERIX_BRIDGE_PYTHON        interpreter of the ChemOS conda environment (sila2 0.10.1)
#   MATTERIX_BRIDGE_PORT          SiLA 2 port of the bridge (default 65200)
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BRIDGE_DIR="$ROOT/integrations/matterix"
BRIDGE_PYTHON="${MATTERIX_BRIDGE_PYTHON:-/home/ry/anaconda3/envs/chemos2/bin/python}"
BRIDGE_PORT="${MATTERIX_BRIDGE_PORT:-65200}"
UI_PORT="${PORT:-3000}"
LOG_DIR="$ROOT/.state/logs"

with_matterix=1 with_ui=1 ui_args=()
for arg in "$@"; do
  case "$arg" in
    --no-matterix) with_matterix=0 ;;
    --no-ui) with_ui=0 ;;
    --rebuild-ui) ui_args+=(--build) ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数：$arg（见 --help）" >&2; exit 2 ;;
  esac
done

if ((with_matterix)); then
  LABS="${EOS_LABS:-matterix_pipetting_lab,mixture_mock_lab}"
  PROTOCOLS="${EOS_PROTOCOLS:-matterix_full_station,mixture_mock}"
else
  LABS="${EOS_LABS:-mixture_mock_lab}"
  PROTOCOLS="${EOS_PROTOCOLS:-mixture_mock}"
fi
# These comma-separated launcher options are passed as CLI arguments, not Pydantic JSON settings.
unset EOS_LABS EOS_PROTOCOLS
# GNOME exports ALL_PROXY as socks://, a scheme httpx rejects, which breaks every AI request (drafts, decisions);
# HTTP_PROXY/HTTPS_PROXY still route those requests
for name in ALL_PROXY all_proxy; do
  [[ "${!name:-}" == socks://* ]] && unset "$name"
done

port_open() { (echo >"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

wait_for_port() {  # port, seconds, pid of the process that should open it, name
  for ((i = 0; i < $2; i++)); do
    port_open "$1" && return 0
    kill -0 "$3" 2>/dev/null || { echo "$4 启动失败，日志：$LOG_DIR" >&2; return 1; }
    sleep 1
  done
  echo "$4 在 $2 秒内没有监听端口 $1，日志：$LOG_DIR" >&2
  return 1
}

started_pids=()
cleanup() {
  for pid in "${started_pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT

cd "$ROOT"
[[ -x .venv/bin/eos ]] || { echo "缺少 .venv，请先运行：uv sync --all-groups" >&2; exit 1; }
# shellcheck disable=SC1091
source .venv/bin/activate
mkdir -p "$LOG_DIR"

# A second orchestrator would share the Ray namespace and database with the running one
running="$(ps -eo pid=,args= | awk '/bin\/eos start( |$)/ && !/eos start (ui|services)/ {print $1}')"
if [[ -n "$running" ]]; then
  echo "已有 EOS 编排器在运行（pid $running）。请先停止它（Ctrl+C 或 kill $running），再运行本脚本。" >&2
  exit 1
fi

echo "==> 基础服务（PostgreSQL、SeaweedFS）"
eos services up

if ((with_matterix)); then
  echo "==> Matterix SiLA 2 bridge（端口 $BRIDGE_PORT）"
  if port_open "$BRIDGE_PORT"; then
    # gRPC servers share ports (SO_REUSEPORT), so never start a second bridge on an occupied port
    pid="$(ss -ltnpH "sport = :$BRIDGE_PORT" 2>/dev/null | grep -o 'pid=[0-9]*' | head -1 | cut -d= -f2)"
    echo "端口 $BRIDGE_PORT 已有服务（pid ${pid:-?}，目录 $(readlink "/proc/${pid:-0}/cwd" 2>/dev/null || echo 未知)），直接复用。"
  else
    [[ -x "$BRIDGE_PYTHON" ]] || {
      echo "找不到 bridge 的 Python：$BRIDGE_PYTHON。按 integrations/matterix/README.md 创建 conda 环境，或设置 MATTERIX_BRIDGE_PYTHON。" >&2
      exit 1
    }
    # --no-db: EOS keeps the records; the bridge does not mirror them into the ChemOS database
    (cd "$BRIDGE_DIR" && PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
      exec "$BRIDGE_PYTHON" -B -m bridge.server --no-db --port "$BRIDGE_PORT") >"$LOG_DIR/matterix-bridge.log" 2>&1 &
    started_pids+=($!)
    wait_for_port "$BRIDGE_PORT" 60 "$!" "Matterix bridge"
  fi
fi

if ((with_ui)); then
  echo "==> Web 界面（端口 $UI_PORT）"
  if port_open "$UI_PORT"; then
    echo "端口 $UI_PORT 已有服务，直接复用。若代码有更新，请先停止它再加 --rebuild-ui 运行。"
  else
    eos start ui "${ui_args[@]}" >"$LOG_DIR/web-ui.log" 2>&1 &
    started_pids+=($!)
    # A first build can take several minutes
    wait_for_port "$UI_PORT" 600 "$!" "Web 界面"
    echo "Web 界面：http://127.0.0.1:$UI_PORT"
  fi
fi

echo "==> EOS 编排器（实验室：${LABS:-无}；协议：${PROTOCOLS:-无}）。按 Ctrl+C 停止全部。"
orchestrator_args=()
[[ -n "$LABS" ]] && orchestrator_args+=(--labs "$LABS")
[[ -n "$PROTOCOLS" ]] && orchestrator_args+=(--protocols "$PROTOCOLS")
eos start "${orchestrator_args[@]}"
