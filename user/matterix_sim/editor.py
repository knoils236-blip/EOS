"""Editing presets share the bridge's validated catalog; discovery never opens a session."""

from integrations.matterix.bridge.capabilities import catalog
from integrations.matterix.bridge.plan_check import scene_notes
from matterix_sim.capability import VOLUME_OPERATIONS

DISPLAY_LABELS = {
    "station_mixture_stock": "加入母液（可调体积）",
    "station_mixture_diluent": "加入稀释液（可调体积）",
    "station_mixture_close": "配液结束与关门",
}
GROUPS = {"operation": "场景操作", "skill": "原子技能"}


def _editable(name: str, spec: dict) -> bool:
    """Recording is set per run, and a parameter with a single allowed value is not a choice."""
    return name not in {"record_video", "hide_shell"} and not (spec["min"] is not None and spec["min"] == spec["max"])


def _preset_task(capability_id: str, values: dict) -> dict:
    if capability_id in VOLUME_OPERATIONS:
        floats = {key: float(value) for key, value in values.items()}  # the typed task declares float parameters
        task = {"type": "Matterix Volume Operation", "parameters": {"capability_id": capability_id, **floats}}
    else:
        task = {"type": "Matterix Capability", "parameters": {"capability_id": capability_id, "parameters": values}}
    return {**task, "timeout": 660, "retries": 0}


def workstation_scene(scene_id: str) -> dict:
    presets = []
    for capability in catalog():
        if capability["scene_id"] != scene_id or capability["tier"] == "hidden":
            continue
        specs = {key: spec for key, spec in capability["parameters"].items() if _editable(key, spec)}
        presets.append(
            {
                "id": capability["id"],
                "label": DISPLAY_LABELS.get(capability["id"], capability["label"]),
                "description": "；".join(capability["success_criteria"]),
                "group": GROUPS[capability["tier"]],
                "parameter_specs": {
                    key: {**spec, "label": spec.get("label", "移液体积" if key == "volume_ul" else key),
                          "unit": spec.get("unit", "µL" if key == "volume_ul" else "")}
                    for key, spec in specs.items()
                },
                "device_slot": "matterix",
                "task": _preset_task(capability["id"], {key: spec["default"] for key, spec in specs.items()}),
            }
        )
    if not presets:
        raise ValueError(f"No registered editor capabilities for scene '{scene_id}'")
    if scene_id == "pipetting_realtime":
        presets.extend(
            [
                {
                    "id": "begin_experiment",
                    "label": "实验初始化",
                    "group": "实验边界",
                    "description": "重置并检查初态，开始独占工作站。应放在移液站操作之前。",
                    "device_slot": "matterix",
                    "task": {"type": "Matterix Begin Experiment", "timeout": 240},
                },
                {
                    "id": "end_custom_experiment",
                    "label": "结束自定义实验",
                    "group": "实验边界",
                    "description": "核验本协议各操作已成功且工作站空闲后释放；不要求完成七阶段整站演示。",
                    "device_slot": "matterix",
                    "task": {"type": "Matterix End Experiment", "parameters": {"expected_phase": 0}, "timeout": 240},
                },
                {
                    "id": "end_experiment",
                    "label": "结束检查（七阶段）",
                    "group": "实验边界",
                    "description": "核对整站流程已完成七个阶段，释放工作站；其他流程需按实际验收阶段设置。",
                    "device_slot": "matterix",
                    "task": {"type": "Matterix End Experiment", "parameters": {"expected_phase": 7}, "timeout": 240},
                },
            ]
        )
    return {
        "scene_id": scene_id,
        "label": "移液工作站" if scene_id == "pipetting_realtime" else "烧杯机器人",
        "chain_device": True,
        "notes": scene_notes(scene_id),
        "presets": presets,
    }


def mixture_scene(backend: str) -> dict:
    return {
        "scene_id": "mixture_" + backend,
        "label": "模拟配液" if backend == "mock" else "Matterix 参数化配液",
        "chain_device": False,
        "presets": [
            {
                "id": "prepare_mixture",
                "label": "制备 200 µL 配液",
                "group": "场景操作",
                "description": "设置母液量，稀释液补足至 200 µL。",
                "device_slot": "workstation",
                "task": {"type": "Prepare Mixture", "parameters": {"stock_volume_ul": 74.0}, "timeout": 1200},
            },
            {
                "id": "measure_mixture",
                "label": "虚拟浓度测量",
                "group": "分析与反馈",
                "description": "接收制备步骤输出的样品，生成明确标记的合成观测。",
                "task": {"type": "Measure Mixture"},
            },
            {
                "id": "check_mixture_quality",
                "label": "测量质量检查",
                "group": "分析与反馈",
                "description": "检查空白、参考样与重复性。",
                "task": {"type": "Check Mixture Quality"},
            },
            {
                "id": "score_mixture",
                "label": "目标浓度评分",
                "group": "分析与反馈",
                "description": "仅对通过质控的观测计算目标误差。",
                "task": {"type": "Score Mixture"},
            },
        ],
    }
