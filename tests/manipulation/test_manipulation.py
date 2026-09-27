"""Geometry equivariance and real Panda contact-driven grasp acceptance."""
import json
import os
from pathlib import Path
import tempfile
import unittest

import mujoco
import numpy as np

from cad_mujoco.mesh import write_stl
from cad_mujoco.pipeline import convert
from manipulation.geometry import Pose, generate_candidates, matrix_quat, quat_matrix
from manipulation.panda import PlanningFailure, rotation_error
from manipulation.pipeline import Manipulator, run_pipeline


ROOT = Path(__file__).resolve().parents[2]
PANDA = os.environ.get("CAD_MUJOCO_PANDA_SCENE")


def box(size):
    vertices = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                         [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], dtype=float)
    vertices = (vertices - 0.5) * np.asarray(size)
    return vertices[[[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
                     [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5],
                     [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]]


def z_rotation(angle):
    return np.array([[np.cos(angle), -np.sin(angle), 0],
                     [np.sin(angle), np.cos(angle), 0], [0, 0, 1]])


class GeometryTests(unittest.TestCase):
    calibration = dict(open_gap_m=0.08, pad_tip_recess_m=0.00086, pad_half_width_m=0.0085)

    def test_grasps_transform_with_live_position_and_orientation(self):
        triangles = box([0.09, 0.036, 0.008])
        origin = Pose(np.zeros(3), np.eye(3))
        moved = Pose(np.array([0.43, -0.073, 0.18]), z_rotation(0.67))
        candidates, _ = generate_candidates(triangles, origin, self.calibration)
        changed, _ = generate_candidates(triangles, moved, self.calibration)
        self.assertTrue(candidates)
        self.assertEqual([c.id for c in candidates], [c.id for c in changed])
        for before, after in zip(candidates, changed):
            for a, b in zip(before.poses(origin), after.poses(moved)):
                np.testing.assert_allclose(b.position, moved.position + moved.rotation @ a.position, atol=1e-14)
                np.testing.assert_allclose(b.rotation, moved.rotation @ a.rotation, atol=1e-14)

    def test_candidate_widths_are_measured_not_fixed(self):
        for length, width, thick in ((0.11, 0.028, 0.007), (0.064, 0.043, 0.014), (0.15, 0.063, 0.009)):
            candidates, _ = generate_candidates(box([length, width, thick]), Pose(np.zeros(3), np.eye(3)), self.calibration)
            self.assertTrue(candidates)
            self.assertAlmostEqual(candidates[0].width_m, min(length, width))
            self.assertTrue(all(c.width_m + 0.006 <= self.calibration["open_gap_m"] for c in candidates))

    def test_oversized_thin_and_tilted_objects_reject_explicitly(self):
        for triangles, pose in ((box([0.15, 0.12, 0.008]), Pose(np.zeros(3), np.eye(3))),
                                 (box([0.1, 0.03, 0.0008]), Pose(np.zeros(3), np.eye(3))),
                                 (box([0.1, 0.03, 0.008]), Pose(np.zeros(3), quat_matrix([.9238795, .3826834, 0, 0])))):
            candidates, reasons = generate_candidates(triangles, pose, self.calibration)
            self.assertEqual(candidates, [])
            self.assertTrue(reasons)

    def test_aabb_alone_cannot_claim_exterior_face_support(self):
        triangles = box([0.12, 0.11, 0.01])
        # Rotate a solid in its body frame: the AABB edges now have no planar faces.
        triangles = (triangles * [0.35, 0.35, 1]) @ z_rotation(0.31).T
        candidates, reasons = generate_candidates(triangles, Pose(np.zeros(3), np.eye(3)), self.calibration)
        self.assertFalse(candidates)
        self.assertTrue(any("surface support" in reason for reason in reasons))

    def test_rotation_error_does_not_disappear_at_half_turn(self):
        np.testing.assert_allclose(np.linalg.norm(rotation_error(z_rotation(np.pi), np.eye(3))), np.pi)

    def test_invalid_input_writes_failure_not_stale_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "result"
            output.mkdir()
            (output / ".manipulation-output").write_text("test")
            (output / "result.json").write_text('{"success": true}')
            (output / "trajectory.npz").write_bytes(b"old trajectory")
            result = run_pipeline(root / "missing_scene.xml", output)
            self.assertFalse(result["success"])
            self.assertEqual(result["failure"]["stage"], "input")
            self.assertFalse((output / "trajectory.npz").exists())
            self.assertFalse(json.loads((output / "result.json").read_text())["success"])


@unittest.skipUnless(PANDA and Path(PANDA).is_file(), "Set CAD_MUJOCO_PANDA_SCENE to run real Panda grasp tests")
class PhysicalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="manipulation_tests_")
        cls.root = Path(cls.temporary.name)
        cls.scenes = {}
        # These are synthetic test inputs, never read by production candidate generation.
        for name, size, mass in (("small", [0.084, 0.036, 0.010], 0.08),
                                  ("large", [0.128, 0.054, 0.012], 0.15)):
            stl, parsed = cls.root / (name + ".stl"), cls.root / (name + ".json")
            write_stl(stl, box(size))
            parsed.write_text(json.dumps(dict(status="generated", units={"cad": "m"}, recipe={"unit": "m"})), encoding="utf8")
            out = cls.root / name
            _, drop = convert(stl, parsed, out, mass_kg=mass, base_scene=PANDA, duration=1.5)
            assert drop["success"], drop["checks"]
            cls.scenes[name] = out / "scene.xml"
        source = ROOT / "examples/bracket/result"
        out = cls.root / "exported_cad"
        _, drop = convert(source / "model.stl", source / "parsed.json", out,
                          density_kg_m3=7800, base_scene=PANDA, duration=1.5)
        assert drop["success"], drop["checks"]
        cls.scenes["exported_cad"] = out / "scene.xml"
        cls.evidence = []

    @classmethod
    def tearDownClass(cls):
        if os.environ.get("MANIPULATION_TEST_REPORT"):
            path = Path(os.environ["MANIPULATION_TEST_REPORT"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(cls.evidence, ensure_ascii=False, indent=2), encoding="utf8")
        cls.temporary.cleanup()

    def trial(self, case, *, translation=(0, 0, 0), yaw=0.0, close=True):
        sim = Manipulator(self.scenes[case])
        qadr = sim.model.jnt_qposadr[sim.object_joint]
        # Fixture placement only, before any simulation or controller observation.
        sim.data.qpos[qadr:qadr + 3] += translation
        sim.data.qpos[qadr + 3:qadr + 7] = matrix_quat(z_rotation(yaw))
        mujoco.mj_forward(sim.model, sim.data)
        initial = sim.observe()
        body_mass = float(sim.model.body_mass[sim.object_id])
        result = sim.run(close_gripper=close)
        result["source_scene"] = "fixture/" + case
        self.evidence.append(dict(test=self.id(), case=case, translation=list(translation), yaw_rad=yaw, result=result))
        self.assertIsNone(result["perception_success"])
        self.assertTrue(result["grasp_execution_completed"])
        self.assertIsNone(result["visual_task_success"])
        self.assertEqual(result["success"], result["offline_ground_truth_metrics"]["grasp_acceptance_success"])
        self.assertEqual(float(sim.model.body_mass[sim.object_id]), body_mass)
        np.testing.assert_allclose(sim.data.xfrc_applied[sim.object_id], 0)
        np.testing.assert_allclose(sim.data.qfrc_applied[sim.object_dof:sim.object_dof + 6], 0)
        self.assertFalse(np.any(sim.model.eq_type == mujoco.mjtEq.mjEQ_WELD))
        np.testing.assert_allclose(result["observed_grasp_start"]["position_m"][:2], initial.position[:2], atol=0.002)
        if close:
            self.assertTrue(result["success"], result)
            self.assertGreater(result["minimum_hold_table_clearance_m"], 0.10)
            self.assertGreaterEqual(result["hold_bilateral_fraction"], 0.99)
            self.assertGreater(result["hold_duration_s"], 1.9)
            self.assertLess(result["maximum_relative_slip_m"], 0.003)
            self.assertLess(result["maximum_relative_rotation_rad"], 0.05)
        return sim, result

    def test_existing_exported_cad_grasp_lift_and_hold(self):
        self.trial("exported_cad")

    def test_translated_target_grasp(self):
        self.trial("exported_cad", translation=(-0.065, 0.085, 0))

    def test_z_rotated_target_grasp(self):
        sim, result = self.trial("exported_cad", yaw=np.deg2rad(57))
        q = np.array(result["observed_grasp_start"]["quaternion_wxyz"])
        self.assertGreater(abs(q @ matrix_quat(z_rotation(np.deg2rad(57)))), 0.999)

    def test_different_small_plate(self):
        _, result = self.trial("small", translation=(-0.025, -0.055, 0), yaw=np.deg2rad(-28))
        self.assertAlmostEqual(min(np.ptp(result["object_local_bounds_m"], axis=0)[:2]), 0.036, places=5)

    def test_different_large_plate(self):
        self.trial("large", translation=(0.025, 0.04, 0), yaw=np.deg2rad(90))

    def test_open_gripper_is_not_a_success(self):
        sim, result = self.trial("small", close=False)
        self.assertFalse(result["success"])
        self.assertFalse(result["checks"]["lifted_clear_of_table"])
        self.assertEqual(result["hold_bilateral_fraction"], 0)
        self.assertEqual(result["failure"]["stage"], "evaluation")
        self.assertLess(abs(sim.bottom() - sim.table_top), 0.001)

    def test_unreachable_ik_is_explicit_failure(self):
        sim = Manipulator(self.scenes["small"])
        pose = sim.panda.pose()
        target = Pose(pose.position + [2, 1, 2], pose.rotation)
        initial = sim.data.qpos.copy()
        with self.assertRaises(PlanningFailure):
            sim.panda.solve(target, sim.data.qpos[sim.panda.qids], max_iterations=60)
        np.testing.assert_array_equal(sim.data.qpos, initial)


if __name__ == "__main__":
    unittest.main()
