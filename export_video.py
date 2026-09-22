"""导出完整 20 秒机械臂抓球视频：python export_video.py。

默认 1920×1080、30 fps、H.264 MP4；不打开原生 MuJoCo 显示窗口。
编码器使用 PATH 中的 ffmpeg，或本项目 .video_dependencies 中的版本。
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

import mujoco
from PIL import Image, ImageDraw, ImageFont

from pick_and_place import OUTPUT_DIR, ROOT, PickAndPlace, save_results


def find_ffmpeg() -> str:
    executable = shutil.which("ffmpeg")
    if executable:
        return executable
    bundled = list((ROOT / ".video_dependencies" / "imageio_ffmpeg" / "binaries").glob("ffmpeg*.exe"))
    if bundled:
        return str(bundled[0])
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as exc:
        raise SystemExit("缺少 MP4 编码器。请运行：python -m pip install imageio-ffmpeg") from exc


def export_video(output: Path, width: int, height: int, fps: int) -> Path:
    ffmpeg = find_ffmpeg()
    output.mkdir(parents=True, exist_ok=True)
    destination = output / "robot_pick_and_place.mp4"
    temporary = output / "robot_pick_and_place.partial.mp4"
    sim = PickAndPlace()
    sim.model.vis.global_.offwidth = width
    sim.model.vis.global_.offheight = height
    camera = mujoco.MjvCamera()
    camera.lookat[:] = [0.23, 0.02, 0.32]
    camera.distance, camera.azimuth, camera.elevation = 1.65, 135, -28
    total_frames = round(sim.duration * fps)
    total_steps = round(sim.duration / sim.model.opt.timestep)
    font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "msyh.ttc"
    chinese = font_path.exists()
    font = ImageFont.truetype(str(font_path), max(16, round(height / 32))) if chinese else ImageFont.load_default()
    command = [ffmpeg, "-hide_banner", "-loglevel", "warning", "-y",
               "-f", "rawvideo", "-vcodec", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0", "-an",
               "-c:v", "libx264", "-preset", "medium", "-crf", "18",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(temporary)]
    step_count = 0
    preview_frames = {0: "start", round(total_frames * 0.48): "carrying", total_frames - 1: "finish"}
    with (output / "encoding.log").open("w", encoding="utf-8") as error_log:
        with mujoco.Renderer(sim.model, height=height, width=width) as renderer:
            encoder = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                       stderr=error_log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            try:
                for index in range(total_frames):
                    # 按仿真时间抽帧；编码/渲染速度不会改变视频时长或物理步长。
                    target_step = round((index + 1) * total_steps / total_frames)
                    while step_count < target_step:
                        sim.step()
                        step_count += 1
                        if step_count % 10 == 0:
                            sim.record()
                    renderer.update_scene(sim.data, camera=camera)
                    frame = Image.fromarray(renderer.render())
                    draw = ImageDraw.Draw(frame)
                    stage = sim.segments[sim.command_at(sim.data.time)[0]][0]
                    if chinese:
                        title = "机械臂抓球与搬运仿真"
                        subtitle = f"{stage.split(' / ')[0]}    {sim.data.time:04.1f} / {sim.duration:.0f} 秒"
                    else:
                        title = "Robot pick and place"
                        subtitle = f"{stage.split(' / ')[-1]}    {sim.data.time:04.1f} / {sim.duration:.0f} s"
                    bar = round(height * 0.08)
                    draw.rectangle((0, 0, width, bar), fill=(20, 28, 38))
                    draw.text((round(width * 0.025), round(height * 0.018)), title, font=font, fill=(240, 245, 250))
                    draw.text((round(width * 0.975), round(height * 0.018)), subtitle, font=font,
                              fill=(166, 219, 207), anchor="ra")
                    draw.rectangle((0, height - 5, round(width * (index + 1) / total_frames), height), fill=(38, 178, 141))
                    encoder.stdin.write(frame.tobytes())
                    if index in preview_frames:
                        frame.save(output / f"video_{preview_frames[index]}.png")
                    if (index + 1) % (fps * 4) == 0:
                        print(f"视频渲染：{index + 1}/{total_frames} 帧", flush=True)
                encoder.stdin.close()
                if encoder.wait() != 0:
                    raise RuntimeError(f"视频编码失败，请查看 {output / 'encoding.log'}")
            except BaseException:
                if encoder.poll() is None:
                    encoder.kill()
                encoder.wait()
                raise
    report = save_results(sim, output)
    if not report["success"]:
        raise RuntimeError("本次抓取未成功，视频暂存为 partial.mp4；请检查结果。")
    temporary.replace(destination)
    metadata = dict(file=str(destination), width=width, height=height, fps=fps, frames=total_frames,
                    duration_seconds=total_frames / fps, video_codec="H.264", pixel_format="yuv420p",
                    audio=False, physics=report)
    (output / "video_info.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n视频已保存：{destination}\n{width}×{height}，{fps} fps，{total_frames / fps:g} 秒", flush=True)
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="导出机械臂抓球完整 MP4 视频")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR / "video")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--fps", type=int, default=30)
    args = parser.parse_args()
    if args.width < 320 or args.height < 240 or args.width % 2 or args.height % 2:
        parser.error("宽高必须是偶数，且至少 320×240")
    if not 1 <= args.fps <= 60:
        parser.error("帧率应在 1～60 之间")
    export_video(args.output_dir.resolve(), args.width, args.height, args.fps)


if __name__ == "__main__":
    main()
