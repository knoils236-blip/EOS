"""Continuous Matterix sessions backed by the local bridge."""

import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import grpc
import pandas as pd
import streamlit as st

from .capabilities import catalog, validate_task
from .common import ROOT, TERMINAL, loads, read_json
from .web_data import (
    download,
    history,
    load_run,
    load_samples,
    new_request_id,
    session_history,
    video_path,
)


SESSION_LABELS = {
    "starting": "启动中",
    "idle": "空闲",
    "running": "运行中",
    "resetting": "正在重置场景",
    "ending": "正在结束会话",
    "frozen": "已冻结",
    "ended": "已结束",
    "interrupted": "执行中断",
    "unreadable": "记录损坏",
    "rejected": "请求被拒绝",
}
TASK_LABELS = {
    "starting": "启动中",
    "running": "运行中",
    "cancelling": "正在取消",
    "succeeded": "成功",
    "failed": "失败",
    "cancelled": "已取消",
    "timed_out": "超时",
    "interrupted": "执行中断",
    "unreadable": "记录损坏",
    "rejected": "请求被拒绝",
}
CLIENT_ERRORS = (grpc.RpcError, OSError, RuntimeError, AttributeError)
ACTIVE_SESSION_STATUSES = frozenset({"starting", "running", "resetting", "ending"})
NOTICE_KEY = "matterix_session_notice"
PENDING_SELECTION_KEY = "matterix_pending_selection"
SILA_FEATURE = Path(__file__).with_name("Matterix.sila.xml")
SILA_NAMESPACE = {"sila": "http://www.sila-standard.org"}
DEFAULT_SCENE_ID = "franka_beaker"
SCENE_LABELS = {
    "franka_beaker": "Franka 烧杯（franka_beaker）",
    "pipetting_realtime": "移液站运动学仿真（pipetting_realtime）",
}
TASK_ERROR_MESSAGES = {
    "return_requires_previous_successful_pickup": "放回前需先成功抓取并抬升烧杯",
    "pickup_requires_returned_beaker_or_reset": "烧杯仍在夹持状态，请先放回或重置场景",
    "simulation_timeout": "仿真任务超时",
    "return_acceptance_failed": "放回并松爪验收未通过",
    "pickup_acceptance_failed": "抓取并抬升验收未通过",
    "cancel_reason:cancelled": "用户取消任务，现场已冻结",
    "cancel_reason:timed_out": "任务超过仿真时间上限，现场已冻结",
}


def scene_options(capabilities):
    scenes = {
        capability.get("scene_id")
        for capability in capabilities
        if capability.get("scene_id")
    }
    return [DEFAULT_SCENE_ID, *sorted(scenes - {DEFAULT_SCENE_ID})]


def scene_label(scene_id):
    return SCENE_LABELS.get(scene_id, scene_id)


def parameter_defaults(capability):
    defaults = {}
    for name, spec in capability.get("parameters", {}).items():
        defaults[name] = False if name == "record_video" else spec["default"]
    return defaults


def parameter_step(spec):
    """Pick an input step that suits the registered range unless the catalog sets one."""
    if "step" in spec:
        return float(spec["step"])
    span = float(spec["max"]) - float(spec["min"])
    return 0.01 if span <= 1 else 0.1 if span <= 10 else 1.0


def render_parameter_fields(parameters, prefix):
    values = {}
    for name, spec in parameters.items():
        unit = spec.get("unit", "")
        label = spec.get("label", name) + (f"（{unit}）" if unit else "")
        widget_key = f"{prefix}_{name}"
        if spec["type"] == "boolean":
            default = False if name == "record_video" else spec["default"]
            values[name] = st.checkbox(label, value=default, key=widget_key)
        elif spec["min"] == spec["max"]:
            values[name] = spec["default"]
            st.caption(f"{label}：{spec['default']}（已验证固定值）")
        else:
            step = parameter_step(spec)
            decimals = 0 if step >= 1 else len(f"{step:g}".split(".")[1])
            values[name] = st.number_input(
                label,
                min_value=float(spec["min"]),
                max_value=float(spec["max"]),
                value=float(spec["default"]),
                step=step,
                format=f"%.{decimals}f",
                help=f"允许范围：{spec['min']}–{spec['max']} {unit}".rstrip(),
                key=widget_key,
            )
    return values


def show_table(rows):
    """st.table without the meaningless 0, 1, 2 index: the first column labels each row."""
    frame = pd.DataFrame(rows)
    st.table(frame.set_index(frame.columns[0]) if len(frame.columns) > 1 else frame)


def parameter_rows(parameters):
    rows = []
    for name, value in parameters.items():
        if isinstance(value, bool):
            text = "是" if value else "否"
        elif isinstance(value, float):
            text = f"{value:g}"
        else:
            text = str(value)
        rows.append({"参数": name, "值": text})
    return rows


def result_checks(result):
    checks = []
    acceptance = result.get("acceptance")
    lift_check = result.get("lift_check")
    return_check = result.get("return_check")
    duplicate_lift = (
        isinstance(acceptance, dict)
        and isinstance(lift_check, dict)
        and acceptance.get("lift") == lift_check
    )
    duplicate_return = (
        isinstance(acceptance, dict)
        and isinstance(return_check, dict)
        and acceptance == return_check
    )
    if isinstance(acceptance, dict):
        if isinstance(acceptance.get("checks"), list):
            for index, check in enumerate(acceptance["checks"], 1):
                if isinstance(check, dict):
                    checks.append((
                        check.get("name") or check.get("label") or f"验收 {index}",
                        check,
                    ))
        elif "passed" in acceptance and not (duplicate_lift or duplicate_return):
            checks.append(("通用验收", acceptance))
    for key, label in (("lift_check", "抓取并抬升验收"), ("return_check", "放回并松爪验收")):
        check = result.get(key)
        if isinstance(check, dict):
            checks.append((label, check))
    return checks


def check_summary(check):
    if "actual" in check and "expected" in check:
        unit = f" {check['unit']}" if check.get("unit") else ""
        return (
            f"验收值：实际 {check['actual']}{unit}，"
            f"期望 {check['expected']}{unit}。"
        )

    lift = check.get("maximum_lift_m")
    hold = check.get("current_hold_s")
    if lift is not None or hold is not None:
        parts = []
        if isinstance(lift, (int, float)):
            parts.append(f"最大抬升 {lift * 100:.2f} 厘米")
        if isinstance(hold, (int, float)):
            parts.append(f"连续保持 {hold:.2f} 秒")
        return "抬升摘要：" + "，".join(parts) + "。" if parts else ""

    stable = check.get("current_stable_s")
    if stable is not None:
        parts = []
        horizontal = check.get("horizontal_error_m")
        vertical = check.get("vertical_error_m")
        tilt = check.get("tilt_rad")
        if isinstance(horizontal, (int, float)):
            parts.append(f"水平误差 {horizontal * 1000:.2f} 毫米")
        if isinstance(vertical, (int, float)):
            parts.append(f"垂直误差 {vertical * 1000:.2f} 毫米")
        if isinstance(stable, (int, float)):
            parts.append(f"稳定保持 {stable:.2f} 秒")
        if isinstance(tilt, (int, float)):
            parts.append(f"倾角 {math.degrees(tilt):.2f}°")
        return "放回摘要：" + "，".join(parts) + "。" if parts else ""

    elapsed = check.get("elapsed_sim_s")
    requested = check.get("requested_duration_s")
    if isinstance(elapsed, (int, float)) and isinstance(requested, (int, float)):
        return f"等待摘要：仿真等待 {elapsed:.2f} 秒，要求 {requested:.2f} 秒。"
    return ""


def elapsed_summary(result, state):
    sim_time = result.get("elapsed_sim_s")
    wall_time = result.get("elapsed_wall_s")
    if sim_time is None:
        sim_time = state.get("elapsed_sim_s")
    if wall_time is None:
        wall_time = state.get("elapsed_wall_s")
    sim_text = f"{sim_time:.2f}" if isinstance(sim_time, (int, float)) else "未知"
    wall_text = f"{wall_time:.2f}" if isinstance(wall_time, (int, float)) else "未知"
    return f"任务耗时：仿真 {sim_text} 秒 · 实际 {wall_text} 秒"


def merge_sessions(remote, local):
    records = {}
    for value in local:
        records[value["session_id"]] = value
    for value in remote:
        records[value["session_id"]] = value
    return sorted(records.values(), key=lambda row: row.get("updated_at", row.get("created_at", "")), reverse=True)


def task_versions(state):
    request = state.get("request") or {}
    capability_version = request.get("capability_version") or state.get("capability_version")
    config_version = request.get("config_version") or state.get("config_version")
    return (
        str(capability_version) if capability_version is not None else "历史记录",
        str(config_version) if config_version is not None else None,
    )


def task_error_message(error):
    code = str(error)
    label = TASK_ERROR_MESSAGES.get(code)
    return f"{label}（原始错误：{code}）" if label else code


def session_action_availability(online, status, active_task_id):
    no_task = not active_task_id
    return (
        online and no_task and status in {"idle", "frozen"},
        online and no_task and status in {"starting", "idle", "frozen"},
    )


def session_needs_refresh(status):
    return status in ACTIVE_SESSION_STATUSES


def session_status_message(status, session):
    if status == "frozen":
        reason = session.get("freeze_reason") or session.get("error") or "任务失败、取消或超时"
        return "error", "会话已冻结，不能提交新任务：" + task_error_message(reason) + "。可重置场景或结束会话。"
    return {
        "starting": ("info", "会话正在启动，完成后可提交任务。"),
        "running": ("info", "任务运行中，完成后可继续提交；如需结束会话，请先取消当前任务。"),
        "resetting": ("info", "正在重置同一仿真场景；页面会自动刷新，完成后可继续提交。"),
        "ending": ("info", "正在结束会话并释放仿真进程；页面会自动刷新。"),
        "interrupted": ("error", "会话执行中断。当前版本不承诺从进程退出前的完整物理现场继续运行，请新建会话。"),
        "ended": ("info", "会话已结束。历史观测和结果仍可查看。"),
    }.get(status, (None, None))


def rerun_with_notice(message):
    st.session_state[NOTICE_KEY] = ("success", message)
    st.experimental_rerun()


def render_notice():
    level, message = st.session_state.pop(NOTICE_KEY, (None, None))
    if message:
        getattr(st, level)(message)


def apply_deep_link(requested, choices, selected_key, opened_key):
    if requested in choices and st.session_state.get(opened_key) != requested:
        st.session_state[selected_key] = requested
        st.session_state[opened_key] = requested


def cancel_active_task(client, active_task_id):
    response = client.cancel(active_task_id)
    if response.get("status") != "rejected":
        rerun_with_notice("取消请求已发送。")
    return response


def render_task(task_id, state, online, render_samples):
    latest = state.get("latest_sample") or {}
    result = state.get("result") or {}
    request = state.get("request") or {}
    parameters = state.get("parameters") or request.get("parameters") or {}
    status = state.get("status", "unreadable")
    capability_version, config_version = task_versions(state)
    capability_id = request.get("capability_id") or state.get("capability_id") or request.get("workflow_id") or "未知"
    scene_id = state.get("scene_id") or request.get("scene_id") or DEFAULT_SCENE_ID
    columns = st.columns(2)
    columns[0].metric("任务状态", TASK_LABELS.get(status, status))
    columns[1].metric("最新仿真时间（秒）", f"{latest.get('sim_time_s', state.get('sim_time_s', 0)):.2f}")
    details = [f"能力 {capability_id}（版本 {capability_version}）", scene_label(scene_id)]
    if status in TERMINAL:
        details.append(elapsed_summary(result, state))
    if config_version:
        details.append(f"配置 {config_version[:12]}…")
    simulation_label = "Isaac Sim 运动学仿真" if scene_id == "pipetting_realtime" else "Isaac Sim 仿真"
    details.append(("在线记录" if online else "本地记录") + " · " + simulation_label)
    st.caption(" · ".join(details))

    task_error = state.get("freeze_reason") or state.get("error")
    if task_error:
        st.warning("任务原因：" + task_error_message(task_error))

    checks = result_checks(result)
    for label, check in checks:
        passed = check.get("passed")
        message = f"{label}：" + ("通过" if passed else "未通过") if passed is not None else label
        summary = check_summary(check)
        if summary:
            message += "  \n" + summary
        (st.success if passed else st.warning if passed is False else st.info)(message)
    if not checks:
        st.info("暂无验收结果。" if status in TERMINAL else "任务完成后显示验收结果。")

    record_video = bool(parameters.get("record_video"))
    movie = video_path(task_id)
    if record_video or movie is not None:
        if status not in TERMINAL and movie is None:
            st.info("本任务已启用视频输出，完成后可在此播放。")
        elif movie is not None:
            st.video(str(movie))
            st.caption("实际 Isaac Sim 仿真画面；可播放、暂停和拖动进度。")
        else:
            st.warning("本任务没有生成可播放视频。" + str(result.get("video_error", "")))

    samples = load_samples(task_id)
    tabs = st.tabs(["观测曲线", "参数快照", "验收详情", "下载与原始数据"])
    with tabs[0]:
        if samples:
            render_samples(samples, result)
        elif status in {"starting", "running"}:
            st.info("等待第一条仿真观测。")
        else:
            st.info("本任务没有观测记录。")
    with tabs[1]:
        if parameters:
            st.caption("提交时保存的不可变参数快照。")
            show_table(parameter_rows(parameters))
        else:
            st.info("本任务没有参数快照。")
        if config_version:
            st.caption("完整配置版本：" + config_version)
    with tabs[2]:
        for label, check in checks:
            st.markdown(f"**{label}**")
            st.json(check)
        if not checks:
            st.info("暂无验收详情。")
    with tabs[3]:
        columns = st.columns(2)
        if result:
            columns[0].download_button(
                "下载结果 JSON",
                download(task_id, "result"),
                file_name=f"{task_id}-result.json",
                mime="application/json",
                use_container_width=True,
            )
        if samples:
            columns[1].download_button(
                "下载观测 JSONL",
                download(task_id, "samples"),
                file_name=f"{task_id}-samples.jsonl",
                mime="application/x-ndjson",
                use_container_width=True,
            )
        if result:
            st.json(result)
        elif not samples:
            st.info("任务完成后可下载结果。")
    return status not in TERMINAL


def render(client, online, render_samples):
    render_notice()

    try:
        local_sessions = session_history()
        tasks = history()
    except (OSError, ValueError) as exc:
        st.error("本地 Matterix 记录读取失败：" + str(exc))
        local_sessions, tasks = [], []

    remote_sessions = []
    if online:
        try:
            remote_sessions = client.list_sessions()
        except CLIENT_ERRORS as exc:
            online = False
            st.warning("无法读取在线会话，以下仅显示本地记录：" + str(exc))
    sessions = merge_sessions(remote_sessions, local_sessions)

    left, right = st.columns([2, 3], gap="large")
    with left:
        heading = st.columns(2)
        heading[0].subheader("会话")
        heading[1].button("刷新会话与任务", use_container_width=True)

        if online:
            try:
                scenes = scene_options(catalog())
            except (OSError, ValueError) as exc:
                scenes = [DEFAULT_SCENE_ID]
                st.warning("场景登记读取失败，仅保留默认 Franka 烧杯场景：" + str(exc))
            with st.expander("创建新的仿真会话", expanded=not sessions):
                if "matterix_new_experiment_id" not in st.session_state:
                    st.session_state.matterix_new_experiment_id = "experiment-" + new_request_id()[4:]
                if "matterix_new_session_id" not in st.session_state:
                    st.session_state.matterix_new_session_id = "session-" + new_request_id()[4:]
                with st.form("matterix_create_session"):
                    experiment_id = st.text_input(
                        "实验 ID", key="matterix_new_experiment_id", max_chars=64
                    )
                    session_id = st.text_input(
                        "会话 ID", key="matterix_new_session_id", max_chars=64
                    )
                    scene_id = st.selectbox(
                        "场景",
                        scenes,
                        format_func=scene_label,
                        key="matterix_new_scene_id",
                    )
                    create = st.form_submit_button("创建会话", type="primary")
                if create:
                    try:
                        session = client.create_session(
                            {
                                "session_id": session_id.strip(),
                                "experiment_id": experiment_id.strip(),
                                "scene_id": scene_id,
                            }
                        )
                        if session.get("status") == "rejected":
                            st.error("会话创建失败：" + str(session.get("error", "未知错误")))
                        else:
                            st.session_state.matterix_selected_session = session["session_id"]
                            rerun_with_notice("会话已创建。")
                    except (ValueError, *CLIENT_ERRORS) as exc:
                        st.error("会话创建失败：" + str(exc))

        if not sessions:
            st.info("暂无 Matterix 会话。" + ("请先创建会话。" if online else "服务恢复后可创建新会话。"))
            right.subheader("任务与结果")
            right.info("创建会话并提交任务后，结果会显示在这里。")
            return False

        pending = st.session_state.pop(PENDING_SELECTION_KEY, None)
        if pending:
            st.session_state.matterix_selected_session = pending["session_id"]
            st.session_state[f"matterix_selected_task_{pending['session_id']}"] = pending["request_id"]
        requested = st.experimental_get_query_params().get("session", [None])[0]
        session_ids = [row["session_id"] for row in sessions]
        apply_deep_link(
            requested,
            session_ids,
            "matterix_selected_session",
            "matterix_opened_session_link",
        )
        if st.session_state.get("matterix_selected_session") not in session_ids:
            st.session_state.matterix_selected_session = session_ids[0]
        status_by_id = {row["session_id"]: row.get("status") for row in sessions}
        selected_id = st.selectbox(
            "查看会话",
            session_ids,
            key="matterix_selected_session",
            format_func=lambda value: f"{value} · {SESSION_LABELS.get(status_by_id[value], status_by_id[value])}",
        )
        local_session = next((row for row in local_sessions if row["session_id"] == selected_id), {})
        listed_session = next(row for row in sessions if row["session_id"] == selected_id)
        session = local_session
        if online:
            try:
                remote = client.get_session(selected_id)
                if remote.get("status") != "rejected":
                    session = remote
                else:
                    st.warning("服务未找到此会话，以下显示本地记录。")
            except CLIENT_ERRORS as exc:
                online = False
                st.warning("在线查询中断，以下显示本地记录：" + str(exc))
        if not session:
            session = listed_session

        status = session.get("status", "unreadable")
        latest = session.get("latest_sample") or {}
        metrics = st.columns(2)
        metrics[0].metric("会话状态", SESSION_LABELS.get(status, status))
        metrics[1].metric("最新仿真时间（秒）", f"{session.get('sim_time_s', latest.get('sim_time_s', 0)):.2f}")
        st.caption(
            f"场景 {scene_label(session.get('scene_id', DEFAULT_SCENE_ID))} · "
            f"场景重置 {session.get('generation', '未知')} 次 · "
            f"实验 {session.get('experiment_id', '未知')} · {'在线状态' if online else '本地快照'}"
        )
        level, message = session_status_message(status, session)
        if message:
            getattr(st, level)(message)

        can_reset, can_end = session_action_availability(online, status, session.get("active_task_id"))
        active_task_id = session.get("active_task_id")
        manage = st.columns(3)
        if manage[0].button("重置场景", disabled=not can_reset, use_container_width=True):
            try:
                response = client.reset_session(selected_id)
                if response.get("status") == "rejected":
                    st.error(response.get("error", "重置失败"))
                else:
                    rerun_with_notice("已请求重置场景。")
            except CLIENT_ERRORS as exc:
                st.error("重置失败：" + str(exc))
        if manage[1].button("结束会话", disabled=not can_end, use_container_width=True):
            try:
                response = client.end_session(selected_id)
                if response.get("status") == "rejected":
                    st.error(response.get("error", "结束失败"))
                else:
                    rerun_with_notice("已请求结束会话。")
            except CLIENT_ERRORS as exc:
                st.error("结束会话失败：" + str(exc))
        if manage[2].button(
            "取消任务",
            disabled=not (online and active_task_id),
            key=f"matterix_cancel_active_{selected_id}",
            use_container_width=True,
        ):
            try:
                response = cancel_active_task(client, active_task_id)
                if response.get("status") == "rejected":
                    st.error("取消未被接受：" + str(response.get("error", "未知错误")))
            except CLIENT_ERRORS as exc:
                st.error("取消失败：" + str(exc))

        capabilities = []
        if online and status == "idle":
            try:
                capabilities = [
                    value
                    for value in client.capabilities()
                    if value.get("scene_id") == session.get("scene_id", DEFAULT_SCENE_ID)
                ]
            except CLIENT_ERRORS as exc:
                online = False
                st.warning("无法读取已登记能力，提交表单暂不可用：" + str(exc))

        st.divider()
        st.subheader("提交任务")
        if capabilities:
            capability_labels = {
                value["id"]: f"{value.get('label', value['id'])} · v{value.get('version', '?')}"
                for value in capabilities
            }
            capability_id = st.selectbox(
                "已登记能力",
                list(capability_labels),
                format_func=capability_labels.get,
                key=f"matterix_capability_{selected_id}",
            )
            capability = next(value for value in capabilities if value["id"] == capability_id)
            if capability.get("observations") or capability.get("success_criteria"):
                with st.expander("能力说明：观测与验收依据"):
                    if capability.get("observations"):
                        st.markdown("**登记观测**：" + "、".join(f"`{value}`" for value in capability["observations"]))
                    if capability.get("success_criteria"):
                        st.markdown("**验收依据**")
                        st.markdown("\n".join(f"- {value}" for value in capability["success_criteria"]))

            sequence_key = f"matterix_request_sequence_{selected_id}"
            default_key = f"matterix_request_default_{selected_id}"
            if sequence_key not in st.session_state:
                st.session_state[sequence_key] = 0
            if default_key not in st.session_state:
                st.session_state[default_key] = new_request_id()
            widget_key = f"matterix_request_id_{selected_id}_{st.session_state[sequence_key]}"
            with st.form(f"matterix_submit_{selected_id}_{capability_id}"):
                request_id = st.text_input(
                    "任务编号",
                    value=st.session_state[default_key],
                    key=widget_key,
                    max_chars=64,
                    help="相同任务编号不会重复执行；提交后会自动生成下一个编号。",
                )
                parameters = render_parameter_fields(
                    capability["parameters"],
                    f"matterix_{selected_id}_{capability_id}_{st.session_state[sequence_key]}",
                )
                st.caption("提交后参数保存为不可变快照；表单修改只影响下一次提交。")
                submitted = st.form_submit_button("提交任务", type="primary", use_container_width=True)
            if st.button("生成新任务编号"):
                st.session_state[default_key] = new_request_id()
                st.session_state[sequence_key] += 1
                st.experimental_rerun()
            if submitted:
                request = {
                    "request_id": request_id.strip(),
                    "session_id": selected_id,
                    "capability_id": capability_id,
                    "parameters": parameters,
                }
                try:
                    task = client.submit_task(request, wait=False, on_event=None)
                    if task.get("status") == "rejected":
                        st.error("任务提交失败：" + str(task.get("error", "未知错误")))
                    else:
                        task_id = task.get("request_id") or request["request_id"]
                        st.session_state[f"matterix_selected_task_{selected_id}"] = task_id
                        st.session_state[default_key] = new_request_id()
                        st.session_state[sequence_key] += 1
                        rerun_with_notice("任务已提交；相同任务编号不会重复执行。")
                except (ValueError, *CLIENT_ERRORS) as exc:
                    st.error("任务提交失败：" + str(exc))
        elif status == "idle" and online:
            st.info("当前场景没有已登记能力。")
        elif not online:
            st.caption("服务离线，暂不能提交任务。")
        else:
            st.caption("会话空闲时可提交任务。")

    with right:
        session_tasks = [
            row
            for row in tasks
            if row.get("session_id") == selected_id or (row.get("request") or {}).get("session_id") == selected_id
        ]
        st.subheader("任务与结果")
        if not session_tasks:
            st.info("本会话暂无任务记录。提交任务后，状态、验收、观测曲线和视频会显示在这里。")
            return session_needs_refresh(status)

        task_by_id = {row["request_id"]: row for row in session_tasks}
        requested_run = st.experimental_get_query_params().get("run", [None])[0]
        selected_key = f"matterix_selected_task_{selected_id}"
        apply_deep_link(
            requested_run,
            list(task_by_id),
            selected_key,
            "matterix_opened_task_link",
        )
        if st.session_state.get(selected_key) not in task_by_id:
            st.session_state[selected_key] = session_tasks[0]["request_id"]

        def task_label(value):
            row = task_by_id[value]
            capability = row.get("capability_id") or (row.get("request") or {}).get("capability_id")
            parts = [value, capability, TASK_LABELS.get(row["status"], row["status"])]
            return " · ".join(part for part in parts if part)

        task_id = st.selectbox(
            f"查看任务（本会话共 {len(session_tasks)} 个）",
            list(task_by_id),
            key=selected_key,
            format_func=task_label,
        )
        try:
            state = load_run(task_id)
            if online:
                try:
                    remote = client.get(task_id)
                    if remote.get("status") != "rejected":
                        state = remote
                except CLIENT_ERRORS:
                    online = False
                    st.warning("在线任务查询中断，以下显示本地记录。")
            active_task = render_task(task_id, state, online, render_samples)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            st.error("无法完整读取这条任务记录：" + str(exc))
            return session_needs_refresh(status)
        return active_task or session_needs_refresh(status)


UPLOAD_NOTICE_KEY = "matterix_upload_notice"
MAX_JOB_BYTES = 8192


def available_capabilities(client, online):
    """Prefer the live registry; fall back to the local catalog file the server also reads."""
    if online:
        try:
            values = client.capabilities()
            if isinstance(values, list):
                return values
        except CLIENT_ERRORS:
            pass
    return catalog()


def known_session_ids(client, online):
    sessions = session_history()
    if online:
        try:
            sessions = merge_sessions(client.list_sessions(), sessions)
        except CLIENT_ERRORS:
            pass
    return [row["session_id"] for row in sessions if row.get("status") in {"idle", "starting"}]


def job_template(session_id, capability, request_id):
    job = {
        "request_id": request_id,
        "session_id": session_id,
        "capability_id": capability["id"],
        "parameters": parameter_defaults(capability),
    }
    return json.dumps(job, ensure_ascii=False, indent=2)


def render_job_upload(client, online):
    st.caption(
        "沿用 ChemOS 的人可读 JSON job 约定，与命令行 client.sh submit-task --job 使用同一格式。"
        "上传后先按能力登记在本地校验，提交时服务端会再次校验。"
    )
    try:
        capabilities = available_capabilities(client, online)
        session_ids = known_session_ids(client, online)
    except (OSError, ValueError) as exc:
        st.error("能力登记或会话记录读取失败：" + str(exc))
        return
    upload, template = st.columns([3, 2], gap="large")

    with template:
        st.subheader("生成 job 模板")
        labels = {value["id"]: f"{value.get('label', value['id'])} · v{value.get('version', '?')}"
                  for value in capabilities}
        capability_id = st.selectbox("能力", list(labels), format_func=labels.get,
                                     key="matterix_template_capability")
        session_id = st.selectbox(
            "目标会话（空闲或启动中）",
            session_ids or ["<请先创建会话>"],
            key="matterix_template_session",
        )
        if "matterix_template_request_id" not in st.session_state:
            st.session_state.matterix_template_request_id = new_request_id()
        capability = next(value for value in capabilities if value["id"] == capability_id)
        text = job_template(session_id, capability, st.session_state.matterix_template_request_id)
        st.code(text, language="json")
        st.download_button("下载模板", text, file_name=f"{capability_id}-job.json",
                           mime="application/json", use_container_width=True)

    with upload:
        st.subheader("上传并提交")
        message = st.session_state.pop(UPLOAD_NOTICE_KEY, None)
        if message:
            st.success(message)
        uploaded = st.file_uploader("job 文件（JSON，不超过 8 KB）", type=["json"],
                                    key="matterix_job_upload")
        if uploaded is None:
            st.info("选择或拖入一个 job 文件。可以先在右侧下载模板，修改参数后再上传。")
            return
        raw = uploaded.getvalue()
        if len(raw) > MAX_JOB_BYTES:
            st.error("job 文件超过 8 KB。")
            return
        try:
            job = loads(raw.decode("utf-8"))
            normalized = validate_task(job)
        except (UnicodeDecodeError, ValueError) as exc:
            st.error("job 文件无效：" + str(exc))
            return
        st.success("格式与参数范围校验通过。")
        st.caption(
            f"任务编号 {normalized['request_id']} · 会话 {normalized['session_id']} · "
            f"能力 {normalized['capability_id']}（未写的参数取登记默认值）"
        )
        show_table(parameter_rows(normalized["parameters"]))
        if st.button("提交 job", type="primary", disabled=not online, use_container_width=True):
            try:
                task = client.submit_task(job, wait=False, on_event=None)
            except CLIENT_ERRORS as exc:
                st.error("提交失败：" + str(exc))
                return
            if task.get("status") == "rejected":
                st.error("提交被拒绝：" + task_error_message(task.get("error", "未知错误")))
                return
            st.session_state[PENDING_SELECTION_KEY] = {
                "session_id": normalized["session_id"],
                "request_id": normalized["request_id"],
            }
            st.session_state[UPLOAD_NOTICE_KEY] = (
                f"job {normalized['request_id']} 已提交；在“会话与任务”中可查看进度和结果。"
            )
            st.session_state.matterix_template_request_id = new_request_id()
            st.experimental_rerun()
        if not online:
            st.caption("服务离线，暂不能提交。")


def sila_commands():
    root = ET.parse(SILA_FEATURE).getroot()
    rows = []
    for command in root.findall("sila:Command", SILA_NAMESPACE):
        observable = command.findtext("sila:Observable", "", SILA_NAMESPACE) == "Yes"
        rows.append({
            "命令": command.findtext("sila:Identifier", "", SILA_NAMESPACE) + ("（回传进度）" if observable else ""),
            "说明": command.findtext("sila:Description", "", SILA_NAMESPACE),
        })
    return root.findtext("sila:DisplayName", "", SILA_NAMESPACE), rows


def capability_parameter_rows(capability):
    rows = []
    for name, spec in capability.get("parameters", {}).items():
        if spec["type"] == "boolean":
            span, default = "是 / 否", "是" if parameter_defaults(capability)[name] else "否"
        else:
            span, default = f"{spec['min']}–{spec['max']}", f"{spec['default']}"
        rows.append({"参数": spec.get("label", name), "字段": name, "默认": default,
                     "范围": span, "单位": spec.get("unit", "")})
    return rows


def render_device_info(client, online):
    try:
        config = read_json(ROOT / "config.json")
        feature, commands = sila_commands()
        capabilities = available_capabilities(client, online)
    except (OSError, ValueError, ET.ParseError) as exc:
        st.error("设备信息读取失败：" + str(exc))
        return
    show_table([
        {"项目": "SiLA2 地址", "值": "127.0.0.1:65200"},
        {"项目": "SiLA2 特性", "值": f"{feature}（Matterix.sila.xml）"},
        {"项目": "Matterix 项目", "值": str(config.get("matterix_root", "未配置"))},
        {"项目": "仿真解释器", "值": str(config.get("matterix_python", "未配置"))},
        {"项目": "单任务实际时间上限", "值": f"{config.get('wall_timeout_s', '未知')} 秒"},
        {"项目": "执行方式", "值": "每个 GPU 一个活动会话，会话内任务串行"},
    ])
    commands_column, capabilities_column = st.columns(2, gap="large")
    with commands_column:
        st.subheader("SiLA2 命令")
        st.caption("ChemOS 编排脚本、网页和命令行都通过这些命令访问工作站；标注“回传进度”的命令在执行中持续推送状态。")
        show_table(commands)
    with capabilities_column:
        st.subheader("已登记能力")
        st.caption(
            "来源：bridge/capabilities.json 与移液站 bridge/station_catalog.json。"
            "网页表单、job 校验和服务端都使用这些登记。"
        )
        for capability in capabilities:
            st.markdown(f"**{capability.get('label', capability['id'])}** · `{capability['id']}` · "
                        f"v{capability.get('version', '?')} · `{capability.get('scene_id', '未知场景')}`")
            if capability.get("parameters"):
                show_table(capability_parameter_rows(capability))
            if capability.get("success_criteria"):
                st.markdown("\n".join(f"- {value}" for value in capability["success_criteria"]))
