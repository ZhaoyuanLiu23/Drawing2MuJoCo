"""PyCharm 右键 Run 本文件：用本机 Menagerie Franka Panda 模型抓球。

python panda_grasp.py                 # 兼容显示窗口
python panda_grasp.py --headless      # 无窗口验证
python panda_grasp.py --gif           # 导出预览 GIF
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import mujoco
import numpy as np

from pick_and_place import CompatibilityViewer, PickAndPlace, run_headless, save_results, smoothstep

# 在其他电脑上，只需修改此处；路径中的下划线前不要添加反斜杠。
MODEL_PATH = Path(r"C:\Users\86155\Desktop\mujoco_menagerie\franka_emika_panda\scene.xml")
OUTPUT_DIR = Path(__file__).resolve().parent / "outputs" / "panda_grasp"
BALL_RADIUS = 0.027
BALL_START = np.array([0.48, -0.16, 0.157])
PLACE_TARGET = np.array([0.50, 0.19, 0.165])
LIFT_HEIGHT = 0.40
OPEN_GRIPPER, CLOSED_GRIPPER = 255.0, 0.0


def load_panda_scene(scene_path: Path) -> mujoco.MjModel:
    """合并用户 scene.xml 与 panda.xml，在内存里加道具，不改原模型文件。"""
    scene_path = scene_path.expanduser().resolve()
    if not scene_path.is_file():
        raise FileNotFoundError(f"找不到 Panda 场景：{scene_path}\n请修改 panda_grasp.py 顶部 MODEL_PATH。")
    scene = ET.parse(scene_path).getroot()
    include = scene.find("include")
    if include is None:
        raise ValueError("需要 Menagerie franka_emika_panda/scene.xml（其中 include panda.xml）。")
    robot_path = scene_path.parent / include.attrib["file"]
    root = ET.parse(robot_path).getroot()
    root.set("model", "franka_panda_ball_pick_and_place")
    compiler = root.find("compiler")
    mesh_root = robot_path.parent / compiler.attrib.pop("meshdir", "")
    assets = {}
    for mesh in root.findall("asset/mesh"):
        source = mesh_root / mesh.attrib["file"]
        # VFS 通过 Python 读取网格，避免 Windows 中文路径的原生 fopen 问题。
        name = source.name
        assets[name] = source.read_bytes()
        mesh.set("file", name)
    for section in scene:
        if section.tag == "include":
            continue
        existing = root.find(section.tag)
        if existing is None:
            root.append(section)
        else:
            existing.extend(list(section))
    option = root.find("option")
    option.attrib.update(timestep="0.002", integrator="implicitfast", cone="elliptic",
                         solver="Newton", iterations="100", impratio="5")
    visual = root.find("visual")
    global_view = visual.find("global")
    if global_view is None:
        global_view = ET.SubElement(visual, "global")
    global_view.attrib.update(offwidth="960", offheight="720")
    world = root.find("worldbody")
    # 理想重力补偿用于演示；原有网格、质量惯量、关节和执行器保持不变。
    for body in world.iter("body"):
        if body.find("joint") is not None or body.attrib.get("name") == "hand":
            body.set("gravcomp", "1")
    hand = world.find(".//body[@name='hand']")
    if hand is None:
        raise ValueError("该模型不包含 Panda hand body。")
    ET.SubElement(hand, "site", name="grip_center", pos="0 0 0.1029", size="0.003", rgba="0 1 0 0")
    # 保留原指垫形状，仅调整接触摩擦/求解参数以夹持 54 mm 球体。
    for name in ("left_finger", "right_finger"):
        finger = world.find(f".//body[@name='{name}']")
        for geom in finger.findall("geom"):
            if geom.attrib.get("class") != "visual":
                geom.attrib.update(friction="1.5 0.025 0.002", condim="6", priority="1",
                                   solref="0.006 1", solimp="0.95 0.99 0.001")
    props = ET.fromstring('''<worldbody>
      <camera name="overview" pos="1.50 -1.65 1.25" xyaxes="0.827 0.562 0 -0.233 0.342 0.910"/>
      <geom name="work_table" type="box" pos="0.51 0 0.095" size="0.29 0.38 0.035" rgba="0.72 0.77 0.80 1"/>
      <site name="pick_marker" type="cylinder" pos="0.48 -0.16 0.131" size="0.065 0.0005" rgba="1 0.65 0.12 1"/>
      <body name="tray" pos="0.50 0.19 0.13">
        <geom name="tray_floor" type="box" pos="0 0 0.004" size="0.09 0.09 0.004" rgba="0.1 0.6 0.43 1" friction="1 0.01 0.001"/>
        <geom type="box" pos="0.09 0 0.014" size="0.004 0.094 0.014" rgba="0.05 0.34 0.24 1"/>
        <geom type="box" pos="-0.09 0 0.014" size="0.004 0.094 0.014" rgba="0.05 0.34 0.24 1"/>
        <geom type="box" pos="0 0.09 0.014" size="0.086 0.004 0.014" rgba="0.05 0.34 0.24 1"/>
        <geom type="box" pos="0 -0.09 0.014" size="0.086 0.004 0.014" rgba="0.05 0.34 0.24 1"/>
        <site name="place_target" pos="0 0 0.035" size="0.003" rgba="0 1 0 0"/>
      </body>
      <body name="ball" pos="0.48 -0.16 0.157">
        <freejoint name="ball_free"/>
        <geom name="ball_geom" type="sphere" size="0.027" mass="0.035" rgba="1 0.3 0.035 1"
              friction="1.5 0.025 0.002" condim="6" solref="0.006 1" solimp="0.95 0.99 0.001"/>
      </body>
    </worldbody>''')
    world.extend(list(props))
    ball_state = " " + " ".join(map(str, BALL_START)) + " 1 0 0 0"
    for key in root.findall("keyframe/key"):
        key.set("qpos", key.attrib["qpos"] + ball_state)
    return mujoco.MjModel.from_xml_string(ET.tostring(root, encoding="unicode"), assets=assets)


class PandaGrasp(PickAndPlace):
    """7 关节位姿 IK、原模型位置伺服和 0～255 夹爪控制。"""

    def __init__(self, scene_path: Path = MODEL_PATH):
        self.scene_path = Path(scene_path).resolve()
        self.model = load_panda_scene(self.scene_path)
        self.data = mujoco.MjData(self.model)
        self.ik_data = mujoco.MjData(self.model)
        self.arm_jids = np.array([self.model.joint(f"joint{i}").id for i in range(1, 8)])
        self.arm_qpos = self.model.jnt_qposadr[self.arm_jids]
        self.arm_dofs = self.model.jnt_dofadr[self.arm_jids]
        self.arm_act = [self.model.actuator(f"actuator{i}").id for i in range(1, 8)]
        self.gripper_act = self.model.actuator("actuator8").id
        self.finger_qpos = [self.model.joint(n).qposadr[0] for n in ("finger_joint1", "finger_joint2")]
        self.finger_bodies = {self.model.body(n).id for n in ("left_finger", "right_finger")}
        self.ball_id, self.ball_geom = self.model.body("ball").id, self.model.geom("ball_geom").id
        self.grip_id, self.place_id = self.model.site("grip_center").id, self.model.site("place_target").id
        self.home_key = self.model.key("home").id
        self.jacp = np.zeros((3, self.model.nv))
        self.jacr = np.zeros((3, self.model.nv))
        self.references = None
        self.reset()
        print("正在为 Panda 规划抓球轨迹…", flush=True)
        self.reference_dt = 0.01
        self.reference_times = np.linspace(0, self.duration, round(self.duration / self.reference_dt) + 1)
        references, seed = [], self.data.qpos[self.arm_qpos].copy()
        self.max_ik_position_error = 0.0
        for t in self.reference_times:
            _, target, _ = self.command_at(t)
            seed = self.inverse_kinematics(target, seed)
            references.append(seed.copy())
        self.references = np.array(references)
        print(f"轨迹规划完成：{len(self.references)} 个参考点。", flush=True)

    def reset(self):
        mujoco.mj_resetDataKeyframe(self.model, self.data, self.home_key)
        mujoco.mj_forward(self.model, self.data)
        self.home = self.data.site_xpos[self.grip_id].copy()
        self.target_rotation = self.data.site_xmat[self.grip_id].reshape(3, 3).copy()
        self.pick, self.place = self.data.xpos[self.ball_id].copy(), self.data.site_xpos[self.place_id].copy()
        pick_above = np.array([*self.pick[:2], LIFT_HEIGHT])
        place_above = np.array([*self.place[:2], LIFT_HEIGHT])
        self.segments = [
            ("准备 / Ready", 1.0, self.home, OPEN_GRIPPER),
            ("靠近 / Approach", 2.0, pick_above, OPEN_GRIPPER),
            ("下降 / Descend", 2.5, self.pick, OPEN_GRIPPER),
            ("夹紧 / Close gripper", 2.0, self.pick, CLOSED_GRIPPER),
            ("抬起 / Lift", 2.5, pick_above, CLOSED_GRIPPER),
            ("搬运 / Transfer", 3.0, place_above, CLOSED_GRIPPER),
            ("放低 / Lower", 2.5, self.place, CLOSED_GRIPPER),
            ("松开 / Release", 1.5, self.place, OPEN_GRIPPER),
            ("撤回 / Retreat", 2.0, place_above, OPEN_GRIPPER),
            ("归位 / Return", 2.5, self.home, OPEN_GRIPPER),
            ("完成 / Done", 2.0, self.home, OPEN_GRIPPER),
        ]
        self.duration = sum(s[1] for s in self.segments)
        self.records, self.last_stage = [], -1
        self.max_ball_height = float(self.pick[2])
        self.bilateral_steps = self.transfer_steps = self.transfer_held_steps = 0
        self.record()

    def command_at(self, t):
        start_t, start_pos, start_gap = 0.0, self.home, OPEN_GRIPPER
        for i, (_, duration, end_pos, end_gap) in enumerate(self.segments):
            if t < start_t + duration or i == len(self.segments) - 1:
                s = smoothstep((t - start_t) / duration)
                return i, start_pos + s * (end_pos - start_pos), start_gap + s * (end_gap - start_gap)
            start_t, start_pos, start_gap = start_t + duration, end_pos, end_gap

    def inverse_kinematics(self, target, seed):
        q = seed.copy()
        limits = self.model.jnt_range[self.arm_jids]
        for _ in range(100):
            self.ik_data.qpos[self.arm_qpos] = q
            mujoco.mj_kinematics(self.model, self.ik_data)
            mujoco.mj_comPos(self.model, self.ik_data)
            position_error = target - self.ik_data.site_xpos[self.grip_id]
            rotation = self.ik_data.site_xmat[self.grip_id].reshape(3, 3)
            orientation_error = sum(np.cross(rotation[:, j], self.target_rotation[:, j]) for j in range(3)) * 0.5
            if np.linalg.norm(position_error) < 2e-6 and np.linalg.norm(orientation_error) < 1e-5:
                self.max_ik_position_error = max(self.max_ik_position_error, float(np.linalg.norm(position_error)))
                return q
            mujoco.mj_jacSite(self.model, self.ik_data, self.jacp, self.jacr, self.grip_id)
            jac = np.vstack([self.jacp[:, self.arm_dofs], 0.35 * self.jacr[:, self.arm_dofs]])
            error = np.r_[position_error, 0.35 * orientation_error]
            delta = jac.T @ np.linalg.solve(jac @ jac.T + 1e-5 * np.eye(6), error)
            delta *= min(1.0, 0.08 / max(1e-12, np.max(np.abs(delta))))
            q = np.clip(q + delta, limits[:, 0] + 0.001, limits[:, 1] - 0.001)
        raise RuntimeError(f"Panda 逆运动学未收敛，目标 {target}，位置误差 {np.linalg.norm(position_error):.6f} m")

    def touching_pads(self):
        # 多个指垫几何体只算一根手指，避免把单侧多接触误判为双指抓取。
        touching = set()
        for contact in self.data.contact:
            other = contact.geom2 if contact.geom1 == self.ball_geom else contact.geom1 if contact.geom2 == self.ball_geom else -1
            if other >= 0 and self.model.geom_bodyid[other] in self.finger_bodies:
                touching.add(int(self.model.geom_bodyid[other]))
        return touching

    def step(self, announce=True):
        stage, _, opening = self.command_at(self.data.time)
        if stage != self.last_stage:
            if announce:
                print(f"[{self.data.time:5.1f} s] {self.segments[stage][0]}", flush=True)
            self.last_stage = stage
        index = min(int(self.data.time / self.reference_dt), len(self.references) - 2)
        fraction = np.clip((self.data.time - self.reference_times[index]) / self.reference_dt, 0, 1)
        self.data.ctrl[self.arm_act] = (1 - fraction) * self.references[index] + fraction * self.references[index + 1]
        self.data.ctrl[self.gripper_act] = opening
        # 不移动球的 qpos，不给球施加外力，不添加球与夹爪的焊接约束。
        mujoco.mj_step(self.model, self.data)
        mujoco.mj_forward(self.model, self.data)
        if not np.isfinite(self.data.qpos).all() or not np.isfinite(self.data.qvel).all() or np.any(self.data.warning.number):
            raise RuntimeError("MuJoCo 状态或数值警告异常。")
        self.max_ball_height = max(self.max_ball_height, float(self.data.xpos[self.ball_id, 2]))
        both = len(self.touching_pads()) == 2
        self.bilateral_steps += int(both)
        if stage == 5:
            self.transfer_steps += 1
            self.transfer_held_steps += int(both and self.data.xpos[self.ball_id, 2] > self.pick[2] + 0.12)

    def record(self):
        super().record()
        self.records[-1]["finger_gap"] = float(sum(self.data.qpos[self.finger_qpos]) + 0.003)

    def summary(self):
        report = super().summary()
        report.update(robot="Franka Emika Panda (MuJoCo Menagerie)", source_scene=str(self.scene_path),
                      arm_joints=7, actuators=int(self.model.nu), gravity_compensation="ideal, robot bodies only",
                      max_ik_position_error_m=self.max_ik_position_error)
        return report


def main():
    parser = argparse.ArgumentParser(description="PyCharm 可直接运行的 Franka Panda 抓球仿真")
    parser.add_argument("--model", type=Path, default=MODEL_PATH, help="原 Menagerie scene.xml 路径")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--gif", action="store_true")
    parser.add_argument("--plot", action="store_true")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    if not math.isfinite(args.speed) or not 0.1 <= args.speed <= 4:
        parser.error("--speed 必须在 0.1～4 之间")
    sim = PandaGrasp(args.model)
    print(f"已加载 Panda：7 个机械臂关节，{sim.model.nu} 个执行器（最后一个控制夹爪）。")
    if args.headless or args.gif:
        run_headless(sim, args.output_dir.resolve(), args.gif)
    else:
        viewer = CompatibilityViewer(sim, args.speed)
        viewer.root.title("Franka Panda 抓球 — PyCharm 兼容显示")
        viewer.camera.lookat[:] = [0.30, 0.0, 0.35]
        viewer.camera.distance = 1.8
        viewer.camera.azimuth, viewer.camera.elevation = 135, -25
        viewer.run()
    report = save_results(sim, args.output_dir.resolve(), args.plot)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"结果保存到：{args.output_dir.resolve()}")
    return 0 if report["success"] or not report["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
