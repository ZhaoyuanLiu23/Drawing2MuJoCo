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
