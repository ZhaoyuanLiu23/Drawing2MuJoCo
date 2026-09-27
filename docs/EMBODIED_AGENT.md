# 独立 Embodied Agent / Robot Skills

`embodied_agent/` 把现有 perception、manipulation、pick-place 包装成五个顺序调用的 skills。Agent 只处理明确给定的 JSON TaskPlan 和 SkillResult，没有 MuJoCo 对象、关节控制或目标真值接口；不接 LLM/VLM，不解析自然语言。

## Windows / PyCharm 运行

使用已有 MuJoCo / perception Python 环境，不新增运行依赖。输入须是已由 CAD2MuJoCo 生成、包含 Panda 和目标刚体的 `scene.xml`。

```cmd
python run_embodied_agent.py outputs\cad2mujoco\panda_bracket\scene.xml --plan examples\agent\task_plan.json --output outputs\embodied_agent\panda_cad
```

可选实时窗口（默认仍为 headless）：

```cmd
python run_embodied_agent.py outputs\cad2mujoco\panda_bracket\scene.xml --plan examples\agent\task_plan.json --output outputs\embodied_agent\panda_live --viewer
```

窗口使用 MuJoCo 原生渲染 API，绑定真实执行的同一份 MjModel/MjData，每个物理步后同步；不读取轨迹文件回放。左键拖动旋转相机、右键平移、滚轮缩放。界面仅操作显示相机，不提供状态重置、关节编辑或物体拖拽。关闭窗口或按 Esc 会取消当前 skill、停止执行，并等待执行/渲染线程退出。任务正常结束时窗口自动关闭。渲染开销可能使显示慢于仿真时间；物理时间步、控制器和任务判定保持原值。

Python API 对应 `run_agent(..., viewer=True)`。不传此参数时不会创建 viewer 或渲染线程。

示例中的世界米制目标坐标只是运行配置，不是生产代码中的特殊位置。其他目标可通过下面的 Python API 创建计划。不要只手改 `goal.target` 而留下旧的 step 参数；输入验证会拒绝不一致的计划。

PyCharm：Script path 选择 `run_embodied_agent.py`，Parameters 填 scene 路径及上述选项，Working directory 设为仓库根目录，解释器选已有 MuJoCo 环境。默认离屏运行，退出码 0 表示全部 skills 及最终视觉复核成功，1 表示失败。结果查看 `task_plan.json`。

```python
from embodied_agent import TaskPlan
from embodied_agent.runner import run_agent

plan = TaskPlan.build({
    "type": "pick_and_place",
    "object": "cad_part",  # 场景中明确配置的目标 ID
    "target": {
        "center_xy_m": [0.535, 0.085],
        "size_xy_m": [0.16, 0.14],  # 区域的完整宽、高
        "frame": "world",
        "unit": "m",
    },
})
result = run_agent("outputs/cad2mujoco/panda_bracket/scene.xml",
                   plan, "outputs/embodied_agent/another_target")
print(result.status, result.failures)
```

## Skill API

每个方法返回 `SkillResult(skill, success, data, error)`；`error` 成功时为 `null`，失败时为 `code/message/origin_stage/details`。`object` 是场景目标 ID 字符串，`target` 的单位和坐标系必须显式为 `m/world`。

| 方法 | 复用的实际工作 | 返回的关键证据 |
| --- | --- | --- |
| `observe()` | 原 pipeline 初始化、静置、固定 RGB-D 相机采集 | RGB/depth/segmentation 文件、时间戳、相机外参、焦距 |
| `locate(object)` | 原 perception 处理首帧，再采集第二帧，检查位姿有效、初始稳定和近桌面条件 | 最新 `estimated_pose`，含 position / quaternion / 质量及歧义信息 |
| `pick(object)` | 原候选/全路径预检、IK、approach → grasp → close → lift → lift_hold | 选定候选、已完成阶段；不把未测量的夹持状态写成已证实 |
| `place(object, target)` | 原放置规划、搬运、下降、释放、撤离、清空相机视线 | release/retreat 完成状态及目标区域 |
| `verify(object, target)` | 原 perception 重新采集 4 帧，原视觉任务判定检查区域、中心误差、底面高度、稳定性及移动证据 | 新观测、逐项 checks、visual_verification、verified |

`RobotSkills(PipelineSession(scene, goal, output))` 也可直接使用，按上述顺序调用，并在 `finally` 中 `skills.close()`。五个 skills 共用一次连续仿真，调用之间仿真暂停；不会每个 skill 重启场景、重新抓取或自行执行后续阶段。

`pick` 返回的是 `lift_phase_completed`，不是通过真值或接触统计独立验证的持物成功。底层任务没有视觉抬升检查，适配层保留此限制；滑落可在后续碰撞保护或最终视觉复核中失败。`place` 同样只确认释放撤离阶段完成。只有 `verify` 返回 `verified=true` 且所有视觉 checks 均为 true，Agent 才可成功。

## TaskPlan schema 与状态

机器可读契约：[task_plan.schema.json](../schemas/task_plan.schema.json)。完整待执行示例：[task_plan.json](../examples/agent/task_plan.json)。

| 字段 | 内容 |
| --- | --- |
| `goal` | `type="pick_and_place"`、目标 object、世界米制 target |
| `steps` | 五项固定顺序，每项 `id/skill/arguments/status/result` |
| `status` | `pending / running / succeeded / failed` |
| `observations` | observe、locate、verify 返回的观测证据，关联 step_id |
| `failures` | 失败的 step_id、skill 和原始结构化 error |

```text
Task: pending → running → succeeded
                       ↘ failed
Step: pending → running → succeeded / failed
      pending → skipped（前序步骤失败）
```

schema 描述输入和执行记录的结构。Python 运行时还验证：输入必须是尚未执行的标准计划、所有参数和 goal 一致、有限数值、正区域尺寸。拒绝任意控制指令、陈旧结果和直接续跑失败计划。当前没有重试、回滚、恢复或多目标编排。

目标在创建会话时绑定，因为原 pipeline 在 pick 阶段就预检完整放置路径。`place/verify` 必须传入同一个 target；不能抓起后临时换目标。

## 适配边界与失败传播

`backend.py` 继承现有 `PickPlaceTask`，仅在相机首帧、候选计算前、抬升保持后、释放撤离后设置暂停点，随后调用原方法。一次 `PickPlaceTask.run()` 包含所有机器人动作；没有复制控制器、改写物体状态或新增物理约束。仿真和 GL 资源始终在同一工作线程创建、使用和关闭。

`agent.py` 仅调用 skill API；`skills.py` 检查顺序和参数；`runner.py` 保存计划与状态快照。底层原有离线真值评估仍在最终判定固定后运行，但适配层按白名单取结果，`offline_evaluation`、仿真状态和控制器内部对象不进入 skill 返回值或 Agent 判定。没有感知失败回退真值。

| 失败 | skill / error code |
| --- | --- |
| 图像采集/初始观察失败 | observe / `OBSERVATION_FAILED` |
| 分割、配准或初始稳定性失败 | locate / `PERCEPTION_FAILED` |
| 无可行候选、IK 或抓取抬升阶段失败 | pick / `GRASP_FAILED` |
| 搬运、释放或撤离失败 | place / `PLACE_FAILED` |
| 新视觉观测失败、实际落点错误等 | verify / `VERIFY_FAILED` |
| 输入或未分类异常 | `PIPELINE_INPUT_ERROR / PIPELINE_EXCEPTION`，保留原阶段 |
| 顺序/对象/目标/返回类型错误、超时 | 明确的 contract 或 session 错误，不启动后续阶段 |

错误原样保留底层 `origin_stage` 和原因。完整放置路径预检发生于 pick，所以不可达放置路径可能体现为 `pick` 失败，同时保留真实阶段/候选失败证据，不能仅凭 skill 名判断底层原因。失败后关闭会话，当前 step 标为 failed，其余 skipped，TaskPlan 为 failed。

## 输出与测试

```text
outputs/embodied_agent/panda_cad/
├── input_plan.json
├── task_plan.json          # 持续更新的执行结果
├── transitions.json        # 任务/步骤状态变更
└── skills/
    ├── pipeline_result.json  # 白名单保留的原 pipeline 结果
    ├── frame_0.png ... frame_5.png
    ├── frame_*_depth.npy
    ├── frame_*_segmentation.npy
    └── frame_*.json          # 时间戳/标定/来源
```

输入错误或早期感知失败可能没有全部帧或 pipeline_result；以 TaskPlan 的 failures 为准。重复运行相同受标记的输出目录会更新本轮证据。

```cmd
set CAD_MUJOCO_PANDA_SCENE=C:\path\mujoco_menagerie\franka_emika_panda\scene.xml
python -m unittest discover -s tests\embodied_agent -v
```

11 项契约测试及 7 项真实 pipeline 测试覆盖正常任务、改变目标、缺失分割、真实超开口几何、注入释放故障、实际搬往错误区域的视觉判失败，以及暂停会话取消。检查暂停点前后实际动作/采集序列，禁用真值 observer，并检查 Agent 不访问控制属性。未配置 Panda 模型时物理组会 skip，不算通过。完整本机结果见 [验证记录](VALIDATION.md) 和 [机器可读报告](validation/embodied_agent_verification.json)。

当前仍沿用原有理想 RGB-D 分割、已知 CAD/标定相机、水平桌面、单静态目标、可抓取板件外缘、有限路径采样、夹持变换预测及视觉复核容限。对称姿态歧义与凸包碰撞限制不变。此层是确定性任务编排，不增加新的机器人运动或感知能力。
