"""Keep the uncalibrated twin pump stationary while its measured stroke transfers liquid."""

import copy

from matterix_plc.actions.program_action import ProgramStep, ProgramStepCfg
from matterix_sm.action_base import ActionBaseCfg
from matterix_sm.semantic_info import SemanticInfo

PUMP_SETTLE_S = 1.0


def prepare_liquid_steps(workflow: ActionBaseCfg) -> ActionBaseCfg:
    """Adapt every atomic/composite tree without editing the exported PLC programs or shared source configs."""
    workflow = copy.deepcopy(workflow)

    def prepare(node: ActionBaseCfg) -> ActionBaseCfg:
        if isinstance(node, ProgramStepCfg) and node.step in (500, 510, 700, 710):
            node.settle_time = max(node.settle_time, PUMP_SETTLE_S)
        if hasattr(node, "sub_actions"):
            node.sub_actions = [prepare(child) for child in node.sub_actions]
        return node

    return prepare(workflow)


def stop_liquid_before_retract(actions) -> None:
    for action in actions:
        if isinstance(action, ProgramStep) and action.step in (510, 710):
            # The export stops at 520/720, after Z retracts; inertial piston motion then pumps into air.
            action.semantics = [
                *(action.semantics or ()),
                SemanticInfo(type="StationOperation", asset_name="env", value="stop",
                             additional_info={"request": {"action": "stop"}}),
            ]
