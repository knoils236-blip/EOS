#!/usr/bin/env bash
# EOS 演示。先在另一个终端运行 ./start_eos.sh，再用本脚本：
#   demo/run.sh setup          加载演示用的实验室和协议（可重复运行）
#   demo/run.sh mult [轮数]     提交乘法优化实验计划（默认 15 轮），等待完成并打印最优解
#   demo/run.sh matterix       提交 Matterix 抓取烧杯协议运行，等待完成并打印验收结果
set -euo pipefail

API="${EOS_API:-http://127.0.0.1:8070/api}"
UI="${EOS_UI:-http://127.0.0.1:3000}"

post() { curl -fsS -X POST "$API/$1" -H 'Content-Type: application/json' -d "$2"; echo; }
field() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

wait_for() {  # path, then prints the final JSON
  local state
  for _ in $(seq 300); do
    state="$(curl -fsS "$API/$1")"
    case "$(field "d['status']" <<<"$state")" in
      COMPLETED | FAILED | CANCELLED) echo "$state"; return ;;
    esac
    sleep 2
  done
  echo "等待超时，请在 Web 界面查看。" >&2
  return 1
}

curl -fsS -o /dev/null "$API/labs" || { echo "连不上 EOS（$API），请先运行 ./start_eos.sh" >&2; exit 1; }

case "${1:-}" in
  setup)
    post labs/load '{"lab_types": ["multiplication_lab", "matterix_lab"]}'
    post protocols/load '{"protocol_types": ["optimize_multiplication", "matterix_pick_and_return"]}'
    ;;
  mult)
    name="demo_mult_$(date +%H%M%S)"
    post campaigns/ "{\"name\": \"$name\", \"protocol\": \"optimize_multiplication\", \"owner\": \"demo\",
      \"max_protocol_runs\": ${2:-15}, \"max_concurrent_protocol_runs\": 2, \"optimize\": true}"
    echo "已提交实验计划 $name，可在 $UI/campaigns 查看。等待完成…"
    wait_for "campaigns/$name" | field "f\"状态 {d['status']}，完成 {d['protocol_runs_completed']} 轮，最优解 {d['pareto_solutions']}\""
    ;;
  matterix)
    name="demo_matterix_$(date +%H%M%S)"
    post protocols/ "{\"name\": \"$name\", \"type\": \"matterix_pick_and_return\", \"owner\": \"demo\"}"
    echo "已提交协议运行 $name，可在 $UI/protocol-runs 查看。等待完成…"
    wait_for "protocols/$name" | field "'状态 ' + d['status']"
    for task in pickup return_beaker; do
      curl -fsS "$API/tasks/$name/$task" |
        field "f\"  {d['name']}: {d['status']}，验收通过={(d.get('output_parameters') or {}).get('passed')}\""
    done
    ;;
  *)
    sed -n '2,5p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
    ;;
esac
