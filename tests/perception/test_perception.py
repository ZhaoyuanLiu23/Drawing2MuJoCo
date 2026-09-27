"""Independent RGB-D, registration, no-GT-input and physical grasp acceptance."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import mujoco
import numpy as np

from cad_mujoco.mesh import write_stl
from cad_mujoco.pipeline import convert
from manipulation.geometry import matrix_quat, quat_matrix
from perception.camera import CameraConfig, Frame
from perception.evaluation import pose_error, state_pose
from perception.pipeline import VisionManipulator, run_pipeline
from perception.registration import PerceptionFailure, estimate_pose


ROOT = Path(__file__).resolve().parents[2]
PANDA = os.environ.get("CAD_MUJOCO_PANDA_SCENE")


def box(size):
    vertices = (np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                          [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], dtype=float) - .5) * size
    return vertices[[[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
                     [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]]


def yaw_quat(degrees):
    angle = np.deg2rad(degrees) / 2
    return np.array([np.cos(angle), 0, 0, np.sin(angle)])


class FrameTests(unittest.TestCase):
    def test_invalid_camera_calibration_rejected(self):
        for config in (CameraConfig(offset_m=(0, 0, 0)), CameraConfig(fovy=float("nan")), CameraConfig(width=-1)):
            with self.assertRaises(ValueError):
                config.validate()

    def test_metric_axial_depth_projection_round_trip(self):
        depth = np.linspace(.4, 1.2, 48).reshape(6, 8)
        frame = Frame(np.zeros((6, 8, 3), np.uint8), depth, np.zeros((6, 8, 2), int),
                      np.array([.1, -.3, .7]), quat_matrix([.923879533, .382683432, 0, 0]), 17., 0.)
        points = frame.unproject(np.ones((6, 8), bool))
        u, v = np.meshgrid(np.arange(8), np.arange(6))
        np.testing.assert_allclose(frame.project(points), np.column_stack((u.ravel(), v.ravel())), atol=1e-8)
        local = (points - frame.camera_position) @ frame.camera_rotation
        np.testing.assert_allclose(-local[:, 2], depth.ravel(), atol=1e-8)

    def test_empty_segmentation_fails_without_pose_input(self):
        frame = Frame(np.zeros((40, 40, 3), np.uint8), np.ones((40, 40)), np.full((40, 40, 2), -1),
                      np.zeros(3), np.eye(3), 30., 0.)
        with self.assertRaisesRegex(PerceptionFailure, "insufficient segmented"):
            estimate_pose(frame, box([.1, .04, .01]), [7])

    def test_missing_scene_explicit_failure_and_no_stale_results(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "out"
            out.mkdir()
            (out / ".perception-output").touch()
            (out / "estimated_pose.json").write_text("old success")
            result = run_pipeline(Path(directory) / "missing.xml", out)
            self.assertFalse(result["success"])
            self.assertFalse(result["ground_truth_fallback"])
            self.assertFalse((out / "estimated_pose.json").exists())


@unittest.skipUnless(PANDA and Path(PANDA).is_file(), "Set CAD_MUJOCO_PANDA_SCENE for real rendered Panda acceptance")
class RenderedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="perception_tests_")
        cls.root = Path(cls.tmp.name)
        cls.scenes = {}
        cls.evidence = []
        for name, size, mass in (("small", [.091, .034, .008], .075), ("large", [.123, .052, .011], .14)):
            stl, parsed = cls.root / (name + ".stl"), cls.root / (name + ".json")
            write_stl(stl, box(size))
            parsed.write_text(json.dumps(dict(status="generated", units={"cad": "m"}, recipe={"unit": "m"})))
            out = cls.root / name
            _, drop = convert(stl, parsed, out, mass_kg=mass, base_scene=PANDA, duration=3.0)
            # This fixture supplies mesh/scene inputs for registration and grasp;
            # it is NOT a stable-drop acceptance test. Preserve the independent
            # conversion verdict; full_validation treats false as a release blocker.
            cls.evidence.append(dict(fixture=name, cad_drop_success=drop["success"],
                                     fixture_purpose="perception_and_grasp; not drop acceptance",
                                     drop_acceptance_claimed=False,
                                     cad_drop_checks=drop["checks"], tail_speed_m_s=drop["tail_max_linear_speed_m_s"],
                                     tail_height_range_m=drop["tail_height_range_m"],
                                     tail_contact_fraction=drop["tail_contact_fraction"]))
            assert drop["checks"]["no_numerical_warnings"], drop["checks"]
            cls.scenes[name] = out / "scene.xml"
        out = cls.root / "exported"
        source = ROOT / "examples/bracket/result"
        _, drop = convert(source / "model.stl", source / "parsed.json", out, density_kg_m3=7800, base_scene=PANDA, duration=1.5)
        assert drop["success"], drop
        cls.scenes["exported"] = out / "scene.xml"

    @classmethod
    def tearDownClass(cls):
        if os.environ.get("PERCEPTION_TEST_REPORT"):
            path = Path(os.environ["PERCEPTION_TEST_REPORT"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cls.evidence, indent=2, allow_nan=False), encoding="utf8")
        cls.tmp.cleanup()

    def sim(self, name="small"):
        sim = VisionManipulator(self.scenes[name])
        self.addCleanup(sim.close)
        return sim

    def place(self, sim, xy=(0, 0), yaw=0, rotation=None):
        adr = int(sim.model.jnt_qposadr[sim.object_joint])
        # Test setup before sensing/control. Production does not receive these values.
        sim.data.qpos[adr:adr + 2] += xy
        sim.data.qpos[adr + 3:adr + 7] = yaw_quat(yaw) if rotation is None else matrix_quat(rotation)
        mujoco.mj_forward(sim.model, sim.data)

    def trial(self, case, xy=(0, 0), yaw=0, close=True):
        sim = self.sim(case)
        self.place(sim, xy, yaw)
        # Fail immediately if the original GT observer is ever invoked (incl. summary).
        with patch("manipulation.pipeline.object_pose", side_effect=AssertionError("GT observer forbidden")), \
             patch("manipulation.geometry.object_pose", side_effect=AssertionError("GT observer forbidden")):
            result = sim.run(close_gripper=close)
        self.evidence.append(dict(test=self.id(), case=case, xy=list(xy), yaw_degrees=yaw, result=result))
        result["source_scene"] = "fixture/" + case
        self.assertFalse(result["ground_truth_fallback"])
        self.assertEqual(result["pose_source"], "vision")
        self.assertTrue(result["perception_success"])
        self.assertTrue(result["grasp_execution_completed"])
        self.assertIsNone(result["visual_task_success"])
        self.assertEqual(result["offline_ground_truth_metrics"]["grasp_acceptance_success"], result["success"])
        error = result["pose_evaluation"]
        self.assertLess(error["position_error_m"], .0015)
        # For asymmetric CAD, ambiguity is retained and raw angle is reported, not hidden.
        if error["verified_mesh_symmetry_count"] > 1:
            self.assertLess(error["symmetry_aware_rotation_error_deg"], 1.)
        self.assertTrue(result["estimated_pose"]["diagnostics"]["orientation_ambiguous"])
        if close:
            self.assertTrue(result["success"], result)
            self.assertGreater(result["minimum_hold_table_clearance_m"], .10)
            self.assertGreater(result["hold_duration_s"], 1.9)
            self.assertGreaterEqual(result["hold_bilateral_fraction"], .99)
        else:
            self.assertFalse(result["success"])
            self.assertFalse(result["checks"]["lifted_clear_of_table"])
        np.testing.assert_allclose(sim.data.xfrc_applied[sim.object_id], 0)
        np.testing.assert_allclose(sim.data.qfrc_applied[sim.object_dof:sim.object_dof + 6], 0)
        self.assertFalse(np.any(sim.model.eq_type == mujoco.mjtEq.mjEQ_WELD))

    def test_exported_cad_vision_grasp(self):
        self.trial("exported")

    def test_translation_vision_grasp(self):
        self.trial("exported", xy=(-.058, .074))

    def test_yaw_vision_grasp(self):
        self.trial("exported", yaw=53)

    def test_smaller_plate_xy_yaw_grasp(self):
        self.trial("small", xy=(-.029, -.046), yaw=-31)

    def test_larger_plate_xy_yaw_grasp(self):
        self.trial("large", xy=(.021, .038), yaw=104)

    def test_open_gripper_negative_control(self):
        self.trial("small", close=False)

    def test_rendered_depth_and_six_dof_registration(self):
        sim = self.sim()
        # Small out-of-plane tilt tests plane-derived roll/pitch, independent of grasps.
        tilt = quat_matrix([np.cos(.06), np.sin(.06), 0, 0])
        self.place(sim, xy=(-.043, -.067), rotation=quat_matrix(yaw_quat(27)) @ tilt)
        frame = sim.camera.capture(sim.data)
        estimate = estimate_pose(frame, sim.triangles, sim.target_geom_ids)
        truth = state_pose(sim.data.qpos, int(sim.model.jnt_qposadr[sim.object_joint]))
        error = pose_error(estimate.pose, truth, sim.triangles)
        self.assertLess(error["position_error_m"], .0015)
        self.assertLess(error["symmetry_aware_rotation_error_deg"], 1.)
        self.assertEqual(frame.rgb.shape, (720, 960, 3))
        self.assertEqual(frame.depth.dtype, np.float32)
        self.assertTrue(np.isfinite(estimate.points).all())
        self.evidence.append(dict(test=self.id(), pose_evaluation=error))

    def test_invalid_depth_and_partial_occlusion_fail(self):
        sim = self.sim()
        self.place(sim, xy=(-.04, -.06))
        frame = sim.camera.capture(sim.data)
        mask = frame.mask(sim.target_geom_ids)
        good_depth = frame.depth.copy()
        frame.depth[mask] = np.nan
        with self.assertRaisesRegex(PerceptionFailure, "invalid target depth"):
            estimate_pose(frame, sim.triangles, sim.target_geom_ids)
        frame.depth = good_depth
        u = np.nonzero(mask)[1]
        frame.segmentation[:, int((u.min() + u.max()) / 2):] = -1
        with self.assertRaises(PerceptionFailure):
            estimate_pose(frame, sim.triangles, sim.target_geom_ids)

    def test_no_gt_fallback_when_target_missing(self):
        sim = self.sim()
        with patch.object(sim.camera, "capture", wraps=sim.camera.capture) as capture:
            sim.target_geom_ids = [-999]
            result = sim.run()
        self.assertFalse(result["success"])
        self.assertFalse(result["ground_truth_fallback"])
        self.assertIsNone(result["estimated_pose"])
        self.assertFalse(result["perception_success"])
        self.assertFalse(result["grasp_execution_completed"])
        self.assertIsNone(result["visual_task_success"])
        self.assertIsNone(result["offline_ground_truth_metrics"])
        self.assertIn("insufficient segmented", result["failure"]["reason"])
        self.assertEqual(result["failure"]["stage"], "perception")
        self.assertIsNone(sim.selected)
        self.assertEqual(capture.call_count, 1)

    def test_planning_state_uses_estimate_not_live_target_state(self):
        sim = self.sim()
        self.place(sim, xy=(-.037, -.06))
        estimate = sim.observe()
        adr = int(sim.model.jnt_qposadr[sim.object_joint])
        # Poison both state representations after capture. No GT-dependent pose input.
        sim.data.qpos[adr:adr + 7] = np.nan
        sim.data.xpos[sim.object_id] = np.nan
        sim.data.xquat[sim.object_id] = np.nan
        with patch.object(sim.camera, "capture", side_effect=AssertionError("snapshot must be immutable")):
            np.testing.assert_array_equal(sim.observe().position, estimate.position)
            planning = sim.panda.data.qpos
        np.testing.assert_allclose(planning[adr:adr + 3], estimate.position)
        np.testing.assert_allclose(planning[adr + 3:adr + 7], matrix_quat(estimate.rotation))
        self.assertTrue(np.isfinite(planning).all())


if __name__ == "__main__":
    unittest.main()
