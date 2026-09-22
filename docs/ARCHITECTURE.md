# 架构与证据流

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

`pick_and_place.py` 管理简化机器人及兼容显示，`panda_grasp.py` 读取外部 Panda 场景，`export_video.py` 导出简化机械臂视频。两条管线共处仓库，但没有 CAD→MJCF 自动转换器。

生产代码不读取 `examples/` 的答案。案例仅用于复现与展示；`outputs/`、解释器、缓存和第三方可执行文件不提交。
