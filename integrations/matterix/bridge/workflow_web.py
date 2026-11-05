"""Views for launching and inspecting the original ChemOS workflow."""

import socket

import pandas as pd
import streamlit as st

from .common import TERMINAL
from .web import LABELS, schedule_refresh
from .workflow import STAGES, get_state, history, new_id, submit

STEP_LABELS = {**LABELS, "pending": "等待执行", "skipped": "未执行"}


def render_details(state):
    identifier = state["request_id"]
    st.write(f"工作流编号：{identifier}")
    st.write(f"ChemOS 任务编号：{state.get('job_id', '未创建')} · 状态：{STEP_LABELS.get(state['status'], state['status'])}")
    st.dataframe(pd.DataFrame([{"步骤": label, "状态": STEP_LABELS.get(state["steps"][key]["status"], state["steps"][key]["status"]),
                               "设备任务编号": ", ".join(map(str, state["steps"][key]["job_ids"]))}
                              for key, label in STAGES.items()]))
    if state.get("candidate"):
        st.write("Atlas 推荐分子：" + state["candidate"])
    if state.get("objective") is not None:
        st.write(f"样例光学目标值：{state['objective']:.8g}")
        st.caption("该数值来自现有光学模拟样例，用于验证 campaign 更新。")
    if state.get("error"):
        st.error(state["error"])
        st.caption("后续步骤已停止。重新执行请创建新任务；设备步骤中断后需先确认模拟设备已空闲。")
    st.markdown(f"[打开完整工作流](./Workflow?run={identifier})")
    if state["steps"]["matterix"]["status"] != "pending" and state["steps"]["matterix"]["job_ids"]:
        st.markdown(f"[查看 Matterix 仿真数据与视频](./Matterix?run={state['matterix_request_id']})")


def choose(records, key):
    by_id = {value["request_id"]: value for value in records}
    requested = st.experimental_get_query_params().get("run", [None])[0]
    if requested in by_id and st.session_state.get(key + "_opened") != requested:
        st.session_state[key] = requested
        st.session_state[key + "_opened"] = requested
    if st.session_state.get(key) not in by_id:
        st.session_state[key] = records[0]["request_id"]
    identifier = st.selectbox("选择集成工作流", list(by_id), key=key,
                              format_func=lambda value: f"{by_id[value]['request']['name']} · {STEP_LABELS.get(by_id[value]['status'], by_id[value]['status'])} · {value}")
    return get_state(identifier)


def render_monitor():
    st.subheader("集成工作流与 Matterix")
    try:
        records = history()
        if not records:
            st.info("暂无集成工作流。")
            return
        render_details(choose(records, "workflow_monitor_selected"))
    except Exception as exc:
        st.error("工作流记录暂不可用：" + str(exc))


def render():
    st.title("集成工作流")
    st.info("本机模拟演示 · 一轮激光材料示例 · Matterix 实际运行 Isaac Sim，其余仪器返回模拟样例数据。")
    st.caption("Atlas 推荐 → Matterix 抓取验收 → Chemspeed → 过滤 → HPLC → 光学表征 → 更新 campaign。运行期间请勿从其他页面另行提交设备任务。")
    st.button("刷新工作流记录")
    if "workflow_request_id" not in st.session_state:
        st.session_state.workflow_request_id = new_id()
    if st.button("准备新工作流"):
        st.session_state.workflow_request_id = new_id()
    st.caption("待提交编号：" + st.session_state.workflow_request_id)
    missing = []
    for port in (65100, 65200, 65001, 65010, 65070):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.15):
                pass
        except OSError:
            missing.append(port)
    if missing:
        st.warning("请另行运行 start_demo.sh 启动可选论文 demo 服务。离线端口：" + ", ".join(map(str, missing)))
    with st.form("integrated_workflow"):
        name = st.text_input("任务名称", value="激光材料集成模拟", max_chars=64)
        video = st.checkbox("输出仿真视频", value=False)
        submitted = st.form_submit_button("运行一轮集成工作流", disabled=bool(missing))
    if submitted:
        try:
            state = submit(st.session_state.workflow_request_id, name, video)
            st.session_state.workflow_selected = state["request_id"]
            st.success("工作流已提交；相同编号不会重复执行。")
        except Exception as exc:
            st.error("提交失败：" + str(exc))
    active = False
    try:
        records = history()
        if records:
            st.subheader("工作流进度与历史")
            state = choose(records, "workflow_selected")
            render_details(state)
            active = state["status"] not in TERMINAL
        else:
            st.info("暂无工作流记录。")
    except Exception as exc:
        st.error("记录读取失败：" + str(exc))
    schedule_refresh(active, label="刷新工作流记录", timer="workflowRefresh")
