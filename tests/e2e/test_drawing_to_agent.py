"""Fresh drawing -> CAD -> drop -> real RGB-D -> skill Agent -> visual verify.

Panda absence is an ERROR (not a skip). Artifacts survive success and failure.
"""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf8")


class DrawingToAgentTests(unittest.TestCase):
    def test_fresh_engineering_drawing_to_visual_placement(self):
        root = Path(os.environ.get("E2E_ARTIFACT_DIR") or tempfile.mkdtemp(prefix="drawing_agent_e2e_"))
        root.mkdir(parents=True, exist_ok=True)
        report = dict(success=False, stages=[], artifacts=str(root),
                      inputs="freshly generated engineering PDF and installed Panda robot assets; no prebuilt part mesh/scene",
                      perception_success=False, grasp_execution_completed=False, visual_task_success=False,
                      offline_ground_truth_metrics=None)
        stage = "environment"
        try:
            panda = os.environ.get("CAD_MUJOCO_PANDA_SCENE")
            self.assertTrue(panda and Path(panda).is_file(), "E2E requires CAD_MUJOCO_PANDA_SCENE; missing environment is not a pass/skip")
            cad_python = os.environ.get("CAD_TEST_PYTHON", str(ROOT / ".venv-drawing2cad/Scripts/python.exe"))
            self.assertTrue(Path(cad_python).is_file(), "E2E requires CAD_TEST_PYTHON")
            stage = "drawing_to_cad"
            with (root / "drawing2cad.log").open("w", encoding="utf8") as log:
                process = subprocess.run([cad_python, str(ROOT / "tests/e2e/build_drawing.py"), str(root)], cwd=ROOT,
                                         stdout=log, stderr=subprocess.STDOUT, timeout=180)
            self.assertEqual(process.returncode, 0, (root / "drawing2cad.log").read_text(encoding="utf8", errors="replace"))
            cad = root / "drawing2cad"
            for artifact in ("parsed.json", "model.stl", "model.step", "preview.png"):
                self.assertTrue((cad / artifact).is_file(), artifact)
            parsed = json.loads((cad / "parsed.json").read_text(encoding="utf8"))
            self.assertEqual(parsed["source"]["sha256"], hashlib.sha256((root / "input.pdf").read_bytes()).hexdigest())
            report["stages"].append(dict(stage=stage, success=True, parsed_status=parsed["status"],
                                         inferences=parsed["inferences"], output=str(cad)))
            stage = "cad_to_mujoco_and_drop"
            from cad_mujoco.pipeline import convert
            _, drop = convert(cad / "model.stl", cad / "parsed.json", root / "cad2mujoco", density_kg_m3=2700.,
                              base_scene=panda, duration=3.)
            report["stages"].append(dict(stage=stage, success=drop["success"], checks=drop["checks"],
                                         output=str(root / "cad2mujoco")))
            self.assertTrue(drop["success"], drop["checks"])
            stage = "perception_agent_pick_place_verify"
            import mujoco
            import numpy as np
            from cad_mujoco.mjcf import load_spec, save_spec
            from embodied_agent import TaskPlan
            from embodied_agent.backend import gated_task_type
            from embodied_agent.runner import run_agent
            from perception.evaluation import state_pose

            # These guards observe state for invariants only. They do not supply
            # target pose/state to control, planning, registration or task verdicts.
            outer = self
            class WatchedTask(gated_task_type()):
                def run(self, **kwargs):
                    save_spec(load_spec(self.scene_path), root / "task_scene.xml")
                    adr, dof = int(self.model.jnt_qposadr[self.object_joint]), self.object_dof
                    state = [self.data.qpos[adr:adr+7].copy(), self.data.qvel[dof:dof+6].copy()]
                    original_step = mujoco.mj_step
                    counter = [0]

                    def guard(model, data, *a, **kw):
                        if data is self.data:
                            np.testing.assert_array_equal(data.qpos[adr:adr+7], state[0], err_msg="object qpos written outside physics")
                            np.testing.assert_array_equal(data.qvel[dof:dof+6], state[1], err_msg="object qvel written outside physics")
                            outer.assertFalse(np.any(data.xfrc_applied[self.object_id]))
                            outer.assertFalse(np.any(data.qfrc_applied[dof:dof+6]))
                        original_step(model, data, *a, **kw)
                        if data is self.data:
                            state[:] = [data.qpos[adr:adr+7].copy(), data.qvel[dof:dof+6].copy()]
                            counter[0] += 1

                    def offline_only(*a, **kw):
                        outer.assertTrue(self.attempt_finished, "Ground truth evaluated during execution")
                        return state_pose(*a, **kw)

                    outer.assertFalse(np.any(self.model.eq_type == mujoco.mjtEq.mjEQ_WELD))
                    with ExitStack() as stack:
                        stack.enter_context(patch("mujoco.mj_step", side_effect=guard))
                        for path in ("manipulation.pipeline.object_pose", "manipulation.geometry.object_pose"):
                            stack.enter_context(patch(path, side_effect=AssertionError("GT pose input forbidden")))
                        for path in ("perception.evaluation.state_pose", "pick_place_task.evaluation.state_pose"):
                            stack.enter_context(patch(path, side_effect=offline_only))
                        stack.enter_context(patch.object(self, "contacts", side_effect=AssertionError("GT contact decision forbidden")))
                        raw = super().run(**kwargs)
                    np.testing.assert_array_equal(self.data.qpos[adr:adr+7], state[0])
                    np.testing.assert_array_equal(self.data.qvel[dof:dof+6], state[1])
                    outer.assertGreater(counter[0], 0)
                    write_json(root / "task_result_with_offline_metrics.json", raw)
                    write_json(root / "trace.json", self.records)
                    write_json(root / "candidates.json", self.candidates)
                    for label, estimate in self.observations:
                        np.savez_compressed(root / (label + "_pointcloud.npz"), world_points_m=estimate.points)
                    report["guards"] = dict(live_steps_watched=counter[0], live_state_writes=False, weld=False,
                                             gt_observer_calls=0, offline_evaluation_after_verdict=True)
                    report["visual_verification"] = raw["visual_verification"]
                    for key in ("perception_success", "grasp_execution_completed", "visual_task_success", "offline_ground_truth_metrics"):
                        report[key] = raw[key]
                    return raw

            goal = dict(type="pick_and_place", object="cad_part",
                        target=dict(center_xy_m=[.46, -.09], size_xy_m=[.18, .14], unit="m", frame="world"))
            plan = run_agent(root / "cad2mujoco/scene.xml", TaskPlan.build(goal), root / "agent", task_factory=WatchedTask)
            report["stages"].extend(dict(stage=s["skill"], success=s["status"] == "succeeded", result=s["result"])
                                    for s in plan.steps)
            self.assertEqual(plan.status, "succeeded", plan.failures)
            self.assertEqual([s["status"] for s in plan.steps], ["succeeded"] * 5)
            self.assertTrue(plan.steps[-1]["result"]["data"]["visual_task_success"])
            for forbidden in ('"offline_ground_truth_metrics"', '"offline_evaluation"', '"qpos"', '"qvel"', '"final_gt_pose"'):
                self.assertNotIn(forbidden, json.dumps(plan.json()))
            self.assertTrue(all(report[key] is True for key in ("perception_success", "grasp_execution_completed", "visual_task_success")))
            self.assertFalse(report["offline_ground_truth_metrics"]["used_for_task_success"])
            report["stages"].append(dict(stage=stage, success=True, output=str(root / "agent")))
            report["success"] = True
        except BaseException as exc:
            report["failure"] = dict(stage=stage, type=type(exc).__name__, reason=str(exc))
            raise
        finally:
            write_json(root / "validation.json", report)


if __name__ == "__main__":
    unittest.main()
