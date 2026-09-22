"""PyCharm 直接运行本文件：MuJoCo 机械臂夹取、搬运、放下小球。

依赖：mujoco、numpy、matplotlib（无需下载机器人模型）。
python pick_and_place.py --headless         # 无窗口检查完整抓取
python pick_and_place.py --headless --gif   # 导出动画 GIF
python pick_and_place.py --plot             # 关闭仿真后显示轨迹图
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import threading
import time
from pathlib import Path

try:
    import mujoco
    import numpy as np
except ImportError as exc:
    raise SystemExit(
        f"缺少依赖：{exc}\n请在 PyCharm 当前解释器中安装："
        "python -m pip install mujoco numpy matplotlib"
    ) from exc

ROOT = Path(__file__).resolve().parent
MODEL_PATH = ROOT / "models" / "pick_and_place.xml"
OUTPUT_DIR = ROOT / "outputs" / "pick_and_place"
OPEN_FINGER = 0.064  # 每根手指中心到夹爪中线的距离，m
CLOSED_FINGER = 0.022  # 伺服目标；接触后真实间距由球体与接触力决定
HOME = np.array([0.32, 0.0, 0.36])  # 夹爪中心的世界坐标，m
LIFT_HEIGHT = 0.39


def smoothstep(value: float) -> float:
    """五次时间缩放，段落两端的速度和加速度均为零。"""
    s = float(np.clip(value, 0.0, 1.0))
    return s * s * s * (10.0 - 15.0 * s + 6.0 * s * s)


class PickAndPlace:
    """解析逆运动学 + 位置伺服；球始终由 MuJoCo 的动力学推进。"""

    def __init__(self) -> None:
        # 从 Python 读取 XML，兼容 Windows 用户名/项目目录中的中文。
        self.model = mujoco.MjModel.from_xml_string(MODEL_PATH.read_text(encoding="utf-8"))
        self.data = mujoco.MjData(self.model)
        self.arm_names = ("yaw", "shoulder", "elbow", "wrist")
        self.arm_jids = np.array([self.model.joint(n).id for n in self.arm_names])
        self.arm_qpos = self.model.jnt_qposadr[self.arm_jids]
        self.finger_qpos = [self.model.joint(n).qposadr[0] for n in ("finger_left", "finger_right")]
        self.arm_act = [self.model.actuator(n + "_servo").id for n in self.arm_names]
        self.finger_act = [self.model.actuator(n).id for n in ("left_servo", "right_servo")]
        self.ball_id = self.model.body("ball").id
        self.ball_geom = self.model.geom("ball_geom").id
        self.pad_ids = {self.model.geom(n).id for n in ("left_pad", "right_pad")}
        self.grip_id = self.model.site("grip_center").id
        self.place_id = self.model.site("place_target").id
        self.shoulder_height = 0.42
        self.l1, self.l2, self.tool_offset = 0.30, 0.28, 0.11
        self.reset()

    def inverse_kinematics(self, target: np.ndarray) -> np.ndarray:
        x, y, z = target
        radius = math.hypot(x, y)
        down = self.shoulder_height - (z + self.tool_offset)
        c2 = (radius * radius + down * down - self.l1**2 - self.l2**2) / (2 * self.l1 * self.l2)
        if abs(c2) > 1.0 + 1e-9:
            raise ValueError(f"夹爪目标超出机械臂工作空间：{target}")
        elbow = math.acos(float(np.clip(c2, -1.0, 1.0)))
        shoulder = math.atan2(down, radius) - math.atan2(self.l2 * math.sin(elbow), self.l1 + self.l2 * math.cos(elbow))
        q = np.array([math.atan2(y, x), shoulder, elbow, -shoulder - elbow])
        limits = self.model.jnt_range[self.arm_jids]
        if np.any(q < limits[:, 0]) or np.any(q > limits[:, 1]):
            raise ValueError(f"目标导致关节超限：{q}")
        return q

    def reset(self) -> None:
        mujoco.mj_resetData(self.model, self.data)
        self.data.qpos[self.arm_qpos] = self.inverse_kinematics(HOME)
        self.data.qpos[self.finger_qpos] = OPEN_FINGER
        self.data.ctrl[self.arm_act] = self.data.qpos[self.arm_qpos]
        self.data.ctrl[self.finger_act] = OPEN_FINGER
        mujoco.mj_forward(self.model, self.data)
        self.pick = self.data.xpos[self.ball_id].copy()
        self.place = self.data.site_xpos[self.place_id].copy()
        pick_above = np.array([*self.pick[:2], LIFT_HEIGHT])
        place_above = np.array([*self.place[:2], LIFT_HEIGHT])
        # (阶段名称，持续秒数，阶段末夹爪位置，阶段末手指开度)
        self.segments = [
            ("准备 / Ready", 1.0, HOME, OPEN_FINGER),
            ("靠近 / Approach", 2.0, pick_above, OPEN_FINGER),
            ("下降 / Descend", 2.0, self.pick, OPEN_FINGER),
            ("夹紧 / Close gripper", 1.5, self.pick, CLOSED_FINGER),
            ("抬起 / Lift", 2.0, pick_above, CLOSED_FINGER),
            ("搬运 / Transfer", 2.5, place_above, CLOSED_FINGER),
            ("放低 / Lower", 2.0, self.place, CLOSED_FINGER),
            ("松开 / Release", 1.0, self.place, OPEN_FINGER),
            ("撤回 / Retreat", 2.0, place_above, OPEN_FINGER),
            ("归位 / Return", 2.0, HOME, OPEN_FINGER),
            ("完成 / Done", 2.0, HOME, OPEN_FINGER),
        ]
        self.duration = sum(s[1] for s in self.segments)
        self.records: list[dict] = []
        self.last_stage = -1
        self.max_ball_height = float(self.pick[2])
        self.bilateral_steps = 0
        self.transfer_steps = 0
        self.transfer_held_steps = 0
        self.record()

    def command_at(self, t: float):
        start_t, start_pos, start_gap = 0.0, HOME, OPEN_FINGER
        for i, (_, duration, end_pos, end_gap) in enumerate(self.segments):
            if t < start_t + duration or i == len(self.segments) - 1:
                blend = smoothstep((t - start_t) / duration)
                return i, start_pos + blend * (end_pos - start_pos), start_gap + blend * (end_gap - start_gap)
            start_t, start_pos, start_gap = start_t + duration, end_pos, end_gap
        raise RuntimeError("Empty trajectory")

    def touching_pads(self) -> set[int]:
        touched = set()
        for contact in self.data.contact:
            if contact.geom1 == self.ball_geom and contact.geom2 in self.pad_ids:
                touched.add(int(contact.geom2))
            if contact.geom2 == self.ball_geom and contact.geom1 in self.pad_ids:
                touched.add(int(contact.geom1))
        return touched

    def step(self, announce: bool = True) -> None:
        stage, target, opening = self.command_at(self.data.time)
        if stage != self.last_stage:
            if announce:
                print(f"[{self.data.time:5.1f} s] {self.segments[stage][0]}", flush=True)
            self.last_stage = stage
        self.data.ctrl[self.arm_act] = self.inverse_kinematics(target)
        self.data.ctrl[self.finger_act] = opening
        # 没有 weld、没有运行中改写球的 qpos，也没有给球施加额外外力。
        mujoco.mj_step(self.model, self.data)
        mujoco.mj_forward(self.model, self.data)
        if not np.isfinite(self.data.qpos).all() or not np.isfinite(self.data.qvel).all():
            raise RuntimeError("仿真产生非有限状态，请检查模型与控制参数。")
        if np.any(self.data.warning.number):
            raise RuntimeError(f"MuJoCo 数值警告：{self.data.warning.number}")
        self.max_ball_height = max(self.max_ball_height, float(self.data.xpos[self.ball_id, 2]))
        touching_both = len(self.touching_pads()) == 2
        if touching_both:
            self.bilateral_steps += 1
        if stage == 5:  # 搬运阶段必须持续夹住，而且小球确实处在空中。
            self.transfer_steps += 1
            if touching_both and self.data.xpos[self.ball_id, 2] > self.pick[2] + 0.12:
                self.transfer_held_steps += 1

    def record(self) -> None:
        ball = self.data.xpos[self.ball_id].copy()
        grip = self.data.site_xpos[self.grip_id].copy()
        stage, target, _ = self.command_at(self.data.time)
        self.records.append(dict(
            time=float(self.data.time), stage=stage,
            ball_x=float(ball[0]), ball_y=float(ball[1]), ball_z=float(ball[2]),
            grip_x=float(grip[0]), grip_y=float(grip[1]), grip_z=float(grip[2]),
            target_x=float(target[0]), target_y=float(target[1]), target_z=float(target[2]),
            finger_gap=float(sum(self.data.qpos[self.finger_qpos]) - 0.016),
            touching_pads=len(self.touching_pads()),
        ))

    def summary(self) -> dict:
        ball = self.data.xpos[self.ball_id].copy()
        xy_error = float(np.linalg.norm(ball[:2] - self.place[:2]))
        z_error = float(abs(ball[2] - self.place[2]))
        ball_dof = self.model.joint("ball_free").dofadr[0]
        speed = float(np.linalg.norm(self.data.qvel[ball_dof:ball_dof + 3]))
        complete = self.data.time >= self.duration - 1e-8
        lifted = self.max_ball_height - self.pick[2] > 0.12
        released = len(self.touching_pads()) == 0
        transfer_ratio = self.transfer_held_steps / max(1, self.transfer_steps)
        success = complete and lifted and transfer_ratio > 0.95 and xy_error < 0.03 and z_error < 0.006 and speed < 0.03 and released
        return dict(success=bool(success), completed=bool(complete), simulated_seconds=float(self.data.time),
                    lifted=bool(lifted), max_lift_m=float(self.max_ball_height - self.pick[2]),
                    bilateral_contact_seconds=float(self.bilateral_steps * self.model.opt.timestep),
                    transfer_held_ratio=float(transfer_ratio),
                    final_ball_position_m=ball.tolist(), target_position_m=self.place.tolist(),
                    final_xy_error_m=xy_error, final_z_error_m=z_error,
                    final_ball_speed_m_s=speed, released=bool(released),
                    mujoco_version=mujoco.__version__, grasp_method="physical contact and friction; no weld")


def save_results(sim: PickAndPlace, output: Path, show_plot: bool = False) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    report = sim.summary()
    (output / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "trajectory.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(sim.records[0]))
        writer.writeheader()
        writer.writerows(sim.records)
    import matplotlib
    if not show_plot:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    times = [r["time"] for r in sim.records]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.3), layout="constrained")
    axes[0].plot(times, [r["ball_z"] for r in sim.records], color="#f47722", lw=2.3, label="Ball")
    axes[0].plot(times, [r["grip_z"] for r in sim.records], color="#197eaa", ls="--", label="Gripper center")
    axes[0].set(xlabel="Simulation time (s)", ylabel="Height (m)", title="Lift and release")
    axes[0].legend()
    axes[1].plot([r["ball_x"] for r in sim.records], [r["ball_y"] for r in sim.records], color="#f47722", lw=2, label="Ball path")
    axes[1].scatter(*sim.pick[:2], marker="o", s=70, color="#f47722", label="Pick")
    axes[1].scatter(*sim.place[:2], marker="s", s=100, color="#249d74", label="Place")
    axes[1].set(xlabel="X (m)", ylabel="Y (m)", title="Top view")
    axes[1].axis("equal")
    axes[1].legend()
    for ax in axes:
        ax.grid(alpha=0.2)
    fig.savefig(output / "trajectory.png", dpi=170)
    if show_plot:
        plt.show()
    plt.close(fig)
    return report


def run_headless(sim: PickAndPlace, output: Path, export_gif: bool) -> None:
    renderer, frames = None, []
    if export_gif:
        from PIL import Image, ImageDraw
        renderer = mujoco.Renderer(sim.model, height=480, width=640)
    next_frame = 0.0
    steps = round(sim.duration / sim.model.opt.timestep)
    try:
        for i in range(steps):
            sim.step()
            if i % 10 == 0 or i == steps - 1:
                sim.record()
            if renderer is not None and sim.data.time >= next_frame:
                renderer.update_scene(sim.data, camera="overview")
                frame = Image.fromarray(renderer.render())
                draw = ImageDraw.Draw(frame)
                draw.rectangle((0, 0, 640, 32), fill=(20, 28, 38))
                label = sim.segments[sim.command_at(sim.data.time)[0]][0].split(" / ")[-1]
                draw.text((14, 10), f"ROBOT PICK & PLACE  |  {label}  |  {sim.data.time:04.1f} s", fill=(240, 245, 250))
                frames.append(frame)
                next_frame += 1 / 15
        if frames:
            output.mkdir(parents=True, exist_ok=True)
            frames[0].save(output / "pick_and_place.gif", save_all=True, append_images=frames[1:], duration=67, loop=0)
            frames[round(len(frames) * 0.47)].save(output / "preview.png")
            print(f"动画已保存：{output / 'pick_and_place.gif'}")
    finally:
        if renderer is not None:
            renderer.close()


def run_viewer(sim: PickAndPlace, speed: float) -> None:
    import mujoco.viewer
    pause_event, reset_event = threading.Event(), threading.Event()

    def on_key(key: int) -> None:
        if key == 32:
            if pause_event.is_set():
                pause_event.clear()
            else:
                pause_event.set()
        elif key in (ord("R"), ord("r")):
            reset_event.set()

    print("\n打开 3D 动画窗口。空格：暂停/继续；R：重新抓取；关闭窗口：退出并保存结果。")
    print("一次动作约 20 秒，结束后保留窗口，按 R 可重播。\n")
    with mujoco.viewer.launch_passive(sim.model, sim.data, key_callback=on_key) as viewer:
        with viewer.lock():
            viewer.cam.lookat[:] = [0.23, 0.02, 0.32]
            viewer.cam.distance = 1.48
            viewer.cam.azimuth = 135
            viewer.cam.elevation = -28
        count, finished = 0, False
        time_budget = 0.0
        previous_wall = time.perf_counter()
        while viewer.is_running():
            frame_start = time.perf_counter()
            wall_elapsed = min(frame_start - previous_wall, 0.1)
            previous_wall = frame_start
            if reset_event.is_set():
                with viewer.lock():
                    sim.reset()
                reset_event.clear()
                pause_event.clear()
                count, finished = 0, False
                time_budget = 0.0
            if not pause_event.is_set() and sim.data.time < sim.duration - 1e-8:
                # 固定 2 ms 物理步长，改变播放速度不会改变接触求解步长。
                time_budget += wall_elapsed * speed
                steps = int(time_budget / sim.model.opt.timestep)
                time_budget -= steps * sim.model.opt.timestep
                with viewer.lock():
                    for _ in range(steps):
                        if sim.data.time >= sim.duration - 1e-8:
                            break
                        sim.step()
                        count += 1
                        if count % 10 == 0:
                            sim.record()
            if sim.data.time >= sim.duration - 1e-8 and not finished:
                sim.record()
                print("\n抓取搬运完成。" if sim.summary()["success"] else "\n动作结束，但未达到抓取成功判据。")
                print("按 R 重播，或关闭 3D 窗口保存轨迹图和数据。", flush=True)
                finished = True
            viewer.sync()
            time.sleep(max(0, 1 / 60 - (time.perf_counter() - frame_start)))
    sim.record()


class CompatibilityViewer:
    """MuJoCo 离屏渲染 + Tk 显示，绕过原生 Viewer 的屏幕绘制路径。

    所有 OpenGL、Tk 和物理调用均在主线程执行；仍使用真实的 MuJoCo 画面。
    """

    def __init__(self, sim: PickAndPlace, speed: float) -> None:
        import tkinter as tk
        from PIL import Image, ImageTk

        self.sim, self.speed = sim, speed
        self.Image, self.ImageTk = Image, ImageTk
        self.root = tk.Tk()
        self.root.title("机械臂抓球 — 兼容显示模式")
        self.root.configure(bg="#17212d")
        self.root.resizable(False, False)
        self.renderer = None
        try:
            self.renderer = mujoco.Renderer(sim.model, height=600, width=800)
        except Exception:
            self.root.destroy()
            raise
        self.camera = mujoco.MjvCamera()
        self.camera.lookat[:] = [0.23, 0.02, 0.32]
        self.camera.distance = 1.48
        self.camera.azimuth = 135
        self.camera.elevation = -28
        self.status = tk.StringVar(master=self.root, value="正在准备画面…")
        tk.Label(self.root, textvariable=self.status, bg="#17212d", fg="white",
                 font=("Microsoft YaHei", 11), pady=8).pack()
        self.picture = tk.Label(self.root, bg="#17212d", borderwidth=0)
        self.picture.pack()
        controls = tk.Frame(self.root, bg="#17212d")
        controls.pack(fill="x", padx=12, pady=10)
        self.pause_button = tk.Button(controls, text="暂停 / 继续（空格）", command=self.toggle_pause, takefocus=False)
        self.pause_button.pack(side="left")
        tk.Button(controls, text="重新播放（R）", command=self.restart, takefocus=False).pack(side="left", padx=10)
        tk.Label(controls, text="鼠标拖动旋转 · 滚轮缩放 · 关闭窗口保存结果",
                 fg="#cbd5e1", bg="#17212d").pack(side="right")
        self.root.bind("<space>", self.toggle_pause)
        self.root.bind("<KeyPress-r>", self.restart)
        self.root.bind("<KeyPress-R>", self.restart)
        self.picture.bind("<ButtonPress-1>", self.begin_drag)
        self.picture.bind("<B1-Motion>", self.drag)
        self.picture.bind("<MouseWheel>", self.zoom)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.paused = self.finished = self.closed = False
        self.dirty = True
        self.steps = self.rendered_frames = 0
        self.budget = 0.0
        self.previous_wall = time.perf_counter()
        self.drag_origin = None
        self.photo = None  # 保持 PhotoImage 引用，否则 Tk 会清除画面。
        self.timer = None
        self.error = None

    def toggle_pause(self, event=None):
        self.paused = not self.paused
        self.previous_wall = time.perf_counter()
        return "break"

    def restart(self, event=None):
        self.sim.reset()
        self.paused = self.finished = False
        self.steps, self.budget = 0, 0.0
        self.previous_wall = time.perf_counter()
        self.dirty = True
        return "break"

    def begin_drag(self, event):
        self.drag_origin = (event.x, event.y)
        self.picture.focus_set()

    def drag(self, event):
        if self.drag_origin is not None:
            dx, dy = event.x - self.drag_origin[0], event.y - self.drag_origin[1]
            self.camera.azimuth -= 0.4 * dx
            self.camera.elevation = float(np.clip(self.camera.elevation - 0.3 * dy, -85, -5))
            self.drag_origin = (event.x, event.y)
            self.dirty = True

    def zoom(self, event):
        self.camera.distance = float(np.clip(self.camera.distance * 0.9 ** (event.delta / 120), 0.65, 3.0))
        self.dirty = True

    def tick(self):
        if self.closed:
            return
        start = time.perf_counter()
        elapsed = min(start - self.previous_wall, 0.1)
        self.previous_wall = start
        try:
            if not self.paused and not self.finished:
                self.budget += elapsed * self.speed
                while self.budget >= self.sim.model.opt.timestep and self.sim.data.time < self.sim.duration - 1e-8:
                    self.sim.step()
                    self.budget -= self.sim.model.opt.timestep
                    self.steps += 1
                    self.dirty = True
                    if self.steps % 10 == 0:
                        self.sim.record()
                if self.sim.data.time >= self.sim.duration - 1e-8:
                    self.finished = True
                    self.sim.record()
                    message = "抓取搬运完成。" if self.sim.summary()["success"] else "动作结束，未达到成功判据。"
                    print(message + "按 R 重播，关闭窗口保存结果。", flush=True)
            if self.dirty:
                self.renderer.update_scene(self.sim.data, camera=self.camera)
                self.photo = self.ImageTk.PhotoImage(self.Image.fromarray(self.renderer.render()), master=self.root)
                self.picture.configure(image=self.photo)
                self.rendered_frames += 1
                self.dirty = False
            stage = self.sim.segments[self.sim.command_at(self.sim.data.time)[0]][0].split(" / ")[0]
            mode = "已暂停" if self.paused else ("完成，可按 R 重播" if self.finished else stage)
            self.status.set(f"机械臂抓球   |   {mode}   |   {self.sim.data.time:04.1f} / {self.sim.duration:.0f} 秒")
        except Exception as exc:
            self.error = exc
            self.close()
            return
        self.timer = self.root.after(max(1, round(1000 / 30 - 1000 * (time.perf_counter() - start))), self.tick)

    def close(self):
        if not self.closed:
            self.closed = True
            if self.timer is not None:
                self.root.after_cancel(self.timer)
            self.root.destroy()

    def run(self):
        print("\n使用兼容窗口：空格暂停，R 重播，鼠标拖动旋转，滚轮缩放。", flush=True)
        try:
            self.tick()
            self.root.mainloop()
        finally:
            self.close()
            self.renderer.close()
            self.sim.record()
        if self.error is not None:
            raise self.error


def main() -> int:
    parser = argparse.ArgumentParser(description="MuJoCo 机械臂真实接触抓球动画；PyCharm 可直接运行。")
    parser.add_argument("--headless", action="store_true", help="不打开交互窗口，运行一次仿真")
    parser.add_argument("--gif", action="store_true", help="离屏渲染并导出 GIF（仍需 OpenGL）")
    parser.add_argument("--plot", action="store_true", help="仿真结束后显示 Matplotlib 轨迹图")
    parser.add_argument("--display", choices=("auto", "tk", "native"), default="auto",
                        help="auto：Windows 使用兼容窗口；tk：兼容窗口；native：MuJoCo 原生窗口")
    parser.add_argument("--speed", type=float, default=1.0, help="交互窗口播放速度，默认 1")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR, help="结果输出目录")
    args = parser.parse_args()
    if not math.isfinite(args.speed) or not 0.1 <= args.speed <= 4:
        parser.error("--speed 必须在 0.1 到 4 之间")
    output = args.output_dir.resolve()
    sim = PickAndPlace()
    if args.headless or args.gif:
        run_headless(sim, output, args.gif)
    elif args.display == "tk" or (args.display == "auto" and sys.platform == "win32"):
        CompatibilityViewer(sim, args.speed).run()
    else:
        run_viewer(sim, args.speed)
    report = save_results(sim, output, args.plot)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"结果目录：{output}")
    return 0 if report["success"] or not report["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
