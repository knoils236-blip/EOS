"""Contracts for the export's seven phases (MATTERIX Fig. 4 / hierarchical skills)."""

# ruff: noqa: PLC0415 -- legacy checks import this module

import math
from pathlib import Path

from .common import read_json

INITIAL_STATE_PATH = Path(__file__).with_name("station_initial_state.json")
PHASES = (
    ("open_door", "开门"),
    ("prepare_vial", "小瓶取放与开盖合盖"),
    ("transfer_small", "TIP：小瓶吸液至中瓶吐液"),
    ("open_bottle", "大瓶取瓶与开盖"),
    ("transfer_big", "TIP：大瓶吸吐与合盖归位"),
    ("syringe_transfer", "注射器：使用注射器移液 50 µL"),
    ("close_door", "关门"),
)
WORKFLOWS = {f"full_station_{name}": index for index, (name, _) in enumerate(PHASES, 1)}
REACTOR = "EightPositionReactor_662"
TIP2 = "_1mlTipBox_222_Tip_73"
OPEN_DOOR, PREPARE_VIAL, TRANSFER_SMALL, OPEN_BOTTLE, TRANSFER_BIG, SYRINGE_TRANSFER, CLOSE_DOOR = range(1, 8)
PIPETTE_UL = 100.0
SYRINGE_UL = 50.0
SYRINGE_GOAL_TOLERANCE_UL = 5.0  # ProgramStep uses syringe_stroke_for_volume(5.0) for arrival.
IMMERSION_ML = 52.9
JOINT_TOLERANCE = 0.001
DOOR_TOLERANCE_M = 0.0015
DOOR_TRAVEL_M = 0.38


def initial_state() -> dict:
    value = read_json(INITIAL_STATE_PATH)
    if value["schema_version"] != 1 or value["source"] != "simulation_fixture":
        raise ValueError("Unsupported station initial-state configuration")
    for name, volume in value["initial_liquid_ml"].items():
        if isinstance(volume, bool) or not math.isfinite(volume) or volume < 0:
            raise ValueError(f"Invalid initial volume for {name}")
    if (
        value["tip_indices"] != [1, 2]
        or value["pipette_volume_ul"] != PIPETTE_UL
        or value["syringe_volume_ul"] != SYRINGE_UL
        or value["syringe_goal_tolerance_ul"] != SYRINGE_GOAL_TOLERANCE_UL
    ):
        raise ValueError("Only the fixed 100/50 uL station fixture is supported")
    return value


def _volume_ul(workflow, parameters=None) -> float:
    from . import station_semantics as s

    default = SYRINGE_UL if workflow == "full_station_syringe_transfer" else PIPETTE_UL
    actual = s._scalar((parameters or {}).get("volume_ul", default), "volume_ul")
    if not s.MIN_TRANSFER_VOLUME_UL <= actual <= s.MAX_TRANSFER_VOLUME_UL:
        raise ValueError(
            f"volume_ul must be between {s.MIN_TRANSFER_VOLUME_UL:g} and {s.MAX_TRANSFER_VOLUME_UL:g}"
        )
    return actual


def preconditions(workflow, snapshot, parameters=None) -> list[dict]:
    from . import station_semantics as s

    phase = WORKFLOWS[workflow]
    volume_ul = _volume_ul(workflow, parameters)
    volume_ml = volume_ul / 1000.0
    actual = snapshot.get("experiment_phase")
    checks = [s._check("连续阶段", actual, phase - 1, "phase", actual == phase - 1)]
    if phase in (TRANSFER_SMALL, TRANSFER_BIG, SYRINGE_TRANSFER):
        checks.append(
            s._check(
                "可配置移液体积",
                volume_ul,
                f"{s.MIN_TRANSFER_VOLUME_UL:g}-{s.MAX_TRANSFER_VOLUME_UL:g}",
                "µL",
                s.MIN_TRANSFER_VOLUME_UL <= volume_ul <= s.MAX_TRANSFER_VOLUME_UL,
            )
        )
    if phase in (TRANSFER_SMALL, TRANSFER_BIG):
        tip = s.FIXED_TIP if phase == TRANSFER_SMALL else TIP2
        used = s._event_observed(s._events(snapshot), "grasp", tip)
        checks.append(s._check("TIP 未使用", used, False, "bool", not used))
        source = s.SOURCE_BOTTLE if phase == TRANSFER_SMALL else s.BIG_BOTTLE
        volume = s._liquid(snapshot, source)
        checks.append(
            s._check(
                "吸液源液量充足",
                volume,
                f">= {volume_ml:g}",
                "mL",
                volume + s.VOLUME_TOLERANCE_ML >= volume_ml,
            )
        )
    if phase == TRANSFER_BIG:
        opened = s._semantic_state(snapshot).get("BigCapOpen")
        checks.append(s._check("大瓶已开盖", opened, True, "bool", opened is True))
    if phase == SYRINGE_TRANSFER:
        volume = s._liquid(snapshot, REACTOR)
        checks.extend(
            [
                s._check("反应器初始液量", volume, ">= 52.9（针尖浸没）", "mL", volume >= IMMERSION_ML),
                s._check(
                    "反应器可吸液量",
                    volume,
                    f">= {volume_ml:g}",
                    "mL",
                    volume + s.VOLUME_TOLERANCE_ML >= volume_ml,
                ),
            ]
        )
    return checks


def initial_checks(snapshot) -> dict:
    from . import station_semantics as s

    config = initial_state()
    checks = []
    for name, expected in config["initial_liquid_ml"].items():
        actual = s._liquid(snapshot, name)
        checks.append(s._check(name, actual, expected, "mL", abs(actual - expected) <= s.VOLUME_TOLERANCE_ML))
    checks.append(
        s._check(
            "目标瓶为空",
            s._liquid(snapshot, s.TARGET_BOTTLE),
            0,
            "mL",
            abs(s._liquid(snapshot, s.TARGET_BOTTLE)) <= s.VOLUME_TOLERANCE_ML,
        )
    )
    device = s._device(snapshot)
    checks.append(
        s._check("命令状态已复位", device["operation_status"], "idle", "state", device["operation_status"] == "idle")
    )
    checks.extend(
        [
            s._check("阶段已清零", snapshot.get("experiment_phase"), 0, "phase", snapshot.get("experiment_phase") == 0),
            s._check("夹持为空", device["grips"], {}, "state", not device["grips"]),
            s._check("操作历史清空", device["operation_event_count"], 0, "count", device["operation_event_count"] == 0),
        ]
    )
    joints = device["joint_positions"]
    checks.append(
        s._check(
            "初始关节归零",
            joints,
            "all zero ± 0.001",
            "SI",
            bool(joints) and all(abs(v) <= JOINT_TOLERANCE for v in joints.values()),
        )
    )
    for key in (s.SMALL_VIAL, s.BIG_BOTTLE):
        closed = device["cap_closed"][key]
        checks.append(s._check("初始瓶盖闭合 " + key, closed, True, "bool", closed is True))
    for tip in (s.FIXED_TIP, TIP2):
        mounted = device["attached"].get(tip, False) or device["seated_tips"].get(tip, False)
        checks.append(s._check("初始 TIP 未装载 " + tip, mounted, False, "bool", not mounted))
    return {"passed": all(c["passed"] for c in checks), "checks": checks}


def evaluate(workflow, initial, final, history, extrema, parameters=None) -> dict:
    from . import station_semantics as s

    phase = WORKFLOWS[workflow]
    volume_ul = _volume_ul(workflow, parameters)
    volume_ml = volume_ul / 1000.0
    snapshots = [initial, *history, final]
    checks = preconditions(workflow, initial, parameters)
    if phase in (OPEN_DOOR, CLOSE_DOOR):
        start = s._device(initial)["joint_positions"]["front_door"]
        end = s._device(final)["joint_positions"]["front_door"]
        target = 0.39 if phase == OPEN_DOOR else 0.0
        checks.extend(
            [
                s._check("门实际到位", end, target, "m", abs(end - target) <= DOOR_TOLERANCE_M),
                s._check("门实际运动", abs(end - start), "> DOOR_TRAVEL_M", "m", abs(end - start) > DOOR_TRAVEL_M),
            ]
        )
    elif phase == PREPARE_VIAL:
        checks.extend(s._evaluate_small_vial(initial, final, snapshots))
    elif phase == TRANSFER_SMALL:
        checks.extend(s._evaluate_transfer(initial, final, {"volume_ul": volume_ul}, snapshots))
        checks.extend(
            _liquid_checks(initial, final, extrema, s.SOURCE_BOTTLE, s.TARGET_BOTTLE, s.FIXED_TIP, volume_ml)
        )
    elif phase == OPEN_BOTTLE:
        checks.extend(s._common_history_checks(initial, snapshots))
        events = s._task_events(initial, final)
        for event in ("grasp", "cap_detached"):
            found = s._event_observed(events, event, s.BIG_BOTTLE_CAP)
            checks.append(s._check("大瓶盖 " + event, found, True, "bool", found))
        device = s._device(final)
        opened = s._semantic_state(final).get("BigCapOpen")
        checks.extend(
            [
                s._check("大瓶保持开盖", opened, True, "bool", opened is True),
                s._check(
                    "大瓶盖仍被夹持",
                    device["grips"].get(s.BIG_BOTTLE_CAP),
                    "held",
                    "state",
                    s.BIG_BOTTLE_CAP in device["grips"],
                ),
            ]
        )
    elif phase == TRANSFER_BIG:
        checks.extend(s._common_history_checks(initial, snapshots))
        checks.extend(_liquid_checks(initial, final, extrema, s.BIG_BOTTLE, s.BIG_BOTTLE, TIP2, volume_ml))
        events = s._task_events(initial, final)
        for event in ("cap_engaged", "release"):
            found = s._event_observed(events, event, s.BIG_BOTTLE_CAP)
            checks.append(s._check("大瓶盖 " + event, found, True, "bool", found))
        closed = s._device(final)["cap_closed"][s.BIG_BOTTLE]
        checks.append(s._check("大瓶最终闭合", closed, True, "bool", closed is True))
    elif phase == SYRINGE_TRANSFER:
        checks.extend(s._common_history_checks(initial, snapshots))
        transferred = initial["liquid_volumes_ml"][REACTOR] - final["liquid_volumes_ml"][REACTOR]
        checks.append(
            s._check(
                "针筒体积目标（沿用控制器容差）",
                transferred * 1000,
                f"{volume_ul:g} ± {SYRINGE_GOAL_TOLERANCE_UL:g}",
                "uL",
                abs(transferred * 1000 - volume_ul) <= SYRINGE_GOAL_TOLERANCE_UL,
            )
        )
        checks.extend(_liquid_checks(initial, final, extrema, REACTOR, s.SOURCE_BOTTLE, s.SYRINGE_BARREL, transferred))
        events = s._task_events(initial, final)
        installed = any(e.get("event") == "filter_installed" for e in events)
        checks.append(s._check("过滤头已安装", installed, True, "bool", installed))
        for obj in (s.SYRINGE_BARREL, s.SYRINGE_PISTON):
            for event in ("grasp", "release"):
                found = s._event_observed(events, event, obj)
                checks.append(s._check(obj + " " + event, found, True, "bool", found))
    if phase in (TRANSFER_BIG, SYRINGE_TRANSFER, CLOSE_DOOR):
        grips = s._device(final)["grips"]
        installed_filters = {
            e["object"]
            for e in s._events(final)
            if e.get("event") == "filter_installed" and e.get("barrel") == s.SYRINGE_BARREL
        }
        # The discarded filter stays connected to its barrel, not to an end effector.
        tool_grips = {
            name: path
            for name, path in grips.items()
            if name not in installed_filters or not path.endswith("/FilterMount_" + name)
        }
        checks.append(s._check("最终工具夹持为空", tool_grips, {}, "state", not tool_grips))
    rejected, detail = s._rejection_count(snapshots)
    checks.append(s._check("操作无拒绝", detail, [], "events", rejected == 0))
    for name, limit in (("air_ml", s.AIR_LIMIT_ML), ("spilled_ml", s.VOLUME_TOLERANCE_ML)):
        amount = final["liquid_balance"][name] - initial["liquid_balance"][name]
        checks.append(s._check(name, amount, 0.0, "mL", abs(amount) <= limit))
    return {"workflow_name": workflow, "passed": all(c["passed"] for c in checks), "checks": checks}


def _liquid_checks(initial, final, extrema, source, target, tool, volume) -> list[dict]:
    from . import station_semantics as s

    before, after = initial["liquid_volumes_ml"], final["liquid_volumes_ml"]
    checks = []
    expected = dict.fromkeys(before, 0.0)
    expected[source] -= volume
    expected[target] += volume
    for name, delta in expected.items():
        actual = after[name] - before[name]
        tolerance = s.TRANSFER_TOLERANCE_ML if name in (source, target) else s.VOLUME_TOLERANCE_ML
        if abs(actual - delta) > tolerance:
            checks.append(s._check("容器液量变化 " + name, actual, delta, "mL", False))
    checks.append(s._check("容器集合不变", sorted(after), sorted(before), "objects", set(after) == set(before)))
    for label, actual, wanted, tolerance in (
        ("源容器中途液量下降", before[source] - extrema[source][0], volume, s.TRANSFER_TOLERANCE_ML),
        ("工具内液量峰值", extrema[tool][1], volume, s.TRANSFER_TOLERANCE_ML),
        ("工具最终无残液", after[tool], 0.0, s.VOLUME_TOLERANCE_ML),
        ("液量总量守恒", sum(after.values()) - sum(before.values()), 0.0, s.VOLUME_TOLERANCE_ML),
    ):
        checks.append(s._check(label, actual, wanted, "mL", abs(actual - wanted) <= tolerance))
    events = s._task_events(initial, final)
    for event in ("grasp", "release"):
        found = s._event_observed(events, event, tool)
        checks.append(s._check("工具 " + event, found, True, "bool", found))
    return checks
