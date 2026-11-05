"""Exercise the real joint ramp without starting Kit or importing its native bindings."""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import torch


def test_joint_ramp_preserves_direction_and_finishes_short_moves_exactly():
    path = Path(__file__).resolve().parents[2] / "integrations/matterix/bridge/beaker_ik.py"
    tree = ast.parse(path.read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    method = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "apply_actions")
    namespace = {}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
    state = SimpleNamespace(
        joint_target=torch.tensor([[1.0, -0.5, 0.1]]),
        joint_command=torch.zeros((1, 3)),
        physics_dt=0.1,
        _joint_ids=[0, 1, 2],
        _asset=SimpleNamespace(set_joint_position_target_index=Mock()),
    )
    namespace["apply_actions"](state)
    torch.testing.assert_close(state.joint_command, torch.tensor([[0.02, -0.01, 0.002]]))
    state.joint_target = state.joint_command + torch.tensor([[0.001, 0.002, -0.003]])
    namespace["apply_actions"](state)
    torch.testing.assert_close(state.joint_command, state.joint_target)
