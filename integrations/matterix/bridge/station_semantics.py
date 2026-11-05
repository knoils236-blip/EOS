"""Read-only semantic snapshots for the fixed pipetting station export."""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping


VOLUME_TOLERANCE_ML = 1e-6
# Transferred volume vs the request, as PipettingStation_20261031 scripts/run_full_workflow.py accepts a phase
# (|moved - expected| <= 5 µL); its piston controller also counts as arrived within volume_tolerance_ul = 5 µL.
# Ledger conservation and residue stay at VOLUME_TOLERANCE_ML.
TRANSFER_TOLERANCE_ML = 0.005
AIR_LIMIT_ML = 0.005  # same source acceptance: aspirated air above 5 µL fails
MIN_TRANSFER_VOLUME_UL = 20.0
MAX_TRANSFER_VOLUME_UL = 180.0
SOURCE_BOTTLE = "TransferBottle10ml_617"
TARGET_BOTTLE = "DesignC_BottleBody50ml_231"
SMALL_VIAL = "DesignD_DesignC_ChromatographyVial2ml_190"
SMALL_VIAL_CAP = "DesignD_DesignC_ChromatographyVial2ml_190_Cap"
BIG_BOTTLE = "DesignD_DesignC_BottleBody500ml_238"
BIG_BOTTLE_CAP = "DesignD_DesignC_BottleCap_237_Cap"
FIXED_TIP = "_1mlTipBox_222_Tip_85"
SYRINGE_BARREL = "SyringeComponent1_676"
SYRINGE_PISTON = "SyringeComponent2_677"


def _scalar(value, label):
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "tolist"):
        value = value.tolist()
    while isinstance(value, (list, tuple)):
        if len(value) != 1:
            raise ValueError(f"{label} must contain one scalar")
        value = value[0]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _json_value(value):
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            raise ValueError("Operation history contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return _scalar(value, "operation history value")


def _station_operations(env):
    cache = getattr(env, "_pipetting_operations", None)
    drivers = cache.get("station") if isinstance(cache, Mapping) else None
    if not isinstance(drivers, (list, tuple)) or not drivers:
        raise ValueError("Station operations are not initialized")
    driver = drivers[0]
    operations = getattr(driver, "operations", None)
    if operations is None:
        raise ValueError("Station driver has no operations state")
    return driver, operations


def _volume_map(operations):
    values = getattr(operations, "volume", None)
    if not isinstance(values, Mapping):
        raise ValueError("Station operations.volume must be a mapping")
    return {
        str(name): _scalar(value, f"liquid volume {name}")
        for name, value in sorted(values.items(), key=lambda item: str(item[0]))
    }


def _grip_map(operations):
    values = getattr(operations, "grips", None)
    if not isinstance(values, Mapping):
        raise ValueError("Station operations.grips must be a mapping")
    result = {}
    attached = {}
    for name, path in sorted(values.items(), key=lambda item: str(item[0])):
        name = str(name)
        result[name] = str(path)
        attached[name] = bool(operations.attached(name))
    return result, attached


def _seated_tips(operations):
    mounts = getattr(operations, "tip_mounts", None)
    if not isinstance(mounts, Mapping):
        return {}
    return {
        str(name): bool(mount.get("seated", False))
        for name, mount in sorted(mounts.items(), key=lambda item: str(item[0]))
        if isinstance(mount, Mapping)
    }


def _closed_caps(operations):
    objects = getattr(operations, "objects", None)
    if not isinstance(objects, Mapping):
        return {}
    result = {}
    for name, record in sorted(objects.items(), key=lambda item: str(item[0])):
        if isinstance(record, Mapping) and record.get("kind") == "bottle" and record.get("cap"):
            result[str(name)] = bool(operations.cap_closed(name))
    return result


def _operation_status(driver):
    prim = driver.stage.GetPrimAtPath(driver.root)
    if not prim:
        raise ValueError("Station operation root is unavailable")
    attribute = prim.GetAttribute("operation:status")
    if not attribute:
        raise ValueError("Station operation:status is unavailable")
    return str(attribute.Get() or "")


def _semantic_values(env):
    manager = getattr(env, "semantic_manager", None)
    states = getattr(manager, "full_semantic_states", {}) if manager is not None else {}
    predicates = getattr(manager, "full_semantic_predicates", {}) if manager is not None else {}
    result = {}
    for name, state in sorted(states.items()):
        if name not in {"env/PipetteVolume", "env/SyringeVolume"}:
            continue
        result[f"{name.split('/')[-1]}_uL"] = _scalar(
            getattr(state, "state"), name)
    for name, predicate in sorted(predicates.items()):
        short = name.split("/")[-1]
        if short not in {
                "TipOn", "SyringeOn", "FilterHeadOn", "SmallCapOpen", "BigCapOpen", "JawsOpen"}:
            continue
        value = predicate.get_value()
        result[short] = (
            {str(key): bool(item) for key, item in sorted(value.items())}
            if isinstance(value, Mapping) else bool(value)
        )
    return result


def _semantic_operation_commands(env):
    manager = getattr(env, "semantic_manager", None)
    predicates = getattr(manager, "full_semantic_predicates", {}) if manager is not None else {}
    station = predicates.get("env/StationOperation")
    history = getattr(station, "history", []) if station is not None else []
    if not isinstance(history, list):
        raise ValueError("StationOperation.history must be a list")
    return [_json_value(copy.deepcopy(value)) for value in history]


def _plc_vial_volumes(env):
    manager = getattr(env, "semantic_manager", None)
    states = getattr(manager, "full_semantic_states", {}) if manager is not None else {}
    vials = states.get("env/VialVolumes")
    values = getattr(vials, "volumes", {}) if vials is not None else {}
    if not isinstance(values, Mapping):
        raise ValueError("VialVolumes.volumes must be a mapping")
    return {
        str(name): _scalar(value, f"PLC vial volume {name}") / 1000.0
        for name, value in sorted(values.items(), key=lambda item: str(item[0]))
    }


def snapshot_semantics(env):
    """Return one JSON-safe, read-only snapshot of the station semantic state."""

    driver, operations = _station_operations(env)
    grips, attached = _grip_map(operations)
    history = getattr(operations, "history", None)
    if not isinstance(history, list):
        raise ValueError("Station operations.history must be a list")
    events = [_json_value(copy.deepcopy(value)) for value in history]
    commands = _semantic_operation_commands(env)
    return {
        "liquid_volumes_ml": _volume_map(operations),
        "plc_vial_volumes_ml": _plc_vial_volumes(env),
        "temperature_c": {
            "available": False,
            "value": None,
            "reason": "当前移液站导出未挂载温度语义",
        },
        "reaction_progress": {
            "available": False,
            "value": None,
            "reason": "当前移液站导出未提供转化率、产率或反应进度语义",
        },
        "device_state": {
            "semantic_state": _semantic_values(env),
            "operation_status": _operation_status(driver),
            "operation_event_count": len(history),
            "operation_events": events,
            "command_event_count": len(commands),
            "operation_commands": commands,
            "grips": grips,
            "attached": attached,
            "seated_tips": _seated_tips(operations),
            "cap_closed": _closed_caps(operations),
        },
    }


def _check(name, actual, expected, unit, passed):
    return {
        "name": name,
        "actual": actual,
        "expected": expected,
        "unit": unit,
        "passed": bool(passed),
    }


def _failure(workflow_name, message, check_name="输入"):
    return {
        "workflow_name": workflow_name,
        "passed": False,
        "checks": [_check(check_name, message, "有效运行快照", "", False)],
        "error": message,
    }


def _device(snapshot):
    value = snapshot.get("device_state")
    if not isinstance(value, Mapping):
        raise ValueError("device_state must be an object")
    return value


def _semantic_state(snapshot):
    value = _device(snapshot).get("semantic_state")
    if not isinstance(value, Mapping):
        raise ValueError("device_state.semantic_state must be an object")
    return value


def _operation_count(snapshot):
    value = _device(snapshot).get("operation_event_count")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("operation_event_count must be a non-negative integer")
    return value


def _command_count(snapshot):
    value = _device(snapshot).get("command_event_count")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("command_event_count must be a non-negative integer")
    return value


def _events(snapshot):
    value = _device(snapshot).get("operation_events", [])
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise ValueError("operation_events must be a list of objects")
    return value


def _commands(snapshot):
    value = _device(snapshot).get("operation_commands", [])
    if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
        raise ValueError("operation_commands must be a list of objects")
    return value


def _accepted_status(status):
    if status in ("", "idle"):
        return True
    if not isinstance(status, str):
        return False
    return all(
        part == "accepted" or part.partition(": ")[2] == "accepted"
        for part in status.split(" | ")
    )


def _rejection_count(history):
    rejected = []
    for index, snapshot in enumerate(history):
        try:
            status = _device(snapshot).get("operation_status", "")
        except (AttributeError, ValueError):
            return len(history) or 1, ["invalid device_state"]
        if not _accepted_status(status):
            rejected.append(f"history[{index}]={status}")
    return len(rejected), rejected


def _task_events(initial, final):
    baseline = _operation_count(initial)
    events = _events(final)
    if baseline > len(events):
        raise ValueError("Initial operation events exceed the readable final history")
    return events[baseline:]


def _task_commands(initial, final):
    baseline = _command_count(initial)
    commands = _commands(final)
    if baseline > len(commands):
        raise ValueError("Initial operation commands exceed the readable final history")
    return commands[baseline:]


def _event_observed(events, event, object_name):
    return any(
        item.get("event") == event and item.get("object") == object_name
        for item in events
    )


def _command_observed(commands, action, object_name):
    return any(
        item.get("action") == action and item.get("object") == object_name
        for item in commands
    )


def _kinematic_tip_mount_observed(initial, history):
    initial_events = _operation_count(initial)
    initial_commands = _command_count(initial)
    for snapshot in history:
        if (_operation_count(snapshot) <= initial_events
                or _command_count(snapshot) <= initial_commands):
            continue
        device = _device(snapshot)
        attached = device.get("attached")
        seated = device.get("seated_tips")
        semantic = _semantic_state(snapshot)
        if (isinstance(attached, Mapping)
                and attached.get(FIXED_TIP) is True
                and isinstance(seated, Mapping)
                and seated.get(FIXED_TIP) is True
                and semantic.get("TipOn") is True):
            return True, {"attached": True, "seated": True, "TipOn": True}
    return False, {"attached": False, "seated": False, "TipOn": False}


def _final_tip_detached(snapshot):
    device = _device(snapshot)
    attached = device.get("attached")
    seated = device.get("seated_tips")
    semantic = _semantic_state(snapshot)
    values = {
        "attached": (
            attached.get(FIXED_TIP, False) if isinstance(attached, Mapping) else None),
        "seated": (
            seated.get(FIXED_TIP, False) if isinstance(seated, Mapping) else None),
        "TipOn": semantic.get("TipOn"),
    }
    passed = (
        values["attached"] is False
        and values["seated"] is False
        and values["TipOn"] is False
    )
    return passed, values


def _current_task_snapshots(initial, history):
    initial_events = _operation_count(initial)
    initial_commands = _command_count(initial)
    return [
        snapshot for snapshot in history
        if (_operation_count(snapshot) > initial_events
            and _command_count(snapshot) > initial_commands)
    ]


def _semantic_transition_observed(initial, history, name, expected):
    return any(
        _semantic_state(snapshot).get(name) is expected
        for snapshot in _current_task_snapshots(initial, history)
    )


def _final_status(snapshot):
    status = _device(snapshot).get("operation_status")
    passed = status not in (None, "", "idle") and _accepted_status(status)
    return status, passed


def _common_history_checks(initial, history):
    checks = []
    rejected_count, rejected = _rejection_count(history)
    checks.append(_check(
        "操作历史无拒绝", rejected if rejected else 0, "0 条拒绝", "count",
        rejected_count == 0))
    counts = []
    try:
        initial_count = _operation_count(initial)
        counts = [_operation_count(snapshot) for snapshot in history]
        increased = bool(counts) and max(counts) > initial_count
    except (AttributeError, ValueError):
        initial_count = None
        increased = False
    checks.append(_check(
        "资产操作事件增长", max(counts) if counts else None,
        f"> {initial_count}" if initial_count is not None else "有可读初始计数",
        "count", increased))
    command_counts = []
    try:
        initial_commands = _command_count(initial)
        command_counts = [_command_count(snapshot) for snapshot in history]
        commands_increased = bool(command_counts) and max(command_counts) > initial_commands
    except (AttributeError, ValueError):
        initial_commands = None
        commands_increased = False
    checks.append(_check(
        "已发送命令增长", max(command_counts) if command_counts else None,
        f"> {initial_commands}" if initial_commands is not None else "有可读初始计数",
        "count", commands_increased))
    return checks


def _evaluate_take_drop(initial, final, history):
    checks = _common_history_checks(initial, history)
    try:
        events = _task_events(initial, final)
    except (AttributeError, ValueError) as exc:
        events = []
        checks.append(_check(
            "本次资产事件窗口", str(exc), "可读取本次操作前缀之后的事件", "", False))
    try:
        commands = _task_commands(initial, final)
    except (AttributeError, ValueError) as exc:
        commands = []
        checks.append(_check(
            "本次已发送命令窗口", str(exc), "可读取本次命令前缀之后的事件", "", False))
    observed = {
        "small_grasp": _event_observed(events, "grasp", SMALL_VIAL_CAP),
        "small_release": _event_observed(events, "release", SMALL_VIAL_CAP),
        "tip_grasp": _event_observed(events, "grasp", FIXED_TIP),
        "tip_release": _event_observed(events, "release", FIXED_TIP),
        "tip_eject_command": _command_observed(commands, "eject_tip", FIXED_TIP),
        "big_grasp": _event_observed(events, "grasp", BIG_BOTTLE_CAP),
        "big_release": _event_observed(events, "release", BIG_BOTTLE_CAP),
    }
    try:
        tip_mounted, mount_state = _kinematic_tip_mount_observed(initial, history)
    except (AttributeError, ValueError) as exc:
        tip_mounted, mount_state = False, str(exc)
    try:
        tip_detached, final_mount_state = _final_tip_detached(final)
        final_status = _device(final).get("operation_status", "")
    except (AttributeError, ValueError) as exc:
        tip_detached, final_mount_state = False, str(exc)
        final_status = None
    status_accepted = (
        final_status not in (None, "", "idle") and _accepted_status(final_status))
    checks.extend([
        _check(
            "小瓶盖抓取事件", observed["small_grasp"],
            f"grasp {SMALL_VIAL_CAP}", "", observed["small_grasp"]),
        _check(
            "小瓶盖释放事件", observed["small_release"],
            f"release {SMALL_VIAL_CAP}", "", observed["small_release"]),
        _check(
            "TIP 抓取事件", observed["tip_grasp"],
            f"grasp {FIXED_TIP}", "", observed["tip_grasp"]),
        _check(
            "运动学 TIP 装座语义", mount_state,
            "本次任务快照内 attached=true、seated=true、TipOn=true",
            "state", tip_mounted),
        _check(
            "运动学 TIP 抛头资产释放事件", observed["tip_release"],
            f"release {FIXED_TIP}", "", observed["tip_release"]),
        _check(
            "运动学 TIP eject_tip 命令", observed["tip_eject_command"],
            f"eject_tip {FIXED_TIP}", "", observed["tip_eject_command"]),
        _check(
            "运动学 TIP 抛头语义", final_mount_state,
            "最终 attached/seated 两个 map 均存在且对象键为 false 或缺失；TipOn=false",
            "state", tip_detached),
        _check(
            "最终 operation:status 接受", final_status,
            "accepted", "", status_accepted),
        _check(
            "大瓶盖抓取事件", observed["big_grasp"],
            f"grasp {BIG_BOTTLE_CAP}", "", observed["big_grasp"]),
        _check(
            "大瓶盖释放事件", observed["big_release"],
            f"release {BIG_BOTTLE_CAP}", "", observed["big_release"]),
        _check(
            "小瓶盖抓取命令",
            _command_observed(commands, "grasp", SMALL_VIAL_CAP),
            f"grasp {SMALL_VIAL_CAP}", "",
            _command_observed(commands, "grasp", SMALL_VIAL_CAP)),
        _check(
            "大瓶盖抓取命令",
            _command_observed(commands, "grasp", BIG_BOTTLE_CAP),
            f"grasp {BIG_BOTTLE_CAP}", "",
            _command_observed(commands, "grasp", BIG_BOTTLE_CAP)),
    ])
    try:
        device = _device(final)
        semantic = _semantic_state(final)
        grips = device.get("grips")
        caps = device.get("cap_closed")
        final_tip = semantic.get("TipOn")
        checks.extend([
            _check("最终夹持为空", sorted(grips) if isinstance(grips, Mapping) else grips,
                   [], "objects", isinstance(grips, Mapping) and not grips),
            _check("最终 TipOn", final_tip, False, "bool", final_tip is False),
            _check(
                "小瓶盖闭合", caps.get(SMALL_VIAL) if isinstance(caps, Mapping) else None,
                True, "bool", isinstance(caps, Mapping) and caps.get(SMALL_VIAL) is True),
            _check(
                "大瓶盖闭合", caps.get(BIG_BOTTLE) if isinstance(caps, Mapping) else None,
                True, "bool", isinstance(caps, Mapping) and caps.get(BIG_BOTTLE) is True),
        ])
    except (AttributeError, ValueError) as exc:
        checks.append(_check("最终状态", str(exc), "可读工作流终态", "", False))
    return checks


def _evaluate_small_vial(initial, final, history):
    checks = _common_history_checks(initial, history)
    try:
        events = _task_events(initial, final)
    except (AttributeError, ValueError) as exc:
        events = []
        checks.append(_check(
            "本次资产事件窗口", str(exc), "可读取本次操作前缀之后的事件", "", False))
    try:
        commands = _task_commands(initial, final)
    except (AttributeError, ValueError) as exc:
        commands = []
        checks.append(_check(
            "本次已发送命令窗口", str(exc), "可读取本次命令前缀之后的事件", "", False))
    detached = _event_observed(events, "cap_detached", SMALL_VIAL_CAP)
    engaged = _event_observed(events, "cap_engaged", SMALL_VIAL_CAP)
    grasp = _event_observed(events, "grasp", SMALL_VIAL_CAP)
    release = _event_observed(events, "release", SMALL_VIAL_CAP)
    checks.extend([
        _check(
            "小瓶盖抓取事件", grasp,
            f"grasp {SMALL_VIAL_CAP}", "", grasp),
        _check(
            "小瓶盖释放事件", release,
            f"release {SMALL_VIAL_CAP}", "", release),
        _check(
            "小瓶盖抓取命令",
            _command_observed(commands, "grasp", SMALL_VIAL_CAP),
            f"grasp {SMALL_VIAL_CAP}", "",
            _command_observed(commands, "grasp", SMALL_VIAL_CAP)),
        _check(
            "小瓶盖释放命令",
            _command_observed(commands, "release", SMALL_VIAL_CAP),
            f"release {SMALL_VIAL_CAP}", "",
            _command_observed(commands, "release", SMALL_VIAL_CAP)),
        _check(
            "小瓶盖旋开事件", detached, f"cap_detached {SMALL_VIAL_CAP}", "", detached),
        _check(
            "小瓶盖旋合事件", engaged, f"cap_engaged {SMALL_VIAL_CAP}", "", engaged),
    ])
    try:
        device = _device(final)
        semantic = _semantic_state(final)
        grips = device.get("grips")
        caps = device.get("cap_closed")
        open_state = semantic.get("SmallCapOpen")
        checks.extend([
            _check("最终 SmallCapOpen", open_state, False, "bool", open_state is False),
            _check("最终夹持为空", sorted(grips) if isinstance(grips, Mapping) else grips,
                   [], "objects", isinstance(grips, Mapping) and not grips),
            _check(
                "小瓶盖闭合", caps.get(SMALL_VIAL) if isinstance(caps, Mapping) else None,
                True, "bool", isinstance(caps, Mapping) and caps.get(SMALL_VIAL) is True),
        ])
    except (AttributeError, ValueError) as exc:
        checks.append(_check("最终状态", str(exc), "可读工作流终态", "", False))
    return checks


def _liquid(snapshot, name):
    values = snapshot.get("liquid_volumes_ml")
    if not isinstance(values, Mapping) or name not in values:
        raise ValueError(f"liquid volume is unknown: {name}")
    return _scalar(values[name], f"liquid volume {name}")


def _evaluate_filter_take_syringe(initial, final, history):
    checks = _common_history_checks(initial, history)
    try:
        events = _task_events(initial, final)
    except (AttributeError, ValueError) as exc:
        events = []
        checks.append(_check(
            "本次资产事件窗口", str(exc), "可读取本次操作前缀之后的事件", "", False))
    barrel = _event_observed(events, "grasp", SYRINGE_BARREL)
    piston = _event_observed(events, "grasp", SYRINGE_PISTON)
    checks.extend([
        _check("针筒筒身抓取事件", barrel, f"grasp {SYRINGE_BARREL}", "", barrel),
        _check("针筒活塞抓取事件", piston, f"grasp {SYRINGE_PISTON}", "", piston),
    ])
    try:
        device = _device(final)
        attached = device.get("attached")
        semantic = _semantic_state(final)
        barrel_attached = (
            attached.get(SYRINGE_BARREL, False) if isinstance(attached, Mapping) else None)
        piston_attached = (
            attached.get(SYRINGE_PISTON, False) if isinstance(attached, Mapping) else None)
        syringe_on = semantic.get("SyringeOn")
        status, status_passed = _final_status(final)
        checks.extend([
            _check("最终筒身 attached", barrel_attached, True, "bool",
                   barrel_attached is True),
            _check("最终活塞 attached", piston_attached, True, "bool",
                   piston_attached is True),
            _check("最终 SyringeOn", syringe_on, True, "bool", syringe_on is True),
            _check("最终 operation:status 接受", status, "accepted", "",
                   status_passed),
        ])
    except (AttributeError, ValueError) as exc:
        checks.append(_check("最终取针筒状态", str(exc), "可读最终语义", "", False))
    return checks


def _evaluate_big_bottle_decap_recap(initial, final, history):
    checks = _common_history_checks(initial, history)
    try:
        events = _task_events(initial, final)
    except (AttributeError, ValueError) as exc:
        events = []
        checks.append(_check(
            "本次资产事件窗口", str(exc), "可读取本次操作前缀之后的事件", "", False))
    observed = {
        "grasp": _event_observed(events, "grasp", BIG_BOTTLE_CAP),
        "release": _event_observed(events, "release", BIG_BOTTLE_CAP),
        "detached": _event_observed(events, "cap_detached", BIG_BOTTLE_CAP),
        "engaged": _event_observed(events, "cap_engaged", BIG_BOTTLE_CAP),
    }
    try:
        opened = _semantic_transition_observed(initial, history, "BigCapOpen", True)
    except (AttributeError, ValueError) as exc:
        opened = False
        checks.append(_check("本次 BigCapOpen 历史", str(exc), "历史可读", "", False))
    checks.extend([
        _check("大瓶盖抓取事件", observed["grasp"],
               f"grasp {BIG_BOTTLE_CAP}", "", observed["grasp"]),
        _check("大瓶盖释放事件", observed["release"],
               f"release {BIG_BOTTLE_CAP}", "", observed["release"]),
        _check("大瓶盖旋开事件", observed["detached"],
               f"cap_detached {BIG_BOTTLE_CAP}", "", observed["detached"]),
        _check("大瓶盖旋合事件", observed["engaged"],
               f"cap_engaged {BIG_BOTTLE_CAP}", "", observed["engaged"]),
        _check("本次 BigCapOpen 曾为 true", opened, True, "bool", opened),
    ])
    try:
        device = _device(final)
        semantic = _semantic_state(final)
        grips = device.get("grips")
        caps = device.get("cap_closed")
        big_open = semantic.get("BigCapOpen")
        closed = caps.get(BIG_BOTTLE) if isinstance(caps, Mapping) else None
        status, status_passed = _final_status(final)
        checks.extend([
            _check("最终 BigCapOpen", big_open, False, "bool", big_open is False),
            _check("最终瓶盖闭合", closed, True, "bool", closed is True),
            _check("最终夹持为空", sorted(grips) if isinstance(grips, Mapping) else grips,
                   [], "objects", isinstance(grips, Mapping) and not grips),
            _check("最终 operation:status 接受", status, "accepted", "",
                   status_passed),
        ])
    except (AttributeError, ValueError) as exc:
        checks.append(_check("最终大瓶盖状态", str(exc), "可读最终语义", "", False))
    return checks


def _evaluate_transfer(initial, final, parameters, history):
    checks = _common_history_checks(initial, history)
    try:
        if "volume_ul" in parameters:
            volume_ul = _scalar(parameters["volume_ul"], "volume_ul")
            volume = volume_ul / 1000.0
            checks.append(_check(
                "可配置移液体积", volume_ul, "20-180", "µL",
                MIN_TRANSFER_VOLUME_UL <= volume_ul <= MAX_TRANSFER_VOLUME_UL))
            if "target_min_volume_ml" in parameters:
                target_min = _scalar(
                    parameters["target_min_volume_ml"], "target_min_volume_ml")
                checks.append(_check(
                    "兼容最小液量参数", target_min, volume, "mL",
                    math.isclose(target_min, volume, rel_tol=0.0,
                                 abs_tol=VOLUME_TOLERANCE_ML)))
        else:
            volume = _scalar(parameters.get("target_min_volume_ml"), "target_min_volume_ml")
            checks.append(_check(
                "兼容固定目标液量参数", volume, 0.1, "mL",
                abs(volume - 0.1) <= VOLUME_TOLERANCE_ML))
    except (AttributeError, ValueError) as exc:
        checks.append(_check(
            "移液体积参数", str(exc), "20-180 µL", "µL", False))
        return checks
    try:
        source_initial = _liquid(initial, SOURCE_BOTTLE)
        source_final = _liquid(final, SOURCE_BOTTLE)
        target_initial = _liquid(initial, TARGET_BOTTLE)
        target_final = _liquid(final, TARGET_BOTTLE)
        required = volume - VOLUME_TOLERANCE_ML
        source_delta = source_initial - source_final
        target_delta = target_final - target_initial
        checks.extend([
            _check(
                "源瓶初始液量", source_initial, f">= {required}", "mL",
                source_initial + VOLUME_TOLERANCE_ML >= required),
            _check(
                "源瓶液量减少", source_delta, f"{volume} ± {TRANSFER_TOLERANCE_ML}", "mL",
                math.isclose(source_delta, volume, rel_tol=0.0,
                             abs_tol=TRANSFER_TOLERANCE_ML)),
            _check(
                "目标瓶最终液量", target_final, f">= {volume - TRANSFER_TOLERANCE_ML}", "mL",
                target_final + TRANSFER_TOLERANCE_ML >= volume),
            _check(
                "目标瓶液量增加", target_delta, f"{volume} ± {TRANSFER_TOLERANCE_ML}", "mL",
                math.isclose(target_delta, volume, rel_tol=0.0,
                             abs_tol=TRANSFER_TOLERANCE_ML)),
        ])
    except (AttributeError, ValueError) as exc:
        checks.append(_check("资产液量账本", str(exc), "源瓶和目标瓶数值均已知", "mL", False))
    return checks


SELF_TRANSFER_BOTTLE = TARGET_BOTTLE  # 中瓶区1
SELF_TRANSFER_ML = 0.1                 # fixed 100 uL in the exported workflow
SELF_TRANSFER_WORKFLOW = "pipette_zhongping1_tip2"
# Both exported pipetting workflows mount TIP slot 1; an ejected TIP never returns to its slot,
# so a second pipetting task in one session needs another slot. TIP objects per slot come from
# matterix_plc.executor.plan_macro("装小TIP头", index, resolve_operations=True).
TIP_BY_WORKFLOW = {
    "pipette_xiaoping_to_zhongping1": FIXED_TIP,          # slot 1
    SELF_TRANSFER_WORKFLOW: "_1mlTipBox_222_Tip_73",      # slot 2
}
# Workflows whose outcome depends on state carried over from earlier tasks in the session.
from .station_experiment import WORKFLOWS as FULL_WORKFLOWS
from .station_mixture import WORKFLOWS as MIXTURE_WORKFLOWS

PRECONDITION_WORKFLOWS = frozenset(TIP_BY_WORKFLOW) | frozenset(FULL_WORKFLOWS) | frozenset(MIXTURE_WORKFLOWS)


def _tip_keys(snapshot):
    values = snapshot.get("liquid_volumes_ml")
    return sorted(key for key in values if "_Tip_" in key) if isinstance(values, Mapping) else []


def precondition_checks(workflow_name, snapshot, parameters=None):
    """All checks that must hold before a run starts, passed or not."""
    from .station_skills import RULES, precondition_checks as skill_precondition_checks

    if workflow_name in RULES:
        return skill_precondition_checks(workflow_name, snapshot, parameters)
    if workflow_name in MIXTURE_WORKFLOWS:
        from .station_mixture import preconditions
        return preconditions(workflow_name, snapshot, parameters)
    if workflow_name in FULL_WORKFLOWS:
        from .station_experiment import preconditions
        return preconditions(workflow_name, snapshot, parameters)
    if workflow_name not in PRECONDITION_WORKFLOWS:
        return []
    checks = []
    tip = TIP_BY_WORKFLOW[workflow_name]
    try:
        if "operation_events" not in _device(snapshot):
            raise ValueError("operation_events missing; TIP use cannot be confirmed")
        used = _event_observed(_events(snapshot), "grasp", tip)
        checks.append(_check("TIP 槽位可用", "已用过" if used else "未使用",
                             f"{tip} 本会话未使用（否则先重置场景）", "", not used))
    except (AttributeError, ValueError) as exc:
        checks.append(_check("TIP 槽位可用", str(exc), f"{tip} 本会话未使用", "", False))
    if workflow_name == SELF_TRANSFER_WORKFLOW:
        try:
            volume = _liquid(snapshot, SELF_TRANSFER_BOTTLE)
            required_ml = _scalar(
                (parameters or {}).get("volume_ul", SELF_TRANSFER_ML * 1000),
                "volume_ul") / 1000.0
            checks.append(_check(
                "中瓶区1 执行前液量", volume, f">= {required_ml}（先执行从小瓶向中瓶移液）", "mL",
                volume + VOLUME_TOLERANCE_ML >= required_ml))
        except (AttributeError, ValueError) as exc:
            checks.append(_check("中瓶区1 执行前液量", str(exc), ">= 参数 volume_ul", "mL", False))
    return checks


def check_preconditions(workflow_name, snapshot, parameters=None):
    """Failed preconditions only; an empty list means the task may start."""
    return [
        item for item in precondition_checks(workflow_name, snapshot, parameters)
        if not item["passed"]
    ]


def _no_tip_mounted(snapshot):
    device = _device(snapshot)
    attached = device.get("attached")
    seated = device.get("seated_tips")
    if not isinstance(attached, Mapping) or not isinstance(seated, Mapping):
        return False, "attached/seated 不可读"
    mounted = sorted(key for key, value in {**attached, **seated}.items() if "_Tip_" in key and value)
    tip_on = _semantic_state(snapshot).get("TipOn")
    return not mounted and tip_on is False, {"mounted": mounted, "TipOn": tip_on}


def _evaluate_self_transfer(initial, final, parameters, history, extrema):
    """Same-bottle aspirate then dispense: the liquid must leave and fully return."""
    checks = _common_history_checks(initial, history)
    checks.extend(precondition_checks(SELF_TRANSFER_WORKFLOW, initial, parameters))
    tip = TIP_BY_WORKFLOW[SELF_TRANSFER_WORKFLOW]
    try:
        volume_ml = _scalar(parameters.get("volume_ul", SELF_TRANSFER_ML * 1000), "volume_ul") / 1000.0
        checks.append(_check("使用 2 号位 TIP", tip, f"grasp {tip}", "",
                             _event_observed(_task_events(initial, final), "grasp", tip)))
        start = _liquid(initial, SELF_TRANSFER_BOTTLE)
        end = _liquid(final, SELF_TRANSFER_BOTTLE)
        initial_volumes = initial["liquid_volumes_ml"]
        final_volumes = final["liquid_volumes_ml"]
        if set(initial_volumes) != set(final_volumes):
            raise ValueError("liquid ledger keys changed during the task")
        # Per-step extrema from the worker catch a short aspirate window that 0.5 s samples miss;
        # without them, fall back to the sampled history.
        if isinstance(extrema, Mapping) and SELF_TRANSFER_BOTTLE in extrema:
            lowest = _scalar(extrema[SELF_TRANSFER_BOTTLE][0], "bottle minimum")
            tip_peak = max((_scalar(extrema[key][1], "tip maximum")
                            for key in extrema if "_Tip_" in key), default=0.0)
        else:
            lowest = min(_liquid(item, SELF_TRANSFER_BOTTLE) for item in history)
            tip_peak = max((_scalar(item["liquid_volumes_ml"][key], "tip volume")
                            for item in history for key in _tip_keys(item)), default=0.0)
        others = [key for key in initial_volumes if key != SELF_TRANSFER_BOTTLE and "_Tip_" not in key]
        changed = [key for key in others
                   if abs(_scalar(final_volumes[key], key) - _scalar(initial_volumes[key], key))
                   > VOLUME_TOLERANCE_ML]
        total_initial = sum(_scalar(value, key) for key, value in initial_volumes.items())
        total_final = sum(_scalar(value, key) for key, value in final_volumes.items())
        tips_final = sum(_scalar(final_volumes[key], key) for key in _tip_keys(final))
        detached, tip_state = _no_tip_mounted(final)
        checks.extend([
            _check("中瓶区1 吸液时液量下降", start - lowest, f"{volume_ml} ± {TRANSFER_TOLERANCE_ML}", "mL",
                   math.isclose(start - lowest, volume_ml, rel_tol=0.0, abs_tol=TRANSFER_TOLERANCE_ML)),
            _check("TIP 内液量峰值", tip_peak, f"{volume_ml} ± {TRANSFER_TOLERANCE_ML}", "mL",
                   math.isclose(tip_peak, volume_ml, rel_tol=0.0, abs_tol=TRANSFER_TOLERANCE_ML)),
            _check("中瓶区1 最终液量回到初始", end - start, "0 ± 1e-6", "mL",
                   abs(end - start) <= VOLUME_TOLERANCE_ML),
            _check("账本总量守恒", total_final - total_initial, "0 ± 1e-6", "mL",
                   abs(total_final - total_initial) <= VOLUME_TOLERANCE_ML),
            _check("其他容器液量不变", changed, "[]", "objects", not changed),
            _check("TIP 内无残液", tips_final, "0", "mL", tips_final <= VOLUME_TOLERANCE_ML),
            _check("TIP 已弃置", tip_state, "无已装 TIP 且 TipOn=false", "state", detached),
        ])
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        checks.append(_check("资产液量账本", str(exc), "中瓶区1、TIP 与全部容器数值均已知", "mL", False))
    return checks


def evaluate_postconditions(workflow_name, initial, final, parameters, history, extrema=None):
    """Evaluate workflow semantics from snapshots without trusting a status flag alone."""

    from .station_skills import RULES, evaluate_postconditions as evaluate_skill

    if workflow_name in RULES:
        return evaluate_skill(workflow_name, initial, final, parameters, history, extrema)
    if workflow_name in MIXTURE_WORKFLOWS:
        from .station_mixture import evaluate
        return evaluate(workflow_name, initial, final, parameters, history, extrema)
    if workflow_name in FULL_WORKFLOWS:
        from .station_experiment import evaluate
        return evaluate(workflow_name, initial, final, history, extrema, parameters=parameters)

    if not isinstance(workflow_name, str):
        return _failure(str(workflow_name), "workflow_name must be a string", "workflow")
    if not isinstance(initial, Mapping) or not isinstance(final, Mapping):
        return _failure(workflow_name, "initial and final must be semantic snapshots", "快照")
    if not isinstance(parameters, Mapping):
        return _failure(workflow_name, "parameters must be an object", "参数")
    if not isinstance(history, list) or not all(isinstance(item, Mapping) for item in history):
        return _failure(workflow_name, "history must be a list of semantic snapshots", "历史")

    snapshots = [initial, *history, final]
    if workflow_name == "take_drop_tip_big_bottle":
        checks = _evaluate_take_drop(initial, final, snapshots)
    elif workflow_name == "prepare_small_vial":
        checks = _evaluate_small_vial(initial, final, snapshots)
    elif workflow_name == "pipette_xiaoping_to_zhongping1":
        checks = _evaluate_transfer(initial, final, parameters, snapshots)
    elif workflow_name == SELF_TRANSFER_WORKFLOW:
        checks = _evaluate_self_transfer(initial, final, parameters, snapshots, extrema)
    elif workflow_name == "filter_take_syringe":
        checks = _evaluate_filter_take_syringe(initial, final, snapshots)
    elif workflow_name == "big_bottle_decap_recap":
        checks = _evaluate_big_bottle_decap_recap(initial, final, snapshots)
    else:
        return _failure(workflow_name, "workflow is not enabled for execution", "workflow")
    return {
        "workflow_name": workflow_name,
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
    }
