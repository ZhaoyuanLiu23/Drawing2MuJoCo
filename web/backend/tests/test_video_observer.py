"""Optional display failures must remain outside robot task control flow."""
from types import SimpleNamespace
import sys
from unittest.mock import Mock

import simulation_video


def test_missing_encoder_disables_only_recording(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "mujoco", SimpleNamespace())
    def unavailable():
        raise RuntimeError("encoder missing")
    monkeypatch.setattr(simulation_video, "find_encoder", unavailable)
    task = SimpleNamespace(data=SimpleNamespace(time=0.))
    recorder = simulation_video.SimulationVideo(task, tmp_path)
    recorder.sample()
    recorder.close()
    assert recorder.info["available"] is False
    assert recorder.info["error"] == "encoder missing"
    assert recorder.closed and recorder.encoder is None
    assert task.data.time == 0.
    assert not (tmp_path / "simulation.mp4").exists()


def test_render_failure_stops_only_recorder_and_closes_resources(tmp_path, monkeypatch):
    # No fake MP4 is published: this tests the exception boundary, not encoding.
    recorder = simulation_video.SimulationVideo.__new__(simulation_video.SimulationVideo)
    recorder.task = SimpleNamespace(data=SimpleNamespace(time=1.), stage="pick")
    recorder.directory = tmp_path
    recorder.closed = False
    recorder.next_time = 0.
    recorder.first_time = 0.
    recorder.info = dict(available=False, error=None, frames=0)
    recorder.renderer = Mock()
    recorder.renderer.update_scene.side_effect = RuntimeError("offscreen rendering failed")
    recorder.camera = object()
    recorder.encoder = Mock()
    recorder.encoder.wait.return_value = 1
    recorder.encoder.poll.return_value = 1
    recorder.log = Mock()
    recorder.sample()
    recorder.close()  # Idempotent, including failure cleanup.
    assert recorder.info["error"] == "offscreen rendering failed"
    assert recorder.info["available"] is False
    recorder.renderer.close.assert_called_once()
    recorder.log.close.assert_called_once()
    recorder.encoder.stdin.close.assert_called_once()


def test_encoder_timeout_kills_and_reaps_process(tmp_path):
    recorder = simulation_video.SimulationVideo.__new__(simulation_video.SimulationVideo)
    recorder.closed = False
    recorder.info = dict(available=False, error=None, frames=10)
    recorder.renderer, recorder.log, recorder.encoder = Mock(), Mock(), Mock()
    recorder.encoder.wait.side_effect = [simulation_video.subprocess.TimeoutExpired("encoder", 5), 0]
    recorder.encoder.poll.return_value = None
    recorder.close()
    recorder.encoder.kill.assert_called_once()
    assert recorder.encoder.wait.call_count == 2
    assert recorder.info["available"] is False
    assert recorder.info["error"]
