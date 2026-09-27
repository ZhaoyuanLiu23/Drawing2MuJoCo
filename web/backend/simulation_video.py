"""Read-only offscreen observer of one live robot task. No stepping or replay."""
from pathlib import Path
import shutil
import subprocess


def find_encoder():
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    root = Path(__file__).resolve().parents[2]
    bundled = sorted((root / ".video_dependencies/imageio_ffmpeg/binaries").glob("ffmpeg*.exe"))
    if bundled:
        return str(bundled[0])
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as exc:
        raise RuntimeError("FFmpeg unavailable; install imageio-ffmpeg in the simulation environment") from exc


class SimulationVideo:
    """Failures stay in metadata; they never change the physical task verdict.

    Construct, sample and close on the task thread, like its RGB-D renderer.
    A bounded-memory raw RGB pipe feeds the project's existing FFmpeg binary.
    Only a finalized, fully decodable file is published as simulation.mp4.
    """
    WIDTH, HEIGHT, FPS = 960, 540, 15

    def __init__(self, task, directory):
        self.task = task
        self.directory = Path(directory)
        self.partial = self.directory / "simulation.partial.mp4"
        self.destination = self.directory / "simulation.mp4"
        self.renderer = self.encoder = self.log = None
        self.closed = False
        self.first_time = float(task.data.time)
        self.next_time = self.first_time
        self.info = dict(available=False, width=self.WIDTH, height=self.HEIGHT, fps=self.FPS,
                         codec="H.264", pixel_format="yuv420p", frames=0, error=None,
                         source="same live Agent MjModel/MjData; post-step read-only observer",
                         first_simulation_time_s=self.first_time, last_simulation_time_s=None,
                         stages=[], complete=False)
        try:
            import mujoco
            self.executable = find_encoder()
            self.camera = mujoco.MjvCamera()
            # Frame the table and robot base; independent of object pose or size.
            table = task.data.geom_xpos[task.table_id]
            self.camera.lookat[:] = [table[0] / 2, table[1], task.table_top + 0.25]
            self.camera.distance = max(1.65, 4 * max(task.model.geom_size[task.table_id, :2]))
            self.camera.azimuth, self.camera.elevation = 135, -28
            self.info["camera"] = dict(lookat=self.camera.lookat.tolist(), distance=self.camera.distance,
                                       azimuth=self.camera.azimuth, elevation=self.camera.elevation)
            self.renderer = mujoco.Renderer(task.model, height=self.HEIGHT, width=self.WIDTH)
            self.log = (self.directory / "video_encoding.log").open("wb")
            command = [self.executable, "-hide_banner", "-loglevel", "error", "-y",
                       "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{self.WIDTH}x{self.HEIGHT}",
                       "-r", str(self.FPS), "-i", "pipe:0", "-an", "-c:v", "libx264",
                       "-threads", "2", "-preset", "veryfast", "-crf", "25",
                       "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(self.partial)]
            self.encoder = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                            stderr=self.log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.sample()
        except Exception as exc:
            self.info["error"] = str(exc)
            self.close()

    def sample(self):
        if self.closed or self.info["error"]:
            return
        time = float(self.task.data.time)
        if time + 1e-9 < self.next_time:
            return
        try:
            self.renderer.update_scene(self.task.data, camera=self.camera)
            self.encoder.stdin.write(self.renderer.render().tobytes())
            self.info["frames"] += 1
            self.info["last_simulation_time_s"] = time
            stage = self.task.stage
            if not self.info["stages"] or self.info["stages"][-1] != stage:
                self.info["stages"].append(stage)
            self.next_time = self.first_time + self.info["frames"] / self.FPS
        except Exception as exc:
            self.info["error"] = str(exc)
            self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            if self.encoder is not None:
                try:
                    self.encoder.stdin.close()
                except OSError:
                    pass
                if self.encoder.wait(timeout=5) != 0:
                    raise RuntimeError("MP4 encoding failed; see video_encoding.log")
                if self.info["frames"] < 2:
                    raise RuntimeError("Not enough executed frames for a video")
                # Validate the entire stream, not just an MP4 filename/header.
                subprocess.run([self.executable, "-v", "error", "-xerror", "-i", str(self.partial),
                                "-map", "0:v:0", "-f", "null", "-"], check=True, timeout=5,
                               stdout=subprocess.DEVNULL, stderr=self.log,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                self.partial.replace(self.destination)
                self.info.update(available=True, complete=self.info["error"] is None,
                                 duration_s=self.info["frames"] / self.FPS,
                                 bytes=self.destination.stat().st_size)
        except Exception as exc:
            self.info["error"] = self.info["error"] or str(exc)
        finally:
            if self.encoder is not None and self.encoder.poll() is None:
                self.encoder.kill()
                self.encoder.wait()
            if self.log is not None:
                self.log.close()
            if self.renderer is not None:
                try:
                    self.renderer.close()
                except Exception as exc:
                    self.info["error"] = self.info["error"] or str(exc)
