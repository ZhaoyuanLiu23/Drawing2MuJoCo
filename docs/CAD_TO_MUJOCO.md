# STL + parsed.json → MuJoCo

这是独立于 Drawing2CAD 识别器和 Panda 抓球控制器的管线。输入已生成的二进制 STL 和对应 `parsed.json`，输出自由刚体 MJCF、带桌面的测试场景和真实落体验证记录。没有新增识别、抓取、视觉或 VLM 功能。

## Windows / PyCharm

使用安装了 MuJoCo 的 Python，**不要使用 `.venv-drawing2cad`**。现有环境已具备依赖时无需安装；独立安装只需：

```cmd
python -m pip install -r requirements-cad2mujoco.txt
```

在项目根目录运行独立桌面测试：

```cmd
python cad2mujoco.py examples\bracket\result\model.stl examples\bracket\result\parsed.json --density-kg-m3 7800 --output outputs\cad2mujoco\bracket
```

加入本机 Menagerie Panda 场景（修改模型目录即可）：

```cmd
python cad2mujoco.py examples\bracket\result\model.stl examples\bracket\result\parsed.json --density-kg-m3 7800 --panda-scene "C:\Users\86155\Desktop\mujoco_menagerie\franka_emika_panda\scene.xml" --output outputs\cad2mujoco\panda_bracket
```

`7800 kg/m³` **只是此命令显式提供的均匀密度测试值**，不表示系统识别了材料。也可用 `--mass-kg 0.08` 提供实测总质量，两种输入必须二选一，且仍假设质量均匀分布。不默认物性，不用碰撞凸包计算质量。

PyCharm：脚本选 `cad2mujoco.py`；解释器选原有 MuJoCo 环境；工作目录选项目根目录；Parameters 填上述输入和选项。`--help` 查看单位、原坐标上轴、桌面位置、落下间隙和时长参数。

## 输出与加载

```text
result/
├── part.xml             # 独立零件；自身没有桌面、机器人或控制器
├── meshes/visual.stl    # 转成米并移到质心原点的原表面网格
├── scene.xml            # 新建的桌面/Panda 落体场景
├── scene_assets/        # 场景引用资产的独立副本，可整体移动目录
├── manifest.json        # 来源哈希、单位/坐标、物性、碰撞近似、验证状态
├── source_parsed.json   # 原 JSON 逐字节副本，保留公差/推断/冲突
└── drop_test.json       # 接触、速度、穿入量、轨迹和通过/失败结果
```

输出目录必须为空或带有本工具标记。重复运行只清理本工具的固定结果文件；异常时移除 `part.xml` / `scene.xml`，写入错误状态，避免把上次成功模型误认成这次结果。资产目录可能留有上次的未引用副本，只有当前 XML 中的引用有效。

ASCII 路径可以直接用 MuJoCo 的 `MjModel.from_xml_path()`。Windows 中文路径建议使用提供的 VFS 加载器，它通过 Python 读取 XML、include 和资产：

```python
from cad_mujoco.mjcf import load_spec
from cad_mujoco.validation import initialize

model = load_spec("outputs/cad2mujoco/panda_bracket/scene.xml").compile()
data = initialize(model)  # 有 home key 时使用其原始关节位置和控制值
```

将零件加入自己的现有场景：

```python
from cad_mujoco.mjcf import load_spec, attach_part, save_spec

scene = load_spec("path/to/panda/scene.xml")
attach_part(scene, "outputs/cad2mujoco/bracket/part.xml",
            position_m=[0.5, 0.0, 0.3], prefix="cad_")
save_spec(scene, "path/to/new_scene.xml")
```

`position_m` 是世界坐标下的**质心**位置。通过 `MjSpec.attach` 合并资产/默认类/关节，并补齐已有 keyframe 的新 freejoint 状态；原有关节和控制值不变。名称冲突直接报错。仅调用 `attach_part` 不会添加桌面或改变求解器，接触配置由调用方负责。`save_spec` 的目标父目录须存在。

自动测试场景使用现有 home key 的常量控制值，没有运行抓球控制器，也未更改 Panda 的关节、执行器、质量和惯量。源模型文件不写回。未提供 Panda 场景时生成独立桌面测试；测试场景会复制机器人资产到输出目录，第三方资产不会加入项目源码。

## MJCF 结构

```xml
<mujoco>
  <asset>
    <mesh name="part_mesh" file="meshes/visual.stl" inertia="exact"/>
  </asset>
  <worldbody>
    <body name="part">
      <freejoint name="free"/>
      <inertial pos="0 0 0" mass="..." fullinertia="Ixx Iyy Izz Ixy Ixz Iyz"/>
      <geom name="visual" type="mesh" mesh="part_mesh"
            contype="0" conaffinity="0" density="0" group="2"/>
      <geom name="collision" type="mesh" mesh="part_mesh"
            contype="1" conaffinity="1" density="0" group="3"/>
    </body>
  </worldbody>
</mujoco>
```

两个 geom 共用网格资产，但接触与显示分离；碰撞 geom 默认在隐藏的 group 3。两者 `density=0` 避免重复计质量。body 有显式惯量、一个 freejoint（7 个 qpos、6 个速度自由度），不使用 weld 或外力维持位置。场景序列化后 MuJoCo 会把 `fullinertia` 等价转换成主惯量和惯性坐标四元数。

## 单位、坐标与物性来源

- STL 无单位。优先读取 `units.cad` 与 `recipe.unit`；两者冲突则失败，缺失时要求 `--stl-unit`。原图标了英寸不代表 STL 为英寸：现有 Drawing2CAD 统一导出毫米。
- 转换支持 mm/cm/m/in → m；`--stl-unit` 不得覆盖已声明的不同单位。匹配 JSON 中的导出包围盒和体积，并记录输入哈希；这是几何一致性检查，不是输入文件配对的完整语义证明。
- Drawing2CAD 的既有约定是右手坐标、XY 草图、+Z 拉伸；本工具默认使用这个接口约定，并明确记录来源。`--source-up-axis x/y/z` 可显式旋转原坐标上轴到仿真的 +Z。
- 使用 `body_m = R @ (stl_coordinates * scale - source_com_m)` 把显示网格转到米、旋转并移至质心。质心偏移和旋转矩阵都保留在 manifest，不改变源 STL。
- 对原始封闭表面做带符号四面体积分，得到体积、质心和完整惯量张量；孔、槽和沉孔的空腔会扣除。用用户密度求质量，或用用户总质量反算均匀密度。惯量通过平行轴和坐标旋转表达在质心处，单位 kg·m²。
- 先验证网格拓扑、方向、有限数值、正体积和正定惯量，不静默修补破洞或反转法线。JSON 原有 `nominal/min/max/inferred/confidence/evidence` 和 B3 冲突只作为上游证据保留，不重新解析或修正。

## 碰撞与落体测试

使用 MuJoCo 对 mesh 的**单凸包碰撞**，保持原网格作为 visual。此策略不依赖零件名称、轮廓类型、孔槽数量、尺寸或文件名。孔槽/内凹空间被凸包填平：显示和质量包含真实开口，但接触没有这些开口。不能据此验证插孔、装配或凹部抓取。[MuJoCo 碰撞说明](https://mujoco.readthedocs.io/en/3.3.7/computation/index.html#collision-detection)

新测试场景的默认桌面中心/顶面是 `(0.5, 0, 0.13) m`，最小半宽/半长 `0.25 m`，并按零件范围自动扩大；默认从桌面上方 `0.15 m` 释放。它们是可见的环境参数，不是从零件图纸推断的尺寸。

测试场景使用 implicitfast、椭圆摩擦锥、100 次迭代、MPR + multiCCD。MuJoCo 3.3.7 中 native mesh/box CCD 在近共面薄网格上出现了接触抖动，因此统一选择 MPR，不对具体零件分支。时间步不大于 `0.00025 s`，并根据最小包围盒尺寸和预计冲击速度缩小；接触时间常数为步长的 8 倍。需要小于 1 微秒步长时明确拒绝，避免不受控的长运行。独立 `part.xml` 不强制宿主场景使用这些求解器设置。

默认真实仿真 5 s，持续施加重力；初始化之后不改零件 qpos/qvel，不添加力或焊接。验证包括：初始悬空且无穿插、确实下落、与桌面接触、未碰其他物体、无数值警告，最后 0.5 s 接触比例 ≥95%、最大线速度 <1 mm/s、最大角速度 <0.01 rad/s、高度波动 <0.1 mm。静置穿入容限为 `min(1 mm, 厚度包围盒的10%)`，瞬态冲击穿入容限为 `min(2 mm, 厚度包围盒的50%)`；报告记录实测值，不把软接触宣称为零穿透。

## 验证与边界

```cmd
set "CAD_MUJOCO_PANDA_SCENE=C:\Users\86155\Desktop\mujoco_menagerie\franka_emika_panda\scene.xml"
python -m unittest discover -s tests\cad2mujoco -v
.venv-drawing2cad\Scripts\python.exe -m pytest tests\drawing2cad -q
python -m unittest discover -s tests -p "test_*.py" -v
```

新增测试覆盖解析盒体惯量、离轴惯量、空腔积分、尺寸/坐标/单位变更、缺失物性/单位、损坏网格、错误输入配对、失败清理、场景 keyframe 保留、中文路径与移动目录，以及 B1/B2/B3/工业槽板的实际落体。真实 Panda 集成测试由上述环境变量指定，未设置时标记 skip，不能算作验证通过。

仍有以下假设：单个连通、方向一致的封闭表面；均匀密度和刚体；STL 三角化误差可接受；未检测自相交，也不支持多个不连通壳层/密闭内腔。只支持标准 MJCF file 资产和 include，不保证自定义插件或特殊外部资源可打包。默认平放，从无初速度状态释放；未验证任意姿态、极端质量/尺度、任意宿主求解器配置。碰撞凸包、摩擦及接触参数都是明确的近似或测试配置，未进行材料辨识。
