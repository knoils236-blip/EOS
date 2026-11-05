"""Matterix page rendered inside the existing ChemOS Streamlit application."""

import socket
import time

import grpc
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from . import session_web
from .client import MatterixClient
from .common import TERMINAL, WORKFLOW
from .web_data import download, history, load_run, load_samples, video_path

LABELS = {"starting": "启动中", "running": "运行中", "cancelling": "正在取消", "succeeded": "成功",
          "failed": "失败", "cancelled": "已取消", "timed_out": "超时", "interrupted": "执行中断",
          "unreadable": "记录损坏", "rejected": "请求被拒绝"}
ACTION_NAMES = {"Wait": "静置或保持", "OpenGripper": "打开夹爪", "CloseGripper": "闭合夹爪",
                "MoveToFrame": "移动到操作位置", "MoveRelative": "抬升烧杯"}
ERROR_LABELS = {"cancelled": "用户取消了任务", "timed_out": "任务超过时间上限", "interrupted": "服务关闭或任务执行中断",
                "server_restart": "服务重启，未自动重试任务", "simulation_time_limit": "达到仿真时间上限",
                "beaker_did_not_pass_lift_check": "烧杯抬升量或保持时间未达到要求"}
CONNECTION_ERRORS = (grpc.RpcError, OSError, RuntimeError)


def connect():
    with socket.create_connection(("127.0.0.1", 65200), timeout=0.3):
        pass
    client = MatterixClient()
    try:
        client.get("web-health-check")
    except Exception:
        client.close()
        raise
    return client


def render():
    client = None
    try:
        client = connect()
    except CONNECTION_ERRORS:
        pass
    online = client is not None
    try:
        if not online:
            st.warning("服务离线：可查看本地会话与历史任务；创建、提交、重置和结束暂不可用。")
        # ChemOS 2.0 device page (paper Figure 4): actions and inputs, job-file upload, results.
        tabs = st.tabs(["会话与任务", "上传 job 文件", "设备与能力", "旧抓取记录"])
        with tabs[0]:
            session_active = session_web.render(client, online, render_samples)
        with tabs[1]:
            session_web.render_job_upload(client, online)
        with tabs[2]:
            session_web.render_device_info(client, online)
        with tabs[3]:
            st.caption("迁移到持续会话之前的一次性抓取任务，仅供查看历史。")
            legacy_active = render_legacy(client, online)
    finally:
        if client is not None:
            client.close()
    # Finish each Streamlit run before refreshing, so obsolete widgets are removed.
    schedule_refresh(
        online and (session_active or legacy_active),
        label="刷新会话与任务" if session_active else "刷新旧抓取记录",
        timer="matterixRefresh",
    )


def render_legacy(client, online):
    try:
        records = history()
        records = [
            row
            for row in records
            if row.get("workflow_id") == WORKFLOW
            or (row.get("request") or {}).get("workflow_id") == WORKFLOW
        ]
        if not records:
            st.info("暂无旧固定抓取记录。新的抓取和放回任务请在持续会话中提交。")
            return False
        by_id = {row["request_id"]: row for row in records}
        requested = st.experimental_get_query_params().get("run", [None])[0]
        if requested in by_id and st.session_state.get("matterix_opened_link") != requested:
            st.session_state.matterix_legacy_selected = requested
            st.session_state.matterix_opened_link = requested
        if st.session_state.get("matterix_legacy_selected") not in by_id:
            st.session_state.matterix_legacy_selected = records[0]["request_id"]
        st.button("刷新旧抓取记录")
        identifier = st.selectbox(
            "查看旧抓取",
            list(by_id),
            key="matterix_legacy_selected",
            format_func=lambda key: f"{key} · {LABELS.get(by_id[key]['status'], by_id[key]['status'])}",
        )
        try:
            state = load_run(identifier)
            if online:
                try:
                    remote = client.get(identifier)
                    if remote.get("status") != "rejected":
                        state = remote
                    else:
                        st.warning("服务未找到此任务，以下显示本地历史记录。")
                except CONNECTION_ERRORS:
                    online = False
                    st.warning("连接已中断，以下为离线记录。")
            active = state["status"] not in TERMINAL
            latest = state.get("latest_sample", {})
            result = state.get("result", {})
            check = result.get("lift_check") or {}
            columns = st.columns(3)
            columns[0].metric("旧抓取状态", LABELS.get(state["status"], state["status"]))
            columns[1].metric("仿真时间（秒）", f"{latest.get('sim_time_s', 0):.2f}")
            columns[2].metric("最大抬升（厘米）", f"{check['maximum_lift_m'] * 100:.2f}" if check else "待验收")
            st.caption(f"任务：{identifier} · {'在线查询' if online else '离线记录'}")
            steps = state.get("latest_event", {}).get("steps", result.get("final_steps", []))
            if steps:
                action = steps[0].get("action_name")
                st.write("当前步骤：" + ACTION_NAMES.get(action, action or "流程结束"))
            if latest:
                st.caption("最近观测时间（UTC）：" + latest.get("wall_time_utc", "未知"))
            if state.get("error"):
                message = "任务记录的原因：" + ERROR_LABELS.get(state["error"], state["error"])
                (st.info if state["status"] == "cancelled" else st.error)(message)
            if state.get("read_errors") or state.get("result_read_error"):
                st.warning("部分结果文件读取失败；不能据此认定验收通过。")
            if check:
                if state["status"] == "succeeded" and check.get("passed"):
                    st.success(f"物理验收通过：烧杯最大抬升 {check['maximum_lift_m'] * 100:.2f} 厘米，连续保持 {check['current_hold_s']:.2f} 秒。")
                else:
                    st.warning("任务未通过完整验收。")
            if state.get("request", {}).get("record_video"):
                st.subheader("仿真视频")
                movie = video_path(identifier)
                if active:
                    st.info("本任务已启用视频输出，完成后可在此播放。")
                elif movie is not None:
                    st.video(str(movie))
                    st.caption("实际 Isaac Sim 仿真画面；可使用播放器播放、暂停和拖动进度。")
                else:
                    st.warning("本任务没有生成可播放视频。" + result.get("video_error", ""))
            samples = load_samples(identifier)
            if samples:
                render_samples(samples, result)
            else:
                st.info("等待第一条仿真观测。启动阶段也可以取消任务。")
            cols = st.columns(2)
            if result:
                cols[0].download_button("下载结果 JSON", download(identifier, "result"),
                                        file_name=f"{identifier}-result.json", mime="application/json")
            if samples:
                cols[1].download_button("下载观测 JSONL", download(identifier, "samples"),
                                        file_name=f"{identifier}-samples.jsonl", mime="application/x-ndjson")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "任务编号": row["request_id"],
                            "状态": LABELS.get(row["status"], row["status"]),
                            "创建时间（UTC）": row.get("created_at", "未知"),
                        }
                        for row in records
                    ]
                )
            )
            return active
        except (OSError, ValueError, KeyError, TypeError) as exc:
            st.error("无法完整读取这条记录，请选择其他任务：" + str(exc))
            return False
    except (OSError, ValueError) as exc:
        st.error("旧抓取历史读取失败：" + str(exc))
        return False


def schedule_refresh(enabled, label="刷新连接与记录", timer="matterixRefresh"):
    refresh = "true" if enabled else "false"
    components.html(f"""<script>
      const host = window.parent;
      if (host.__{timer}) host.clearTimeout(host.__{timer});
      if ({refresh}) host.__{timer} = host.setTimeout(() => {{
        const button = [...host.document.querySelectorAll('button')]
          .find(b => b.textContent.trim() === '{label}');
        if (button) button.click();
      }}, 2000);
    </script><!-- {time.monotonic_ns()} -->""", height=0)


def station_volume_rows(volumes_ml):
    return [
        {"USD 对象 key": key, "液量（mL）": value}
        for key, value in sorted(volumes_ml.items())
    ]


def station_acceptance_rows(result):
    rows = []
    for _, check in session_web.result_checks(result):
        if "name" not in check:
            continue
        rows.append({
            "name": check["name"],
            "actual": check.get("actual"),
            "expected": check.get("expected"),
            "unit": check.get("unit"),
            "passed": check.get("passed"),
        })
    return rows


def availability_text(value):
    if value.get("available") is True:
        return f"可用：{value.get('value')}"
    reason = value.get("reason") or "未说明原因"
    return f"不可用：{reason}"


def render_station_samples(samples, result):
    latest = samples[-1]
    semantics = latest["semantics"]
    columns = st.columns(3)
    columns[0].metric("仿真时间（秒）", f"{latest['sim_time_s']:.2f}")
    columns[1].metric("流程阶段", latest.get("stage") or "未知")
    columns[2].metric("数据来源", latest.get("source") or "未知")
    st.caption("该场景使用 Isaac Sim 运动学仿真；液量来自 USD 对象语义账本，单位 mL。")

    volume_tab, state_tab = st.tabs(["语义液量", "设备、可用性与验收"])
    with volume_tab:
        rows = station_volume_rows(semantics["liquid_volumes_ml"])
        nonzero = [row for row in rows if row["液量（mL）"] != 0]
        st.markdown("**USD 对象液量（非零项）**")
        st.dataframe(pd.DataFrame(nonzero or rows), use_container_width=True)
        with st.expander("完整 USD 液量账本"):
            st.dataframe(pd.DataFrame(rows), use_container_width=True)
        plc_volumes = semantics.get("plc_vial_volumes_ml") or {}
        if plc_volumes:
            with st.expander("PLC 瓶位体积账本"):
                st.dataframe(
                    pd.DataFrame(station_volume_rows(plc_volumes)),
                    use_container_width=True,
                )

    with state_tab:
        unavailable = st.columns(2)
        unavailable[0].metric(
            "温度",
            "可用" if semantics["temperature_c"].get("available") is True else "不可用",
        )
        unavailable[0].caption(availability_text(semantics["temperature_c"]))
        unavailable[1].metric(
            "反应进度",
            "可用" if semantics["reaction_progress"].get("available") is True else "不可用",
        )
        unavailable[1].caption(availability_text(semantics["reaction_progress"]))
        st.markdown("**设备状态**")
        st.json(semantics.get("device_state") or {})
        st.markdown("**语义验收**")
        acceptance_rows = station_acceptance_rows(result)
        if acceptance_rows:
            st.dataframe(pd.DataFrame(acceptance_rows), use_container_width=True)
        else:
            st.info("暂无独立液体验收结果；流程或状态机完成不能代替液体成功。")


def render_samples(samples, result):
    if samples[-1].get("scene_id") == "pipetting_realtime":
        render_station_samples(samples, result)
        return

    tabs = st.tabs(["烧杯高度", "末端位置", "关节状态"])
    with tabs[0]:
        st.caption("烧杯世界坐标高度（米）；验收以静置后的基准高度为起点。")
        frame = pd.DataFrame([{"仿真时间（秒）": s["sim_time_s"], "烧杯高度（米）": s["beaker"]["position_w"][2]}
                              for s in samples]).set_index("仿真时间（秒）")
        st.line_chart(frame)
    with tabs[1]:
        frame = pd.DataFrame([{"仿真时间（秒）": s["sim_time_s"], **dict(zip(("X（米）", "Y（米）", "Z（米）"),
                                                                                     s["robot"]["ee_position_w"]))}
                              for s in samples]).set_index("仿真时间（秒）")
        st.line_chart(frame)
        st.write("最新末端四元数（XYZW）：", samples[-1]["robot"]["ee_quaternion_xyzw"])
    with tabs[2]:
        frame = pd.DataFrame([{"仿真时间（秒）": s["sim_time_s"], **dict(zip(s["robot"]["joint_names"][:7],
                                                                                        s["robot"]["joint_position"][:7]))}
                              for s in samples]).set_index("仿真时间（秒）")
        st.caption("机械臂关节角度曲线，单位 rad。")
        st.line_chart(frame)
        robot = samples[-1]["robot"]
        st.dataframe(pd.DataFrame({"关节": robot["joint_names"], "位置": robot["joint_position"],
                                   "速度": robot["joint_velocity"],
                                   "单位（位置 / 速度）": ["rad / rad·s⁻¹"] * 7 + ["m / m·s⁻¹"] * 2}))
