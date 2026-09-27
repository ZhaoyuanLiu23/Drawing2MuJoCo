"""Vision-only task decisions; simulated rigid-body motion is contact-driven."""
from dataclasses import asdict
from pathlib import Path
import json
import tempfile

import mujoco
import numpy as np
from PIL import Image

from cad_mujoco.mjcf import load_spec, save_spec
from manipulation.geometry import Pose, generate_candidates
from manipulation.panda import PlanningFailure, interpolate_pose
from perception.camera import CameraConfig
from perception.pipeline import VisionManipulator
from perception.registration import estimate_pose, PerceptionFailure
from .planning import TaskConfig, compose, inverse, place_plan, world_bounds
from .scene import add_target_zone
from .verification import verify_estimates


class PickPlaceTask(VisionManipulator):
    def __init__(self, scene_path, *, target_xy, zone_size, task_config=None, **kwargs):
        self.task_config = task_config or TaskConfig()
        self.task_config.validate()
        self._zone_temporary = tempfile.TemporaryDirectory(prefix="pick_place_scene_")
        zone_scene = Path(self._zone_temporary.name) / "scene.xml"
        # More pixels over the full transfer workspace; unchanged registration gates.
        kwargs.setdefault("camera_config", CameraConfig(width=1440, height=1080))
        try:
            self.zone = add_target_zone(scene_path, zone_scene, target_xy, zone_size, kwargs.get("table_name", "cad_test_table"))
            super().__init__(zone_scene, **kwargs)
        except Exception:
            self._zone_temporary.cleanup()
            raise
        self.original_scene = Path(scene_path).resolve()
        self.pose_belief = None
        self.held_transform = None
        self.planning_pose = None
        self.observations, self.snapshots, self.capture_states = [], [], []
        self.perception_attempts = 0  # Report failures even when camera capture raises before a frame exists.
        self.stages, self.plans = [], []
        self.verification = None
        self.released = False
        self.retreated = False
        self.retreat_time = None
        self.initial_pose = None
        self.home_tcp = None

    def observe(self):
        # This is a belief interface for the unchanged IK adapter, never a GT getter.
        if self.planning_pose is not None:
            return self.planning_pose
        if self.held_transform is not None:
            return compose(self.panda.pose(), self.held_transform)
        if self.pose_belief is None:
            raise PerceptionFailure("perception: no visual pose; ground-truth fallback forbidden")
        return self.pose_belief

    def sense(self, label):
        self.perception_attempts += 1
        frame = self.camera.capture(self.data)
        self.snapshots.append((label, frame))  # Keep failed captures, too.
        if frame.time < self.data.time - self.model.opt.timestep:
            raise PerceptionFailure("perception: stale camera frame")
        estimate = estimate_pose(frame, self.triangles, self.target_geom_ids)
        self.observations.append((label, estimate))
        self.capture_states.append(self.data.qpos.copy())  # Opaque offline archive.
        self.estimate, self.frame = estimate, frame
        self.pose_belief = estimate.pose
        return estimate

    def record(self, force=False):
        if not force and self.data.time - self.last_record < self.config.control_dt * .99:
            return
        self.last_record = float(self.data.time)
        if not self.stages or self.stages[-1]["stage"] != self.stage:
            self.stages.append(dict(stage=self.stage, time_s=float(self.data.time)))
        self.records.append(dict(time_s=float(self.data.time), stage=self.stage, tcp_pose=self.panda.pose().json()))
        self.trajectory.append(self.data.qpos.copy())  # No target state decoding during execution.
        if self.callback is not None:
            self.callback(self)

    def physics(self, target_q, opening, seconds, *, allow_fingers=False, monitor=True):
        holding_stages = {"close", "lift", "lift_hold", "transfer_raise", "move", "place", "release", "retreat"}
        # Only original robot collision/numerical guards; no GT target pose or velocity decisions.
        super().physics(target_q, opening, seconds, allow_fingers=allow_fingers or self.stage in holding_stages, monitor=monitor)

    def plan_place(self, tcp_object, current_tcp):
        plan = place_plan(self.vertices, self.initial_pose, tcp_object, self.zone, current_tcp, self.task_config)
        self.plans.append({name: pose.json() for name, pose in plan.items()})
        return plan

    def check_transport(self, start, poses, tcp_object=None, initial_seed=None):
        """Sampled IK/collision checks in the existing scratch world, not live state."""
        seed = self.data.qpos[self.panda.qids].copy() if initial_seed is None else np.array(initial_seed, copy=True)
        try:
            for end in poses:
                for alpha in np.linspace(0, 1, 16)[1:]:
                    tcp = interpolate_pose(start, end, float(alpha))
                    self.planning_pose = compose(tcp, tcp_object) if tcp_object is not None else self.pose_belief
                    seed = self.panda.solve(tcp, seed)
                    mujoco.mj_forward(self.model, self.panda.ik_data)
                    if self.panda.unsafe_contacts(self.panda.ik_data, self.object_id, allow_fingers=True):
                        raise PlanningFailure("unsafe_sampled_robot_transfer_path")
                    if tcp_object is not None:
                        for contact in self.panda.ik_data.contact:
                            a, b = [int(self.model.geom_bodyid[g]) for g in (contact.geom1, contact.geom2)]
                            if self.object_id in (a, b) and contact.dist < -.00025:
                                other = b if a == self.object_id else a
                                if other not in self.panda.fingers:
                                    raise PlanningFailure("unsafe_sampled_carried_object_path")
                start = end
        finally:
            self.planning_pose = None

    def select_task_candidate(self):
        """Use the existing candidates; require the whole grasp-to-place path to fit.

        A grasp reachable at the source may have an unsuitable wrist orientation
        at the destination. Reject that candidate and try the next exterior edge.
        """
        candidates, rejects = generate_candidates(self.triangles, self.initial_pose, self.calibration,
                                                   pregrasp_clearance=self.config.pregrasp_clearance_m)
        self.candidates = [candidate.json(self.initial_pose) for candidate in candidates]
        self.rejections.extend(rejects)
        for candidate in candidates:
            pre, grasp = candidate.poses(self.initial_pose)
            try:
                self.panda.check_path([pre, grasp], self.object_id)
                grasp_seed = self.panda.ik_data.qpos[self.panda.qids].copy()
                relation = compose(inverse(grasp), self.initial_pose)
                lift = Pose(grasp.position + [0, 0, self.config.lift_distance_m], grasp.rotation)
                plan = self.plan_place(relation, lift)
                self.check_transport(grasp, [lift, plan["raise_pose"], plan["pre_place"], plan["place"]],
                                     relation, initial_seed=grasp_seed)
                self.selected = candidate
                return candidate
            except PlanningFailure as exc:
                self.rejections.append(dict(candidate=candidate.id, stage="full_task_planning", reason=str(exc)))
        raise PlanningFailure("no_feasible_grasp_and_place_candidate")

    def open_gripper(self, q):
        self.stage = "release"
        ticks = max(1, round(self.task_config.release_seconds / self.config.control_dt))
        for i in range(ticks):
            opening = self.panda.closed + (self.panda.open - self.panda.closed) * (i + 1) / ticks
            self.physics(q, opening, self.task_config.release_seconds / ticks, allow_fingers=True)
        self.physics(q, self.panda.open, .3, allow_fingers=True)
        fingers = [self.model.joint(name).qposadr[0] for name in ("finger_joint1", "finger_joint2")]
        self.released = bool(self.data.qpos[fingers].sum() >= .95 * self.calibration["open_gap_m"])
        if not self.released:
            raise PlanningFailure("gripper_did_not_open")

    def run(self, *, callback=None):
        if self.records or self.attempt_finished:
            raise ValueError("Create a new task instance for every attempt")
        self.callback = callback
        failure = None
        grasp_completed = False  # Execution telemetry, not evidence of a held object.
        c, t = self.config, self.task_config
        try:
            if np.any(self.model.eq_type == mujoco.mjtEq.mjEQ_WELD):
                raise PlanningFailure("weld_constraints_not_supported")
            self.stage = "settle"
            self.physics(self.data.qpos[self.panda.qids].copy(), self.panda.open, c.settle_seconds)
            self.home_tcp = self.panda.pose()
            self.stage = "observe"
            first = self.sense("initial_0")
            self.physics(self.data.qpos[self.panda.qids].copy(), self.panda.open, t.verify_interval_s)
            second = self.sense("initial_1")
            a, b = world_bounds(self.vertices, first.pose), world_bounds(self.vertices, second.pose)
            if np.linalg.norm(a.mean(0) - b.mean(0)) > t.stability_tolerance_m or abs(b[0, 2] - self.table_top) > t.bottom_tolerance_m:
                raise PlanningFailure("initial_visual_pose_not_stable_on_table")
            self.initial_pose = second.pose
            if np.linalg.norm(b.mean(0)[:2] - self.zone.xy) <= t.center_tolerance_m:
                raise PlanningFailure("initial_object_already_at_target; no_pick_place_demonstrated")
            self.stage = "planning"
            self.record(force=True)
            candidate = self.select_task_candidate()
            pre, grasp = candidate.poses(self.initial_pose)
            self.motion("approach", lambda: pre, c.approach_seconds)
            self.motion("grasp", lambda: grasp, c.descend_seconds)
            self.stage = "close"
            q = self.data.qpos[self.panda.qids].copy()
            ticks = round(c.close_seconds / c.control_dt)
            for i in range(ticks):
                opening = self.panda.open + (self.panda.closed - self.panda.open) * (i + 1) / ticks
                self.physics(q, opening, c.close_seconds / ticks, allow_fingers=True)
            self.physics(q, self.panda.closed, .3, allow_fingers=True)
            # Encoder/vision rigid-grasp belief ONLY. It applies no physical constraint.
            self.held_transform = compose(inverse(self.panda.pose()), self.initial_pose)
            start = self.panda.pose()
            lift = Pose(start.position + [0, 0, c.lift_distance_m], start.rotation)
            q = self.motion("lift", lambda: lift, c.lift_seconds, opening=self.panda.closed)
            self.stage = "lift_hold"
            self.physics(q, self.panda.closed, .3, allow_fingers=True)
            grasp_completed = True
            plan = self.plan_place(self.held_transform, self.panda.pose())
            self.check_transport(self.panda.pose(), [plan["raise_pose"], plan["pre_place"], plan["place"]], self.held_transform)
            self.motion("transfer_raise", lambda: plan["raise_pose"], .5, opening=self.panda.closed)
            self.motion("move", lambda: plan["pre_place"], t.move_seconds, opening=self.panda.closed)
            q = self.motion("place", lambda: plan["place"], t.place_seconds, opening=self.panda.closed)
            self.open_gripper(q)
            self.held_transform = None
            self.pose_belief = plan["object_release"]  # Predicted release pose, not a measurement.
            current = self.panda.pose()
            retreat = Pose(current.position + [0, 0, t.transfer_clearance_m], current.rotation)
            self.check_transport(current, [retreat, self.home_tcp])
            self.motion("retreat", lambda: retreat, t.retreat_seconds)
            q = self.motion("clear_view", lambda: self.home_tcp, t.retreat_seconds)
            self.retreated = True
            self.retreat_time = float(self.data.time)
            self.stage = "visual_verify"
            verified = []
            for i in range(t.verify_frames):
                self.physics(q, self.panda.open, t.verify_interval_s)
                verified.append(self.sense("verify_" + str(i)))
            self.verification = verify_estimates(verified, self.vertices, self.zone, t, after_time=self.retreat_time,
                                                released=self.released, retreated=self.retreated, initial_pose=self.initial_pose)
            if not self.verification["success"]:
                raise PlanningFailure(self.verification["reason"] + ": " + str(self.verification.get("failed_checks")))
            self.stage = "done"
        except (PlanningFailure, ValueError, RuntimeError) as exc:
            failure = dict(stage=self.stage, reason=str(exc))
        self.record(force=True)
        self.attempt_finished = True
        # Task verdict is now frozen. Evaluation cannot turn a visual failure into success.
        result = dict(success=failure is None and self.stage == "done", failure=failure,
                      success_semantics="legacy alias: visual_task_success",
                      perception_success=bool(self.observations) and len(self.observations) == self.perception_attempts,
                      grasp_execution_completed=grasp_completed,
                      visual_task_success=failure is None and self.stage == "done",
                      checks=self.verification["checks"] if self.verification else {},
                      success_source="fresh post-release RGB-D only", pose_source="vision", ground_truth_fallback=False,
                      target_zone=self.zone.json(), task_config=asdict(t), controller_config=asdict(c),
                      stages=self.stages, plans=self.plans, visual_verification=self.verification,
                      chosen_candidate=self.selected.id if self.selected else None, rejected_candidates=self.rejections,
                      simulated_seconds=float(self.data.time), source_scene=str(self.original_scene),
                      observations=[dict(label=label, **e.json()) for label, e in self.observations],
                      assumptions=["Known metric rectangular plate mesh and fixed calibrated RGB-D with simulator segmentation.",
                                   "Observed yaw retained. Initial grasp transform assumed rigid during transport; no slip tracking.",
                                   "No target qpos/qvel writes, weld, object forces or GT pose input. Robot servo commands only.",
                                   "Sampled IK/collision checking in a scratch world; not a global obstacle-avoidance planner.",
                                   "Release above a calibrated horizontal table, then retreat to the initial robot view-clearing pose.",
                                   "Success is finite-window visual stability and containment, not indefinite stability or unique symmetric orientation."])
        for candidate in self.candidates:
            candidate["source"] = "known CAD outer edges + initial RGB-D estimate"
        from .evaluation import evaluate_offline
        result["offline_evaluation"] = evaluate_offline(self)
        result["offline_ground_truth_metrics"] = result["offline_evaluation"]
        return result

    def close(self):
        super().close()
        self._zone_temporary.cleanup()


def run_pipeline(scene, output, **options):
    scene, output = Path(scene).resolve(), Path(output).resolve()
    if scene == output or output in scene.parents:
        raise ValueError("Task output must not contain the source scene")
    marker = output / ".pick-place-output"
    if output.exists() and any(output.iterdir()) and not marker.is_file():
        raise ValueError("Choose an empty directory or an existing pick-place output")
    output.mkdir(parents=True, exist_ok=True)
    marker.write_text("Independent visual pick-place task evidence\n", encoding="utf8")
    for filename in ("result.json", "observations.json", "candidates.json", "trace.json", "trajectory.npz", "task_scene.xml"):
        (output / filename).unlink(missing_ok=True)
    # Only remove the previous generated capture names, never arbitrary user files.
    for old in output.glob("capture_*"):
        if old.is_file() and old.suffix in {".json", ".png", ".npy", ".npz"}:
            old.unlink()
    sim = None
    try:
        sim = PickPlaceTask(scene, **options)
        save_spec(load_spec(sim.scene_path), output / "task_scene.xml")
        result = sim.run()
        for filename, value in (("observations.json", result["observations"]), ("trace.json", sim.records), ("candidates.json", sim.candidates)):
            (output / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf8")
        for label, frame in sim.snapshots:
            prefix = output / ("capture_" + label)
            Image.fromarray(frame.rgb).save(str(prefix) + ".png")
            np.save(str(prefix) + "_depth.npy", frame.depth)
            np.save(str(prefix) + "_segmentation.npy", frame.segmentation)
            calibration = dict(camera_position_m=frame.camera_position.tolist(), camera_rotation=frame.camera_rotation.tolist(),
                               focal_pixels=frame.focal, time_s=frame.time, target_geom_ids=sim.target_geom_ids)
            Path(str(prefix) + "_camera.json").write_text(json.dumps(calibration, indent=2), encoding="utf8")
        for label, estimate in sim.observations:
            np.savez_compressed(output / ("capture_" + label + "_points.npz"), world_points_m=estimate.points)
        np.savez_compressed(output / "trajectory.npz", qpos=np.array(sim.trajectory), time=np.array([r["time_s"] for r in sim.records]))
    except (ValueError, RuntimeError, OSError, KeyError) as exc:
        result = dict(success=False, checks={}, failure=dict(stage="input", reason=str(exc)), ground_truth_fallback=False,
                      perception_success=False, grasp_execution_completed=False, visual_task_success=False,
                      offline_ground_truth_metrics=None, success_semantics="legacy alias: visual_task_success")
    finally:
        if sim is not None:
            sim.close()
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf8")
    return result
