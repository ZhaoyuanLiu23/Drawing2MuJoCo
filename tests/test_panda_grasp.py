"""Panda end-to-end physics checks using the user's installed Menagerie model."""
import unittest

import mujoco
import numpy as np

from panda_grasp import MODEL_PATH, OPEN_GRIPPER, PandaGrasp


@unittest.skipUnless(MODEL_PATH.is_file(), "Local Panda Menagerie model is not installed")
class PandaGraspTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sim = PandaGrasp()

    def setUp(self):
        self.sim.reset()

    def finish(self):
        for _ in range(round(self.sim.duration / self.sim.model.opt.timestep)):
            self.sim.step(announce=False)
        return self.sim.summary()

    def test_real_panda_carries_and_releases_ball(self):
        initial = self.sim.data.qpos.copy()
        self.assertEqual(self.sim.model.nu, 8)
        self.assertFalse(np.any(self.sim.model.eq_type == mujoco.mjtEq.mjEQ_WELD))
        result = self.finish()
        self.assertTrue(result["success"], result)
        self.assertGreater(result["max_lift_m"], 0.20)
        self.assertGreater(result["transfer_held_ratio"], 0.99)
        self.assertLess(result["final_xy_error_m"], 0.005)
        self.assertTrue(result["released"])
        self.sim.reset()
        np.testing.assert_allclose(self.sim.data.qpos, initial, atol=1e-12)
        self.assertEqual(self.sim.data.time, 0)

    def test_open_fingers_do_not_carry_ball(self):
        self.sim.segments = [(name, duration, target, OPEN_GRIPPER)
                             for name, duration, target, _ in self.sim.segments]
        result = self.finish()
        self.assertTrue(result["completed"])
        self.assertFalse(result["success"])
        self.assertFalse(result["lifted"])
        self.assertEqual(result["transfer_held_ratio"], 0)
        np.testing.assert_allclose(result["final_ball_position_m"][:2], self.sim.pick[:2], atol=0.002)


if __name__ == "__main__":
    unittest.main()
