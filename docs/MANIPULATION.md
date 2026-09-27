# CAD 刚体的 Panda 外缘抓取

入口 `manipulate_cad.py`，实现位于独立 `manipulation/` 包。输入是 CAD2MuJoCo 输出的、已包含 Panda 的 `scene.xml`。不导入原抓球控制器，不修改 Drawing2CAD、CAD2MuJoCo 或原抓球代码。

流程：**等待物体落稳 → ground-truth pose → 网格外缘候选 → pre-grasp/grasp → 位姿 IK 与路径检查 → approach/descend → close → lift → hold → 成功判定**。不包含视觉、VLM、RL、装配、释放或重新抓取。

## Windows / PyCharm 运行

使用现有 MuJoCo 解释器。依赖与 CAD2MuJoCo 相同，无需安装新的库。新环境可执行 `python -m pip install -r requirements-manipulation.txt`。

项目根目录 CMD：

```cmd
python manipulate_cad.py "outputs\cad2mujoco\panda_bracket\scene.xml" --output "outputs\manipulation\panda_cad"
```

如果尚未生成含 Panda 的场景，先运行上一阶段的入口。路径与质量/密度由使用者指定：

```cmd
python cad2mujoco.py "examples\bracket\result\model.stl" "examples\bracket\result\parsed.json" --density-kg-m3 7800 --panda-scene "C:\Users\86155\Desktop\mujoco_menagerie\franka_emika_panda\scene.xml" --output "outputs\cad2mujoco\panda_bracket"
```

这里的密度是显式演示参数，不是图纸识别的材料。转换与 manipulation 都支持中文路径。PyCharm 使用同一 MuJoCo 解释器，脚本选择 `manipulate_cad.py`，工作目录为项目根目录，Parameters 填 scene 路径与 `--output`。

默认物体名 `cad_part`、桌面 geom 名 `cad_test_table` 是 CAD2MuJoCo 接口名，可用 `--object` / `--table` 替换；不检查零件名、文件名或图纸型号。

输出：

```text
outputs/manipulation/panda_cad/
├── result.json       # 成功判定、失败阶段、姿态、IK 误差与控制假设
├── candidates.json   # 候选宽度、局部接触位置与世界 pre-grasp/grasp 位姿
├── trace.json        # 实时物体/TCP 位姿、双指接触力、相对位姿和阶段
└── trajectory.npz    # 时间与实际 qpos，用于复核或显示重放
```

成功退出码为 0，失败为 1。无可行候选、IK/碰撞错误和抓取判定失败都有记录。输入错误会写入失败结果，并清理旧轨迹，避免误读上次成功输出。输出不能覆盖输入场景目录。每次抓取应新建一个 `Manipulator`，不复用上次成功保持段。

Python 接口：

```python
from manipulation.pipeline import Manipulator, Config

sim = Manipulator("outputs/cad2mujoco/panda_bracket/scene.xml", config=Config())
current_pose = sim.observe()  # position: m; quaternion in Pose.json(): wxyz
report = sim.run()
```

## 实时位姿与 grasp 规则

- 直接读取 `MjData.xpos` / `xquat`，位置和方向均来自运行中的物体。观测使用世界坐标、米、wxyz 四元数。
- 从**已编译模型的 visual mesh**读取三角形，应用 MuJoCo mesh/geom 的局部变换，恢复物体坐标下的实际尺寸；不读取当前样例的答案，也不只依赖一个手写包围盒。
- 第一版要求近水平薄板，最短局部尺寸对应板厚。最薄方向与世界 +Z 的点积必须 ≥0.98，板厚小于两平面尺寸较小值的一半。不满足时明确拒绝。
- 沿两个板面局部轴尝试对向外侧面。候选宽度等于对应网格跨度，必须小于夹爪实测开口减 6 mm 的进场余量。切向边长须容纳指垫。
- 在每对边的中部与长度的 ±22% 偏移处采样，并尝试两个夹爪朝向。两个预期接触点都必须落在真实外侧面的三角形上；只有 AABB 而没有外侧面支撑不能通过。此检查不是完整接触斑或力闭合证明。
- 指尖 TCP、指垫缩进和宽度从 Panda 原碰撞网格/box 指垫计算，最大开口来自 finger joint limits。抓取高度按板厚和指尖到桌面的间隙计算；过薄板件明确失败，不降低到穿桌位置。
- 将局部候选乘以**当前**物体旋转并加上平移得到世界 grasp pose；pre-grasp 沿进场反方向偏移，默认 0.10 m。approach 和 descend 的每个控制周期重新读取物体 pose。进入闭合后固定夹持目标，抬升沿世界 +Z 0.12 m，避免用物体运动反向拖动机器人目标。
- 默认优先边中点、较窄开口；逐个排除不可达或路径碰撞候选。失败不会切换到零件专用规则。

本版使用 CAD2MuJoCo 的单凸包碰撞。外侧面显示与接触基本对应，但孔槽内部没有可抓取的碰撞开口，因此不生成内孔/内槽抓取。

## IK、控制与接触

Panda 七关节采用 6D 阻尼最小二乘 IK：位置误差加 SO(3) 四元数旋转误差，雅可比来自 `mj_jacSite`；不会将 180° 姿态差误认为零误差。[MuJoCo API](https://mujoco.readthedocs.io/en/3.3.7/APIreference/APIfunctions.html#mj-jacsite)

旋转项权重 0.2，阻尼项 1e-5，单次最大关节更新 0.1 rad；限制关节范围，位置误差门限 0.15 mm，方向误差门限 0.0015 rad，最多 180 次迭代。以上都是控制器设置，不是零件尺寸。上一解作为后续初值。

用独立 `MjData` 做 IK 与候选碰撞检查，不写实时物体状态。approach/descend 每段采样 14 个路径点；执行时以 0.01 s 控制周期和五次平滑曲线更新目标，物理仿真继续使用场景步长。关闭阶段允许双指接触目标，其他机器人穿入超过 0.25 mm 会终止。它不是全局避障规划器，也没有多分支 IK 搜索。

**新控制器对内存场景有明确的独立配置：**

- 保留 Panda 的连杆、原指垫形状、质量、惯量和关节/执行器力限。
- 夹爪位置刚度从模型原始 100 N/m 调整为默认 1000 N/m，阻尼 20 N·s/m；仍通过原 tendon actuator 驱动两个手指，原 ±100 N 力限保留。该参数写入结果，不是对上游源 XML 的修改，也不是实机已标定设置。
- 仅对七个机械臂 DOF 施加 `qfrc_bias` 前馈，使用原位置伺服执行 IK 目标。物体无额外外力、无重力补偿、无焊接。执行过程中不重写物体 qpos/qvel。
- 接触摩擦和凸包策略沿用输入 CAD2MuJoCo scene；不根据零件型号调整摩擦系数。

## 成功判定

默认抬升后保持 **2 s**；统计整个保持段，不只挑选最后一个成功帧。以下条件必须同时成立：

| 条件 | 默认门限 |
| --- | --- |
| 执行完整且没有规划/碰撞/数值错误 | 必须 |
| 两个不同手指持续接触目标 | 保持段比例 ≥95%；每侧法向力 >0.01 N |
| 物体最低点高于桌面 | 整段至少 60 mm |
| 物体与桌面接触 | 保持段为零 |
| 物体线速度 | 最大值 ≤15 mm/s |
| 物体相对 TCP 的位置变化 | 最大值 ≤3 mm |
| 物体相对 TCP 的方向变化 | 最大值 ≤0.05 rad |

实际抬升距离、指垫法向力、双指接触比例、滑移、角度变化、IK 误差和失败阶段均写入 JSON。线速度/接触只能证明此次配置下的模拟保持，不能保证真实机械手抓取。

## 本轮测试与失败边界

```cmd
set "CAD_MUJOCO_PANDA_SCENE=C:\Users\86155\Desktop\mujoco_menagerie\franka_emika_panda\scene.xml"
python -m unittest discover -s tests\manipulation -v
python -m unittest discover -s tests\cad2mujoco -v
.venv-drawing2cad\Scripts\python.exe -m pytest tests\drawing2cad -q
python -m unittest discover -s tests -p "test_*.py" -v
```

真实 Panda 测试要求设置模型路径；未设置时明确 skip，不代表通过。本轮模型已安装，所有物理测试实际执行。合成板件通过现有 CAD2MuJoCo 转换生成，再进行 manipulation；尺寸只出现在测试夹具中。

实际成功案例包括：已有 CAD 槽板、平移 `(-65,+85,0) mm`、绕 Z 轴旋转 57°、`84×36×10 mm` 板的平移及 -28° 旋转、`128×54×12 mm` 板的平移及 90° 旋转。均保持约 120 mm 离桌高度和 2 s 双指夹持。另有夹爪始终张开的物理反例，正确返回失败，目标仍留在桌面。

其他拒绝测试覆盖：夹爪开口不足、板件太薄、倾斜板、仅 AABB 支撑而无真实对向侧面、不可达 IK 和输入文件缺失。当前不支持任意曲面/侧向抓取、立放板件、运动传送带、滑动物体追踪保证、未知摩擦、复杂障碍物、超出机器人载荷/行程的物体，失败后不自动重抓。没有验证真实硬件或全局抓取成功率。
