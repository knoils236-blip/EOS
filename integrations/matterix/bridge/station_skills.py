"""Atomic station skills backed by the exported Matterix action constructors."""

# ruff: noqa: ANN201, ANN202

from __future__ import annotations

import copy
import dataclasses
import functools
import hashlib
import importlib
import math
from collections.abc import Mapping
from pathlib import Path

from . import station_semantics as semantics
from .common import read_json

SCENE_ID = "pipetting_realtime"
WORKFLOW_YAML = "station_catalog.json"
MATTERIX_ROOT = Path("/home/ry/projects/Matterix/PipettingStation_20261031")
SKILLS_SOURCE = MATTERIX_ROOT / "source/matterix_plc/matterix_plc/actions/skills.py"
WORKFLOWS_SOURCE = MATTERIX_ROOT / "source/matterix_plc/matterix_plc/actions/workflows.py"
# Per-workflow kinematic acceptance of the registered workflows (scene reset between them)
REGISTERED_EVIDENCE = MATTERIX_ROOT / "out/workflow_validation_20261031/registered.json"
PROGRAM_SOURCE = MATTERIX_ROOT / "source/matterix_plc/matterix_plc/actions/program_action.py"

MIN_VOLUME_UL = 20.0
MAX_VOLUME_UL = 180.0
TIP_BY_INDEX = {
    1: semantics.FIXED_TIP,
    2: "_1mlTipBox_222_Tip_73",
}
# The export's zones minus 中瓶区2: it holds the reactor, which the pipette planner never targets (it looks for a
# bottle), so pipetting there would move without moving liquid. The syringe skill reaches the reactor instead.
PIPETTE_ZONES = ("中瓶区1", "大开盖区", "大瓶区", "小开盖区", "小瓶区")
UNREACHABLE_PIPETTE_ZONES = ("中瓶区2",)
ZONE_LIQUID_OBJECTS = {
    "小瓶区": semantics.SOURCE_BOTTLE,
    "中瓶区1": semantics.TARGET_BOTTLE,
    "中瓶区2": "EightPositionReactor_662",
    "大瓶区": semantics.BIG_BOTTLE,
    "大开盖区": semantics.BIG_BOTTLE,
    "小开盖区": semantics.SMALL_VIAL,
}
# The decap stations hold no container of their own (their consumables were removed from the scene); the bottle
# or vial must be carried there and decapped first. In its home zone the 500 mL bottle stays capped until then.
CAPPED_ZONES = {"大开盖区": "BigCapOpen", "大瓶区": "BigCapOpen", "小开盖区": "SmallCapOpen"}
CAPPED_ZONE_NEEDS = {
    "大开盖区": "大开盖区本身没有容器，需要先把 500 mL 大瓶取到这里并开盖",
    "大瓶区": "500 mL 大瓶在大瓶区是盖着的，需要先开盖",
    "小开盖区": "小开盖区本身没有容器，需要先把 2 mL 小瓶取到这里并开盖",
}
SMALL_CAP_HOME_TOLERANCE_M = 0.001


def _number(default, minimum, maximum, unit, label):
    return {
        "type": "number",
        "default": default,
        "min": minimum,
        "max": maximum,
        "unit": unit,
        "label": label,
    }


def _integer(default, minimum, maximum, choices, label):
    return {
        "type": "integer",
        "default": default,
        "min": minimum,
        "max": maximum,
        "choices": list(choices),
        "unit": "",
        "label": label,
    }


def _enum(default, choices, label):
    return {
        "type": "string",
        "default": default,
        "choices": list(choices),
        "min": None,
        "max": None,
        "unit": "",
        "label": label,
    }


TIMEOUT = _number(120, 1, 180, "s", "仿真超时")
HOLD = _number(0.5, 0, 5, "s", "动作末尾停顿")
DWELL = _number(1.5, 0.5, 5, "s", "TIP 脱开等待")
VOLUME = _number(50, MIN_VOLUME_UL, MAX_VOLUME_UL, "µL", "吸/吐体积")
DURATION = _number(1, 0.1, 30, "s", "等待时长")
SLOT_INDEX = _integer(1, 1, 1, (1,), "固定槽位")
TIP_INDEX = _integer(1, 1, 2, TIP_BY_INDEX, "TIP 槽位")
DOOR_OPENING = _number(0.39, 0.0, 0.39, "m", "前门开度")  # FrontDoorCfg default; workflows use 0.39 and 0


def _parameters(extra=None):
    return {
        "timeout_s": dict(TIMEOUT),
        **(extra or {}),
        "record_video": {
            "type": "boolean",
            "default": False,
            "min": None,
            "max": None,
            "unit": "",
            "label": "录制仿真视频",
        },
        "hide_shell": {
            "type": "boolean",
            "default": False,
            "min": None,
            "max": None,
            "unit": "",
            "label": "录制时隐藏外壳",
        },
    }


def _skill(
    workflow_name,
    label,
    constructor,
    description,
    *,
    extra_parameters=None,
    fixed_parameters=None,
    observations=None,
    success_criteria,
    tier="skill",
    source_workflow=False,
):
    """`source_workflow`: run `matterix_plc.actions.workflows.WORKFLOWS[workflow_name]` as exported."""
    return {
        "id": f"station_{workflow_name}",
        "tier": tier,
        "source_workflow": source_workflow,
        "workflow_name": workflow_name,
        "label": label,
        "constructor": constructor,
        "description": description,
        "parameters": _parameters(extra_parameters),
        "fixed_parameters": dict(fixed_parameters or {}),
        "observations": list(
            observations
            or [
                "device_state.joint_positions",
                "device_state.semantic_state",
                "device_state.operation_status",
                "device_state.operation_events",
                "device_state.operation_commands",
            ]
        ),
        "success_criteria": list(success_criteria),
    }


SKILLS = (
    _skill(
        "skill_mount_tip",
        "装载 TIP",
        "MountTipCfg",
        "从已验证的 TIP 1 或 2 号位装载一次性 TIP",
        extra_parameters={
            "tip_index": dict(TIP_INDEX),
            "hold_s": dict(HOLD),
        },
        success_criteria=[
            "本次任务出现所选 TIP 的真实 grasp/tip_seated 记录",
            "最终 TipOn=true 且该 TIP attached、seated",
        ],
    ),
    _skill(
        "skill_eject_tip",
        "卸载 TIP",
        "EjectTipCfg",
        "卸载当前已装载的 TIP",
        extra_parameters={"dwell_s": dict(DWELL)},
        fixed_parameters={"drop_target": "TIP 固定弃置位"},
        success_criteria=[
            "本次任务出现 eject_tip 命令和当前已装 TIP 的 release 事件",
            "最终 TipOn=false 且没有 attached/seated TIP",
        ],
    ),
    _skill(
        "skill_aspirate_from",
        "从指定区吸液",
        "AspirateFromCfg",
        "使用已装载 TIP 从当前真实区 1 号位吸液",
        extra_parameters={
            "index": dict(SLOT_INDEX),
            "zone": _enum("中瓶区1", PIPETTE_ZONES, "吸液区"),
            "volume_ul": dict(VOLUME),
            "hold_s": dict(HOLD),
        },
        success_criteria=["本次任务出现 aspirate 命令", "所选源容器液量下降、TIP 液量上升且总量守恒"],
    ),
    _skill(
        "skill_dispense_to",
        "向指定区吐液",
        "DispenseToCfg",
        "使用已装载 TIP 向当前真实区 1 号位吐液",
        extra_parameters={
            "index": dict(SLOT_INDEX),
            "zone": _enum("中瓶区1", PIPETTE_ZONES, "吐液区"),
            "volume_ul": dict(VOLUME),
            "hold_s": dict(HOLD),
        },
        success_criteria=["本次任务出现 dispense 命令", "TIP 液量下降、目标容器液量上升且总量守恒"],
    ),
    _skill(
        "skill_small_vial_pick",
        "夹取 2 mL 小瓶",
        "SmallVialPickCfg",
        "从出样区 1 号位夹取并抬起 2 mL 小瓶",
        extra_parameters={"index": dict(SLOT_INDEX), "hold_s": dict(HOLD)},
        fixed_parameters={"safe_lift": True},
        success_criteria=["本次任务出现小瓶盖 grasp 事件", "最终小瓶盖 attached 且小瓶盖爪闭合"],
    ),
    _skill(
        "skill_small_vial_place",
        "放回 2 mL 小瓶",
        "SmallVialPlaceCfg",
        "将夹持中的 2 mL 小瓶放回出样区 1 号位",
        extra_parameters={"index": dict(SLOT_INDEX), "hold_s": dict(HOLD)},
        fixed_parameters={"safe_lift": True},
        success_criteria=["本次任务出现小瓶盖 release 事件", "最终小瓶盖不再 attached 且小瓶盖爪张开"],
    ),
    _skill(
        "skill_small_vial_decap",
        "打开 2 mL 小瓶盖",
        "SmallVialDecapCfg",
        "夹持小瓶时执行真实小开盖程序",
        extra_parameters={"hold_s": dict(HOLD)},
        fixed_parameters={"safe_lift": True},
        success_criteria=["本次任务出现 cap_detached 事件", "本次状态进入 SmallCapOpen=true 且最终保持打开"],
    ),
    _skill(
        "skill_small_vial_recap",
        "关闭 2 mL 小瓶盖",
        "SmallVialRecapCfg",
        "将已旋开的小瓶盖旋回并重新夹住小瓶",
        extra_parameters={"hold_s": dict(HOLD)},
        fixed_parameters={"safe_lift": True},
        success_criteria=["本次任务出现 cap_engaged 事件", "本次状态回到 SmallCapOpen=false 且瓶盖闭合"],
    ),
    _skill(
        "skill_small_cap_home",
        "小瓶盖头回静息位",
        "SmallCapHomeCfg",
        "将小瓶盖升降轴收回到真实 PLC 静息目标 0",
        observations=["device_state.joint_positions", "device_state.operation_status"],
        success_criteria=["本次任务 small_cap_lift 实际向 0 移动", "最终 small_cap_lift 位于 0 附近"],
    ),
    _skill(
        "skill_big_bottle_pick",
        "夹取 500 mL 大瓶",
        "BigBottlePickCfg",
        "从大瓶区 1 号位夹取并抬起 500 mL 瓶",
        extra_parameters={"index": dict(SLOT_INDEX), "hold_s": dict(HOLD)},
        fixed_parameters={"safe_lift": True, "jaws": {"big_cap_grip": 0.012, "pg1": 0.018}},
        success_criteria=["本次任务出现大瓶盖 grasp 事件", "最终大瓶盖 attached 且相关夹爪闭合"],
    ),
    _skill(
        "skill_big_bottle_place",
        "放回 500 mL 大瓶",
        "BigBottlePlaceCfg",
        "将夹持中的 500 mL 瓶放回大瓶区 1 号位",
        extra_parameters={"index": dict(SLOT_INDEX), "hold_s": dict(HOLD)},
        fixed_parameters={"safe_lift": True},
        success_criteria=["本次任务出现大瓶盖 release 事件", "最终大瓶盖不再 attached 且大瓶盖爪张开"],
    ),
    _skill(
        "skill_big_bottle_decap",
        "打开 500 mL 大瓶盖",
        "BigBottleDecapCfg",
        "夹持大瓶时执行真实大瓶开盖程序",
        extra_parameters={"hold_s": dict(HOLD)},
        fixed_parameters={"safe_lift": True},
        success_criteria=["本次任务出现 cap_detached 事件", "本次状态进入 BigCapOpen=true 且最终保持打开"],
    ),
    _skill(
        "skill_big_bottle_recap",
        "关闭 500 mL 大瓶盖",
        "BigBottleRecapCfg",
        "将已旋开的大瓶盖旋回并重新夹住瓶",
        extra_parameters={"hold_s": dict(HOLD)},
        fixed_parameters={"safe_lift": True},
        success_criteria=["本次任务出现 cap_engaged 事件", "本次状态回到 BigCapOpen=false 且瓶盖闭合"],
    ),
    _skill(
        "skill_syringe_pick",
        "夹取过滤针筒",
        "SyringePickCfg",
        "从过滤机构 1 号位夹取针筒筒身和活塞",
        extra_parameters={"index": dict(SLOT_INDEX), "hold_s": dict(HOLD)},
        success_criteria=["本次任务同时出现筒身和活塞 grasp 事件", "最终两者 attached、SyringeOn=true 且 pg3/pg4 闭合"],
    ),
    _skill(
        "skill_filter_head_mount",
        "安装过滤头",
        "FilterHeadMountCfg",
        "将过滤头安装到当前夹持的针筒",
        extra_parameters={"index": dict(SLOT_INDEX), "hold_s": dict(HOLD)},
        success_criteria=["本次任务出现 filter_installed 事件", "最终 FilterHeadOn=true 且针筒筒身仍 attached"],
    ),
    _skill(
        "skill_syringe_aspirate",
        "针筒吸液",
        "SyringeAspirateCfg",
        "使用已夹持针筒从中瓶区2 的 1 号反应器吸液",
        extra_parameters={
            "index": dict(SLOT_INDEX),
            "volume_ul": dict(VOLUME),
            "hold_s": dict(HOLD),
        },
        success_criteria=["针筒筒身和活塞均 mounted", "本次任务出现 syringe aspirate 命令且源容器液量下降"],
    ),
    _skill(
        "skill_syringe_dispense",
        "针筒吐液",
        "SyringeDispenseCfg",
        "使用已安装过滤头的针筒向小瓶区 10 mL 转移瓶吐液（源包程序 33 的固定接收器）",
        extra_parameters={
            "index": dict(SLOT_INDEX),
            "volume_ul": dict(VOLUME),
            "hold_s": dict(HOLD),
        },
        success_criteria=["针筒和过滤头均 mounted", "本次任务出现 syringe dispense 命令且转移瓶液量上升"],
    ),
    _skill(
        "skill_syringe_discard",
        "弃置针筒和过滤头",
        "SyringeDiscardCfg",
        "将当前针筒连同过滤头弃置到固定废料位",
        extra_parameters={"hold_s": dict(HOLD)},
        success_criteria=[
            "本次任务出现针筒 release 事件",
            "最终 SyringeOn=false、FilterHeadOn=false 且两者不再 attached",
        ],
    ),
    _skill(
        "skill_pipette_transfer",
        "TIP 移液",
        "PipetteTransferCfg",
        "装 TIP → 从源区吸液 → 向目标区吐液 → 弃 TIP（源包 PipetteTransfer，论文 Fig. 4a 的组合技能）",
        extra_parameters={
            "source": _enum("小瓶区", PIPETTE_ZONES, "吸液区"),
            "target": _enum("中瓶区1", PIPETTE_ZONES, "吐液区"),
            "volume_ul": dict(VOLUME),
            "tip_index": dict(TIP_INDEX),
            "hold_s": dict(HOLD),
        },
        success_criteria=[
            "本次任务出现所选 TIP 的 grasp/release 与 aspirate、dispense、eject_tip 命令",
            "源区与目标区液量变化与请求体积相差不超过 5 µL，账本总量守恒、TIP 无残液、无洒漏",
        ],
    ),
    _skill(
        "skill_syringe_filter_transfer",
        "针筒过滤转移",
        "SyringeFilterTransferCfg",
        "取针筒 → 反应器吸液 → 装过滤头 → 向小瓶区吐液 → 弃置针筒（源包 SyringeFilterTransfer）",
        extra_parameters={"volume_ul": dict(VOLUME), "hold_s": dict(HOLD)},
        success_criteria=[
            "本次任务出现筒身、活塞 grasp/release 与 filter_installed 事件",
            "反应器到小瓶区的液量变化与请求体积相差不超过 5 µL，最终 SyringeOn=false、FilterHeadOn=false",
        ],
    ),
    _skill(
        "skill_front_door",
        "前门开合",
        "FrontDoorCfg",
        "把升降前门移到指定开度（源包 FrontDoor；0 为关闭，0.39 m 为全开）",
        extra_parameters={"open_m": dict(DOOR_OPENING)},
        observations=["device_state.joint_positions", "device_state.operation_status"],
        success_criteria=["最终 front_door 与请求开度相差不超过 1.5 mm", "操作历史无拒绝"],
    ),
    _skill(
        "take_drop_tip",
        "TIP 取放",
        "WORKFLOWS[take_drop_tip]",
        "装 1 号位 TIP 再弃置（源包注册工作流）",
        success_criteria=[
            "本次任务出现 1 号位 TIP 的 grasp/release 与 eject_tip 命令",
            "最终 TipOn=false 且没有 attached/seated TIP",
        ],
        tier="operation",
        source_workflow=True,
    ),
    _skill(
        "small_cap_pick_place",
        "小瓶原位取放",
        "WORKFLOWS[small_cap_pick_place]",
        "出样区1 的 2 mL 小瓶原位夹取再放回，不补回安全位（源包注册工作流）",
        success_criteria=["本次任务出现小瓶盖 grasp 与 release 事件", "最终小瓶盖不再 attached 且小瓶盖爪张开"],
        tier="operation",
        source_workflow=True,
    ),
    _skill(
        "big_cap_pick_place",
        "大瓶原位取放",
        "WORKFLOWS[big_cap_pick_place]",
        "大瓶区的 500 mL 瓶原位夹取再放回，不补回安全位（源包注册工作流）",
        success_criteria=["本次任务出现大瓶盖 grasp 与 release 事件", "最终大瓶盖不再 attached"],
        tier="operation",
        source_workflow=True,
    ),
    _skill(
        "skill_wait",
        "推进仿真时间",
        "SettleCfg(duration=duration_s)",
        "只推进真实仿真时间，不改变液量和夹持状态",
        extra_parameters={"duration_s": dict(DURATION)},
        observations=["semantics.sim_time_s", "liquid_volumes_ml", "device_state.semantic_state"],
        success_criteria=["仿真时间实际推进到请求时长", "等待期间液量和设备语义不变"],
    ),
)

SKILL_BY_WORKFLOW = {item["workflow_name"]: item for item in SKILLS}
WORKFLOWS = frozenset(SKILL_BY_WORKFLOW)


def descriptor(workflow_name):
    return SKILL_BY_WORKFLOW[workflow_name]


def capability_entry(workflow_name):
    item = descriptor(workflow_name)
    return {
        "id": item["id"],
        "version": 1,
        "scene_id": SCENE_ID,
        "label": item["label"],
        "workflow_yaml": WORKFLOW_YAML,
        "workflow_name": workflow_name,
        "parameters": item["parameters"],
        "observations": item["observations"],
        "success_criteria": item["success_criteria"],
        "tier": item["tier"],
    }


def _source_sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def catalog_enabled_entry(workflow_name):
    item = descriptor(workflow_name)
    return {
        "workflow_name": workflow_name,
        "source_symbol": item["constructor"],
        "parameter_ranges": {
            name: spec for name, spec in item["parameters"].items() if spec["type"] in {"integer", "number"}
        },
    }


def _registered_evidence(workflow_name):
    phases = {phase["name"]: phase for phase in read_json(REGISTERED_EVIDENCE)["phases"]}
    phase = phases[workflow_name]
    if phase["status"] != "state_machine_success":
        raise ValueError(f"Registered workflow {workflow_name} has no successful run: {phase['status']}")
    return [{
        "path": str(REGISTERED_EVIDENCE),
        "sha256": _source_sha256(REGISTERED_EVIDENCE),
        "marker": f"phases[name='{workflow_name}'].status=state_machine_success",
        "steps": phase["steps"],
        "scope": "kinematic_registered_workflow",
    }]


def catalog_workflow_entry(workflow_name):
    item = descriptor(workflow_name)
    if item["source_workflow"]:
        return {
            "source_symbol": workflow_name,
            "kind": "WORKFLOWS",
            "enabled_capability": item["id"],
            "fixed_parameters": item["fixed_parameters"],
            "parameter_ranges": {
                name: spec for name, spec in item["parameters"].items() if spec["type"] in {"integer", "number"}
            },
            "semantic_acceptance": item["success_criteria"],
            "verified_evidence": _registered_evidence(workflow_name),
            "reference_evidence": [{
                "path": str(WORKFLOWS_SOURCE),
                "sha256": _source_sha256(WORKFLOWS_SOURCE),
                "marker": f"WORKFLOWS['{workflow_name}']",
                "scope": "static_source",
            }],
            "disabled_reason": None,
        }
    return {
        "source_symbol": item["constructor"],
        "kind": "native_skill",
        "enabled_capability": item["id"],
        "fixed_parameters": item["fixed_parameters"],
        "parameter_ranges": {
            name: spec for name, spec in item["parameters"].items() if spec["type"] in {"integer", "number", "string"}
        },
        "semantic_acceptance": item["success_criteria"],
        "verified_evidence": [],
        "reference_evidence": [
            {
                "path": str(SKILLS_SOURCE),
                "sha256": _source_sha256(SKILLS_SOURCE),
                "marker": item["constructor"].split("(", 1)[0],
                "scope": "static_source",
            },
            {
                "path": str(PROGRAM_SOURCE),
                "sha256": _source_sha256(PROGRAM_SOURCE),
                "marker": "program_cfg",
                "scope": "static_source",
            },
        ],
        "disabled_reason": None,
    }


def _parameters_or_defaults(workflow_name, parameters):
    values = {name: spec["default"] for name, spec in descriptor(workflow_name)["parameters"].items()}
    values.update(dict(parameters or {}))
    return values


def apply_workflow_parameters(workflow, parameters):
    """Replace the requested volume in an exported station workflow before execution."""
    if "volume_ul" not in parameters:
        return workflow
    native_skills = importlib.import_module("matterix_plc.actions.skills")
    volume_cfgs = (native_skills.PipetteTransferCfg, native_skills.SyringeFilterTransferCfg)
    replacements = 0

    def replace(node):
        nonlocal replacements
        if isinstance(node, volume_cfgs):
            replacements += 1
            return dataclasses.replace(node, volume=float(parameters["volume_ul"]))
        children = getattr(node, "sub_actions", None)
        if children is None:
            return node
        return dataclasses.replace(node, sub_actions=[replace(child) for child in children])

    result = replace(workflow)
    if replacements == 0:
        raise ValueError("Exported workflow has no configurable volume action")
    return result


def build_workflow(workflow_name, parameters, settle_first=True):
    """Build an executable workflow through the exported native constructors.

    ``settle_first`` prepends the 2 s settle that lets freshly spawned consumables land; a scene already at rest
    after a succeeded task skips it.
    """
    skills = importlib.import_module("matterix_plc.actions.skills")
    compat = importlib.import_module("matterix_sm._compat")
    values = _parameters_or_defaults(workflow_name, parameters)
    settle = skills.SettleCfg

    def make_workflow(*nodes):
        if compat.HAS_ISAACLAB:
            workflows = importlib.import_module("matterix_plc.actions.workflows")
            return workflows.workflow(descriptor(workflow_name)["description"], *nodes)
        return skills.CompositionalActionCfg(
            description=descriptor(workflow_name)["description"],
            sub_actions=list(nodes),
        )

    if workflow_name == "skill_wait":
        return make_workflow(settle(duration=float(values["duration_s"])))
    item = descriptor(workflow_name)
    if item["source_workflow"]:
        return importlib.import_module("matterix_plc.actions.workflows").WORKFLOWS[workflow_name]
    constructor = getattr(skills, item["constructor"])
    kwargs = {field: cast(values[name]) for name, (field, cast) in CFG_FIELDS.items() if name in item["parameters"]}
    if "jaws" in item["fixed_parameters"]:
        kwargs["jaws"] = copy.deepcopy(item["fixed_parameters"]["jaws"])
    if issubclass(constructor, skills.SkillCfg):  # plain station actions take no sub_actions
        kwargs["sub_actions"] = []
    return make_workflow(*([settle()] if settle_first else []), constructor(**kwargs))


def _check(name, actual, expected, unit, passed):
    return semantics._check(name, actual, expected, unit, passed)


def _observed_check(name, observed):
    return _check(name, observed, True, "bool", observed)


def _device(snapshot):
    return semantics._device(snapshot)


def _semantic_state(snapshot):
    return semantics._semantic_state(snapshot)


def _joint(snapshot, name):
    value = _device(snapshot).get("joint_positions")
    if not isinstance(value, Mapping) or name not in value:
        raise ValueError(f"joint position is unknown: {name}")
    return semantics._scalar(value[name], f"joint {name}")


def _liquid_map(snapshot):
    value = snapshot.get("liquid_volumes_ml")
    if not isinstance(value, Mapping):
        raise ValueError("liquid_volumes_ml must be an object")
    return {str(name): semantics._scalar(amount, f"liquid {name}") for name, amount in value.items()}


def _air_check(initial, final):
    air = semantics._scalar(final["liquid_balance"]["air_ml"], "air_ml") - semantics._scalar(
        initial["liquid_balance"]["air_ml"], "air_ml")
    return _check("吸入空气", air, f"<= {semantics.AIR_LIMIT_ML}", "mL", air <= semantics.AIR_LIMIT_ML)


def _spilled_ml(snapshot):
    value = snapshot.get("liquid_balance")
    spilled = value.get("spilled_ml", 0.0) if isinstance(value, Mapping) else 0.0
    return semantics._scalar(spilled, "spilled_ml")


def _is_tip(name):
    return "_Tip_" in name


def _is_syringe(name):
    return name.startswith("SyringeComponent")


def _liquid_transfer(initial, final, tool, operation, volume_ul):
    checks = []
    volume_ml = float(volume_ul) / 1000.0
    before = _liquid_map(initial)
    after = _liquid_map(final)
    if set(before) != set(after):
        checks.append(_check("液量账本对象集合", sorted(set(after) ^ set(before)), [], "objects", False))
        return checks
    carrier = _is_tip if tool == "pipette" else _is_syringe
    initial_carrier = sum(value for name, value in before.items() if carrier(name))
    final_carrier = sum(value for name, value in after.items() if carrier(name))
    carrier_delta = final_carrier - initial_carrier
    expected_direction = 1 if operation == "aspirate" else -1
    checks.append(
        _check(
            "液量载体变化",
            carrier_delta,
            expected_direction * volume_ml,
            "mL",
            math.isclose(
                carrier_delta, expected_direction * volume_ml, rel_tol=0.0, abs_tol=semantics.TRANSFER_TOLERANCE_ML
            ),
        )
    )

    deltas = {name: after[name] - before[name] for name in before if not carrier(name)}
    direction = -expected_direction
    changed = [name for name, delta in deltas.items() if abs(delta) > semantics.VOLUME_TOLERANCE_ML]
    expected_delta = direction * volume_ml
    checks.append(_check("单一容器真实液量变化", changed, "一个容器", "objects", len(changed) == 1))
    if len(changed) == 1:
        delta = deltas[changed[0]]
        checks.append(
            _check(
                "容器变化量",
                delta,
                expected_delta,
                "mL",
                math.isclose(delta, expected_delta, rel_tol=0.0, abs_tol=semantics.TRANSFER_TOLERANCE_ML),
            )
        )
    total_delta = sum(after.values()) - sum(before.values())
    checks.append(
        _check("液量账本总量守恒", total_delta, "0 ± 1e-6", "mL", abs(total_delta) <= semantics.VOLUME_TOLERANCE_ML)
    )
    checks.append(
        _check(
            "泄漏量未增加",
            _spilled_ml(final) - _spilled_ml(initial),
            "<= 0",
            "mL",
            _spilled_ml(final) <= _spilled_ml(initial) + semantics.VOLUME_TOLERANCE_ML,
        )
    )
    checks.append(_air_check(initial, final))
    state_name = "PipetteVolume_uL" if tool == "pipette" else "SyringeVolume_uL"
    state_before = semantics._scalar(_semantic_state(initial).get(state_name), state_name)
    state_after = semantics._scalar(_semantic_state(final).get(state_name), state_name)
    checks.append(
        _check(
            f"语义 {state_name} 变化",
            state_after - state_before,
            expected_direction * float(volume_ul),
            "µL",
            math.isclose(state_after - state_before, expected_direction * float(volume_ul), rel_tol=0.0, abs_tol=0.001),
        )
    )
    return checks


def _current_event(initial, final, event, object_name=None):
    events = semantics._task_events(initial, final)
    return any(
        item.get("event") == event and (object_name is None or item.get("object") == object_name) for item in events
    )


def _current_command(initial, final, action, object_name=None):
    commands = semantics._task_commands(initial, final)
    return any(
        item.get("action") == action and (object_name is None or item.get("object") == object_name) for item in commands
    )


def _mount_state(snapshot, name):
    device = _device(snapshot)
    attached = device.get("attached")
    seated = device.get("seated_tips")
    return {
        "attached": attached.get(name, False) if isinstance(attached, Mapping) else None,
        "seated": seated.get(name, False) if isinstance(seated, Mapping) else None,
        "TipOn": _semantic_state(snapshot).get("TipOn"),
    }


def _attached(snapshot, name):
    value = _device(snapshot).get("attached")
    return value.get(name, False) if isinstance(value, Mapping) else None


def _jaw(snapshot, name):
    value = _semantic_state(snapshot).get("JawsOpen")
    return value.get(name) if isinstance(value, Mapping) else None


def _common(initial, final, history, *, require_events=True, require_commands=True):
    snapshots = [initial, *history, final]
    checks = semantics._common_history_checks(initial, snapshots)
    hidden = set()
    if not require_events:
        hidden.add("资产操作事件增长")
    if not require_commands:
        hidden.add("已发送命令增长")
    return [item for item in checks if item["name"] not in hidden]


def _evaluate_tip_mount(initial, final, history, parameters):
    checks = _common(initial, final, history)
    index = int(parameters["tip_index"])
    tip = TIP_BY_INDEX[index]
    mounted = _mount_state(final, tip)
    checks.extend(
        [
            _observed_check("本次 TIP grasp 事件", _current_event(initial, final, "grasp", tip)),
            _observed_check(
                "本次 TipOn=true 转移",
                semantics._semantic_transition_observed(initial, history, "TipOn", True),
            ),
            _check("最终 TIP attached", mounted["attached"], True, "bool", mounted["attached"] is True),
            _check("最终 TIP seated", mounted["seated"], True, "bool", mounted["seated"] is True),
            _check("最终 TipOn", mounted["TipOn"], True, "bool", mounted["TipOn"] is True),
        ]
    )
    return checks


def _evaluate_tip_eject(initial, final, history):
    checks = _common(initial, final, history)
    device = _device(final)
    attached = device.get("attached")
    seated = device.get("seated_tips")
    remaining = sorted(
        name for name, value in {**(attached or {}), **(seated or {})}.items() if "_Tip_" in name and value
    )
    mounted = [name for name, value in _device(initial).get("attached", {}).items() if _is_tip(name) and value]
    released = bool(mounted) and all(_current_event(initial, final, "release", name) for name in mounted)
    checks.extend(
        [
            _observed_check("本次 eject_tip 命令", _current_command(initial, final, "eject_tip")),
            _observed_check("本次已装 TIP 的 release 事件", released),
            _observed_check(
                "本次 TipOn=false 转移",
                semantics._semantic_transition_observed(initial, history, "TipOn", False),
            ),
            _check("最终无 attached/seated TIP", remaining, [], "objects", not remaining),
            _check(
                "最终 TipOn",
                _semantic_state(final).get("TipOn"),
                False,
                "bool",
                _semantic_state(final).get("TipOn") is False,
            ),
        ]
    )
    return checks


def _evaluate_pipette_liquid(workflow_name, initial, final, history, parameters):
    checks = _common(initial, final, history, require_events=False)
    mode = "aspirate" if workflow_name == "skill_aspirate_from" else "dispense"
    checks.append(_observed_check(f"本次 {mode} 命令", _current_command(initial, final, mode)))
    checks.extend(_liquid_transfer(initial, final, "pipette", mode, parameters["volume_ul"]))
    return checks


def _evaluate_grasp_transition(
    initial,
    final,
    history,
    *,
    event,
    object_name,
    attached_final,
    jaw_name,
    jaw_final,
):
    checks = _common(initial, final, history)
    checks.extend(
        [
            _check(
                "本次对象事件", event, f"{event} {object_name}", "", _current_event(initial, final, event, object_name)
            ),
            _check(
                "最终对象 attached",
                _attached(final, object_name),
                attached_final,
                "bool",
                _attached(final, object_name) is attached_final,
            ),
            _check("最终夹爪状态", _jaw(final, jaw_name), jaw_final, "bool", _jaw(final, jaw_name) is jaw_final),
        ]
    )
    return checks


def _evaluate_cap_transition(initial, final, history, semantic_name, event, expected):
    checks = _common(initial, final, history)
    cap = semantics.SMALL_VIAL_CAP if semantic_name == "SmallCapOpen" else semantics.BIG_BOTTLE_CAP
    bottle = semantics.SMALL_VIAL if semantic_name == "SmallCapOpen" else semantics.BIG_BOTTLE
    closed = _device(final).get("cap_closed")
    checks.extend(
        [
            _check("本次瓶盖事件", event, f"{event} {cap}", "", _current_event(initial, final, event, cap)),
            _check(
                "本次瓶盖打开状态转移",
                expected,
                expected,
                "bool",
                semantics._semantic_transition_observed(initial, history, semantic_name, expected),
            ),
            _check(
                "最终瓶盖打开状态",
                _semantic_state(final).get(semantic_name),
                expected,
                "bool",
                _semantic_state(final).get(semantic_name) is expected,
            ),
            _check(
                "最终瓶盖闭合状态",
                closed.get(bottle) if isinstance(closed, Mapping) else None,
                not expected,
                "bool",
                isinstance(closed, Mapping) and closed.get(bottle) == (not expected),
            ),
        ]
    )
    return checks


def _evaluate_cap_home(initial, final):
    start = _joint(initial, "z_small_cap")
    end = _joint(final, "z_small_cap")
    return [
        _check("小瓶盖升降轴接近静息位", abs(end), abs(start), "m",
               abs(end) < abs(start) or abs(start) <= SMALL_CAP_HOME_TOLERANCE_M),
        _check(
            "小瓶盖升降轴 z_small_cap 最终位置",
            end,
            "0 ± 0.001",
            "m",
            abs(end) <= SMALL_CAP_HOME_TOLERANCE_M,
        ),
    ]


def _evaluate_syringe_pick(initial, final, history):
    checks = _common(initial, final, history)
    checks.extend(
        [
            _observed_check(
                "本次筒身 grasp", _current_event(initial, final, "grasp", semantics.SYRINGE_BARREL)
            ),
            _observed_check(
                "本次活塞 grasp", _current_event(initial, final, "grasp", semantics.SYRINGE_PISTON)
            ),
            _check(
                "最终筒身 attached",
                _attached(final, semantics.SYRINGE_BARREL),
                True,
                "bool",
                _attached(final, semantics.SYRINGE_BARREL) is True,
            ),
            _check(
                "最终活塞 attached",
                _attached(final, semantics.SYRINGE_PISTON),
                True,
                "bool",
                _attached(final, semantics.SYRINGE_PISTON) is True,
            ),
            _check(
                "最终 SyringeOn",
                _semantic_state(final).get("SyringeOn"),
                True,
                "bool",
                _semantic_state(final).get("SyringeOn") is True,
            ),
            _check("pg3 闭合", _jaw(final, "pg3"), False, "bool", _jaw(final, "pg3") is False),
            _check("pg4 闭合", _jaw(final, "pg4"), False, "bool", _jaw(final, "pg4") is False),
        ]
    )
    return checks


def _evaluate_filter_mount(initial, final, history):
    checks = _common(initial, final, history)
    checks.extend(
        [
            _observed_check(
                "本次 filter_installed 事件", _current_event(initial, final, "filter_installed")
            ),
            _observed_check(
                "本次 FilterHeadOn=true 转移",
                semantics._semantic_transition_observed(initial, history, "FilterHeadOn", True),
            ),
            _check(
                "最终 FilterHeadOn",
                _semantic_state(final).get("FilterHeadOn"),
                True,
                "bool",
                _semantic_state(final).get("FilterHeadOn") is True,
            ),
            _check(
                "最终筒身仍 attached",
                _attached(final, semantics.SYRINGE_BARREL),
                True,
                "bool",
                _attached(final, semantics.SYRINGE_BARREL) is True,
            ),
        ]
    )
    return checks


def _evaluate_syringe_liquid(workflow_name, initial, final, history, parameters):
    checks = _common(initial, final, history, require_events=False)
    mode = "aspirate" if workflow_name == "skill_syringe_aspirate" else "dispense"
    checks.append(_observed_check("本次 syringe 液量命令", _current_command(initial, final, mode)))
    checks.extend(_liquid_transfer(initial, final, "syringe", mode, parameters["volume_ul"]))
    container = ZONE_LIQUID_OBJECTS["中瓶区2" if mode == "aspirate" else "小瓶区"]
    before, after = _liquid_map(initial), _liquid_map(final)
    delta = after[container] - before[container]
    expected = float(parameters["volume_ul"]) / 1000 * (-1 if mode == "aspirate" else 1)
    checks.append(_check("指定容器液量变化", delta, f"{container}: {expected:g}", "mL",
                         math.isclose(delta, expected, rel_tol=0, abs_tol=semantics.TRANSFER_TOLERANCE_ML)))
    return checks


def _evaluate_syringe_discard(initial, final, history):
    checks = _common(initial, final, history)
    checks.extend(
        [
            _observed_check("本次 discard 命令", _current_command(initial, final, "release_syringe")),
            _check(
                "最终 SyringeOn",
                _semantic_state(final).get("SyringeOn"),
                False,
                "bool",
                _semantic_state(final).get("SyringeOn") is False,
            ),
            _check(
                "最终 FilterHeadOn",
                _semantic_state(final).get("FilterHeadOn"),
                False,
                "bool",
                _semantic_state(final).get("FilterHeadOn") is False,
            ),
            _check(
                "最终筒身 detached",
                _attached(final, semantics.SYRINGE_BARREL),
                False,
                "bool",
                _attached(final, semantics.SYRINGE_BARREL) is False,
            ),
            _check(
                "最终活塞 detached",
                _attached(final, semantics.SYRINGE_PISTON),
                False,
                "bool",
                _attached(final, semantics.SYRINGE_PISTON) is False,
            ),
        ]
    )
    return checks


def _evaluate_wait(initial, final, history, parameters):
    elapsed = semantics._scalar(final.get("sim_time_s"), "final sim_time_s") - semantics._scalar(
        initial.get("sim_time_s"), "initial sim_time_s"
    )
    expected = float(parameters["duration_s"])
    rejected_count, rejected = semantics._rejection_count(history)
    return [
        _check("仿真时间推进", elapsed, f">= {expected}", "s", elapsed + 1e-9 >= expected),
        _check(
            "等待不改变液量", _liquid_map(final), _liquid_map(initial), "mL", _liquid_map(final) == _liquid_map(initial)
        ),
        _check(
            "等待不改变设备语义",
            _semantic_state(final),
            _semantic_state(initial),
            "state",
            _semantic_state(final) == _semantic_state(initial),
        ),
        _check("操作历史无拒绝", rejected if rejected else 0, "0 条拒绝", "count", rejected_count == 0),
    ]


def _evaluate_take_drop_tip(initial, final, history):
    checks = _common(initial, final, history)
    tip = TIP_BY_INDEX[1]
    device = _device(final)
    remaining = sorted(
        name for name, value in {**(device.get("attached") or {}), **(device.get("seated_tips") or {})}.items()
        if _is_tip(name) and value
    )
    checks.extend([
        _observed_check("本次 TIP grasp 事件", _current_event(initial, final, "grasp", tip)),
        _observed_check("本次 TipOn=true 转移",
                        semantics._semantic_transition_observed(initial, history, "TipOn", True)),
        _observed_check("本次 eject_tip 命令", _current_command(initial, final, "eject_tip")),
        _observed_check("本次 TIP release 事件", _current_event(initial, final, "release", tip)),
        _check("最终无 attached/seated TIP", remaining, [], "objects", not remaining),
        _check("最终 TipOn", _semantic_state(final).get("TipOn"), False, "bool",
               _semantic_state(final).get("TipOn") is False),
    ])
    return checks


def _evaluate_pick_place(initial, final, history, *, cap_object, jaw_name=None):
    checks = _common(initial, final, history)
    checks.extend([
        _check("本次 grasp 事件", "grasp", f"grasp {cap_object}", "",
               _current_event(initial, final, "grasp", cap_object)),
        _check("本次 release 事件", "release", f"release {cap_object}", "",
               _current_event(initial, final, "release", cap_object)),
        _check("最终对象 attached", _attached(final, cap_object), False, "bool", _attached(final, cap_object) is False),
    ])
    if jaw_name is not None:
        checks.append(_check("最终夹爪张开", _jaw(final, jaw_name), True, "bool", _jaw(final, jaw_name) is True))
    return checks


def _transfer_liquid_checks(initial, final, extrema, source, target, tool, volume_ul):
    from .station_experiment import _liquid_checks  # noqa: PLC0415 -- station_experiment imports this module

    checks = _liquid_checks(initial, final, extrema, source, target, tool, float(volume_ul) / 1000)
    checks.append(_check("泄漏量未增加", _spilled_ml(final) - _spilled_ml(initial), "<= 0", "mL",
                         _spilled_ml(final) <= _spilled_ml(initial) + semantics.VOLUME_TOLERANCE_ML))
    checks.append(_air_check(initial, final))
    return checks


def _evaluate_pipette_transfer(initial, final, history, parameters, extrema):
    checks = _common(initial, final, history)
    tip = TIP_BY_INDEX[int(parameters["tip_index"])]
    for action in ("aspirate", "dispense", "eject_tip"):
        checks.append(_observed_check(f"本次 {action} 命令", _current_command(initial, final, action)))
    checks.extend(_transfer_liquid_checks(initial, final, extrema, ZONE_LIQUID_OBJECTS[parameters["source"]],
                                          ZONE_LIQUID_OBJECTS[parameters["target"]], tip, parameters["volume_ul"]))
    checks.append(_check("最终 TipOn", _semantic_state(final).get("TipOn"), False, "bool",
                         _semantic_state(final).get("TipOn") is False))
    return checks


def _evaluate_syringe_filter_transfer(initial, final, history, parameters, extrema):
    checks = _common(initial, final, history)
    for name in (semantics.SYRINGE_BARREL, semantics.SYRINGE_PISTON):
        for event in ("grasp", "release"):
            checks.append(_observed_check(f"本次 {name} {event}", _current_event(initial, final, event, name)))
    checks.append(_observed_check("本次 filter_installed 事件", _current_event(initial, final, "filter_installed")))
    # The barrel is only one of the tools that carry liquid here; its residue is checked by _liquid_checks
    checks.extend(_transfer_liquid_checks(initial, final, extrema, ZONE_LIQUID_OBJECTS["中瓶区2"],
                                          ZONE_LIQUID_OBJECTS["小瓶区"], semantics.SYRINGE_BARREL,
                                          parameters["volume_ul"]))
    for flag in ("SyringeOn", "FilterHeadOn"):
        value = _semantic_state(final).get(flag)
        checks.append(_check(f"最终 {flag}", value, False, "bool", value is False))
    return checks


def _evaluate_front_door(initial, final, history, parameters):
    from .station_experiment import DOOR_TOLERANCE_M  # noqa: PLC0415

    end = _joint(final, "front_door")
    target = float(parameters["open_m"])
    rejected_count, rejected = semantics._rejection_count(history)
    return [
        _check("前门实际到位", end, f"{target} ± {DOOR_TOLERANCE_M}", "m", abs(end - target) <= DOOR_TOLERANCE_M),
        _check("操作历史无拒绝", rejected if rejected else 0, "0 条拒绝", "count", rejected_count == 0),
    ]


# --------------------------------------------------------------------------
# Skill rules: one table drives construction, runtime preconditions, plan-time checks and acceptance
# --------------------------------------------------------------------------
class Unknown(Exception):  # noqa: N818
    """Raised by a plan-time state for a value only the run will know; the check is skipped."""


class SnapshotState:
    """Predicate inputs read from a live session snapshot."""

    def __init__(self, snapshot):
        self.snapshot = snapshot
        self._semantic = _semantic_state(snapshot)

    def semantic(self, name):
        return self._semantic.get(name)

    def tool_ul(self, name):
        return semantics._scalar(self._semantic.get(name), name)

    def attached(self, name):
        return _attached(self.snapshot, name)

    def cap_closed(self, container):
        return _device(self.snapshot)["cap_closed"].get(container)

    def liquid(self, zone):
        return _liquid_map(self.snapshot)[ZONE_LIQUID_OBJECTS[zone]]

    def container_ml(self, name):
        return _liquid_map(self.snapshot)[name]

    def used(self, *objects):
        events = semantics._events(self.snapshot)
        return any(semantics._event_observed(events, "grasp", name) for name in objects)


# A predicate returns (actual, expected, unit, passed, have) or None where it does not apply; `have` fills plan hints.
def _flag(state, values, *, name, want):
    actual = state.semantic(name)
    return actual, want, "bool", actual is want, ""


def _holding(state, values, *, name, want):
    actual = state.attached(name)
    return actual, want, "bool", actual is want, ""


def _fresh_tip(state, values, *, index=None):
    index = int(values["tip_index"]) if index is None else index
    tip = TIP_BY_INDEX[index]
    used = state.used(tip)
    return "used" if used else "fresh", f"fresh {tip}", "", not used, f"TIP {index} 号位"


def _fresh_syringe(state, values):
    used = state.used(semantics.SYRINGE_BARREL, semantics.SYRINGE_PISTON)
    return "used" if used else "fresh", "fresh", "", not used, ""


def _cap_open(state, values, *, param, report):
    zone = values[param]
    cap = CAPPED_ZONES.get(zone)
    if cap is None:
        return None
    opened = state.semantic(cap)
    closed = state.cap_closed(ZONE_LIQUID_OBJECTS[zone])
    passed = opened is True and closed is False
    have = f"{CAPPED_ZONE_NEEDS[zone]}（{CAP_FIX[cap]}）"
    if report == "opened":
        return opened, CAPPED_ZONE_NEEDS[zone], "bool", passed, have
    return closed, False, "bool", passed, have


def _zone_liquid(state, values, *, tolerance, param=None, zone=None):
    available = state.liquid(zone or values[param])
    needed = float(values["volume_ul"]) / 1000
    return (available, f">= {values['volume_ul']} µL", "mL", available + tolerance >= needed,
            f"{available * 1000:g} µL")


def _tool_volume(state, values, *, name):
    available = state.tool_ul(name)
    return (available, f">= {values['volume_ul']}", "µL", available + 0.001 >= float(values["volume_ul"]),
            f"{available:g} µL")


def _barrel_liquid(state, values):
    liquid_ml = state.container_ml(semantics.SYRINGE_BARREL)
    passed = liquid_ml + semantics.TRANSFER_TOLERANCE_ML >= float(values["volume_ul"]) / 1000
    return liquid_ml, f">= {values['volume_ul']} µL", "mL", passed, ""


def _pre(label, test, fix=None, **args):
    """A precondition: runtime check name, predicate and the plan-time hint (None: runtime only)."""
    return label, functools.partial(test, **args), fix


CAP_FIX = {
    "BigCapOpen": "先执行 station_skill_big_bottle_pick、station_skill_big_bottle_decap",
    "SmallCapOpen": "先执行 station_skill_small_vial_pick、station_skill_small_vial_decap",
}
# Request parameter -> native constructor field; skills pass the ones they declare
CFG_FIELDS = {
    "tip_index": ("tip_index", int),
    "zone": ("zone", str),
    "source": ("source", str),
    "target": ("target", str),
    "open_m": ("open_m", float),
    "index": ("index", int),
    "volume_ul": ("volume", float),
    "hold_s": ("hold", float),
    "dwell_s": ("dwell", float),
}
TIP_ON = _pre("当前已装 TIP", _flag, "需要先装 TIP（station_skill_mount_tip）", name="TipOn", want=True)
NEED_SYRINGE = "requires a picked syringe（先执行 station_skill_syringe_pick）"
BARREL = _pre("当前已夹筒身", _holding, name=semantics.SYRINGE_BARREL, want=True)
PISTON = _pre("当前已夹活塞", _holding, name=semantics.SYRINGE_PISTON, want=True)
SYRINGE_ON = _pre("当前 SyringeOn", _flag, NEED_SYRINGE, name="SyringeOn", want=True)


def _container_rules(article, cap_object, cap, pick, place, decap):
    """pick/place/decap/recap of the 2 mL vial or the 500 mL bottle."""
    holding = _pre(f"当前已夹{article}", _holding, f"需要先夹取{article}（{pick}）", name=cap_object, want=True)
    jaw = "small_cap_grip" if cap == "SmallCapOpen" else "big_cap_grip"
    lid = article[0] + "瓶盖"
    return {
        "pick": {
            "pre": [_pre(f"当前未夹{article}", _holding, f"需要先放回当前夹持的{article}（{place}）",
                         name=cap_object, want=False)],
            "effect": {"set": {cap_object: True}},
            "verify": lambda i, f, h, v, x: _evaluate_grasp_transition(
                i, f, h, event="grasp", object_name=cap_object, attached_final=True, jaw_name=jaw, jaw_final=False),
        },
        "place": {
            "pre": [holding],
            "effect": {"set": {cap_object: False}},
            "verify": lambda i, f, h, v, x: _evaluate_grasp_transition(
                i, f, h, event="release", object_name=cap_object, attached_final=False, jaw_name=jaw, jaw_final=True),
        },
        "decap": {
            "pre": [holding, _pre(f"{lid}当前闭合", _flag, f"{lid}已经打开", name=cap, want=False)],
            "effect": {"set": {cap: True}},
            "verify": lambda i, f, h, v, x: _evaluate_cap_transition(i, f, h, cap, "cap_detached", True),
        },
        "recap": {
            "pre": [holding, _pre(f"{lid}当前打开", _flag, f"{lid}没有打开（先执行 {decap}）", name=cap, want=True)],
            "effect": {"set": {cap: False}},
            "verify": lambda i, f, h, v, x: _evaluate_cap_transition(i, f, h, cap, "cap_engaged", False),
        },
    }


_VIAL = _container_rules("小瓶", semantics.SMALL_VIAL_CAP, "SmallCapOpen", "station_skill_small_vial_pick",
                         "station_skill_small_vial_place", "station_skill_small_vial_decap")
_BOTTLE = _container_rules("大瓶", semantics.BIG_BOTTLE_CAP, "BigCapOpen", "station_skill_big_bottle_pick",
                           "station_skill_big_bottle_place", "station_skill_big_bottle_decap")

# pre: ordered preconditions; effect: what a success changes, applied by the plan checker
# (set: state values, use: consumables marked used, transfer: (from, to) of volume_ul between a zone
# parameter / fixed zone and a tool volume); verify: acceptance from (initial, final, history, parameters).
RULES = {
    "skill_mount_tip": {
        "pre": [
            _pre("当前未装 TIP", _flag, "需要先卸载当前 TIP", name="TipOn", want=False),
            _pre("所选 TIP 未使用", _fresh_tip, "需要未用过的 {have}，但本次实验中它已被用过（TIP 不能复用）"),
        ],
        "effect": {"set": {"TipOn": True, "PipetteVolume_uL": 0.0, "tip_disposed": False},
                   "use": lambda v: [TIP_BY_INDEX[v["tip_index"]]]},
        "verify": lambda i, f, h, v, x: _evaluate_tip_mount(i, f, h, v),
    },
    "skill_eject_tip": {
        "pre": [_pre("当前已装 TIP", _flag, "需要当前 mounted TIP", name="TipOn", want=True)],
        "effect": {"set": {"TipOn": False, "PipetteVolume_uL": None, "tip_disposed": True}},
        "verify": lambda i, f, h, v, x: _evaluate_tip_eject(i, f, h),
    },
    "skill_aspirate_from": {
        "pre": [
            TIP_ON,
            _pre("{zone} 有已开盖的容器", _cap_open, "无法在{zone}操作：{have}", param="zone", report="opened"),
            _pre("源容器液量充足", _zone_liquid, "要从{zone}吸 {volume_ul:g} µL，但此时{zone}只有 {have}",
                 param="zone", tolerance=semantics.VOLUME_TOLERANCE_ML),
        ],
        "effect": {"transfer": ("zone", "PipetteVolume_uL")},
        "verify": lambda i, f, h, v, x: _evaluate_pipette_liquid("skill_aspirate_from", i, f, h, v),
    },
    "skill_dispense_to": {
        "pre": [
            TIP_ON,
            _pre("{zone} 有已开盖的容器", _cap_open, "无法在{zone}操作：{have}", param="zone", report="opened"),
            _pre("TIP 内液量充足", _tool_volume, "要吐 {volume_ul:g} µL，但 TIP 内此时只有 {have}；先吸液",
                 name="PipetteVolume_uL"),
        ],
        "effect": {"transfer": ("PipetteVolume_uL", "zone")},
        "verify": lambda i, f, h, v, x: _evaluate_pipette_liquid("skill_dispense_to", i, f, h, v),
    },
    **{f"skill_small_vial_{name}": rule for name, rule in _VIAL.items()},
    "skill_small_cap_home": {
        "pre": [],
        "effect": {},
        "verify": lambda i, f, h, v, x: _evaluate_cap_home(i, f),
    },
    **{f"skill_big_bottle_{name}": rule for name, rule in _BOTTLE.items()},
    "skill_syringe_pick": {
        "pre": [
            _pre("当前无针筒", _flag, name="SyringeOn", want=False),
            _pre("针筒组件本会话未使用", _fresh_syringe,
                 "需要 fresh syringe，但本次实验的 syringe 已经 consumed；同一实验不能 remount/reuse"),
        ],
        "effect": {"set": {"SyringeOn": True, "SyringeVolume_uL": 0.0, "syringe_disposed": False},
                   "use": lambda v: [semantics.SYRINGE_BARREL, semantics.SYRINGE_PISTON]},
        "verify": lambda i, f, h, v, x: _evaluate_syringe_pick(i, f, h),
    },
    "skill_filter_head_mount": {
        "pre": [
            _pre("当前已夹筒身", _holding, "需要先夹取针筒（station_skill_syringe_pick）",
                 name=semantics.SYRINGE_BARREL, want=True),
            PISTON,
            _pre("当前无过滤头", _flag, "需要 fresh filter；本次实验已消耗过滤头", name="FilterHeadOn", want=False),
        ],
        "effect": {"set": {"FilterHeadOn": True}},
        "verify": lambda i, f, h, v, x: _evaluate_filter_mount(i, f, h),
    },
    "skill_syringe_aspirate": {
        "pre": [
            _pre("反应器液量充足", _zone_liquid, zone="中瓶区2", tolerance=0.0),
            BARREL,
            PISTON,
            SYRINGE_ON,
            _pre("过滤头尚未安装", _flag, "must run before mounting the filter（过滤头会挡住吸液）",
                 name="FilterHeadOn", want=False),
        ],
        "effect": {"transfer": ("中瓶区2", "SyringeVolume_uL")},
        "verify": lambda i, f, h, v, x: _evaluate_syringe_liquid("skill_syringe_aspirate", i, f, h, v),
    },
    "skill_syringe_dispense": {
        "pre": [
            _pre("针筒内液量充足", _tool_volume, "要吐 {volume_ul:g} µL，但 the syringe contains only {have}",
                 name="SyringeVolume_uL"),
            _pre("针筒实际液量充足", _barrel_liquid),
            BARREL,
            PISTON,
            SYRINGE_ON,
            _pre("过滤头已安装", _flag, "requires a mounted filter（先执行 station_skill_filter_head_mount）",
                 name="FilterHeadOn", want=True),
        ],
        "effect": {"transfer": ("SyringeVolume_uL", "小瓶区")},
        "verify": lambda i, f, h, v, x: _evaluate_syringe_liquid("skill_syringe_dispense", i, f, h, v),
    },
    "skill_syringe_discard": {
        "pre": [_pre("当前 SyringeOn", _flag, "需要当前 mounted syringe", name="SyringeOn", want=True)],
        "effect": {"set": {"SyringeOn": False, "FilterHeadOn": False, "syringe_disposed": True}},
        "verify": lambda i, f, h, v, x: _evaluate_syringe_discard(i, f, h),
    },
    "skill_pipette_transfer": {
        "pre": [
            _pre("当前未装 TIP", _flag, "需要先卸载当前 TIP", name="TipOn", want=False),
            _pre("所选 TIP 未使用", _fresh_tip, "需要未用过的 {have}，但本次实验中它已被用过（TIP 不能复用）"),
            _pre("{source} 有已开盖的容器", _cap_open, "无法在{source}操作：{have}", param="source", report="opened"),
            _pre("{target} 有已开盖的容器", _cap_open, "无法在{target}操作：{have}", param="target", report="opened"),
            _pre("源容器液量充足", _zone_liquid, "要从{source}吸 {volume_ul:g} µL，但此时{source}只有 {have}",
                 param="source", tolerance=semantics.VOLUME_TOLERANCE_ML),
        ],
        "effect": {"set": {"TipOn": False, "PipetteVolume_uL": None, "tip_disposed": True},
                   "use": lambda v: [TIP_BY_INDEX[v["tip_index"]]], "transfer": ("source", "target")},
        "verify": _evaluate_pipette_transfer,
    },
    "skill_syringe_filter_transfer": {
        "pre": [
            _pre("当前无针筒", _flag, name="SyringeOn", want=False),
            _pre("针筒组件本会话未使用", _fresh_syringe,
                 "需要 fresh syringe，但本次实验的 syringe 已经 consumed；同一实验不能 remount/reuse"),
            _pre("反应器液量充足", _zone_liquid, zone="中瓶区2", tolerance=0.0),
        ],
        "effect": {"set": {"SyringeOn": False, "FilterHeadOn": False, "syringe_disposed": True},
                   "use": lambda v: [semantics.SYRINGE_BARREL, semantics.SYRINGE_PISTON],
                   "transfer": ("中瓶区2", "小瓶区")},
        "verify": _evaluate_syringe_filter_transfer,
    },
    "skill_front_door": {
        "pre": [],
        "effect": {},
        "verify": lambda i, f, h, v, x: _evaluate_front_door(i, f, h, v),
    },
    "take_drop_tip": {
        "pre": [
            _pre("当前未装 TIP", _flag, "需要先卸载当前 TIP", name="TipOn", want=False),
            _pre("TIP 1 号位未使用", _fresh_tip, "需要未用过的 {have}，但本次实验中它已被用过（TIP 不能复用）",
                 index=1),
        ],
        "effect": {"set": {"TipOn": False, "PipetteVolume_uL": None, "tip_disposed": True},
                   "use": lambda v: [TIP_BY_INDEX[1]]},
        "verify": lambda i, f, h, v, x: _evaluate_take_drop_tip(i, f, h),
    },
    "small_cap_pick_place": {
        "pre": [_pre("当前未夹小瓶", _holding, "需要先放回当前夹持的小瓶（station_skill_small_vial_place）",
                     name=semantics.SMALL_VIAL_CAP, want=False)],
        "effect": {"set": {semantics.SMALL_VIAL_CAP: False}},
        "verify": lambda i, f, h, v, x: _evaluate_pick_place(
            i, f, h, cap_object=semantics.SMALL_VIAL_CAP, jaw_name="small_cap_grip"),
    },
    "big_cap_pick_place": {
        "pre": [_pre("当前未夹大瓶", _holding, "需要先放回当前夹持的大瓶（station_skill_big_bottle_place）",
                     name=semantics.BIG_BOTTLE_CAP, want=False)],
        "effect": {"set": {semantics.BIG_BOTTLE_CAP: False}},
        # The exported place passes reopen_jaws={}, so the jaw opening afterwards is not part of the workflow
        "verify": lambda i, f, h, v, x: _evaluate_pick_place(i, f, h, cap_object=semantics.BIG_BOTTLE_CAP),
    },
    "skill_wait": {
        "pre": [],
        "effect": {},
        "verify": lambda i, f, h, v, x: _evaluate_wait(i, f, h, v),
    },
}
if set(RULES) != WORKFLOWS:
    raise RuntimeError(f"Station skill rules do not match SKILLS: {sorted(set(RULES) ^ WORKFLOWS)}")


def evaluate_postconditions(workflow_name, initial, final, parameters, history, extrema=None):
    if workflow_name not in RULES:
        return semantics._failure(workflow_name, "workflow is not enabled for execution", "workflow")
    checks = RULES[workflow_name]["verify"](
        initial, final, history, _parameters_or_defaults(workflow_name, parameters), extrema)
    return {
        "workflow_name": workflow_name,
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
    }


def precondition_checks(workflow_name, snapshot, parameters=None):
    values = _parameters_or_defaults(workflow_name, parameters)
    checks = []
    try:
        state = SnapshotState(snapshot)
        for label, test, _ in RULES[workflow_name]["pre"]:
            result = test(state, values)
            if result is not None:
                actual, expected, unit, passed, _ = result
                checks.append(_check(label.format_map(values), actual, expected, unit, passed))
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        checks.append(_check("执行前状态", str(exc), "可读当前语义", "", False))
    return checks


def unmet_preconditions(workflow_name, state, values, raw):
    """Plan-time hints for the preconditions `state` already violates; values it does not know are skipped."""
    messages = []
    for _, test, fix in RULES[workflow_name]["pre"]:
        if fix is None:
            continue
        try:
            result = test(state, values)
        except Unknown:
            continue
        if result is not None and not result[3]:
            messages.append(fix.format_map({**raw, "have": result[4]}))
    return messages


__all__ = [
    "CAP_FIX",
    "MAX_VOLUME_UL",
    "MIN_VOLUME_UL",
    "PIPETTE_ZONES",
    "RULES",
    "SKILLS",
    "TIP_BY_INDEX",
    "WORKFLOWS",
    "SnapshotState",
    "Unknown",
    "apply_workflow_parameters",
    "build_workflow",
    "capability_entry",
    "catalog_enabled_entry",
    "catalog_workflow_entry",
    "descriptor",
    "evaluate_postconditions",
    "precondition_checks",
    "unmet_preconditions",
]
