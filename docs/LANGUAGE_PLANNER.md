# 独立 Language Planner

`language_planner/` 只把明确的自然语言放置指令转换为现有 TaskPlan。它不启动仿真，不创建 robot skills，不调用 `Agent.execute()`，也不读取物体真值。现有 `embodied_agent/`、机器人核心和 `schemas/task_plan.schema.json` 不修改。

## Windows / PyCharm

使用项目已有 Python，补充独立的 schema 验证依赖：

```cmd
python -m pip install -r requirements-language-planner.txt
python plan_instruction.py "把板件放到装配区" --catalog examples\agent\allowed_catalog.json --output outputs\language_planner\assembly
python plan_instruction.py "Please place the plate into the buffer zone" --catalog examples\agent\allowed_catalog.json --output outputs\language_planner\buffer
```

PyCharm 的 Script path 选择 `plan_instruction.py`，Parameters 填引号包围的指令及上述选项，Working directory 设为仓库根目录。默认 `--provider deterministic`，不联网，不需要模型密钥，不依赖 MuJoCo/CAD 第三方包。

每次输出：

```text
output/
├── instruction.txt
├── planning_result.json   # success、task_plan、failure、metadata
└── task_plan.json         # 仅校验成功时生成；仍是 pending
```

退出码 0 仅表示规划成功，不能解释为机器人任务成功。失败退出码 1，`task_plan=null`，保存结构化 failure；在同一受标记输出目录重跑失败时清除旧 `task_plan.json`，不留下可误用的旧成功计划。

CLI 也支持 `--provider mock --model-output response.json`，用于验证原样模型响应、坏 JSON 和越界计划。CLI 没有执行机器人或加载任意 provider Python 模块的参数。真实 LLM 从下面的 Python adapter 接入。

## Planner API 和能力来源

```python
import json
from pathlib import Path
from language_planner import Planner, DeterministicProvider

def current_catalog():
    return json.loads(Path("examples/agent/allowed_catalog.json").read_text(encoding="utf8"))

planner = Planner(DeterministicProvider(), catalog_source=current_catalog)
result = planner.plan("请把板件放到目标B。")
if result.success:
    ready_plan = planner.validated_task_plan(result)
    # ready_plan 是现有 embodied_agent.models.TaskPlan，仍未执行。
    print(ready_plan.json())
else:
    print(result.failure.json())
```

`catalog_source()` 由应用提供，每次规划及交接都会重新调用。当前示例从显式配置文件读取，**不自动发现 MuJoCo 场景对象或证明目录与场景一致**。接入方负责提供真正允许的对象 ID、目标区域和技能。只读配置里没有物体实时 pose、关节或仿真状态。

目录格式：

| 字段 | 结构 |
| --- | --- |
| `objects` | `[{"id": "场景对象 ID", "aliases": ["用户可用名称"]}]` |
| `targets` | `[{"id": "目标 ID", "aliases": [...], "region": {"center_xy_m": [x,y], "size_xy_m": [w,h], "frame":"world", "unit":"m"}}]` |
| `skills` | 当前启用的技能名称列表，只能来自 observe/locate/pick/place/verify |

现有计划要求五步完整顺序，缺少任意一个 skill 就失败。目标区域坐标必须由可信配置显式给定，不从语言猜坐标。目录可包含多个备选对象/目标，但一个计划只能选一个对象和一个目标。

现有 TaskPlan 没有 target ID 字段，因此保持 schema 原样，将选中的 region 写入 goal/arguments，目标 ID 记录在 PlanningResult.metadata。两个目标 ID 若具有完全相同的 region，目录拒绝加载，避免无法从计划唯一确认目标。别名可跨条目重复；离线解析遇到重复别名返回歧义，不擅自选择。

## Provider adapter 和 prompt

`providers.py` 定义协议 `complete(PlanningRequest) -> str`。Request 提供 instruction、能力目录副本、现有 schema 副本，以及三条 messages：固定 system 约束、system 目录/schema 数据、user 指令 JSON。Provider 只返回原始文本，没有机器人或工具对象。

- `DeterministicProvider`：离线、中英文完整句式匹配，调用同一输出校验流程，不是绕过校验的成功答案。
- `MockProvider`：原样返回给定响应或模拟不可用，适合单元测试及异常响应回放。
- `CallableLLMProvider(completion)`：调用宿主提供的 `completion(messages=[...]) -> str` 一次；SDK、服务商、密钥、超时及 response 文本提取由宿主 adapter 管理。异常转成 `LLM_UNAVAILABLE`，不重试、不回退离线答案。

接入形式：

```python
from language_planner import Planner, CallableLLMProvider

# your_completion 是接入方已经配置好的 LLM 文本请求函数，
# 接受 keyword 参数 messages，并返回模型原始文本。
planner = Planner(CallableLLMProvider(your_completion), current_catalog)
result = planner.plan("把板件放到装配区")
```

本轮未指定服务商，也未配置或调用外部 LLM API；测试验证了 adapter 传参、响应和不可用路径，不宣称真实模型理解率。网络超时必须由接入方的 completion 设置；同步 adapter 不会强制中断一个永久阻塞的宿主调用。

完整 prompt 保存在 [`prompt.py`](../language_planner/prompt.py)，版本 `language-planner-v1`。它要求：只输出 JSON；只能选目录中的对象/目标；完整五步；所有状态 pending、result=null、observations/failures 为空；目标区域原样复制；不生成关节角、qpos/qvel、控制命令、代码、工具调用或额外字段。未知、歧义、超范围指令返回：

```json
{"planning_failure":{"code":"AMBIGUOUS_INSTRUCTION","message":"需要明确目标"}}
```

Provider 还可以返回 `UNKNOWN_OBJECT / UNKNOWN_TARGET / UNSUPPORTED_INSTRUCTION`。失败信封本身也须符合精确结构。

## 校验和交接边界

```text
instruction + 当前能力目录
         ↓
provider 原始响应
         ↓
严格 JSON 解析
         ↓
现有 TaskPlan JSON Schema
         ↓
对象 ID / 技能 / 目标区域允许列表
         ↓
现有 TaskPlan.from_dict：全新计划、五步顺序、参数一致
         ↓
PlanningResult / pending task_plan.json（不执行）
```

所有 provider 共用 `PlanValidator`，直接读取现有 schema 文件，不维护宽松副本。严格 JSON 解析拒绝 Markdown 围栏、重复键、非有限数值、多段 JSON 和超长响应。schema 拒绝额外控制字段；现有计划契约进一步拒绝伪造运行结果、历史观测、执行状态和步骤间对象/目标不一致。

目标必须精确等于目录中已登记的一个 region；不能让模型输出任意坐标。所有 object 参数必须与唯一 goal 一致，所有技能必须属于允许列表。交接函数 `validated_task_plan(result)` 重新读取目录/schema，校验其指纹和计划内容；目录或 schema 变更时返回 `STALE_CAPABILITIES`，需由调用方重新提供任务，模块不会自动 replan。

交接失败抛出 `PlanningRejected`，其中 `.failure` 是结构化 PlanningFailure。`plan()` 中的失败则直接返回 `PlanningResult(success=False, task_plan=None, failure=...)`，字段为 `code/stage/message/details`。主要失败码包含 `UNKNOWN_OBJECT`、`UNKNOWN_TARGET`、`ILLEGAL_SKILL`、`MALFORMED_MODEL_OUTPUT`、`SCHEMA_VALIDATION_FAILED`、`PLAN_CONTRACT_FAILED`、`LLM_UNAVAILABLE`。原始 SDK 异常文本不写入结果，以免包含密钥。

`validated_task_plan()` 只交回 TaskPlan 对象，不执行。直接绕过本模块调用现有 Agent 时，不会自动获得目录校验；现有 Agent API 没有被修改。应用应保留上述交接边界，不能直接执行原始模型响应。

## 真实能力和限制

离线 planner 支持 `把/将 OBJECT 放到/放在/放置到/移动到/搬到/移到 TARGET`（可带“请”），以及 `put/place/move/transfer OBJECT to/in/into/on/onto TARGET`（可带 please/the）。名称必须是配置中的 ID 或完整别名。它不做模糊匹配、代词消解、隐含对象选择、相对方位推理或任意中文理解；“放到那里”、未登记的“左边”、否定、复合及多物体指令会失败。

结构校验和允许列表不能证明一个合法计划准确表达了原指令：LLM 仍可能在多个合法目标中选错。此版本没有语言理解准确率或真实 LLM 测试结果。它也不证明目标可达、能抓起或能放置；这些仍由既有机器人 pipeline 在真正执行时检查。

没有 VLM、多物体执行、retry/replan、新 skill 或机器人控制逻辑。规划成功与执行成功分开记录。测试和回归结果见 [VALIDATION.md](VALIDATION.md) 与 [language_planner_verification.json](validation/language_planner_verification.json)。

```cmd
python -m unittest discover -s tests\language_planner -v
```
