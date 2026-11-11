"""Static check of a protocol's ordered pipetting-station steps against the bridge's own preconditions.

Only rules that hold for every run are checked, so a passing plan can still fail at runtime but a reported
error always would. State the plan cannot know (for example liquid moved by atomic skills) stops that check.
"""

# ruff: noqa: ANN201, ANN202, C901, PLR0912

import contextlib

from . import station_semantics as s
from .capabilities import catalog
from .common import PIPETTING_SCENE_ID
from .station_experiment import PHASES, TIP2, initial_state
from .station_experiment import WORKFLOWS as FULL_WORKFLOWS
from .station_mixture import WORKFLOWS as MIXTURE_WORKFLOWS
from .station_skills import (
    PIPETTE_ZONES,
    RULES,
    Unknown,
    GOALS,
    resolve_workflow,
    unmet_preconditions,
)

# Atomic skills (station_skills.RULES) carry their own preconditions and effects; the tables below describe the
# exported composite operations, which do not decompose into those skills yet.
PHASE_WORKFLOWS = {**FULL_WORKFLOWS, **MIXTURE_WORKFLOWS}
PHASE_LABELS = {index: label for index, (_, label) in enumerate(PHASES, 1)}
TIP_BY_WORKFLOW = {
    **s.TIP_BY_WORKFLOW,
    "full_station_transfer_small": s.FIXED_TIP,
    "full_station_transfer_big": TIP2,
    "mixture_stock": s.FIXED_TIP,
    "mixture_diluent": TIP2,
    "take_drop_tip_big_bottle": s.FIXED_TIP,
}
# Composites whose precondition rejects a TIP used earlier in the session; others just consume it
TIP_CHECKED = set(s.TIP_BY_WORKFLOW) | {"full_station_transfer_small", "full_station_transfer_big",
                                       "mixture_stock", "mixture_diluent"}
TARGET_INFLOW = {"pipette_xiaoping_to_zhongping1", "full_station_transfer_small", "mixture_stock", "mixture_diluent"}
# Pipette skills and the parameters naming a zone the pipette head must reach
PIPETTE_SKILLS = {"skill_aspirate_from": ("zone",), "skill_dispense_to": ("zone",),
                  "skill_pipette_transfer": ("source", "target")}
# Cap and container-hold state each composite leaves behind (station_worker._build_workflow); None where the
# export does not say. Only full_station_open_bottle ends still holding a container (pick + decap).
CAP_EFFECTS = {
    "full_station_open_bottle": {"BigCapOpen": True, s.BIG_BOTTLE_CAP: True},
    "full_station_transfer_big": {"BigCapOpen": False, s.BIG_BOTTLE_CAP: False},
    "mixture_diluent": {"BigCapOpen": False, s.BIG_BOTTLE_CAP: False},
    "big_bottle_decap_recap": {"BigCapOpen": False, s.BIG_BOTTLE_CAP: False},
    "full_station_prepare_vial": {"SmallCapOpen": False, s.SMALL_VIAL_CAP: False},
    "prepare_small_vial": {"SmallCapOpen": False, s.SMALL_VIAL_CAP: False},
    "take_drop_tip_big_bottle": {"BigCapOpen": None, "SmallCapOpen": None,
                                 s.BIG_BOTTLE_CAP: False, s.SMALL_VIAL_CAP: False},
}
CAP_CONTAINERS = {"BigCapOpen": s.BIG_BOTTLE, "SmallCapOpen": s.SMALL_VIAL}
HELD_LABELS = {s.SMALL_VIAL_CAP: "小瓶", s.BIG_BOTTLE_CAP: "大瓶"}
# Zones that start empty after a reset; the other source zones hold far more than one transfer
EMPTY_ZONES = ("中瓶区1", "小开盖区")
TIP_LABELS = {s.FIXED_TIP: "TIP 1 号位", TIP2: "TIP 2 号位"}
END_DEFAULT_PHASE = 7
SYRINGE_PARTS = (s.SYRINGE_BARREL, s.SYRINGE_PISTON)


class _PlanState:
    """What an ordered plan determines about the station; None is only known at runtime."""

    def __init__(self):
        self.values = {
            "TipOn": False, "PipetteVolume_uL": None, "tip_disposed": True,
            "SyringeOn": False, "FilterHeadOn": False, "SyringeVolume_uL": 0.0, "syringe_disposed": True,
            "BigCapOpen": False, "SmallCapOpen": False, s.BIG_BOTTLE_CAP: False, s.SMALL_VIAL_CAP: False,
        }
        self.zone_ml = dict.fromkeys(EMPTY_ZONES, 0.0)
        self.used_objects = set()

    def _known(self, name):
        value = self.values[name]
        if value is None:
            raise Unknown(name)
        return value

    # The predicate interface of station_skills.SnapshotState
    def semantic(self, name):
        return self._known(name)

    tool_ul = semantic

    def attached(self, name):
        return self._known("SyringeOn" if name in SYRINGE_PARTS else name)

    def cap_closed(self, container):
        return not self._known(next(cap for cap, body in CAP_CONTAINERS.items() if body == container))

    def liquid(self, zone):
        if self.zone_ml.get(zone) is None:
            raise Unknown(zone)
        return self.zone_ml[zone]

    def container_ml(self, name):
        raise Unknown(name)

    def used(self, *objects):
        return any(name in self.used_objects for name in objects)

    def apply(self, workflow, known, raw):
        """Apply a skill's declared effect."""
        effect = RULES[workflow]["effect"]
        self.values.update(effect.get("set", {}))
        if "use" in effect:
            with contextlib.suppress(Unknown):
                self.used_objects.update(effect["use"](known))
        if "transfer" in effect:
            source, target = effect["transfer"]
            volume = known.get("volume_ul")
            for end, sign in ((source, -1), (target, 1)):
                if end in self.values:
                    current = self.values[end]
                    self.values[end] = (None if current is None or volume is None
                                        else max(current + sign * volume, 0.0))
                else:
                    zone = raw.get(end, end)  # a zone parameter, or the fixed zone itself
                    if self.zone_ml.get(zone) is not None:
                        self.zone_ml[zone] = (None if volume is None
                                              else max(self.zone_ml[zone] + sign * volume / 1000.0, 0.0))


class _Known(dict):
    """Parameters whose value the plan already knows."""

    def __missing__(self, key):
        raise Unknown(key)


def _known_values(values, specs):
    known = _Known()
    for name, value in values.items():
        spec = specs.get(name, {})
        # eos_dynamic or a reference is only known at runtime; an out-of-range value is reported separately
        if spec.get("type") in ("number", "integer") and (
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not spec["min"] <= value <= spec["max"]):
            continue
        known[name] = value
    return known


def _operation(step, capabilities):
    """Return (workflow name, label, parameters, parameter specs) of a capability step, or None for other task types."""
    parameters = step.get("parameters") or {}
    capability = capabilities.get(parameters.get("capability_id"))
    if capability is None:
        return None
    values = {name: spec["default"] for name, spec in capability["parameters"].items()}
    nested = parameters.get("parameters")
    values.update(nested if isinstance(nested, dict) else parameters)
    return capability["workflow_name"], capability["label"], values, capability["parameters"]


def check_plan(scene_id, steps):
    """Check ordered steps ({task, type, parameters}) on one station; return [{task, message}] errors."""
    if scene_id != PIPETTING_SCENE_ID:
        return []
    capabilities = {item["id"]: item for item in catalog() if item["scene_id"] == scene_id}
    errors = []
    began = False
    phase, executed = 0, 0
    st = _PlanState()

    def error(step, message):
        errors.append({"task": step["task"], "message": message})

    for previous, step in zip([None, *steps], steps, strict=False):
        if not step.get("ordered", True):
            error(step, f"与同一工作站的上一步「{previous['task']}」之间没有依赖，执行顺序不确定；"
                        "请把它加入 dependencies")
        if step["type"] == "Matterix Begin Experiment":
            if began:
                error(step, "工作站已被本协议的实验占用；再次初始化前需要先结束实验")
            began, phase, executed = True, 0, 0
            st = _PlanState()
            continue
        if step["type"] == "Matterix End Experiment":
            expected = (step.get("parameters") or {}).get("expected_phase", END_DEFAULT_PHASE)
            if not began:
                error(step, "结束实验前没有对应的实验初始化")
            elif expected == 0 and executed == 0:
                error(step, "结束自定义实验要求之前至少执行一个移液站操作")
            elif isinstance(expected, int) and expected != 0 and phase is not None and phase != expected:
                error(step, f"结束检查要求完成 {expected} 个阶段，但此时只完成到阶段 {phase}")
            open_caps = sorted(name for name in CAP_CONTAINERS if st.values[name] is True)
            if open_caps:
                error(step, f"实验结束前仍有未关瓶盖：{'、'.join(open_caps)}")
            if st.values["TipOn"] is True:
                error(step, "实验结束前仍有 mounted TIP；先执行 station_skill_eject_tip")
            held = sorted(label for name, label in HELD_LABELS.items() if st.values[name] is True)
            if held:
                error(step, f"实验结束前仍有 held tools：{'、'.join(held)}")
            if st.values["SyringeOn"] or st.values["FilterHeadOn"]:
                error(step, "实验结束前仍有 mounted syringe/filter；先执行 station_skill_syringe_discard")
            if not st.values["tip_disposed"]:
                error(step, "used TIP 缺少 disposal evidence")
            if st.used(*SYRINGE_PARTS) and not st.values["syringe_disposed"]:
                error(step, "used syringe/filter 缺少 disposal evidence")
            began = False
            continue
        operation = _operation(step, capabilities)
        if operation is None:
            continue
        workflow, label, values, specs = operation
        for name, spec in specs.items():
            value = values.get(name)
            if (spec["type"] in ("number", "integer") and isinstance(value, (int, float))
                    and not isinstance(value, bool) and not spec["min"] <= value <= spec["max"]):
                error(step, f"「{label}」的{spec['label']} {value}{spec['unit']} 超出允许范围 "
                            f"{spec['min']}-{spec['max']}{spec['unit']}")
        if not began:
            error(step, f"「{label}」之前需要先执行实验初始化（Matterix Begin Experiment）")
            continue
        if workflow in GOALS:
            try:
                workflow, values = resolve_workflow(workflow, values)
            except ValueError as exc:
                error(step, str(exc))
                continue
            specs = next(item["parameters"] for item in capabilities.values() if item["workflow_name"] == workflow)
        executed += 1
        if workflow in PHASE_WORKFLOWS:
            required = PHASE_WORKFLOWS[workflow] - 1
            if phase is not None and phase != required:
                required_label = PHASE_LABELS.get(required, "无")
                error(step, f"「{label}」必须紧接在阶段 {required}（{required_label}）之后执行，"
                            f"此时已完成到阶段 {phase}")
            phase = PHASE_WORKFLOWS[workflow]
        else:
            phase = None  # only phase workflows are known to advance or keep the phase counter

        known = _known_values(values, specs)
        volume = values.get("volume_ul", 0)
        known_volume = not isinstance(volume, bool) and isinstance(volume, (int, float))
        if not known_volume:  # eos_dynamic or a reference is only known at runtime
            st.zone_ml, st.values["PipetteVolume_uL"] = dict.fromkeys(EMPTY_ZONES), None

        if workflow in RULES:
            for zone in (values.get(name) for name in PIPETTE_SKILLS.get(workflow, ())):
                if zone not in PIPETTE_ZONES:
                    error(step, f"「{label}」不能在{zone}操作：移液头只能对准 {'、'.join(PIPETTE_ZONES)} 的瓶子"
                                "（中瓶区2 是反应器，用 station_skill_syringe_aspirate 从它取液）")
            for message in unmet_preconditions(workflow, st, known, values):
                error(step, f"「{label}」{message}")
            st.apply(workflow, known, values)
            continue

        tip = TIP_BY_WORKFLOW.get(workflow)
        if tip is not None:
            if workflow in TIP_CHECKED and st.used(tip):
                error(step, f"「{label}」需要未用过的 {TIP_LABELS.get(tip, tip)}，"
                            "但本次实验中它已被用过（TIP 不能复用）")
            st.used_objects.add(tip)
            st.values["tip_disposed"] = True
            st.values["TipOn"], st.values["PipetteVolume_uL"] = False, None  # composites mount and eject their own TIP
        if workflow == "filter_take_syringe":
            if st.used(*SYRINGE_PARTS):
                error(step, f"「{label}」需要 fresh syringe，但本次实验的 syringe 已经 consumed；"
                            "同一实验不能 remount/reuse")
            else:
                st.used_objects.update(SYRINGE_PARTS)
                st.values.update({"SyringeOn": True, "SyringeVolume_uL": 0.0, "syringe_disposed": False})
        elif workflow == "full_station_syringe_transfer":
            st.used_objects.update(SYRINGE_PARTS)
            st.values.update({"SyringeOn": False, "FilterHeadOn": False, "syringe_disposed": True})

        target_ml = st.zone_ml["中瓶区1"]
        if known_volume and target_ml is not None:
            volume_ml = volume / 1000.0
            if workflow == s.SELF_TRANSFER_WORKFLOW and target_ml + s.VOLUME_TOLERANCE_ML < volume_ml:
                error(step, f"「{label}」需要中瓶区1 至少有 {volume:g} µL，但此时只有 {target_ml * 1000:g} µL"
                            "（中瓶区1 初始为空，先执行「从小瓶向中瓶移液」）")
            if workflow == "mixture_stock" and target_ml > s.VOLUME_TOLERANCE_ML:
                error(step, f"「{label}」要求中瓶区1 为空，但此时已有 {target_ml * 1000:g} µL")
            if workflow in TARGET_INFLOW:
                st.zone_ml["中瓶区1"] = target_ml + volume_ml
        st.values.update(CAP_EFFECTS.get(workflow, {}))
    return errors


def scene_notes(scene_id):
    """Facts a protocol author (or an AI drafter) needs to order station operations, derived from the rules above."""
    if scene_id != PIPETTING_SCENE_ID:
        return []
    by_workflow = {item["workflow_name"]: item["id"] for item in catalog() if item["scene_id"] == scene_id}
    liquids = "、".join(f"{name} {volume:g} mL" for name, volume in initial_state()["initial_liquid_ml"].items())
    tips = "；".join(
        f"{TIP_LABELS[tip]}：" + "、".join(by_workflow[w] for w, t in TIP_BY_WORKFLOW.items() if t == tip)
        for tip in TIP_LABELS
    )
    phases = "；".join(
        f"阶段 {index}：" + " 或 ".join(by_workflow[w] for w, p in PHASE_WORKFLOWS.items() if p == index)
        for index in sorted(set(PHASE_WORKFLOWS.values()))
    )
    return [
        (
            "推荐使用按对象和工具选择的原子技能：station_skill_pick/place/decap/recap、"
            "station_skill_mount/aspirate/dispense/discard。完整转移用 station_transfer_liquid；"
            "它包含耗材装载、吸液、吐液和弃置，不能当作一次吸液。"
            "对象名表示固定的实际容器，10 mL 转移瓶也接收针筒过滤液；50 mL 接收瓶初始为空。"
            "针筒机构仅支持八位反应器吸液、10 mL 转移瓶吐液，且必须先吸液、再装过滤头、再吐液。"
        ),
        (
            "先执行 Matterix Begin Experiment（重置场景并独占工作站），最后执行 Matterix End Experiment 释放工作站；"
            "移液站操作只能在两者之间执行，并且都要用依赖串成一条顺序链。"
        ),
        (
            f"初始化时核验的液量：{liquids}。中瓶区1（{s.TARGET_BOTTLE}）和 2 mL 小瓶初始为空；"
            "500 mL 大瓶在大瓶区，有液体但盖着。大开盖区和小开盖区是空的开盖工位，没有自己的容器。"
        ),
        (
            "原子技能吸液/吐液前要先装 TIP（station_skill_mount_tip），吐液量不能超过此前吸入 TIP 的量。"
            "在大开盖区吸吐前，要先把大瓶取到大开盖区并开盖（station_skill_big_bottle_pick、"
            "station_skill_big_bottle_decap）；在小开盖区吸吐前，要先把小瓶取到小开盖区并开盖"
            "（station_skill_small_vial_pick、station_skill_small_vial_decap）。"
            "中瓶区2 是八位反应器，移液头吸不到，要从它取液请用针筒（station_skill_syringe_aspirate）。"
        ),
        f"{by_workflow[s.SELF_TRANSFER_WORKFLOW]} 在中瓶区1 内自吸自吐，要求中瓶区1 已有不少于 volume_ul 的液体；"
        f"能向中瓶区1 加液的是 " + "、".join(by_workflow[w] for w in sorted(TARGET_INFLOW)) + "。",
        f"每个 TIP 槽位在一次实验中只能用一次。{tips}。",
        f"阶段操作必须从阶段 1 起逐个连续执行：{phases}。{by_workflow['mixture_stock']} 还要求中瓶区1 为空。",
        "Matterix End Experiment 的 expected_phase 填最后完成的阶段号；没有执行阶段操作时填 0。",
    ]
