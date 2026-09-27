"""Vision adapter around the unchanged candidate/IK/approach/close/lift controller."""
from pathlib import Path
import hashlib
import json
import tempfile

import mujoco
import numpy as np
from PIL import Image

from cad_mujoco.mjcf import load_spec, save_spec
from manipulation.geometry import matrix_quat
from manipulation.pipeline import Manipulator
from .camera import CameraConfig, RGBDCamera, add_camera
from .registration import PerceptionFailure, estimate_pose


class RobotStateView:
    """Feed the existing IK only robot encoders and the estimated target transform.

    No live target qpos enters the planning scratch world. Control writes (ctrl,
    robot bias compensation) still target the original simulation data.
    """
    def __init__(self, sim):
        self.sim = sim
        self.robot_addresses = []
        for joint in range(sim.model.njnt):
            if int(sim.model.jnt_bodyid[joint]) in sim.panda.bodies:
                if sim.model.jnt_type[joint] not in (mujoco.mjtJoint.mjJNT_HINGE, mujoco.mjtJoint.mjJNT_SLIDE):
                    raise ValueError("RobotStateView expects fixed-base Panda scalar joints")
                self.robot_addresses.append(int(sim.model.jnt_qposadr[joint]))

    @property
    def qpos(self):
        sim = self.sim
        q = sim.model.qpos0.copy()
        q[self.robot_addresses] = sim.data.qpos[self.robot_addresses]
        pose = sim.observe()
        adr = int(sim.model.jnt_qposadr[sim.object_joint])
        q[adr:adr + 3] = pose.position
        q[adr + 3:adr + 7] = matrix_quat(pose.rotation)
        return q

    def __getattr__(self, name):
        return getattr(self.sim.data, name)


class VisionManipulator(Manipulator):
    def __init__(self, scene_path, *, camera_config=None, **kwargs):
        self.original_scene = Path(scene_path).resolve()
        self.camera_config = camera_config or CameraConfig()
        self._temporary = tempfile.TemporaryDirectory(prefix="rgbd_scene_")
        camera_scene = Path(self._temporary.name) / "scene.xml"
        try:
            add_camera(scene_path, camera_scene, kwargs.get("table_name", "cad_test_table"), self.camera_config)
            super().__init__(camera_scene, **kwargs)
            self.camera = RGBDCamera(self.model, self.camera_config)
        except Exception:
            self._temporary.cleanup()
            raise
        self.target_geom_ids = [g for g in range(self.model.ngeom) if self.model.geom_bodyid[g] == self.object_id
                                and not (self.model.geom_contype[g] or self.model.geom_conaffinity[g])]
        self.estimate = None
        self.frame = None
        self.evaluation_capture_state = None
        self.attempt_finished = False
        self.panda.data = RobotStateView(self)

    def observe(self):
        if self.estimate is None:
            if self.attempt_finished:
                raise PerceptionFailure("perception: no valid estimate; ground-truth fallback forbidden")
            self.frame = self.camera.capture(self.data)
            self.estimate = estimate_pose(self.frame, self.triangles, self.target_geom_ids)
            # Opaque recording for AFTER the attempt only. Not passed to registration/IK.
            self.evaluation_capture_state = self.data.qpos.copy()
        return self.estimate.pose

    def record(self, force=False):
        if not force and self.data.time - self.last_record < self.config.control_dt * .99:
            return
        self.last_record = float(self.data.time)
        bilateral, table, normals = self.contacts()
        self.records.append(dict(time_s=float(self.data.time), stage=self.stage, tcp_pose=self.panda.pose().json(),
                                 bilateral=bilateral, table_contact=table,
                                 normal_forces_N={str(k): v for k, v in normals.items()},
                                 speed_m_s=float(np.linalg.norm(self.data.qvel[self.object_dof:self.object_dof + 3]))))
        self.trajectory.append(self.data.qpos.copy())  # Archive only; inaccessible to pose estimator.
        if self.callback is not None:
            self.callback(self)

    def summary(self, failure, close_gripper):
        self.attempt_finished = True
        if failure and failure["reason"].startswith("perception:"):
            failure = dict(stage="perception", during_stage=failure["stage"], reason=failure["reason"])
        # First and only import/use of target-state evaluation, after execution ends.
        from .evaluation import pose_error, reconstruct_records, state_pose
        reconstruct_records(self)
        if self.estimate is None:
            return dict(success=False, checks={}, failure=failure or dict(stage="perception", reason="no estimate"),
                        pose_source="vision", ground_truth_fallback=False, estimated_pose=None,
                        perception_success=False, grasp_execution_completed=False, visual_task_success=None,
                        offline_ground_truth_metrics=None, success_semantics="legacy offline grasp acceptance; no valid perception")
        result = super().summary(failure, close_gripper)
        address = int(self.model.jnt_qposadr[self.object_joint])
        truth = state_pose(self.evaluation_capture_state, address)
        result["pose_evaluation"] = pose_error(self.estimate.pose, truth, self.triangles)
        result["perception_success"] = True
        result["offline_ground_truth_metrics"]["pose_error"] = result["pose_evaluation"]
        result["final_object_pose"] = state_pose(self.trajectory[-1], address).json()
        result["final_object_pose_source"] = "offline simulator evaluation only"
        result.update(pose_source="vision", observation_source="fixed RGB-D + segmentation + known CAD; world metres / wxyz",
                      source_scene=str(self.original_scene), source_scene_sha256=hashlib.sha256(self.original_scene.read_bytes()).hexdigest(),
                      ground_truth_fallback=False, estimated_pose=self.estimate.json(),
                      success_evaluation="offline simulator trajectory, contact telemetry and unchanged hold checks")
        result["assumptions"] += ["Single observation after settling; target assumed static during approach/descend. No visual tracking during lift.",
                                  "Known metric mesh, calibrated fixed camera, simulator instance segmentation (not learned detection).",
                                  "Rectangular exterior plate with visible broad face; reject insufficient pixels, clipping, extent or silhouette mismatch.",
                                  "Symmetric objects yield multiple pose hypotheses. Raw and CAD-symmetry-aware errors reported separately.",
                                  "Simulation contact/velocity guards remain; this is pose perception, not a fully sensor-only robot controller."]
        for candidate in self.candidates:
            candidate["source"] = "mesh exterior planar faces + RGB-D estimated pose (single pre-grasp observation)"
        return result

    def close(self):
        self.camera.close()
        self._temporary.cleanup()


def run_pipeline(scene, output, **options):
    scene, output = Path(scene).resolve(), Path(output).resolve()
    if scene == output or output in scene.parents:
        raise ValueError("Vision output must not contain the input scene")
    marker = output / ".perception-output"
    if output.exists() and any(output.iterdir()) and not marker.is_file():
        raise ValueError("Choose an empty directory or existing perception output")
    output.mkdir(parents=True, exist_ok=True)
    marker.write_text("RGB-D perception generated evidence\n", encoding="utf8")
    for name in ("result.json", "estimated_pose.json", "rgb.png", "depth.npy", "segmentation.npy", "pointcloud.npz",
                 "camera.json", "camera_scene.xml", "trace.json", "candidates.json", "trajectory.npz"):
        (output / name).unlink(missing_ok=True)
    sim = None
    try:
        sim = VisionManipulator(scene, **options)
        # Persist the independent camera-augmented scene; original XML stays intact.
        save_spec(load_spec(sim.scene_path), output / "camera_scene.xml")
        result = sim.run()
        values = [("trace.json", sim.records), ("candidates.json", sim.candidates)]
        if sim.estimate is not None:
            values.append(("estimated_pose.json", sim.estimate.json()))
            np.savez_compressed(output / "pointcloud.npz", world_points_m=sim.estimate.points)
        if sim.frame is not None:
            frame = sim.frame
            Image.fromarray(frame.rgb).save(output / "rgb.png")
            np.save(output / "depth.npy", frame.depth)
            np.save(output / "segmentation.npy", frame.segmentation)
            values.append(("camera.json", dict(position_m=frame.camera_position.tolist(), rotation=frame.camera_rotation.tolist(),
                                                focal_pixels=frame.focal, width=frame.depth.shape[1], height=frame.depth.shape[0],
                                                convention="camera X right, Y up, forward -Z; axial depth in metres",
                                                target_geom_ids=sim.target_geom_ids, time_s=frame.time)))
        for filename, value in values:
            (output / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf8")
        np.savez_compressed(output / "trajectory.npz", qpos=np.array(sim.trajectory), time=np.array([r["time_s"] for r in sim.records]))
    except (ValueError, RuntimeError, OSError) as exc:
        result = dict(success=False, checks={}, pose_source="vision", ground_truth_fallback=False,
                      perception_success=False, grasp_execution_completed=False, visual_task_success=None,
                      offline_ground_truth_metrics=None, success_semantics="legacy grasp result; input failure",
                      failure=dict(stage="perception_input", reason=str(exc)))
    finally:
        if sim is not None:
            sim.close()
    (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf8")
    return result
