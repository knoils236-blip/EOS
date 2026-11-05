"""Solve Franka pose goals with Lula and apply ramped joint targets through PD drives."""

from __future__ import annotations

import numpy as np
import torch
from isaaclab.envs.mdp.actions.task_space_actions import DifferentialInverseKinematicsAction
from isaaclab.utils.math import quat_apply


class FrankaPoseAction(DifferentialInverseKinematicsAction):
    """Resolve Cartesian commands once and hold a continuous joint-target path."""

    def __init__(self, cfg, env):
        super().__init__(cfg, env)
        from isaacsim.robot_motion.motion_generation import (
            LulaKinematicsSolver,
            interface_config_loader,
        )

        self.solver = LulaKinematicsSolver(
            **interface_config_loader.load_supported_lula_kinematics_solver_config("Franka"))
        self.pose_command = None
        self.joint_target = None
        self.joint_command = None
        self.physics_dt = env.sim.get_physics_dt()
        self.model_checked = False

    def process_actions(self, actions):
        super().process_actions(actions)
        if (self.pose_command is not None
                and bool((self._processed_actions - self.pose_command).abs().max() < 1e-6)):
            return
        current_joints = self._asset.data.joint_pos.torch[0, self._joint_ids].cpu().numpy()
        if self.joint_command is None:
            self.joint_command = self._asset.data.joint_pos.torch[:, self._joint_ids].clone()
        if not self.model_checked:
            position, quaternion = self._compute_frame_pose()
            measured = (position - quat_apply(quaternion, self._offset_pos))[0].cpu().numpy()
            predicted, _ = self.solver.compute_forward_kinematics("panda_hand", current_joints)
            if np.linalg.norm(predicted - measured) > 0.001:
                raise ValueError("Franka URDF and simulation hand frames differ by more than 1 mm")
            self.model_checked = True
        quaternion = self._ik_controller.ee_quat_des
        hand_position = self._ik_controller.ee_pos_des - quat_apply(
            quaternion, self._offset_pos)
        xyzw = quaternion[0].cpu().numpy()
        warm_start = (
            self.joint_target[0].cpu().numpy()
            if self.joint_target is not None
            else current_joints
        )
        solution, success = self.solver.compute_inverse_kinematics(
            "panda_hand",
            hand_position[0].cpu().numpy(),
            xyzw[[3, 0, 1, 2]],
            warm_start=warm_start,
            position_tolerance=0.0001,
            orientation_tolerance=0.05,
        )
        if not success:
            raise ValueError("Franka pose is outside the reachable workspace")
        self.joint_target = torch.as_tensor(
            solution, dtype=torch.float32, device=self.device).unsqueeze(0)
        self.pose_command = self._processed_actions.clone()

    def apply_actions(self):
        delta = self.joint_target - self.joint_command
        # Keep joint motion synchronized; clipping each joint separately bends the EE path.
        scale = (0.2 * self.physics_dt / delta.abs().amax(dim=-1, keepdim=True).clamp_min(1e-9)).clamp(max=1.0)
        self.joint_command += delta * scale
        self._asset.set_joint_position_target_index(
            target=self.joint_command, joint_ids=self._joint_ids)

    def reset(self, env_ids=None):
        super().reset(env_ids)
        self.pose_command = None
        self.joint_target = None
        self.joint_command = None
