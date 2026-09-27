"""Task planning, camera-only verdicts and actual contact-driven Panda placement."""
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
from manipulation.geometry import Pose, matrix_quat, quat_matrix
from manipulation.panda import PlanningFailure
from perception.registration import Estimate
from pick_place_task.evaluation import evaluate_offline
from pick_place_task.pipeline import PickPlaceTask, run_pipeline
from pick_place_task.planning import TaskConfig, TargetZone, compose, inverse, place_plan, world_bounds
from pick_place_task.verification import verify_estimates


ROOT = Path(__file__).resolve().parents[2]
PANDA = os.environ.get("CAD_MUJOCO_PANDA_SCENE")


def box(size):
    vertices = (np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0],
                          [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1]], dtype=float) - .5) * size
    return vertices[[[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4],
                     [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]]


def rz(angle):
    return quat_matrix([np.cos(angle / 2), 0, 0, np.sin(angle / 2)])


class PlanningTests(unittest.TestCase):
    def test_random_geometry_transform_and_target_positions(self):
        rng = np.random.default_rng(837)
        config = TaskConfig()
        for _ in range(25):
            size = rng.uniform([.07, .025, .006], [.14, .065, .016])
            vertices = box(size).reshape(-1, 3)
            observed = Pose(rng.uniform([.35, -.1, .1], [.60, .1, .2]), rz(rng.uniform(-np.pi, np.pi)))
            tcp = Pose(observed.position + [0, 0, -.002], observed.rotation @ np.diag([1, -1, -1]))
            relation = compose(inverse(tcp), observed)
            zone = TargetZone(rng.uniform([.4, -.12], [.6, .12]), [.25, .25], .13)
            plan = place_plan(vertices, observed, relation, zone, tcp, config)
            realized = compose(plan["place"], relation)
            bounds = world_bounds(vertices, realized)
            np.testing.assert_allclose(bounds.mean(0)[:2], zone.xy, atol=1e-12)
            self.assertAlmostEqual(bounds[0, 2], zone.table_top + config.release_gap_m)
            self.assertGreaterEqual(plan["pre_place"].position[2] - plan["place"].position[2], config.transfer_clearance_m - 1e-12)

    def test_zone_too_small_rejected(self):
        pose = Pose(np.array([.5, 0, .15]), np.eye(3))
        with self.assertRaisesRegex(PlanningFailure, "too_small"):
            place_plan(box([.09, .04, .01]).reshape(-1, 3), pose, Pose(np.zeros(3), np.eye(3)),
                       TargetZone([.5, .1], [.02, .02], .13), pose, TaskConfig())

    def verify(self, *, xy=(.45, -.08), timestamps=None, drift=0., z=.135, released=True):
        vertices = box([.09, .04, .01]).reshape(-1, 3)
        times = timestamps if timestamps is not None else [2.4, 2.8, 3.2, 3.6]
        observations = [Estimate(Pose(np.array([xy[0] + i * drift, xy[1], z]), np.eye(3)), np.empty((0, 3)), [],
                                 dict(capture_time_s=time)) for i, time in enumerate(times)]
        return verify_estimates(observations, vertices, TargetZone([.45, -.08], [.15, .10], .13), TaskConfig(),
                                after_time=2., released=released, retreated=True,
                                initial_pose=Pose(np.array([.5, .07, .135]), np.eye(3)))

    def test_visual_containment_and_stability(self):
        self.assertTrue(self.verify()["success"])

    def test_wrong_target_rejected_even_when_stable(self):
        result = self.verify(xy=(.53, .06))
        self.assertFalse(result["success"])
        self.assertFalse(result["checks"]["center_on_target"])
        self.assertTrue(result["checks"]["visually_stable"])

    def test_stale_moving_hovering_and_unreleased_observations_rejected(self):
        for options in (dict(timestamps=[1, 1, 1, 1]), dict(drift=.002), dict(z=.17), dict(released=False)):
            self.assertFalse(self.verify(**options)["success"], options)

    def test_offline_evaluation_refuses_running_task(self):
        class Running:
            attempt_finished = False
        with self.assertRaisesRegex(RuntimeError, "forbidden"):
            evaluate_offline(Running())

    def test_invalid_task_config(self):
        for config in (TaskConfig(verify_frames=1), TaskConfig(verify_interval_s=.01), TaskConfig(release_gap_m=-.1)):
            with self.assertRaises(ValueError):
                config.validate()

    def test_invalid_input_has_no_stale_success(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "out"
            out.mkdir()
            (out / ".pick-place-output").touch()
            (out / "result.json").write_text('{"success":true}')
            (out / "capture_verify_0.png").write_bytes(b"old")
            result = run_pipeline(Path(directory) / "missing.xml", out, target_xy=[.45, .08], zone_size=[.15, .1])
            self.assertFalse(result["success"])
            self.assertFalse((out / "capture_verify_0.png").exists())


@unittest.skipUnless(PANDA and Path(PANDA).is_file(), "Set CAD_MUJOCO_PANDA_SCENE for physical pick-place tests")
class PhysicalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="pick_place_tests_")
        cls.root, cls.scenes, cls.evidence = Path(cls.tmp.name), {}, []
        for name, size, mass in (("small", [.084, .036, .010], .08), ("large", [.128, .054, .012], .15)):
            stl, parsed = cls.root / (name + ".stl"), cls.root / (name + ".json")
            write_stl(stl, box(size))
            parsed.write_text(json.dumps(dict(status="generated", units={"cad": "m"}, recipe={"unit": "m"})))
            out = cls.root / name
            _, drop = convert(stl, parsed, out, mass_kg=mass, base_scene=PANDA, duration=1.5)
            assert drop["success"], drop["checks"]
            cls.scenes[name] = out / "scene.xml"
        out = cls.root / "exported"
        source = ROOT / "examples/bracket/result"
        _, drop = convert(source / "model.stl", source / "parsed.json", out, density_kg_m3=7800, base_scene=PANDA, duration=1.5)
        assert drop["success"], drop["checks"]
        cls.scenes["exported"] = out / "scene.xml"

    @classmethod
    def tearDownClass(cls):
        if os.environ.get("PICK_PLACE_TEST_REPORT"):
            p = Path(os.environ["PICK_PLACE_TEST_REPORT"])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(cls.evidence, indent=2, allow_nan=False), encoding="utf8")
        cls.tmp.cleanup()

    def task(self, case="exported", target=(.46, -.09), zone=(.16, .16), xy=(0, 0), yaw=0):
        task = PickPlaceTask(self.scenes[case], target_xy=target, zone_size=zone)
        self.addCleanup(task.close)
        adr = int(task.model.jnt_qposadr[task.object_joint])
        # Fixture initialization only; protected live-state watch starts before run().
        task.data.qpos[adr:adr + 2] += xy
        task.data.qpos[adr + 3:adr + 7] = matrix_quat(rz(np.deg2rad(yaw)))
        mujoco.mj_forward(task.model, task.data)
        return task

    def run_watched(self, task):
        adr, dof = int(task.model.jnt_qposadr[task.object_joint]), task.object_dof
        state = [task.data.qpos[adr:adr + 7].copy(), task.data.qvel[dof:dof + 6].copy()]
        original_step = mujoco.mj_step
        counter = [0]

        def guarded_step(model, data, *args, **kwargs):
            if data is task.data:
                # Between physics steps only the physics engine may change object state.
                if not np.array_equal(data.qpos[adr:adr + 7], state[0]) or not np.array_equal(data.qvel[dof:dof + 6], state[1]):
                    raise AssertionError("live target state changed outside mj_step")
                if np.any(data.xfrc_applied[task.object_id]) or np.any(data.qfrc_applied[dof:dof + 6]):
                    raise AssertionError("external target force forbidden")
            original_step(model, data, *args, **kwargs)
            if data is task.data:
                state[:] = [data.qpos[adr:adr + 7].copy(), data.qvel[dof:dof + 6].copy()]
                counter[0] += 1

        with patch("mujoco.mj_step", side_effect=guarded_step), \
             patch("manipulation.pipeline.object_pose", side_effect=AssertionError("GT observer forbidden")), \
             patch("manipulation.geometry.object_pose", side_effect=AssertionError("GT observer forbidden")), \
             patch.object(task, "contacts", side_effect=AssertionError("GT contact task decisions forbidden")):
            result = task.run()
        np.testing.assert_array_equal(task.data.qpos[adr:adr + 7], state[0])
        np.testing.assert_array_equal(task.data.qvel[dof:dof + 6], state[1])
        self.assertGreater(counter[0], 0)
        self.assertFalse(np.any(task.model.eq_type == mujoco.mjtEq.mjEQ_WELD))
        self.assertFalse(result["ground_truth_fallback"])
        result["source_scene"] = "fixture"
        self.evidence.append(dict(test=self.id(), physics_steps_watched=counter[0], result=result))
        return result

    def trial(self, case="exported", **options):
        task = self.task(case, **options)
        result = self.run_watched(task)
        self.assertTrue(result["success"], result["failure"])
        self.assertTrue(result["perception_success"])
        self.assertTrue(result["grasp_execution_completed"])
        self.assertEqual(result["visual_task_success"], result["success"])
        self.assertEqual(result["offline_ground_truth_metrics"], result["offline_evaluation"])
        self.assertEqual(result["success_source"], "fresh post-release RGB-D only")
        self.assertTrue(all(result["checks"].values()))
        self.assertGreater(result["visual_verification"]["duration_s"], 1.19)
        offline = result["offline_evaluation"]
        self.assertTrue(offline["retained_inside_zone"])
        self.assertLess(offline["final_center_error_m"], .003)
        self.assertLess(offline["maximum_center_drift_m"], .001)
        self.assertGreater(offline["minimum_lift_hold_clearance_m"], .10)
        self.assertFalse(offline["used_for_task_success"])
        final_errors = [e for e in offline["pose_errors"] if e["label"].startswith("verify_")]
        self.assertEqual(len(final_errors), task.task_config.verify_frames)
        self.assertLess(max(e["position_error_m"] for e in final_errors), .0015)
        return task, result

    def test_exported_cad_complete_task(self):
        self.trial()

    def test_translated_rotated_start_and_new_target(self):
        self.trial(xy=(-.055, .055), yaw=34, target=(.54, -.075))

    def test_negative_yaw_and_positive_y_target(self):
        self.trial(xy=(.02, -.025), yaw=-49, target=(.43, .075))

    def test_small_plate_different_start_and_target(self):
        self.trial("small", xy=(-.02, -.06), yaw=-27, target=(.535, .065))

    def test_large_plate_different_yaw_and_target(self):
        self.trial("large", xy=(.005, .02), yaw=72, target=(.43, -.085), zone=(.16, .18))

    def test_initial_perception_failure_explicit(self):
        task = self.task()
        task.target_geom_ids = [-999]
        result = self.run_watched(task)
        self.assertFalse(result["success"])
        self.assertEqual(result["failure"]["stage"], "observe")
        self.assertFalse(result["perception_success"])
        self.assertFalse(result["grasp_execution_completed"])
        self.assertFalse(result["visual_task_success"])
        self.assertIn("insufficient segmented", result["failure"]["reason"])
        self.assertIsNone(task.selected)

    def test_post_release_perception_failure_not_gt_fallback(self):
        task = self.task()
        capture = task.camera.capture

        def missing_target(data):
            frame = capture(data)
            if task.stage == "visual_verify":
                frame.segmentation[:] = -1
            return frame

        with patch.object(task.camera, "capture", side_effect=missing_target):
            result = self.run_watched(task)
        self.assertFalse(result["success"])
        self.assertEqual(result["failure"]["stage"], "visual_verify")
        self.assertFalse(result["perception_success"])
        self.assertTrue(result["grasp_execution_completed"])
        self.assertFalse(result["visual_task_success"])
        self.assertIn("insufficient segmented", result["failure"]["reason"])
        self.assertTrue(result["offline_evaluation"]["retained_inside_zone"])

    def test_wrong_physical_destination_rejected_by_visual_verification(self):
        task = self.task(target=(.46, .09))
        planner = task.plan_place

        def wrong_destination(*args):
            # Fault injection into the command, NOT into live object state or verifier.
            expected = task.zone
            try:
                task.zone = TargetZone([.49, -.075], expected.size, expected.table_top)
                return planner(*args)
            finally:
                task.zone = expected

        with patch.object(task, "plan_place", side_effect=wrong_destination):
            result = self.run_watched(task)
        self.assertFalse(result["success"])
        self.assertEqual(result["failure"]["stage"], "visual_verify")
        self.assertFalse(result["checks"]["center_on_target"])
        self.assertTrue(result["checks"]["visually_stable"])

    def test_target_outside_table_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside_table"):
            self.task(target=(1.3, .8))

    def test_unknown_object_rejected(self):
        with self.assertRaises((ValueError, KeyError)):
            PickPlaceTask(self.scenes["exported"], target_xy=(.46, -.09), zone_size=(.15, .1), object_name="missing_object")
        with tempfile.TemporaryDirectory() as directory:
            result = run_pipeline(self.scenes["exported"], Path(directory) / "out", target_xy=(.46, -.09),
                                  zone_size=(.15, .1), object_name="missing_object")
            self.assertFalse(result["success"])
            self.assertEqual(result["failure"]["stage"], "input")


if __name__ == "__main__":
    unittest.main()
