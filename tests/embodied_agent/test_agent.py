"""Contracts and real pipeline-boundary/failure-propagation acceptance."""
import ast
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from embodied_agent import Agent, RobotSkills, TaskPlan, SkillResult
from embodied_agent.backend import PipelineSession, gated_task_type
from embodied_agent.models import SKILL_ORDER


ROOT = Path(__file__).resolve().parents[2]
PANDA = os.environ.get("CAD_MUJOCO_PANDA_SCENE")


def goal(x=.46, y=-.09, size=(.15, .1)):
    return dict(type="pick_and_place", object="cad_part",
                target=dict(center_xy_m=[x, y], size_xy_m=list(size), frame="world", unit="m"))


class StubSession:
    def __init__(self, fail=None):
        self.goal = goal()
        self.calls, self.closed, self.fail = [], False, fail

    def advance(self, skill):
        self.calls.append(skill)
        if skill == self.fail:
            return SkillResult.failure(skill, "BACKEND_FAILURE", "deliberate backend failure", origin_stage="backend_phase")
        return SkillResult(skill, True, dict(verified=True, checks={"fresh_visual": True}) if skill == "verify" else {"evidence": skill})

    def close(self):
        self.closed = True


class ContractTests(unittest.TestCase):
    def test_explicit_plan_roundtrip_and_success_states(self):
        plan = TaskPlan.from_dict(TaskPlan.build(goal()).json())
        session = StubSession()
        updates = []
        Agent(RobotSkills(session)).execute(plan, on_update=updates.append)
        self.assertEqual(plan.status, "succeeded")
        self.assertEqual(session.calls, list(SKILL_ORDER))
        self.assertEqual([s["status"] for s in plan.steps], ["succeeded"] * 5)
        self.assertEqual([o["skill"] for o in plan.observations], ["observe", "locate", "verify"])
        self.assertTrue(session.closed)
        self.assertEqual(updates[0]["status"], "running")
        self.assertEqual(updates[-1]["status"], "succeeded")

    def test_every_skill_failure_short_circuits_and_preserves_origin(self):
        for i, failed in enumerate(SKILL_ORDER):
            session = StubSession(failed)
            plan = Agent(RobotSkills(session)).execute(TaskPlan.build(goal()))
            self.assertEqual(session.calls, list(SKILL_ORDER[:i + 1]))
            self.assertEqual(plan.status, "failed")
            self.assertEqual(plan.failures[0]["error"]["origin_stage"], "backend_phase")
            self.assertEqual([s["status"] for s in plan.steps], ["succeeded"] * i + ["failed"] + ["skipped"] * (4 - i))

    def test_out_of_order_skill_has_structured_error_and_no_action(self):
        session = StubSession()
        result = RobotSkills(session).pick("cad_part")
        self.assertFalse(result.success)
        self.assertEqual(result.error.code, "INVALID_SKILL_STATE")
        self.assertFalse(session.calls)

    def test_wrong_or_missing_object_does_not_advance(self):
        for name in (None, "other"):
            session = StubSession()
            skills = RobotSkills(session)
            skills.observe()
            self.assertEqual(skills.locate(name).error.code, "OBJECT_MISMATCH")
            self.assertEqual(session.calls, ["observe"])

    def test_target_change_after_pick_is_not_silently_used(self):
        for target in (None, goal(.51, .08)["target"]):
            session = StubSession()
            skills = RobotSkills(session)
            skills.observe(); skills.locate("cad_part"); skills.pick("cad_part")
            self.assertFalse(skills.place("cad_part", target).success)
            self.assertEqual(session.calls, ["observe", "locate", "pick"])

    def test_stale_plan_or_arbitrary_actions_rejected_before_execution(self):
        for mutate in (lambda p: p["steps"][2].update(skill="set_joint"),
                       lambda p: p.update(status="succeeded"),
                       lambda p: p["steps"][3]["arguments"].update(target=goal(.55, .1)["target"])):
            value = TaskPlan.build(goal()).json()
            mutate(value)
            with self.assertRaises(ValueError):
                TaskPlan.from_dict(value)

    def test_invalid_units_numbers_and_goal_type_rejected(self):
        for edit in (lambda v: v["target"].update(unit="mm"), lambda v: v["target"].update(center_xy_m=[float("nan"), 0]),
                     lambda v: v["target"].update(size_xy_m=[-.1, .2]), lambda v: v.update(type="natural_language")):
            value = goal(); edit(value)
            with self.assertRaises(ValueError):
                TaskPlan.build(value)

    def test_exception_becomes_structured_failure(self):
        session = StubSession()
        with patch.object(session, "advance", side_effect=RuntimeError("executor unavailable")):
            plan = Agent(RobotSkills(session)).execute(TaskPlan.build(goal()))
        self.assertEqual(plan.status, "failed")
        self.assertEqual(plan.failures[0]["error"]["code"], "SKILL_BACKEND_EXCEPTION")

    def test_offline_success_cannot_replace_visual_failure(self):
        session = StubSession()
        original = session.advance
        def false_verification(skill):
            if skill == "verify":
                return SkillResult(skill, True, dict(verified=False, checks={"in_zone": False}, offline_evaluation={"success": True}))
            return original(skill)
        with patch.object(session, "advance", side_effect=false_verification):
            plan = Agent(RobotSkills(session)).execute(TaskPlan.build(goal()))
        self.assertEqual(plan.status, "failed")
        self.assertEqual(plan.failures[0]["error"]["code"], "VERIFY_RESULT_INVALID")

    def test_invalid_backend_results_are_structured_errors(self):
        for invalid in (None, {"success": True}, SkillResult("pick", True)):
            session = StubSession()
            with patch.object(session, "advance", return_value=invalid):
                result = RobotSkills(session).observe()
            self.assertEqual(result.error.code, "INVALID_SKILL_RESULT")
            self.assertTrue(session.closed)
        with self.assertRaises(ValueError):
            SkillResult("observe", False, error="unstructured error")

    def test_agent_has_no_simulator_or_control_access(self):
        forbidden = {"mujoco", "numpy", "data", "model", "panda", "qpos", "qvel", "ctrl", "xpos", "xquat", "mj_step", "offline_evaluation"}
        for name in ("agent.py", "skills.py"):
            tree = ast.parse((ROOT / "embodied_agent" / name).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    # SkillResult.data is JSON evidence, not simulator data.
                    self.assertNotIn(node.attr, forbidden - {"data"}, (name, node.attr))
                if isinstance(node, ast.Import):
                    self.assertFalse({n.name.split('.')[0] for n in node.names} & forbidden)


@unittest.skipUnless(PANDA and Path(PANDA).is_file(), "Set CAD_MUJOCO_PANDA_SCENE for real skills")
class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cad_mujoco.pipeline import convert
        from cad_mujoco.mesh import write_stl
        import numpy as np
        cls.tmp = tempfile.TemporaryDirectory(prefix="embodied_agent_tests_")
        cls.root = Path(cls.tmp.name)
        source = ROOT / "examples/bracket/result"
        out = cls.root / "normal"
        _, drop = convert(source / "model.stl", source / "parsed.json", out, density_kg_m3=7800, base_scene=PANDA, duration=1.5)
        assert drop["success"], drop["checks"]
        cls.normal = out / "scene.xml"
        v = (np.array([[0,0,0],[1,0,0],[1,1,0],[0,1,0],[0,0,1],[1,0,1],[1,1,1],[0,1,1]], float) - .5) * [.14,.105,.01]
        triangles = v[[[0,2,1],[0,3,2],[4,5,6],[4,6,7],[0,1,5],[0,5,4],[1,2,6],[1,6,5],[2,3,7],[2,7,6],[3,0,4],[3,4,7]]]
        stl, parsed = cls.root / "wide.stl", cls.root / "wide.json"
        write_stl(stl, triangles)
        parsed.write_text(json.dumps(dict(status="generated", units={"cad":"m"}, recipe={"unit":"m"})))
        convert(stl, parsed, cls.root / "wide", mass_kg=.15, base_scene=PANDA, duration=1.5)
        cls.wide = cls.root / "wide/scene.xml"
        cls.evidence = []

    @classmethod
    def tearDownClass(cls):
        if os.environ.get("EMBODIED_AGENT_TEST_REPORT"):
            p = Path(os.environ["EMBODIED_AGENT_TEST_REPORT"])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(cls.evidence, indent=2, allow_nan=False), encoding="utf8")
        cls.tmp.cleanup()

    def run_plan(self, *, selected_goal=None, task_factory=None, scene=None, inspect_boundaries=False):
        base = task_factory or gated_task_type()
        instances = []

        class Spy(base):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.motion_names, self.sense_labels = [], []
                instances.append(self)
            def motion(self, stage, *args, **kwargs):
                self.motion_names.append(stage)
                return super().motion(stage, *args, **kwargs)
            def sense(self, label):
                self.sense_labels.append(label)
                return super().sense(label)

        current_goal = selected_goal or goal()
        session = PipelineSession(scene or self.normal, current_goal, self.root / self._testMethodName, task_factory=Spy)
        self.addCleanup(session.close)
        boundary_evidence = []

        def update(plan):
            if not inspect_boundaries or not instances:
                return
            done = [s["skill"] for s in plan["steps"] if s["status"] == "succeeded"]
            motion = instances[0].motion_names.copy()
            labels = instances[0].sense_labels.copy()
            if done and plan["steps"][len(done) - 1]["status"] == "succeeded":
                boundary_evidence.append(dict(completed=done.copy(), motions=motion, frames=labels))
                if done[-1] in ("observe", "locate"):
                    self.assertFalse(motion)
                elif done[-1] == "pick":
                    self.assertEqual(motion, ["approach", "grasp", "lift"])
                elif done[-1] == "place":
                    self.assertNotIn("verify_0", labels)

        with patch("manipulation.pipeline.object_pose", side_effect=AssertionError("GT forbidden")), \
             patch("manipulation.geometry.object_pose", side_effect=AssertionError("GT forbidden")):
            plan = Agent(RobotSkills(session)).execute(TaskPlan.build(current_goal), on_update=update)
        self.assertFalse(session._thread.is_alive())
        serialized = json.dumps(plan.json())
        for forbidden in ("offline_evaluation", "offline_ground_truth_metrics", '"ground_truth"', '"qpos"', '"qvel"'):
            self.assertNotIn(forbidden, serialized)
        self.evidence.append(dict(test=self.id(), plan=plan.json(), boundaries=boundary_evidence,
                                  motions=instances[0].motion_names if instances else []))
        return plan, instances

    def test_normal_pick_place_and_skill_boundaries(self):
        plan, instances = self.run_plan(inspect_boundaries=True)
        self.assertEqual(plan.status, "succeeded", plan.failures)
        self.assertEqual(len(instances), 1)
        self.assertTrue(plan.steps[1]["result"]["data"]["perception_success"])
        self.assertTrue(plan.steps[2]["result"]["data"]["grasp_execution_completed"])
        self.assertTrue(plan.steps[4]["result"]["data"]["visual_task_success"])
        self.assertEqual(instances[0].sense_labels, ["initial_0", "initial_1", "verify_0", "verify_1", "verify_2", "verify_3"])

    def test_different_target(self):
        plan, _ = self.run_plan(selected_goal=goal(.535, .085, (.16, .14)))
        self.assertEqual(plan.status, "succeeded", plan.failures)
        self.assertEqual(plan.steps[-1]["result"]["data"]["verified"], True)

    def test_perception_failure(self):
        base = gated_task_type()
        class MissingSegmentation(base):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                capture = self.camera.capture
                def blank(data):
                    frame = capture(data)
                    frame.segmentation[:] = -1
                    return frame
                self.camera.capture = blank
        plan, instances = self.run_plan(task_factory=MissingSegmentation)
        self.assertEqual(plan.status, "failed")
        self.assertEqual(plan.failures[0]["skill"], "locate")
        self.assertEqual(plan.failures[0]["error"]["code"], "PERCEPTION_FAILED")
        self.assertFalse(instances[0].motion_names)

    def test_grasp_failure_from_real_oversized_geometry(self):
        plan, instances = self.run_plan(scene=self.wide, selected_goal=goal(size=(.2, .19)))
        self.assertEqual(plan.status, "failed")
        self.assertEqual(plan.failures[0]["skill"], "pick")
        self.assertEqual(plan.failures[0]["error"]["code"], "GRASP_FAILED")
        self.assertIn("no_feasible", plan.failures[0]["error"]["message"])
        self.assertFalse(instances[0].motion_names)

    def test_place_failure_release_fault(self):
        from manipulation.panda import PlanningFailure
        base = gated_task_type()
        class ReleaseFault(base):
            def open_gripper(self, *args, **kwargs):
                self.stage = "release"
                raise PlanningFailure("injected_release_actuator_failure")
        plan, instances = self.run_plan(task_factory=ReleaseFault)
        self.assertEqual(plan.status, "failed")
        self.assertEqual(plan.failures[0]["skill"], "place")
        self.assertEqual(plan.failures[0]["error"]["code"], "PLACE_FAILED")
        self.assertEqual(plan.failures[0]["error"]["origin_stage"], "release")
        self.assertEqual(plan.steps[-1]["status"], "skipped")
        self.assertNotIn("verify_0", instances[0].sense_labels)

    def test_verify_failure_real_wrong_destination(self):
        from pick_place_task.planning import TargetZone
        base = gated_task_type()
        class WrongDestination(base):
            def plan_place(self, *args, **kwargs):
                expected = self.zone
                try:
                    self.zone = TargetZone([.49, -.075], expected.size, expected.table_top)
                    return super().plan_place(*args, **kwargs)
                finally:
                    self.zone = expected
        plan, instances = self.run_plan(selected_goal=goal(.46, .09), task_factory=WrongDestination)
        self.assertEqual(plan.status, "failed")
        self.assertEqual([s["status"] for s in plan.steps], ["succeeded"] * 4 + ["failed"])
        self.assertEqual(plan.failures[0]["error"]["code"], "VERIFY_FAILED")
        self.assertFalse(plan.steps[-1]["result"]["data"]["checks"]["center_on_target"])

    def test_close_paused_session_does_not_start_pick(self):
        session = PipelineSession(self.normal, goal(), self.root / self._testMethodName)
        skills = RobotSkills(session)
        self.assertTrue(skills.observe().success)
        self.assertTrue(skills.locate("cad_part").success)
        skills.close()
        self.assertFalse(session._thread.is_alive())
        self.assertFalse(skills.pick("cad_part").success)


if __name__ == "__main__":
    unittest.main()
