"""Optional viewer wiring/lifecycle, without requiring a desktop for regression."""
import io
import json
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from embodied_agent import Agent, RobotSkills, TaskPlan
from embodied_agent.backend import PipelineSession
from embodied_agent.live_viewer import LiveViewer
import run_embodied_agent

ROOT = Path(__file__).resolve().parents[2]
PANDA = os.environ.get("CAD_MUJOCO_PANDA_SCENE")


def goal():
    return json.loads((ROOT / "examples/agent/task_plan.json").read_text())["goal"]


class ViewerContractTests(unittest.TestCase):
    def test_cli_defaults_to_headless_and_opt_in_enables_viewer(self):
        for flags, expected in (([], False), (["--viewer"], True)):
            with self.subTest(flags=flags):
                result = SimpleNamespace(status="succeeded", steps=[], failures=[])
                with patch("sys.argv", ["run_embodied_agent.py", "scene.xml", "--plan", str(ROOT / "examples/agent/task_plan.json"), *flags]), \
                     patch("run_embodied_agent.run_agent", return_value=result) as run, patch("sys.stdout", new=io.StringIO()):
                    self.assertEqual(run_embodied_agent.main(), 0)
                self.assertIs(run.call_args.kwargs["viewer"], expected)

    def test_default_session_does_not_construct_viewer(self):
        class Camera:
            def capture(self, data):
                raise RuntimeError("stop fixture before any real rendering")
        class Task:
            def __init__(self, *a, **kw):
                self.camera, self.data, self.stage = Camera(), object(), "observe"
                self.closed = False
                instances.append(self)
            def run(self):
                return self.camera.capture(self.data)
            def close(self):
                self.closed = True
        instances = []
        with tempfile.TemporaryDirectory() as directory, patch("embodied_agent.live_viewer.LiveViewer", side_effect=AssertionError("headless must not construct a viewer")) as viewer:
            session = PipelineSession("unused.xml", goal(), directory, task_factory=Task)
            result = Agent(RobotSkills(session)).execute(TaskPlan.build(goal()))
            self.assertEqual(result.status, "failed")
            self.assertIn("stop fixture", result.failures[0]["error"]["message"])
            viewer.assert_not_called()
            self.assertFalse(session._thread.is_alive())
            self.assertTrue(instances[0].closed)

    def test_window_close_unblocks_skill_boundary_and_joins_worker(self):
        instances = []
        class Window:
            def __init__(self, model, data, *, on_close):
                self.model, self.data, self.on_close = model, data, on_close
                self.error = None
                self.closed = False
                instances.append(self)
            def sync(self, **kwargs): pass
            def set_status(self, *args): pass
            def close(self): self.closed = True
            def is_running(self): return not self.closed
        class Task:
            def __init__(self, *args, bridge, **kwargs):
                self.bridge = bridge
                self.camera = SimpleNamespace(capture=lambda data: None)
                self.model, self.data = object(), object()
                self.stage = "observe"
            def run(self):
                self.bridge.checkpoint("observe", {})
                raise AssertionError("closing the viewer must not advance the next skill")
            def close(self): pass
        with tempfile.TemporaryDirectory() as directory, patch("embodied_agent.live_viewer.LiveViewer", Window):
            session = PipelineSession("unused.xml", goal(), directory, viewer=True, task_factory=Task)
            self.assertTrue(session.advance("observe").success)
            instances[0].on_close()
            result = session.advance("locate")
            self.assertFalse(result.success)
            self.assertEqual(result.error.code, "VIEWER_CLOSED")
            self.assertFalse(session._thread.is_alive())
            self.assertTrue(instances[0].closed)

    def test_viewer_close_joins_its_owned_render_thread(self):
        viewer = LiveViewer.__new__(LiveViewer)
        viewer._stop = threading.Event()
        viewer._thread = threading.Thread(target=viewer._stop.wait, name="test-viewer-render")
        viewer._thread.start()
        viewer.close()
        self.assertFalse(viewer._thread.is_alive())


@unittest.skipUnless(PANDA and Path(PANDA).is_file(), "Set CAD_MUJOCO_PANDA_SCENE for real step/viewer wiring")
class ViewerPhysicsTests(unittest.TestCase):
    def test_same_model_data_sync_after_every_step_and_close_stops_execution(self):
        import mujoco
        import numpy as np
        from cad_mujoco.pipeline import convert
        from embodied_agent.backend import gated_task_type
        instances, tasks, steps = [], [], [0]
        limit = 24
        class Window:
            def __init__(self, model, data, *, on_close):
                self.model, self.data, self.on_close = model, data, on_close
                self.error = None
                self.syncs, self.closed = 0, False
                instances.append(self)
            def sync(self, **kwargs):
                self.syncs += 1
                self.time = float(self.data.time)
                if self.syncs == limit:
                    self.closed = True
                    self.on_close()
            def set_status(self, *args): pass
            def is_running(self): return not self.closed
            def close(self): self.closed = True
        class Task(gated_task_type()):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                tasks.append(self)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = ROOT / "examples/bracket/result"
            _, drop = convert(source / "model.stl", source / "parsed.json", root / "cad", density_kg_m3=7800,
                              base_scene=PANDA, duration=1.5)
            self.assertTrue(drop["success"])
            original = mujoco.mj_step
            last = []
            def counted(model, data, *a, **kw):
                if tasks and data is tasks[0].data:
                    if last:
                        np.testing.assert_array_equal(data.qpos, last[0])
                        np.testing.assert_array_equal(data.qvel, last[1])
                    steps[0] += 1
                original(model, data, *a, **kw)
                if tasks and data is tasks[0].data:
                    last[:] = [data.qpos.copy(), data.qvel.copy()]
            with patch("embodied_agent.live_viewer.LiveViewer", Window), patch("mujoco.mj_step", side_effect=counted):
                session = PipelineSession(root / "cad/scene.xml", goal(), root / "session", viewer=True, task_factory=Task)
                result = Agent(RobotSkills(session)).execute(TaskPlan.build(goal()))
            self.assertIs(instances[0].model, tasks[0].model)
            self.assertIs(instances[0].data, tasks[0].data)
            self.assertEqual(instances[0].syncs, steps[0])
            self.assertEqual(steps[0], limit)
            self.assertAlmostEqual(instances[0].time, limit * tasks[0].model.opt.timestep)
            self.assertEqual(result.failures[0]["error"]["code"], "VIEWER_CLOSED")
            self.assertFalse(session._thread.is_alive())
            self.assertFalse(np.any(tasks[0].model.eq_type == mujoco.mjtEq.mjEQ_WELD))
            np.testing.assert_array_equal(tasks[0].data.qpos, last[0])
            np.testing.assert_array_equal(tasks[0].data.qvel, last[1])
