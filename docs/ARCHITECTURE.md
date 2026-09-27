# 架构与证据流

`language_planner/` 作为独立上游，只消费 instruction 和应用提供的当前能力目录。`providers.py` 隔离离线/Mock/LLM callable；`validation.py` 严格解析模型输出并使用现有 TaskPlan schema；`planner.py` 校验对象/目标/技能及待执行状态，返回 PlanningResult。交接再次验证目录/schema 指纹，只返回 TaskPlan，不调用机器人。原 schema 和 `embodied_agent/` 保持不变。见 [Language Planner](LANGUAGE_PLANNER.md)。

`embodied_agent/` 新增确定性调度层：`models.py` 定义 TaskPlan/SkillResult；`skills.py` 限定五个技能及对象/目标契约；`backend.py` 在一次原 `PickPlaceTask.run()` 中设置暂停点；`agent.py` 只消费 skill 结果，`runner.py` 持久化状态。底层五个包不修改；结果白名单隔离离线真值和仿真状态。详见 [Robot Skills](EMBODIED_AGENT.md)。

最新任务层为 `place_cad.py` / `pick_place_task/`：只读复用 perception 与 manipulation，新增桌面 target_zone 场景副本、完整候选路径检查、放置变换、释放/撤离状态机与多帧视觉复核。`verification.py` 仅接收新视觉 Estimate 和标定/任务配置；先固定任务判定，再由 `evaluation.py` 解码归档真值做离线误差分析。既有四个核心包不修改，详见 [Pick-and-Place](PICK_AND_PLACE.md)。

`drawing2cad.py` 隔离解释器；`drawing_cad/pipeline.py` 编排输入、OCR/原生文字、几何、特征、尺寸、CAD 与预览。

| 阶段 | 模块 | 输出 |
| --- | --- | --- |
| 输入 | `ingest.py`, `preprocess.py`, `ocr.py` | 图像、PDF 线段/文字、预处理记录 |
| 几何检测 | `geometry.py`, `profile_geometry.py`, `pattern_geometry.py` | 像素空间轮廓、圆、槽、投影候选 |
| 特征候选 | `features.py`, `profile_features.py`, `pattern_features.py` | 支持的几何组合和尺寸需求 |
| 尺寸/约束 | `dimensions.py`, `leader_dimensions.py`, `feature_callouts.py`, `pattern_constraints.py` | 单位/公差、引线、尺寸链、冲突与位置 |
| 缺失槽推断 | `slot_inference.py` | 按比例估算与来源，不能覆盖被拒绝标注 |
| CAD | `cad.py`, `profile_cad.py`, `pattern_cad.py` | 参数门禁、实体、STEP/STL 与校验 |
| 预览 | `preview.py`, `preview_template.html` | 检查图与离线交互预览 |

候选不等于已解析实体；须经过尺寸/位置约束得到 resolved features。未解决的位置阻止导出。

`schemas/drawing2cad.schema.json` 描述证据契约。`nominal/min/max`、建模使用值、来源、推断和置信度分别保存；中值不回填成名义值，冲突不通过修改原图数字消失。

`pick_and_place.py` 管理简化机器人及兼容显示，`panda_grasp.py` 读取外部 Panda 场景，`export_video.py` 导出简化机械臂视频。这些入口的行为保持不变。

独立的 `cad2mujoco.py` / `cad_mujoco/` 读取 STL 和 `parsed.json`，不导入或修改 `drawing_cad`。`mesh.py` 验证封闭表面并计算质量积分；`metadata.py` 管理单位与输入一致性；`mjcf.py` 输出视觉网格/凸包接触/显式惯量/freejoint，并通过 MjSpec 拼接场景；`validation.py` 检查实际落体接触；`pipeline.py` 保存输入证据与验证记录。详见 [CAD→MuJoCo](CAD_TO_MUJOCO.md)。

生产代码不读取 `examples/` 的答案。案例仅用于复现与展示；`outputs/`、解释器、缓存和第三方可执行文件不提交。

`perception/` 是独立视觉适配层：`camera.py` 生成固定相机场景副本及标定 RGB-D；`registration.py` 仅从帧/已知网格注册位姿；`pipeline.py` 继承原 manipulation，用视觉观察覆盖 pose 输入，并向原 IK 注入机器人编码器和估计目标状态；`evaluation.py` 只在执行结束后读取归档状态做真值评估。CLI 新增 pose-source 路由，三个既有核心包保持不变。数据隔离和单次观察限制见 [Perception](PERCEPTION.md)。

`manipulate_cad.py` / `manipulation/` 是第二个独立消费方：读取 CAD2MuJoCo 场景，用 `geometry.py` 读取实际网格和实时位姿、生成外缘候选；`panda.py` 校准 Panda 指垫 TCP、做位姿 IK 和碰撞检查；`pipeline.py` 执行状态机和接触/抬升/保持判定。复用上游只读场景加载器，不修改上游识别、转换、质量/碰撞策略或原抓球控制器。新控制器的内存伺服配置单独记录，见 [Manipulation](MANIPULATION.md)。

`web/` 是本地工作台，本身不是算法模块：FastAPI 后端（`app/routes/jobs.py` 上传与预览、`routes/cad.py` 以子进程调用根目录 `drawing2cad.py`、`routes/simulation.py` 经 `simulation_worker.py` 在隔离 MuJoCo 解释器中执行既有 embodied agent 路径，`simulation_video.py` 以只读观察器采样同一次执行录制 MP4）；Next.js 前端通过同源 `/api/*` 转发访问。Web 只编排既有管线，不导入 `drawing_cad`，不修改任何上游包；任务成功与误差判定沿用 `pick_place_task` 的视觉复核语义。接口契约、存储结构和限制见 [web/README.md](../web/README.md)。
