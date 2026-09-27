"""Phase gates around ONE unchanged PickPlaceTask.run() invocation.

All camera and simulator work stays on one worker thread (including GL cleanup).
The dispatcher receives only image/vision evidence and stage completion messages.
"""
from copy import deepcopy
from pathlib import Path
from queue import Queue, Empty
import json
import math
import threading

from .models import SKILL_ORDER, SkillResult, validate_goal


class SessionCancelled(Exception):
    """Not a PlanningFailure: unwind the old run loop without executing later phases."""


def gated_task_type():
    # Lazy dependency: contract/Agent-only tests need no MuJoCo installation.
    from pick_place_task.pipeline import PickPlaceTask

    class GatedTask(PickPlaceTask):
        def __init__(self, *args, bridge, **kwargs):
            self.bridge = bridge
            self._picked_gate = False
            self._placed_gate = False
            super().__init__(*args, **kwargs)

        def record(self, *args, **kwargs):
            if self.bridge.cancelled.is_set():
                raise SessionCancelled()
            return super().record(*args, **kwargs)

        def select_task_candidate(self):
            self.bridge.checkpoint("locate", dict(object=self.bridge.goal["object"],
                                                  estimated_pose=self.observations[-1][1].json(),
                                                  perception_success=True,
                                                  source="existing RGB-D perception; two settled observations"))
            return super().select_task_candidate()

        def plan_place(self, *args, **kwargs):
            if self.stage == "lift_hold" and not self._picked_gate:
                self._picked_gate = True
                self.bridge.checkpoint("pick", dict(object=self.bridge.goal["object"],
                                                     completed_phases=["approach", "grasp", "close", "lift", "lift_hold"],
                                                     chosen_candidate=self.selected.id,
                                                     outcome="lift_phase_completed",
                                                     grasp_execution_completed=True,
                                                     holding_verification="not independently sensed; final visual verification remains mandatory"))
            return super().plan_place(*args, **kwargs)

        def physics(self, *args, **kwargs):
            if self.stage == "visual_verify" and not self._placed_gate:
                self._placed_gate = True
                self.bridge.checkpoint("place", dict(object=self.bridge.goal["object"], target=self.bridge.goal["target"],
                                                      released=bool(self.released), retreated=bool(self.retreated),
                                                      outcome="release_and_retreat_completed; awaiting new visual verification"))
            return super().physics(*args, **kwargs)

    return GatedTask


class PipelineSession:
    def __init__(self, scene, goal, output, *, timeout_s=180., task_factory=None, viewer=False):
        self.goal = validate_goal(goal)
        self.scene = Path(scene).resolve()
        self.output = Path(output).resolve()
        if self.scene == self.output or self.output in self.scene.parents:
            raise ValueError("Skill output must not contain the input scene")
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("Skill timeout must be finite and positive")
        self.timeout_s = timeout_s
        marker = self.output / ".robot-skills-output"
        if self.output.exists() and any(self.output.iterdir()) and not marker.is_file():
            raise ValueError("Choose an empty or existing robot-skills output directory")
        self.output.mkdir(parents=True, exist_ok=True)
        marker.write_text("Generated robot skills evidence\n", encoding="utf8")
        for pattern in ("frame_*.png", "frame_*.npy", "frame_*.json", "pipeline_result.json"):
            for old in self.output.glob(pattern):
                if old.is_file():
                    old.unlink()
        self.cancelled = threading.Event()
        self._commands, self._events = Queue(), Queue()
        self._thread = None
        self._active = None
        self._index = 0
        self._closed = False
        self._call_lock = threading.Lock()
        self._task_factory = task_factory
        self._frame_count = 0
        self._viewer_enabled = viewer
        self._viewer = None

    def advance(self, skill):
        if not self._call_lock.acquire(blocking=False):
            return SkillResult.failure(skill, "SESSION_BUSY", "Another skill is running")
        try:
            if self._viewer_enabled and self.cancelled.is_set():
                self.close()
                return self._viewer_failure(skill)
            if self._closed or self._index >= len(SKILL_ORDER) or SKILL_ORDER[self._index] != skill:
                return SkillResult.failure(skill, "INVALID_SESSION_STATE", "Invalid or completed session")
            self._commands.put(skill)
            if self._thread is None:
                self._thread = threading.Thread(target=self._worker, name="robot-pipeline-session", daemon=True)
                self._thread.start()
            try:
                result = self._events.get(timeout=self.timeout_s)
            except Empty:
                self.close()
                return SkillResult.failure(skill, "SKILL_TIMEOUT", "Pipeline did not reach the skill boundary in time")
            if result.skill != skill:
                self.close()
                return SkillResult.failure(skill, "SESSION_PROTOCOL_ERROR", "Pipeline returned the wrong phase")
            if result.success:
                self._index += 1
            else:
                self.close()
            return result
        finally:
            self._call_lock.release()

    def _next_command(self):
        command = self._commands.get()
        if command is None or self.cancelled.is_set():
            raise SessionCancelled()
        self._active = command
        if self._viewer is not None:
            self._viewer.set_status(command, "starting")

    def _cancel_viewer(self):
        self.cancelled.set()
        self._commands.put(None)  # Wake a worker paused at a skill boundary.

    def _viewer_failure(self, skill):
        error = getattr(self._viewer, "error", None)
        return SkillResult.failure(skill, "VIEWER_ERROR" if error else "VIEWER_CLOSED",
                                   str(error) if error else "Viewer closed; execution stopped", origin_stage="viewer")

    def _sync_viewer(self, task):
        self._viewer.sync(skill=self._active, stage=task.stage)
        if self.cancelled.is_set() or not self._viewer.is_running():
            raise SessionCancelled()

    def checkpoint(self, skill, data):
        if skill != self._active:
            raise RuntimeError("Unexpected pipeline skill boundary: " + skill)
        self._events.put(SkillResult(skill, True, deepcopy(data)))
        # The pipeline is paused BEFORE the next phase, until its skill is called.
        self._next_command()

    def _save_frame(self, frame):
        from PIL import Image
        import numpy as np
        stem = self.output / ("frame_" + str(self._frame_count))
        self._frame_count += 1
        Image.fromarray(frame.rgb).save(str(stem) + ".png")
        np.save(str(stem) + "_depth.npy", frame.depth)
        np.save(str(stem) + "_segmentation.npy", frame.segmentation)
        info = dict(capture_id=stem.name, time_s=frame.time, rgb=str(stem) + ".png",
                    depth=str(stem) + "_depth.npy", segmentation=str(stem) + "_segmentation.npy",
                    camera_position_m=frame.camera_position.tolist(), camera_rotation=frame.camera_rotation.tolist(),
                    focal_pixels=frame.focal, depth_unit="m", source="MuJoCo RGB-D camera")
        Path(str(stem) + ".json").write_text(json.dumps(info, indent=2, allow_nan=False), encoding="utf8")
        return info

    def _worker(self):
        task = None
        try:
            self._next_command()
            task_type = self._task_factory or gated_task_type()
            target = self.goal["target"]
            task = task_type(self.scene, bridge=self, object_name=self.goal["object"],
                             target_xy=target["center_xy_m"], zone_size=target["size_xy_m"])
            if self._viewer_enabled:
                from .live_viewer import LiveViewer
                self._viewer = LiveViewer(task.model, task.data, on_close=self._cancel_viewer)
                task.step_observer = lambda: self._sync_viewer(task)
            capture = task.camera.capture

            def capture_and_gate(data):
                frame = capture(data)
                evidence = self._save_frame(frame)
                if self._frame_count == 1:
                    self.checkpoint("observe", evidence)
                return frame

            task.camera.capture = capture_and_gate
            raw = task.run()  # All settling, grasp, transfer and release stay here.
            # Deliberate allowlist: offline truth, archived state and controller internals
            # never cross the skill boundary or enter the Agent's task decisions.
            result = {key: raw[key] for key in ("success", "failure", "checks", "visual_verification", "observations",
                                               "success_semantics", "perception_success", "grasp_execution_completed", "visual_task_success",
                                               "stages", "target_zone", "chosen_candidate", "rejected_candidates", "simulated_seconds") if key in raw}
            (self.output / "pipeline_result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf8")
            data = dict(verified=result.get("success") is True, checks=result.get("checks", {}),
                        visual_task_success=result.get("visual_task_success") is True,
                        visual_verification=result.get("visual_verification"),
                        observations=[o for o in result.get("observations", []) if o["label"].startswith("verify_")],
                        source="fresh post-release RGB-D only", pipeline_result=str(self.output / "pipeline_result.json"))
            if result.get("success") and self._active == "verify":
                self._events.put(SkillResult("verify", True, data))
            else:
                failure = result.get("failure") or dict(stage=task.stage, reason="Pipeline finished without visual success")
                code = {"observe": "OBSERVATION_FAILED", "locate": "PERCEPTION_FAILED", "pick": "GRASP_FAILED",
                        "place": "PLACE_FAILED", "verify": "VERIFY_FAILED"}[self._active]
                self._events.put(SkillResult.failure(self._active, code, failure["reason"], origin_stage=failure["stage"],
                                                    details={"pipeline_failure": failure}, data=data if self._active == "verify" else {}))
        except SessionCancelled:
            self._events.put(self._viewer_failure(self._active or "observe") if self._viewer_enabled
                             else SkillResult.failure(self._active or "observe", "SESSION_CANCELLED", "Session was closed before all phases completed"))
        except Exception as exc:
            code = "PIPELINE_INPUT_ERROR" if task is None else "PIPELINE_EXCEPTION"
            self._events.put(SkillResult.failure(self._active or "observe", code, str(exc),
                                                origin_stage=getattr(task, "stage", "input"), details={"exception_type": type(exc).__name__}))
        finally:
            try:
                if self._viewer is not None:
                    self._viewer.close()
            finally:
                if task is not None:
                    task.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.cancelled.set()
        self._commands.put(None)
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=None if self._viewer_enabled else 10.)
