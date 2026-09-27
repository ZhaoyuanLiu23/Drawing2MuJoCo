"""Panda kinematics and controller adapter; no imports from the ball demo."""
import mujoco
import numpy as np

from cad_mujoco.mjcf import load_spec
from cad_mujoco.validation import initialize
from .geometry import Pose, geom_vertices, matrix_quat, quat_matrix


class PlanningFailure(ValueError):
    pass


def rotation_error(target, current):
    delta = matrix_quat(target @ current.T)
    if delta[0] < 0:
        delta *= -1
    result = np.empty(3)
    mujoco.mju_quat2Vel(result, delta, 1.0)
    return result


def interpolate_pose(start, end, fraction):
    fraction = float(np.clip(fraction, 0, 1))
    delta = rotation_error(end.rotation, start.rotation)
    angle = np.linalg.norm(delta)
    q = np.array([1.0, 0, 0, 0])
    if angle > 1e-12:
        mujoco.mju_axisAngle2Quat(q, delta / angle, angle * fraction)
    return Pose(start.position + fraction * (end.position - start.position), quat_matrix(q) @ start.rotation)


def prepare_scene(path, gripper_stiffness=1000.0):
    spec = load_spec(path)
    original = spec.compile()
    data = initialize(original)
    for name in ("hand", "left_finger", "right_finger"):
        if spec.body(name) is None:
            raise ValueError("A Panda scene with hand/left_finger/right_finger is required")
    finger_joints = [original.joint(n).id for n in ("finger_joint1", "finger_joint2")]
    for j in finger_joints:
        data.qpos[original.jnt_qposadr[j]] = original.jnt_range[j, 0]
    mujoco.mj_forward(original, data)
    hand = original.body("hand").id
    rotation = data.xmat[hand].reshape(3, 3)
    tips, pads = [], []
    for name in ("left_finger", "right_finger"):
        bid = original.body(name).id
        for g in range(original.ngeom):
            if original.geom_bodyid[g] != bid or not (original.geom_contype[g] or original.geom_conaffinity[g]):
                continue
            vertices = geom_vertices(original, g) @ data.geom_xmat[g].reshape(3, 3).T + data.geom_xpos[g]
            local = (vertices - data.xpos[hand]) @ rotation
            tips.append(local)
            if original.geom_type[g] == mujoco.mjtGeom.mjGEOM_BOX:
                pads.append(local)
    if not pads:
        raise ValueError("Panda fingertip pad calibration requires box collision pads")
    tip_z = float(np.concatenate(tips)[:, 2].max())
    pad_vertices = np.concatenate(pads)
    calibration = dict(tcp_hand_m=[0, 0, tip_z], pad_tip_recess_m=tip_z - float(pad_vertices[:, 2].max()),
                       pad_half_width_m=float(np.abs(pad_vertices[:, 0]).max()),
                       open_gap_m=float(sum(original.jnt_range[j, 1] for j in finger_joints)),
                       source="compiled Panda collision geometry and finger joint limits")
    if spec.site("manip_tcp") is not None:
        raise ValueError("Scene already contains manip_tcp")
    spec.body("hand").add_site(name="manip_tcp", pos=calibration["tcp_hand_m"], size=[0.002, 0, 0],
                               rgba=[0, 1, 0, 0])
    # Independent manipulation controller, applied to a new in-memory scene only.
    # Retain the real finger meshes, tendon and force limits; use a usable pinch servo.
    actuator = spec.actuator("actuator8")
    maximum = max(original.jnt_range[j, 1] for j in finger_joints)
    control = np.asarray(actuator.ctrlrange)
    if control[0] != 0 or control[1] <= 0 or gripper_stiffness <= 0 or not np.isfinite(gripper_stiffness):
        raise ValueError("Unsupported gripper control range or stiffness")
    gain = list(actuator.gainprm)
    bias = list(actuator.biasprm)
    gain[0] = maximum * gripper_stiffness / control[1]
    bias[1], bias[2] = -gripper_stiffness, -20.0
    actuator.gainprm, actuator.biasprm = gain, bias
    calibration.update(gripper_stiffness_N_m=gripper_stiffness, gripper_damping_N_s_m=20.0,
                       original_gripper_stiffness_N_m=float(-original.actuator_biasprm[original.actuator("actuator8").id, 1]),
                       gripper_force_range_N=original.actuator_forcerange[original.actuator("actuator8").id].tolist(),
                       robot_compensation="qfrc_bias feedforward only on seven arm DOFs")
    return spec.compile(), calibration


class Panda:
    def __init__(self, model, data):
        self.model, self.data = model, data
        self.jids = np.array([model.joint(f"joint{i}").id for i in range(1, 8)])
        self.qids = model.jnt_qposadr[self.jids]
        self.dofs = model.jnt_dofadr[self.jids]
        self.act = [model.actuator(f"actuator{i}").id for i in range(1, 8)]
        self.gripper = model.actuator("actuator8").id
        self.open, self.closed = float(model.actuator_ctrlrange[self.gripper, 1]), float(model.actuator_ctrlrange[self.gripper, 0])
        self.fingers = {model.body(n).id for n in ("left_finger", "right_finger")}
        self.site = model.site("manip_tcp").id
        self.root = model.body("link0").id
        self.bodies = {i for i in range(model.nbody) if self.is_descendant(i)}
        self.ik_data = mujoco.MjData(model)
        self.jacp, self.jacr = np.zeros((3, model.nv)), np.zeros((3, model.nv))
        self.limits = model.jnt_range[self.jids]
        self.max_ik_position_error = 0.0
        self.max_ik_angle_error = 0.0

    def is_descendant(self, body):
        while body:
            if body == self.root:
                return True
            body = self.model.body_parentid[body]
        return False

    def pose(self):
        return Pose(self.data.site_xpos[self.site].copy(), self.data.site_xmat[self.site].reshape(3, 3).copy())

    def solve(self, target, seed, max_iterations=180):
        q = np.asarray(seed, dtype=float).copy()
        d, m = self.ik_data, self.model
        d.qpos[:] = self.data.qpos  # Scratch FK only; never write the live object's state.
        for _ in range(max_iterations):
            d.qpos[self.qids] = q
            mujoco.mj_kinematics(m, d)
            mujoco.mj_comPos(m, d)
            ep = target.position - d.site_xpos[self.site]
            er = rotation_error(target.rotation, d.site_xmat[self.site].reshape(3, 3))
            if np.linalg.norm(ep) < 0.00015 and np.linalg.norm(er) < 0.0015:
                self.max_ik_position_error = max(self.max_ik_position_error, float(np.linalg.norm(ep)))
                self.max_ik_angle_error = max(self.max_ik_angle_error, float(np.linalg.norm(er)))
                return q
            mujoco.mj_jacSite(m, d, self.jacp, self.jacr, self.site)
            jac = np.vstack((self.jacp[:, self.dofs], 0.2 * self.jacr[:, self.dofs]))
            error = np.r_[ep, 0.2 * er]
            step = jac.T @ np.linalg.solve(jac @ jac.T + 1e-5 * np.eye(6), error)
            step *= min(1.0, 0.10 / max(1e-12, np.max(np.abs(step))))
            q = np.clip(q + step, self.limits[:, 0] + 0.002, self.limits[:, 1] - 0.002)
        raise PlanningFailure(f"IK did not converge: position={np.linalg.norm(ep):.6g} m, rotation={np.linalg.norm(er):.6g} rad")

    def unsafe_contacts(self, data, target_body, *, allow_fingers=False):
        failures = []
        for c in data.contact:
            if c.dist >= -0.00025:
                continue
            a, b = (int(self.model.geom_bodyid[g]) for g in (c.geom1, c.geom2))
            if a not in self.bodies and b not in self.bodies:
                continue
            if allow_fingers and ((a in self.fingers and b == target_body) or (b in self.fingers and a == target_body)):
                continue
            failures.append([int(c.geom1), int(c.geom2), float(c.dist)])
        return failures

    def check_path(self, poses, target_body):
        seed, start = self.data.qpos[self.qids].copy(), self.pose()
        for end in poses:
            for alpha in np.linspace(0, 1, 15)[1:]:
                seed = self.solve(interpolate_pose(start, end, float(alpha)), seed)
                self.ik_data.qpos[self.qids] = seed
                mujoco.mj_forward(self.model, self.ik_data)
                if self.unsafe_contacts(self.ik_data, target_body):
                    raise PlanningFailure("collision on sampled approach path")
            start = end

    def command(self, q, gripper):
        self.data.ctrl[self.act] = q
        self.data.ctrl[self.gripper] = gripper
        self.data.qfrc_applied[self.dofs] = self.data.qfrc_bias[self.dofs]
