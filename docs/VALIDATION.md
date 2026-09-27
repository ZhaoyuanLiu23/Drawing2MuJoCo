# 验证记录

v0.4 本地验证基线来自 Windows：

- Drawing2CAD：112 项 pytest 通过，包含圆形轮廓、B2/B3 原图及变体、孔槽数量/位置、投影视图、普通通孔、公差和 slot-only 推断。
- 原始圆环 PDF：6 项验收通过，覆盖原图、匿名文件名、PNG、JPG、改尺寸和严格名义值策略。
- MuJoCo：4 项 unittest 通过，验证简化机械臂/Panda 的抓取释放，以及张开夹爪时不能搬运球体。

机器可读记录见 [validation/](validation/)。案例和归档报告中的个人绝对路径已转换为相对路径或 `<external>` 占位符；原始本机输出仍保留在未提交的 `outputs/`。

## 复现命令

```cmd
.venv-drawing2cad\Scripts\python.exe -m pytest tests\drawing2cad -q
.venv-drawing2cad\Scripts\python.exe scripts\validate_drawing2cad_sample.py "examples\washer\input.pdf" --output "outputs\acceptance"
python -m unittest discover -s tests -p "test_*.py" -v
```

Panda unittest 目前仍依赖开发机默认模型路径；未安装时明确 skip，不代表通过。其他机器先用 `panda_grasp.py --model "实际 scene.xml 路径" --headless` 检查仿真与输出结果。该 CLI 检查不替代两个 Panda unittest；测试夹具的跨机器模型配置仍待改进。

## 验收重点

尺寸改变后，参数、CAD 体积与包围盒相应改变。STEP 重导入保持有效实体；STL 检查封闭网格、有限坐标、体积与范围。缺失可靠信息或未解决的位置歧义阻止导出；失败不得残留旧模型。

B3 保留原始尺寸链冲突；未标槽推断不能覆盖已有冲突或被拒绝标注。JSON、配方和预览使用相同参数来源。

测试只覆盖已列范围，不能证明任意工程图都可转换。置信度未统计校准，目前没有跨数据集准确率或任意零件泛化率。

## 独立 CAD→MuJoCo 本轮验证

机器可读记录：[cad_mujoco_verification.json](validation/cad_mujoco_verification.json)。

| 验证 | 结果 |
| --- | --- |
| 新增 bridge unittest（含真实 Panda 集成） | 13 通过，0 跳过 |
| 原 Drawing2CAD pytest，含 B1/B2/B3 与变体 | 112 通过，0 失败 |
| 原 B1 PDF 验收脚本 | 6 通过 |
| 原简化机械臂/Panda 抓球 unittest | 4 通过，0 跳过 |
| 移动整个输出目录后，原生 MuJoCo XML 加载 | part nq=7；Panda scene nq=16、nu=8 |

本轮核对了 43 个已有识别、抓球、模型及相关测试文件，Git 内容与修改前一致。Drawing2CAD 仍有 31 条既有依赖弃用警告，无测试失败。

独立转换实际运行了 B1 圆环、B2 单圆角板、B3 多孔槽沉孔板以及工业 PDF 槽板的已导出 STL，并验证其落体；这是 STL→MuJoCo 测试，不是新一轮图片盲测。合成测试另外验证改尺寸、质量/密度、米制输入、上轴转换和离轴惯量。

Panda 槽板演示从桌面上方 0.15 m 释放，仿真 5 s，末尾 0.5 s 持续接触比例 100%，最大线速度约 0.045 mm/s；撞击瞬间最大穿入约 0.974 mm，在已声明的 2 mm 瞬态软接触容限内。密度 7800 kg/m³ 是显式测试参数。模型并未抓取该零件，孔槽也不是可用于插入的碰撞开口。

运行与几何/物性假设见 [CAD→MuJoCo](CAD_TO_MUJOCO.md)。原有测试继续使用各自环境，不把 CadQuery 依赖安装到 MuJoCo 环境。

## 独立 manipulation 本轮验证

机器可读记录：[manipulation_verification.json](validation/manipulation_verification.json)，含每个物理案例的候选、成功判定、失败原因和稳定性指标。

| 验证 | 结果 |
| --- | --- |
| 新 manipulation 测试 | 13 通过，0 跳过 |
| CAD2MuJoCo 全部回归 | 13 通过，0 跳过 |
| Drawing2CAD 全部回归，含 B1/B2/B3 | 112 通过，0 跳过 |
| 原简化机械臂/Panda 抓球 | 4 通过，0 跳过 |
| B1 原图、改名、PNG、JPG、改尺寸及严格名义尺寸验收 | 6 通过 |

5 个真实动力学正例：已有 CAD 槽板、该板平移、该板绕 Z 轴转 57°、84×36×10 mm 板同时平移和 -28° 旋转、128×54×12 mm 板同时平移和 90° 旋转。均抬离桌面约 0.12 m，保持 2 s，双指接触比例 100%。

张开夹爪反例运行完整 approach/lift 后仍返回失败，目标留在桌面；另验证超开口、过薄、倾斜、缺少真实侧面支撑、不可达 IK 和输入错误。失败不是被替换成样例成功答案。

对本轮开始时保存的 49 个已有识别、转换、抓球、模型和测试文件逐个做 SHA-256 比较，全部未变。数据记录在验证 JSON 的 `unchanged_existing_files`。新控制器仅在内存中增加 TCP site、设置夹爪伺服和七关节偏置力前馈，详细范围见 [Manipulation 说明](MANIPULATION.md)。

## RGB-D perception 本轮验证

记录：[perception_verification.json](validation/perception_verification.json)；运行与限制：[PERCEPTION.md](PERCEPTION.md)。

| 验证 | 结果 |
| --- | --- |
| 新 perception 测试（实际渲染、6D 位姿、输入隔离、失败及物理抓取） | 14 通过，0 跳过 |
| 原 manipulation / CAD2MuJoCo / 抓球 | 13 / 13 / 4 通过，0 跳过 |
| Drawing2CAD，含 B1/B2/B3 | 112 通过，31 条既有警告 |
| B1 原图及变体验收 | 6 通过 |
| vision CLI 实例、固定相机场景重新加载 | 通过 |

5 个视觉抓取正例全部完成 lift/hold；位置误差最大 0.268 mm，对称等价角度误差最大 0.076°，保持约 2 s，离桌至少 119.68 mm。104° 对称板的原始角度误差为 179.924°，JSON 保留原始误差和姿态歧义，未用真值选择抓取朝向。张开夹爪反例判失败；目标消失、坏深度、部分遮挡拒绝，未回退真值。真值观察函数禁用和 live 目标状态污染测试均通过。

两个新尺寸夹具的原 CAD2MuJoCo 落体验证因速度峰值约 2.39/2.44 mm/s 超过 1 mm/s 而失败，记录保留 `cad_drop_success=false`；视觉抓取成功不覆盖这两个落体失败。未修改物理参数或上游阈值，旧 CAD2MuJoCo 回归保持通过。

本轮开始时保存的 37 个 Drawing2CAD、CAD2MuJoCo、manipulation 核心及入口源码 SHA-256 全部未变。仅 CLI 增加 `--pose-source` 路由，核心复用通过独立 subclass/adapter 完成。当前是静态目标的单帧视觉定位，不是全程视觉伺服。

## 独立视觉 Pick-and-Place 本轮验证

记录：[pick_place_verification.json](validation/pick_place_verification.json)；运行与限制：[PICK_AND_PLACE.md](PICK_AND_PLACE.md)。

| 验证 | 结果 |
| --- | --- |
| 新任务测试 | 18 通过，0 跳过 |
| 原 perception / manipulation / CAD2MuJoCo / 抓球 | 14 / 13 / 13 / 4 通过，0 跳过 |
| Drawing2CAD，含 B1/B2/B3 | 112 通过，31 条既有警告 |
| B1 原图及变体验收 | 6 通过 |
| task CLI、释放后四次相机复核 | 通过 |
| 最后补充的未知目标输出 JSON 错误路径 | 定向重测通过 |

正常任务成功 5/5，覆盖不同初始 XY、yaw、目标 XY 和两种合成板尺寸。离线真实落点中心误差最大 0.108 mm；释放后视觉感知位置误差最大 0.169 mm；4 帧相机复核覆盖 1.2 s，全部位于目标区并接近桌面。该成功率仅针对这 5 个有限的理想仿真案例。

三个实际运行的反例（起始无分割目标、释放后分割丢失、命令搬往错误区域）全部判失败。越界目标、未知物体、过小区域、陈旧/重复帧、移动/悬空/未释放等输入也明确拒绝。初期低分辨率复核失败、全路径 IK 失败保留在报告中；新任务提高相机分辨率并延续抓取 IK seed、检查完整路径，没有修改原 perception/IK 门限。

所有物理任务累计监视 700,400 次 `mj_step`：步骤间没有额外改写物体 qpos/qvel，没有物体外力和 weld。真值 observer 与接触成功函数禁用期间任务仍完成。真值只在任务判定固定之后解码用于离线误差评估，不改变视觉判定。60 个已有核心源码、入口与测试文件 SHA-256 全部不变。

## 独立 Embodied Agent 本轮验证

记录：[embodied_agent_verification.json](validation/embodied_agent_verification.json)；API、schema、状态流转与运行：[EMBODIED_AGENT.md](EMBODIED_AGENT.md)。

| 验证 | 结果 |
| --- | --- |
| 新 Agent / skills 测试 | 18 通过，0 跳过：11 项契约 + 7 项实际 pipeline 测试 |
| 原 pick_place / perception / manipulation / CAD2MuJoCo / 抓球 | 18 / 14 / 13 / 13 / 4 通过，0 跳过 |
| 原 Drawing2CAD，含 B1/B2/B3 | 112 通过，31 条既有警告 |
| B1 原图与变体验收 | 6 通过 |
| TaskPlan JSON Schema | Draft 2020-12 检查通过，8 个输入/成功/失败记录验证通过 |
| 完整 Agent CLI | 五步全部成功；释放后四帧视觉复核通过 |

合计 192 项单元/物理/pytest 测试通过（原有 174 + 新增 18），另有 B1 的 6 项验收通过。两种目标配置的正例均成功；这只是 2/2 个指定理想仿真案例，不是一般场景成功率。CLI 的视觉落点中心距目标约 0.165 mm，复核帧跨度 1.2 s；该值是视觉测量到目标的距离，不是真值位姿估计误差。

缺失目标分割在 locate 失败；实际超开口板件在 pick 候选预检失败；注入释放执行器故障在 place 失败；实际机器人搬往错误区域后在 verify 失败。均保留原始阶段与错误，后续步骤 skipped。另检查异常/错误返回类型、步骤顺序、目标不一致、取消暂停会话和不能用离线成功覆盖视觉失败。

同一任务只实例化一次原 pipeline。测试监视阶段边界：observe/locate 后没有机械臂 motion；pick 后仅完成 approach/grasp/lift；place 后尚未采集 verify 帧。下一项 skill 调用前不执行后续阶段。真实测试期间禁用原真值 observer；Agent 输出不含离线真值或仿真状态。原有离线评估仍在底层最终判定固定后执行，适配层不向 Agent 传递这些数据。

本轮开始时记录的 68 个既有核心源码、入口和测试文件 SHA-256 全部未变。没有新增控制、感知或 grasp 判定算法；pick 返回阶段完成，最终视觉复核才决定整个任务是否成功。

## 独立 Language Planner 本轮验证

记录：[language_planner_verification.json](validation/language_planner_verification.json)；API、provider、prompt 和边界：[LANGUAGE_PLANNER.md](LANGUAGE_PLANNER.md)。

| 验证 | 结果 |
| --- | --- |
| 新 language_planner 离线测试 | 24 通过，0 跳过 |
| 原 embodied_agent / pick_place / perception / manipulation / CAD2MuJoCo / 抓球 | 18 / 18 / 14 / 13 / 13 / 4 通过，0 跳过 |
| 原 Drawing2CAD，含 B1/B2/B3 | 112 通过，31 条既有警告 |
| B1 原图与变体验收 | 6 通过 |
| 实际规划 CLI | 两种目标生成不同 pending 计划；未知目标失败且无计划文件 |

合计 216 项单元/物理/pytest 测试通过（原有 192 + 新增 24），另有 B1 的 6 项验收通过。所要求的八类用例均验证：正常自然语言放置、不同目标表达、不存在的目标、不存在的物体、非法 skill、malformed response、schema failure、LLM 不可用。反例均返回结构化失败，不产生可交接 TaskPlan。

额外验证拒绝控制参数、模型自造坐标、重复 JSON 键和非有限数值；现有 schema 可容纳的历史运行记录仍由 canonical pending 契约拒绝。能力目录每次获取，交接重新校验；目录更新、结果篡改、provider 修改目录副本不会绕过允许列表。CLI 失败重跑清除自己的旧计划；规划测试禁用 Agent.execute，检查 package 无机器人执行/状态访问。规划模块未运行机器人，物理运行仅来自原有回归测试。

LLM callable adapter 已验证 messages、schema、目录和指令传递，以及超时/未配置时失败且不重试、不 fallback。未调用外部 LLM 服务，不声称真实模型准确率。离线版只支持文档所列中英文完整放置句式，不能以目录/schema 校验替代自然语言理解正确性或运动可行性验证。

78 个既有源码、入口、测试及原 TaskPlan schema 文件的 SHA-256 全部未变。新增依赖仅为独立 requirements-language-planner.txt 中的 jsonschema；本机使用已有 4.25.1。
