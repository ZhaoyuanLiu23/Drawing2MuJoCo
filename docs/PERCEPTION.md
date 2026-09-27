# RGB-D → CAD 位姿 → Panda 抓取（第一阶段）

新增 `perception/`，入口仍为 `manipulate_cad.py`。默认 `ground_truth` 保持原行为；`vision` 用视觉适配层覆盖观察与记录接口，继承原候选生成、IK、approach → descend → close → lift → hold 和成功检查。没有修改 `drawing_cad/`、`cad_mujoco/`、`manipulation/` 或抓球源码。

## Windows / PyCharm

使用已有 MuJoCo Python 解释器，不使用 CadQuery 虚拟环境。当前机器所需依赖均已存在；其他机器执行：

```cmd
python -m pip install -r requirements-perception.txt
python manipulate_cad.py outputs\cad2mujoco\panda_bracket\scene.xml --pose-source vision --output outputs\perception\panda_cad
```

对其他输入，把首个参数换成 CAD2MuJoCo 导出的、包含 Panda 的 `scene.xml`，无需修改尺寸或位置。PyCharm 的 Script path 选 `manipulate_cad.py`，Parameters 填上述参数，Working directory 设为项目根目录。命令默认离屏运行，结果保存在输出目录，不弹出动画窗口。

原模式：

```cmd
python manipulate_cad.py outputs\cad2mujoco\panda_bracket\scene.xml --pose-source ground_truth --output outputs\manipulation\ground_truth
```

输出目录必须为空或具有相应 pipeline 的生成标记。不同模式请使用不同目录。感知失败返回非零退出码和明确的 `result.json`，不回退真值，也不会沿用上一轮的 pose 文件。

## 数据流及算法

```text
独立场景副本 + 固定标定相机
  → RGB / 米制轴向 depth / geom-id segmentation
  → 目标 mask → 世界坐标点云
  → RANSAC 主平面 → 外轮廓最小面积矩形
  → 已知 CAD 三角网格尺寸、厚度、坐标原点对齐
  → CAD 三角形投影 silhouette 验证多个朝向假设
  → estimated position + quaternion(wxyz)
  → 原外缘候选 / 原 IK / 原抓取状态机
```

`camera.py` 将相机安装在 worldbody，相对于固定桌面标定，不跟踪目标。默认 960×720、垂直 FOV 42°，相对桌面中心偏移 `(0, -0.38, 0.65)` m。这是传感器安装参数，不是目标位置。可在 Python API 中传入 `CameraConfig`。输出 `camera_scene.xml` 与相对资产，可独立加载检查相机。原输入 XML 不变。

相机坐标为 X 向右、Y 向上、观察方向 -Z；世界为 MuJoCo 世界坐标、米制。使用像素中心及 FOV 求焦距，将 MuJoCo Python Renderer 的米制轴向 depth 反投影，再由固定相机外参转换。坐标约定参照 [MuJoCo 3.3.7 相机说明](https://mujoco.readthedocs.io/en/3.3.7/programming/visualization.html#cameras)，深度返回语义也核对了本机 3.3.7 `renderer.py`。没有把深度当欧氏射线距离，也没有重复应用 OpenGL z-buffer 转换。

`registration.py` 只接收 Frame、已知 CAD 三角形和目标分割 ID，不接收 `MjData`、目标 body ID、目标位姿或真值 seed。RANSAC 法向恢复 roll/pitch，轮廓方向恢复 yaw，轮廓中心结合 CAD 已知厚度及其 body-local 原点恢复三维平移。网格由已有 MJCF visual mesh 只读还原，包括 MuJoCo 编译网格的坐标补偿。

这是针对当前外缘抓取域的几何配准器，**不是任意 CAD 的通用 ICP 或姿态网络**。RGB 被采集留证，目前配准使用 depth + segmentation。生产实现没有案例文件名、零件名、测试尺寸或测试位置分支。

## 真值隔离与观察时机

- 零件落稳后采集一次 RGB-D，approach/descend 使用该次估计。随后 lift 仍按原机器人 TCP 目标执行；本版没有持续视觉跟踪。
- `RobotStateView` 给原 IK 传入机器人关节编码器和估计目标位姿。IK 的 scratch world 不再复制目标 live qpos，因此规划碰撞检查也不会偷偷使用目标真值位置。
- 控制器不读目标 `xpos/xquat`。渲染器当然需要模拟器生成图像，但解析器仅能获得图像和标定。测试在整个视觉抓取期间将原 GT observer 替换为抛异常函数，并另行污染 live 目标 qpos/xpos/xquat 验证规划仍只接收缓存视觉估计。
- 原始仿真状态仅归档，执行结束后 `evaluation.py` 才解码目标位姿，计算误差和稳定保持指标；该结果不送回候选生成、IK 或控制。
- 原控制器的接触、速度及数值保护仍使用仿真遥测。这里隔离的是**抓取 pose 输入**，不宣称已实现完全只靠相机的硬件机器人控制。

## 输出与失败

```text
result/
├── camera_scene.xml + scene_assets/  # 固定相机场景副本
├── camera.json                       # 内外参、坐标约定、采集时刻
├── rgb.png
├── depth.npy                         # H×W，米
├── segmentation.npy                  # H×W×2，object-id / object-type
├── pointcloud.npz                    # world_points_m
├── estimated_pose.json               # position_m、quaternion_wxyz、候选及诊断
├── candidates.json
├── trace.json                        # 执行后添加真值评估标签
├── trajectory.npz                    # 仅供离线评估/回放
└── result.json                       # 成功判定、误差、来源、假设
```

明确拒绝分割像素不足、无效深度、目标超出画面、不可用主平面、观测外形和 CAD 范围不匹配、投影 silhouette 不匹配。门限属于通用几何/分辨率验收参数，不是统计校准置信度。部分遮挡可能被门限拒绝，但并不保证发现所有微小遮挡。

对称/近似对称薄板的正反面、半周朝向可能在当前视角不可区分。JSON 保存 `orientation_ambiguous` 和多个可行假设，并输出固定规则选择的代表姿态；这不等于已辨明唯一 CAD 坐标系。外缘抓取在这些等价外包络下仍可成立。离线同时报告原始角度误差和**经过 CAD 全部顶点验证**的对称等价角度误差；近似对称但未通过网格对称验证的零件不享受误差折减。此版对称验证限轴置换和轴翻转，不能穷举任意连续对称。

## 本机验证

命令：

```cmd
set CAD_MUJOCO_PANDA_SCENE=C:\Users\86155\Desktop\mujoco_menagerie\franka_emika_panda\scene.xml
set PERCEPTION_TEST_REPORT=outputs\perception_dev\tests.json
python -m unittest discover -s tests\perception -v
python -m unittest discover -s tests\manipulation -v
python -m unittest discover -s tests\cad2mujoco -v
python -m unittest discover -s tests -p "test_*.py" -v
.venv-drawing2cad\Scripts\python.exe -m pytest tests\drawing2cad -q
```

完整记录见 [perception_verification.json](validation/perception_verification.json)。新增 14 项测试；旧测试 142 项（112 Drawing2CAD、13 CAD2MuJoCo、13 manipulation、4 抓球）均保留，另复查 B1 的 6 项验收。缺少 Panda 环境变量时渲染/物理组会明确 skip，不能算通过。

五个完整视觉抓取正例：已有 CAD 槽板、XY 平移槽板、53° yaw 槽板、91×34×8 mm 板同时平移和 -31° yaw、123×52×11 mm 板同时平移和 104° yaw。后两个尺寸只在测试夹具中定义。所有正例抓取、抬升并稳定保持约 2 s，双指接触比例 100%，保持时离桌最小距离约 119.68–119.75 mm。

这些正例的位置误差约 0.005–0.268 mm，对称等价角度误差最大约 0.076°。104° 案例输出另一对称代表，**原始角度误差约 179.924°**，不能将其写成绝对姿态误差小于 1°。另用倾斜板检验 roll/pitch 恢复，位置误差约 0.002 mm、对称等价角误差约 0.0013°。这是理想 MuJoCo 深度下的有限案例结果，不能外推真实相机精度。

张开夹爪的反例正确返回抓取失败。丢失目标、坏深度、部分遮挡正确拒绝；没有真值 fallback。

额外发现：两个新板件在 CAD2MuJoCo 原落体验证中，末尾速度峰值约 2.39/2.44 mm/s，超过其 1 mm/s 阈值；其他落体检查通过。测试记录保留这两个 `cad_drop_success=false`，没有改上游参数或阈值。它们在原 manipulation 的独立 settle/hold 判据下完成视觉抓取；这不代表那两次严格落体验收已经通过。既有 CAD2MuJoCo 回归仍为 13/13。

## 仍有的假设

已知正确尺寸与 body-local 坐标的单个 mesh、固定且准确的相机标定、MuJoCo 提供的目标实例分割、足够可见的矩形板外轮廓和宽面、CAD 局部轴与板外缘对齐。主视宽面估计支持小角度倾斜，但当前原抓取控制器仍只接受近水平薄板。

无未知物体发现、多物体遮挡消解、噪声/缺失深度鲁棒性保证、移动目标视觉伺服、任意曲面或非矩形外轮廓配准、唯一对称姿态恢复、真实相机部署、VLM、RL。沿用 CAD2MuJoCo 凸包碰撞，孔槽不是可穿入的真实碰撞开口。
