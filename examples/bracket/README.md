# 工业 PDF：无圆孔槽板

输入 `input.pdf`；精选结果在 `result/`。

| 参数 | 识别/使用值 | 来源 |
| --- | --- | --- |
| 宽 | 100 ± 0.20 mm | 图纸名义值 |
| 高 | 25 ± 0.20 mm | 图纸名义值 |
| 厚 | 6 ± 0.50 mm | 图纸名义值 |
| 槽数量 | 2 | 轮廓检测 |
| 槽总长 / 宽 | 约 24.83 / 10.98 mm | 图形校准估算，confidence=0.55 |

槽位置及贯穿解释也属于推断。参数保存 `source=inferred`、`value_source=inferred_geometry`、名义值与公差为 null，并保留检测框和标尺。螺钉规格不被查表换算为槽尺寸。

```cmd
python drawing2cad.py "examples\bracket\input.pdf" --output "outputs\bracket"
```

STEP/STL 通过有效实体、包围盒、体积、STEP 重导入和 STL 水密性检查；估算结果不能代替加工尺寸。来源见 [第三方说明](../../THIRD_PARTY_NOTICES.md)。

![槽板预览](result/preview.png)
