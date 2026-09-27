"""Subprocess-only real preflight and live invariant checks, never a mock success."""
from contextlib import ExitStack
import json
from pathlib import Path
import sys
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import simulation_worker as worker
from simulation_video import SimulationVideo
import mujoco
import numpy as np
from cad_mujoco.mjcf import load_spec
from cad_mujoco.validation import initialize
from embodied_agent.backend import gated_task_type
from perception.evaluation import state_pose

job, panda = Path(sys.argv[1]), sys.argv[2]
profile = dict(panda_scene=panda, density_kg_m3=7800., zone_size_m=[.16, .16])
parameters = dict(object_start=dict(x=.445, y=.055, yaw_deg=34.), target=dict(x=.54, y=-.075))

# Existing guard must reject before even CAD2MuJoCo's drop test is started.
bad = dict(object_start=parameters["object_start"], target=dict(x=3., y=0.))
with patch("mujoco.mj_step", side_effect=AssertionError("preflight executed physics")):
    try:
        worker.preflight(job / "cad", job / "invalid_preflight", bad, profile)
    except ValueError as exc:
        assert "target_zone_outside_table" in str(exc)
    else:
        raise AssertionError("Invalid target accepted")


tasks_executed = []


class GuardedTask(gated_task_type()):
    def run(self, **kwargs):
        tasks_executed.append(self)
        assert self.web_video.task is self
        adr = int(self.model.jnt_qposadr[self.object_joint])
        dof = self.object_dof
        # Check actual authored start configuration reached the live task.
        np.testing.assert_allclose(self.data.qpos[adr:adr+2], [.445, .055], atol=1e-10)
        # MuJoCo serializes MJCF quaternion components to six decimal places.
        # This checks the requested initial pose, not a task-success tolerance.
        np.testing.assert_allclose(self.data.qpos[adr+3:adr+7], [np.cos(np.deg2rad(17)),0,0,np.sin(np.deg2rad(17))], atol=1e-6)
        state = [self.data.qpos[adr:adr+7].copy(), self.data.qvel[dof:dof+6].copy()]
        step = mujoco.mj_step
        count = [0]

        def guard(model, data, *args, **kw):
            if data is self.data:
                np.testing.assert_array_equal(data.qpos[adr:adr+7], state[0])
                np.testing.assert_array_equal(data.qvel[dof:dof+6], state[1])
                assert not np.any(data.xfrc_applied[self.object_id])
            step(model, data, *args, **kw)
            if data is self.data:
                state[:] = [data.qpos[adr:adr+7].copy(), data.qvel[dof:dof+6].copy()]
                count[0] += 1

        def offline_only(*args, **kwargs):
            assert self.attempt_finished, "ground truth before verdict"
            return state_pose(*args, **kwargs)

        assert not np.any(self.model.eq_type == mujoco.mjtEq.mjEQ_WELD)
        with ExitStack() as stack:
            render_scene = self.web_video.renderer.update_scene

            def same_live_data(data, *args, **kwargs):
                assert data is self.data
                return render_scene(data, *args, **kwargs)

            stack.enter_context(patch.object(self.web_video.renderer, "update_scene", side_effect=same_live_data))
            stack.enter_context(patch("mujoco.mj_step", side_effect=guard))
            for name in ("manipulation.pipeline.object_pose", "manipulation.geometry.object_pose"):
                stack.enter_context(patch(name, side_effect=AssertionError("GT grasp input")))
            for name in ("perception.evaluation.state_pose", "pick_place_task.evaluation.state_pose"):
                stack.enter_context(patch(name, side_effect=offline_only))
            raw = super().run(**kwargs)
        np.testing.assert_array_equal(self.data.qpos[adr:adr+7], state[0])
        np.testing.assert_array_equal(self.data.qvel[dof:dof+6], state[1])
        assert count[0] > 0
        worker.write_json(job / "live_guard_evidence.json", dict(live_steps=count[0], state_writes_outside_physics=False,
                                                               weld=False, truth_grasp_input=False))
        print("live steps guarded:", count[0])
        return raw


run = job / "simulation/runs" / uuid4().hex
run.mkdir(parents=True)
worker.write_json(run / "request.json", dict(parameters=parameters, profile=profile))
report = worker.execute(run, task_factory=GuardedTask)
assert report["status"] == "succeeded", report["error"]
assert report["visual_task_success"] is True
assert report["parameters"] == parameters
assert (run / "task_scene.xml").is_file()
assert len(tasks_executed) == 1, "Recorder must not run a second task"
assert report["video_available"] is True, report.get("video")
assert report["video"]["complete"] is True
assert abs(report["video"]["last_simulation_time_s"] - tasks_executed[0].data.time) <= 1 / SimulationVideo.FPS
assert tasks_executed[0].web_video.encoder.poll() == 0
print("changed start/yaw/target: success", report["position_error_mm"])


# Separate failed task: real settling frames must survive a perception failure.
class FailingTask(gated_task_type()):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        def fail_capture(data):
            raise RuntimeError("injected perception failure")
        self.camera.capture = fail_capture


failure_run = job / "simulation/runs" / uuid4().hex
failure_run.mkdir()
worker.write_json(failure_run / "request.json", dict(parameters=parameters, profile=profile))
failure = worker.execute(failure_run, task_factory=FailingTask)
assert failure["status"] == "failed" and not failure["visual_task_success"]
assert "injected perception failure" in failure["error"]["message"]
assert failure["video_available"] is True, failure.get("video")
assert failure["video"]["frames"] >= 15
print("failed physical execution retains decodable video")
