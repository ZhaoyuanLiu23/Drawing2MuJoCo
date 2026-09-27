"""Observe -> candidates -> IK -> physical approach/close/lift/hold -> evidence."""
from dataclasses import asdict, dataclass
from pathlib import Path
import hashlib
import json

import mujoco
import numpy as np

from cad_mujoco.validation import initialize
from .geometry import Pose, generate_candidates, object_pose, object_triangles
from .panda import Panda, PlanningFailure, interpolate_pose, prepare_scene, rotation_error


@dataclass
class Config:
    control_dt: float = 0.01
    settle_seconds: float = 2.0
    approach_seconds: float = 2.5
    descend_seconds: float = 2.0
    close_seconds: float = 1.0
    lift_seconds: float = 2.5
    hold_seconds: float = 2.0
    lift_distance_m: float = 0.12
    pregrasp_clearance_m: float = 0.10
    gripper_stiffness_N_m: float = 1000.0
    minimum_lift_m: float = 0.06
    minimum_bilateral_fraction: float = 0.95
    maximum_hold_speed_m_s: float = 0.015
    maximum_relative_slip_m: float = 0.003
    maximum_relative_rotation_rad: float = 0.05

    def validate(self):
        if any(not np.isfinite(v) or v <= 0 for v in asdict(self).values()):
            raise ValueError("All controller parameters must be finite and positive")
        if self.hold_seconds < 0.5 or self.minimum_lift_m >= self.lift_distance_m:
            raise ValueError("Need >=0.5s hold and lift distance greater than minimum lift")
        if self.minimum_bilateral_fraction > 1:
            raise ValueError("Bilateral fraction cannot exceed one")


class Manipulator:
    def __init__(self, scene_path, *, object_name="cad_part", table_name="cad_test_table", config=None):
        self.config = config or Config()
        self.config.validate()
        self.scene_path = Path(scene_path).resolve()
        self.model, self.calibration = prepare_scene(self.scene_path, self.config.gripper_stiffness_N_m)
        self.data = initialize(self.model)
        self.panda = Panda(self.model, self.data)
        self.object_id = self.model.body(object_name).id
        self.table_id = self.model.geom(table_name).id
        self.triangles = object_triangles(self.model, self.object_id)
        self.vertices = np.unique(self.triangles.reshape(-1, 3), axis=0)
        joints = [j for j in range(self.model.njnt) if self.model.jnt_bodyid[j] == self.object_id]
        if len(joints) != 1 or self.model.jnt_type[joints[0]] != mujoco.mjtJoint.mjJNT_FREE:
            raise ValueError("Target must be an independent freejoint rigid body")
        self.object_dof = int(self.model.jnt_dofadr[joints[0]])
        self.object_joint = joints[0]
        if self.model.geom_type[self.table_id] != mujoco.mjtGeom.mjGEOM_BOX:
            raise ValueError("Phase one requires a horizontal box table")
        if not np.allclose(self.data.geom_xmat[self.table_id].reshape(3, 3)[:, 2], [0, 0, 1], atol=1e-6):
            raise ValueError("Table must be horizontal")
        self.table_top = float(self.data.geom_xpos[self.table_id, 2] + self.model.geom_size[self.table_id, 2])
        self.records, self.candidates, self.rejections = [], [], []
        self.trajectory = []
        self.selected = None
        self.stage = "initialized"
        self.callback = None
        self.step_observer = None  # Optional read-only display hook; no controller changes.
        self.last_record = -1.0

    def observe(self):
        return object_pose(self.data, self.object_id)

    def bottom(self):
        pose = self.observe()
        return float((self.vertices @ pose.rotation.T + pose.position)[:, 2].min())

    def contacts(self):
        fingers, table = set(), False
        normals = {}
        for i, contact in enumerate(self.data.contact):
            a, b = (int(self.model.geom_bodyid[g]) for g in (contact.geom1, contact.geom2))
            if self.object_id not in (a, b):
                continue
            if self.table_id in (contact.geom1, contact.geom2):
                table = True
            other = b if a == self.object_id else a
            if other in self.panda.fingers:
                force = np.zeros(6)
                mujoco.mj_contactForce(self.model, self.data, i, force)
                if force[0] > 0.01:
                    fingers.add(other)
                    normals[other] = normals.get(other, 0) + float(force[0])
        return len(fingers) == 2, table, normals

    def record(self, force=False):
        if not force and self.data.time - self.last_record < self.config.control_dt * 0.99:
            return
        self.last_record = float(self.data.time)
        pose, tcp = self.observe(), self.panda.pose()
        bilateral, table, normals = self.contacts()
        relative_position = tcp.rotation.T @ (pose.position - tcp.position)
        relative_rotation = tcp.rotation.T @ pose.rotation
        self.records.append(dict(time_s=float(self.data.time), stage=self.stage, object_pose=pose.json(),
                                 tcp_pose=tcp.json(), bottom_z_m=self.bottom(), bilateral=bilateral,
                                 table_contact=table, normal_forces_N={str(k): v for k, v in normals.items()},
                                 speed_m_s=float(np.linalg.norm(self.data.qvel[self.object_dof:self.object_dof + 3])),
                                 relative_position_m=relative_position.tolist(),
                                 relative_rotation=relative_rotation.tolist()))
        self.trajectory.append(self.data.qpos.copy())
        if self.callback is not None:
            self.callback(self)

    def physics(self, target_q, opening, seconds, *, allow_fingers=False, monitor=True):
        for _ in range(max(1, round(seconds / self.model.opt.timestep))):
            self.panda.command(target_q, opening)
            mujoco.mj_step(self.model, self.data)
            mujoco.mj_forward(self.model, self.data)
            if self.step_observer is not None:
                self.step_observer()
            if not np.isfinite(self.data.qpos).all() or not np.isfinite(self.data.qvel).all() or np.any(self.data.warning.number):
                raise PlanningFailure("numerical_failure")
            if monitor:
                collisions = self.panda.unsafe_contacts(self.data, self.object_id, allow_fingers=allow_fingers)
                if collisions:
                    raise PlanningFailure("execution_collision: " + str(collisions[0]))
            self.record()

    def motion(self, stage, target_function, seconds, *, opening=None):
        self.stage = stage
        start = self.panda.pose()
        q = self.data.qpos[self.panda.qids].copy()
        ticks = max(1, round(seconds / self.config.control_dt))
        for i in range(ticks):
            x = (i + 1) / ticks
            blend = x ** 3 * (10 + x * (-15 + 6 * x))
            desired = interpolate_pose(start, target_function(), blend)
            q = self.panda.solve(desired, q)
            self.physics(q, self.panda.open if opening is None else opening,
                         seconds / ticks, allow_fingers=stage in ("lift", "hold"))
        # Let the physical position servos reach the IK target before next phase.
        self.physics(q, self.panda.open if opening is None else opening, 0.25,
                     allow_fingers=stage in ("lift", "hold"))
        return q

    def select_candidate(self):
        pose = self.observe()
        candidates, rejects = generate_candidates(self.triangles, pose, self.calibration,
                                                   pregrasp_clearance=self.config.pregrasp_clearance_m)
        self.candidates = [c.json(pose) for c in candidates]
        self.rejections.extend(rejects)
        for candidate in candidates:
            pre, grasp = candidate.poses(pose)
            try:
                self.panda.check_path([pre, grasp], self.object_id)
                lift = Pose(grasp.position + [0, 0, self.config.lift_distance_m], grasp.rotation)
                self.panda.solve(lift, self.panda.ik_data.qpos[self.panda.qids])
                self.selected = candidate
                return candidate
            except PlanningFailure as exc:
                self.rejections.append(dict(candidate=candidate.id, reason=str(exc)))
        raise PlanningFailure("no_feasible_candidate")

    def run(self, *, close_gripper=True, callback=None):
        if self.records:
            raise ValueError("Create a fresh Manipulator for each attempt; old hold evidence is never reused")
        self.callback = callback
        failure = None
        self.stage = "settle"
        try:
            self.physics(self.data.qpos[self.panda.qids].copy(), self.panda.open,
                         self.config.settle_seconds)
            self.initial_pose = self.observe()
            self.initial_bottom = self.bottom()
            bilateral, table, _ = self.contacts()
            if not table or np.linalg.norm(self.data.qvel[self.object_dof:self.object_dof + 6]) > 0.02:
                raise PlanningFailure("object_not_settled_on_table")
            self.stage = "planning"
            candidate = self.select_candidate()
            self.motion("approach", lambda: candidate.poses(self.observe())[0], self.config.approach_seconds)
            self.motion("descend", lambda: candidate.poses(self.observe())[1], self.config.descend_seconds)
            self.stage = "close"
            q = self.data.qpos[self.panda.qids].copy()
            ticks = round(self.config.close_seconds / self.config.control_dt)
            for i in range(ticks):
                blend = (i + 1) / ticks
                closing = self.panda.open + blend * (self.panda.closed - self.panda.open) if close_gripper else self.panda.open
                self.physics(q, closing, self.config.close_seconds / ticks, allow_fingers=True)
            self.physics(q, closing, 0.3, allow_fingers=True)
            start_lift = self.panda.pose()
            lift = Pose(start_lift.position + [0, 0, self.config.lift_distance_m], start_lift.rotation.copy())
            q = self.motion("lift", lambda: lift, self.config.lift_seconds, opening=closing)
            self.stage = "hold"
            self.physics(q, closing, self.config.hold_seconds, allow_fingers=True)
            self.stage = "done"
        except PlanningFailure as exc:
            failure = dict(stage=self.stage, reason=str(exc))
        self.record(force=True)
        return self.summary(failure, close_gripper)

    def summary(self, failure, close_gripper):
        hold = [r for r in self.records if r["stage"] == "hold"]
        # All of the hold counts: no selecting only the final good frames.
        relative = np.array([r["relative_position_m"] for r in hold])
        angle = [float(np.linalg.norm(rotation_error(np.array(r["relative_rotation"]),
                                                     np.array(hold[0]["relative_rotation"])))) for r in hold]
        slip = float(np.max(np.linalg.norm(relative - relative[0], axis=1))) if hold else None
        lifted = min((r["bottom_z_m"] - self.table_top for r in hold), default=0.0)
        bilateral = float(np.mean([r["bilateral"] for r in hold])) if hold else 0.0
        max_speed = max((r["speed_m_s"] for r in hold), default=float("inf"))
        checks = dict(completed=failure is None and self.stage == "done",
                      sustained_bilateral_contact=bilateral >= self.config.minimum_bilateral_fraction,
                      lifted_clear_of_table=lifted >= self.config.minimum_lift_m,
                      no_table_contact_during_hold=bool(hold) and not any(r["table_contact"] for r in hold),
                      stable_linear_speed=max_speed <= self.config.maximum_hold_speed_m_s,
                      stable_relative_position=slip is not None and slip <= self.config.maximum_relative_slip_m,
                      stable_relative_orientation=bool(angle) and max(angle) <= self.config.maximum_relative_rotation_rad)
        success = all(checks.values())
        if not success and failure is None:
            failure = dict(stage="evaluation", reason="grasp_checks_failed",
                           failed_checks=[name for name, passed in checks.items() if not passed])
        return dict(success=success, checks=checks, failure=failure,
                    success_semantics="legacy alias: offline_ground_truth_metrics.grasp_acceptance_success",
                    perception_success=None, grasp_execution_completed=checks["completed"], visual_task_success=None,
                    offline_ground_truth_metrics=dict(grasp_acceptance_success=success, checks=checks.copy(),
                        used_for_task_success=False, evaluation_phase="after execution",
                        minimum_hold_table_clearance_m=float(lifted), hold_bilateral_fraction=bilateral,
                        maximum_hold_speed_m_s=float(max_speed) if hold else None,
                        maximum_relative_slip_m=slip, maximum_relative_rotation_rad=max(angle) if angle else None),
                    observation_source="MuJoCo ground-truth xpos/xquat; metres, world frame, quaternion wxyz",
                    source_scene=str(self.scene_path), source_scene_sha256=hashlib.sha256(self.scene_path.read_bytes()).hexdigest(),
                    config=asdict(self.config), robot_calibration=self.calibration,
                    object_local_bounds_m=[self.vertices.min(0).tolist(), self.vertices.max(0).tolist()],
                    observed_grasp_start=getattr(self, "initial_pose", self.observe()).json(),
                    final_object_pose=self.observe().json(), chosen_candidate=self.selected.id if self.selected else None,
                    candidate_count=len(self.candidates), rejected_candidates=self.rejections,
                    grip_enabled=close_gripper, simulated_seconds=float(self.data.time),
                    hold_duration_s=(hold[-1]["time_s"] - hold[0]["time_s"]) if len(hold) > 1 else 0,
                    hold_bilateral_fraction=bilateral, minimum_hold_table_clearance_m=float(lifted),
                    maximum_hold_speed_m_s=float(max_speed) if hold else None,
                    maximum_relative_slip_m=slip, maximum_relative_rotation_rad=max(angle) if angle else None,
                    max_ik_position_error_m=self.panda.max_ik_position_error,
                    max_ik_orientation_error_rad=self.panda.max_ik_angle_error,
                    assumptions=["Nearly horizontal rectangular plate with supported opposed exterior faces; top-down pinch only.",
                                 "Panda Menagerie named joints, tendon gripper and calibrated original collision pads.",
                                 "Current CAD2MuJoCo convex-hull collision; openings are not grasp candidates.",
                                 "Kinematic approach sampling is not a global collision-free motion planner.",
                                 "Object poses are never overwritten and no object force, weld or gravity compensation is applied."])


def run_pipeline(scene, output, **options):
    output = Path(output).resolve()
    scene = Path(scene).resolve()
    if scene == output or output in scene.parents:
        raise ValueError("Manipulation output must not contain the input scene")
    marker = output / ".manipulation-output"
    if output.exists() and any(output.iterdir()) and not marker.is_file():
        raise ValueError("Choose an empty directory or an existing manipulation output")
    output.mkdir(parents=True, exist_ok=True)
    marker.write_text("manipulation generated results\n", encoding="utf8")
    for name in ("result.json", "candidates.json", "trace.json", "trajectory.npz"):
        (output / name).unlink(missing_ok=True)
    try:
        sim = Manipulator(scene, **options)
    except (ValueError, KeyError, OSError) as exc:
        result = dict(success=False, checks={}, failure=dict(stage="input", reason=str(exc)),
                      perception_success=None, grasp_execution_completed=False, visual_task_success=None,
                      offline_ground_truth_metrics=None, success_semantics="legacy grasp result; input failure")
        (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
        return result
    result = sim.run()
    for filename, value in (("result.json", result), ("candidates.json", sim.candidates), ("trace.json", sim.records)):
        (output / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf8")
    np.savez_compressed(output / "trajectory.npz", qpos=np.array(sim.trajectory),
                        time=np.array([r["time_s"] for r in sim.records]))
    return result
