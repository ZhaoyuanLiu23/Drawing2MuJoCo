"""End-to-end checks: the ball is carried by contact, and open fingers cannot lift it."""
import unittest

import numpy as np

from pick_and_place import OPEN_FINGER, PickAndPlace


def finish(sim):
    for _ in range(round(sim.duration / sim.model.opt.timestep)):
        sim.step(announce=False)
    return sim.summary()


class PickAndPlaceTest(unittest.TestCase):
    def test_physical_grasp_and_reset(self):
        sim = PickAndPlace()
        initial = sim.data.qpos.copy()
        result = finish(sim)
        self.assertTrue(result["success"], result)
        self.assertGreater(result["max_lift_m"], 0.15)
        self.assertGreater(result["transfer_held_ratio"], 0.99)
        self.assertLess(result["final_xy_error_m"], 0.005)
        self.assertTrue(result["released"])
        self.assertEqual(sim.model.neq, 0, "Grasp must not use a weld constraint")
        sim.reset()
        np.testing.assert_allclose(sim.data.qpos, initial, atol=1e-12)
        self.assertEqual(sim.data.time, 0.0)
        self.assertEqual(sim.transfer_steps, 0)
        self.assertTrue(finish(sim)["success"], "Replay must also complete")

    def test_open_gripper_does_not_carry_ball(self):
        sim = PickAndPlace()
        sim.segments = [(name, duration, target, OPEN_FINGER)
                        for name, duration, target, _ in sim.segments]
        result = finish(sim)
        self.assertTrue(result["completed"])
        self.assertFalse(result["success"])
        self.assertFalse(result["lifted"])
        self.assertEqual(result["transfer_held_ratio"], 0.0)
        np.testing.assert_allclose(result["final_ball_position_m"][:2], sim.pick[:2], atol=0.002)


if __name__ == "__main__":
    unittest.main()
