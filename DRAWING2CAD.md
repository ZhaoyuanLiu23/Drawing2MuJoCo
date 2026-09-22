# 二维工程图 → 3D CAD · V0.1

本功能已在此 Windows 项目中实现并运行。它是一个有限范围的、基于图纸证据的重建器，支持三组基础几何组合：**圆形等截面拉伸（零个或一个同心孔）**；**四条直边加一个相切四分之一圆弧、等厚拉伸和一个定位圆形通孔**；**矩形等厚板、可变数量圆柱通孔、水平长圆槽及可选锥形沉孔**。算法先识别轮廓与视图，再通过尺寸界线、尺寸线、文字位置关联参数。不会根据文件名、零件名称、商品编号、M4 等规格名称查表生成模型，也不会从当前样例回填未知尺寸。

## 现在直接运行

此电脑的独立 CAD 环境已经配置好。打开 **CMD**，复制以下两行：

```cmd
cd /d "C:\Users\86155\Documents\ChatGPT\仿真"
python drawing2cad.py "C:\Users\86155\Downloads\95610A014_Nylon Plastic Washers.pdf" --open
```

`--open` 会打开本地交互预览，可以拖动旋转、滚轮缩放、切换顶视图与侧视图。不需要 MuJoCo/OpenGL 窗口、不需要网站服务器，也不需要联网加载前端库。去掉 `--open` 仍会生成全部文件。

换图纸只需替换输入路径；PNG/JPG 使用相同命令。含空格的路径要加英文双引号。默认结果写入项目下 `outputs/drawing2cad/<文件名>_<内容哈希前8位>/`，终端会打印实际路径。同一输入重跑会刷新该目录中本功能管理的固定文件。

## Windows + PyCharm

1. 打开项目 `C:\Users\86155\Documents\ChatGPT\仿真`。
2. 打开 `drawing2cad.py`，建立一个 Python 运行配置。
3. Script path 选择该文件；Parameters 填写 `"C:\Users\86155\Downloads\95610A014_Nylon Plastic Washers.pdf" --open`。
4. Working directory 设为项目目录。点击运行。

**现有 Panda 项目解释器可以保留。** 启动器会自动转交给项目的 `.venv-drawing2cad\Scripts\python.exe`。如果需要在 PyCharm 中直接调试 `drawing_cad` 包，可仅为这个运行配置选择该独立解释器，并关闭“将内容根/源根添加到 PYTHONPATH”等设置仅在出现环境冲突时再排查。不要把 Panda 运行配置切到 CAD 环境，CAD 环境不包含 MuJoCo。

## 环境与重新安装

原有 Python 3.9、MuJoCo、NumPy 环境未升级或替换。CAD 使用独立的 **64 位 Python 3.12**，已实际测试的主要依赖是 CadQuery 2.5.2 / OpenCASCADE 7.7.2、OpenCV 4.11、PyMuPDF 1.26.7、RapidOCR 1.4.4、ONNX Runtime 1.22.1 和 NumPy 2.2.6。

在另一台电脑上先安装 64 位 Python 3.12，再在项目目录运行：

```cmd
setup_drawing2cad.cmd
```

如果 `py -3.12` 找不到解释器，可传入实际路径：

```cmd
setup_drawing2cad.cmd "C:\Python312\python.exe"
```

脚本只创建/安装到 `.venv-drawing2cad`。直接依赖见 `requirements-drawing2cad.txt`，本机通过测试的完整 Windows/Python 3.12 版本清单见 `requirements-drawing2cad-lock.txt`。首次安装需要从 Python 包仓库下载依赖；之后 OCR 模型随包本地运行，图纸不会发往在线服务。测试需要的 pytest、ReportLab 也在该环境中。

## 输出内容

| 文件 | 用途 |
| --- | --- |
| `parsed.json` | 完整解析证据：来源哈希、依赖版本、预处理、文字框、轮廓、孔、视图、尺寸、单位、关联的线段、参数、置信度、推断、unknown、导出校验 |
| `source.png` / `preprocessed.png` | 所选页原始渲染与二值化图像 |
| `evidence.png` | 蓝色候选几何，绿色已关联尺寸，橙色未知标注；图框/标题栏矩形可能保留为候选，但不参与模型 |
| `parameters.json` + `model.py` | 参数化建模配方及可独立重建的 CadQuery 源码 |
| `model.step` | OpenCASCADE 生成的实体；导出后重新导入核对有效性、包围盒和体积 |
| `model.stl` | 封闭三角网格；检查流形边、有限坐标和体积误差 |
| `preview.html` | 无外部依赖的交互预览，双击即可打开 |
| `preview.png` / `mesh.json` | 静态检查图和 CAD 内核三角网格 |

STEP 是中性实体文件，不包含原生 CAD 软件的特征历史。参数化能力由 `parameters.json` 与 `model.py` 保留。可编辑参数后使用 CAD 解释器运行输出目录里的 `model.py` 重建 STEP/STL；这不会改写原图解析证据，也不会刷新原始预览。若要完整可追溯的新结果，应修改输入图纸后重新执行 pipeline。

CAD 坐标采用 **mm**，底面 Z=0。圆形模型以圆心为 X=Y=0；直线/圆弧轮廓以主视图左下角为原点，X 向右、Y 向上，Z 为厚度。孔的 `hole_y_from_top` 是距上边的标注距离，建模时换算为 Y=height−hole_y_from_top。STL 格式自身不携带单位，导入其他软件时请选择毫米。

## 第二个 benchmark：直边、单圆角、定位通孔

新增识别由闭合轮廓的直线/圆弧拟合、上方/右方正投影视图配对、R 引线追踪和 THRU 标注组成。圆角位置来自轮廓，半径来自 R 标注；孔的位置来自两个定位标注，不默认居中。直径符号附近低置信度的 THRU 文本会局部重新 OCR，并保留第一次识别证据，不做数字字符替换。

新截图 **没有注明单位**。不传单位时输出 `needs_review` 和 JSON，单位保持 unknown，不导出有歧义的实体。以下是明确指定英寸的**测试配置**，不代表原图单位已获确认；若实际为毫米，应使用 `--units mm`：

```cmd
python drawing2cad.py "tests\drawing2cad\fixtures\benchmark2.png" --units in --open
.venv-drawing2cad\Scripts\python.exe scripts\validate_drawing2cad_profile.py "tests\drawing2cad\fixtures\benchmark2.png" --units in
```

第二条会生成 `outputs/drawing2cad_benchmark2/acceptance_report.json`，包含无单位时停止导出、原始输入指定测试单位、匿名文件名、以及独立改变七个标注的回归。变体保持轮廓像素不变，写有 NOT TO SCALE 与测试单位，验证尺寸来自文字标注。

| 参数（图中数值） | 新 benchmark | 改尺寸版本 |
| --- | ---: | ---: |
| 宽 / 高 / 厚 | 2.00 / 1.00 / 0.25 | 2.80 / 1.40 / 0.35 |
| 孔距左边 / 距上边 | 1.00 / 0.50 | 1.10 / 0.65 |
| 通孔直径 / 圆角半径 | 0.50 / 0.25 | 0.40 / 0.20 |

预览、STEP、STL、独立 `model.py` 均来自同一参数配方。测试核对 STEP 重导入、体积、包围盒、STL 水密性和 CAD 内核里的孔面位置。缺少 R 标注、引线、THRU 或必要定位尺寸时不从像素补尺寸；角部为直线倒角时不当作圆弧。

## 第三个 benchmark：矩形板、尺寸链、长圆槽、锥形沉孔

新增模块为 `pattern_geometry.py`（小圆/槽和薄侧视图）、`pattern_features.py`（有限几何组合）、`feature_callouts.py`（数量引线、剖面直径及角度）、`pattern_constraints.py`（尺寸链冲突和位置推断）、`pattern_cad.py`（参数化切除和体积校验）。沿用既有 pipeline、参数证据、导出器和预览。没有以图片/文件名或零件名称分派。

原图没有单位说明，不传 `--units` 时 `units.status=unknown`、`units.default=null`，保存未缩放尺寸链冲突并停止导出。本次用户明确授权的毫米测试：

```cmd
python drawing2cad.py "tests\drawing2cad\fixtures\benchmark3.png" --units mm --output "outputs\benchmark3" --open
.venv-drawing2cad\Scripts\python.exe scripts\validate_drawing2cad_pattern.py "tests\drawing2cad\fixtures\benchmark3.png" --units mm
```

PyCharm 使用前述 `drawing2cad.py` 配置，Parameters 换成第一条命令的脚本后参数即可。毫米来源记录为 `cli_override`，`units.detected` 不把传入单位列为图纸识别结果；STEP/STL 配方保留 `unit_provenance`。

源图直接总高 **60** 与 **15 + 36 + 15 = 66** 冲突。JSON 的 `constraints` / `conflicts` 保存原始数值、标注文本、尺寸 ID、总和、差值。外形使用直接总高，不改写任何链尺寸。孔距上边候选分别为 **15/9** 和 **51/45**；当前图纸的检测坐标支持暂用 **15、45**，记录 `constraint_conflict_resolved_by_detected_geometry`、`resolution_status=resolved_by_geometry`、`nominal=null`、confidence **0.60**，并保留所有候选。这是依赖投影几何的启发式推断，不判定哪条原标注错误；示意图、不按比例绘制或像素误差可能使该推断失效。几何无法区分冲突候选时保留 unknown/ambiguous 并阻止导出，中心线不得覆盖未解决的冲突。

布局链路为 `geometry → feature_candidates → constraints → resolved_features → CAD`。选定板轮廓内检测到的圆和槽逐个进入候选，不再构造两排孔的笛卡尔网格。各候选分别引用 X/Y 位置约束，允许不同数量、多排及非完整网格；槽不默认在板高一半。位置由一致尺寸链、完整的单边基准路径，或有检测线段证据的板中心线推断。证据不足的 feature 标为 ambiguous，CAD 不导出。中心线识别及坐标聚类仍是带像素容差的启发式方法。

可变布局测试在 `tests/drawing2cad/test_pattern.py` / `variable_pattern_fixture.py`，覆盖 6 孔 + 1 槽、12 孔三排 + 3 槽、非中心槽、5 孔非完整网格 + 不同 Y 位置的双槽，以及缺失定位、未解决冲突和单位换算。测试数量与坐标仅用于生成测试输入和核对结果，运行代码按候选列表处理。

槽宽、总长及数量来自带引线的标注；孔数量与图中检测的圆阵列核对。普通圆柱通孔使用带数量、直径、THRU 的独立标注，不要求沉孔参数。图纸标出沉孔时，通孔直径与沉孔大径仍须由主视图引线及独立剖面标注交叉确认。剖面夹角以 `value_degrees` / `unit=deg` 保存，绝不当毫米。沉孔深度由两径和夹角计算，锥形沉孔解释仍是明确记录的几何推断；缺少沉孔证据不降级为普通孔。低分 OCR 行可保留原文并附字符级证据，只接受可靠数字，不按样例补数字。

薄侧视图通过共同投影跨度和端部闭合边配对，可位于主视图左、右、上、下；厚度标注方向随之确定。长尺寸延长线不作为闭合边；矢量 PDF 优先使用矢量边证据。多个候选无法唯一配对时停止，不能按最近距离或左右优先级选择。剖面候选也不再限定在侧视图下方。

`features.feature_definitions` 将 `cylindrical_through_hole` 与 `straight_slot` 分开表达，候选与已定位特征通过 `definition` 引用它们。`hole.additions=[]` 表示普通通孔；有沉孔时附加 `countersink` 的直径和夹角参数引用。`nX SLOT L × W THRU`、大小写 x、旧 `n-SLOT(WxL)` / 中文括号格式统一进入同一槽定义。文字层拆开的同行词保留原始 `text_ids` 和逐数字来源。旧格式未明确写贯穿时沿用原有贯穿槽解释，但在 `extent_source=legacy_slot_callout_convention`、`inferred=true` 及 assumptions 中明示。

这三项扩展的独立合成测试见 `test_generic_feature_extensions.py` / `orthographic_feature_fixture.py`，覆盖四个侧视方向、普通通孔、无效数量/断开引线/缺失沉孔信息、槽标注格式、同结构不同尺寸、STEP/STL/预览与独立模型重建。本轮不重新运行 Blind Test A。

验收覆盖原始 PNG、无单位停止导出、匿名文件名，以及独立绘制的同结构 PDF 两组尺寸：第一组 **200×70×4**，槽 **14×34**，通孔 **5.6**，沉孔 **10 / 110°**；第二组在保持绘图轮廓不变时修改尺寸文字及角度。变体是独立矢量工程图，不声称是原截图直接换字；JSON、内核锥面位置、STEP 重导入、STL 水密性和解析体积均检查。结果见 `outputs/drawing2cad_benchmark3/acceptance_report.json`。

## 无孔矩形板与未标注槽

矩形板可以只有水平长圆槽，不再要求同时存在圆孔。`100.00mm±0.20` 和 `100.00 ± 0.20 mm` 均保留名义值、上下限及原始文字；连接尺寸线的短折线外置厚度标注也可以关联。尺寸解析不读取零件名称或文件名，也不将螺钉规格换算为槽尺寸。

没有槽尺寸标注时，进入显式 `inferred_geometry` 模式：用板宽、板高的可靠毫米尺寸校准正交主视图，再逐槽估算总长、宽和未标注的位置。检测候选为 `source=detected`，推断参数为 `source=inferred`、`value_source=inferred_geometry`、`nominal/min/max=null`、confidence 0.55，并记录像素框、标尺、尺寸证据及假设。每个槽可有自己的长宽；STEP/STL 的生成配方保存相同来源记录。槽按贯穿切除解释，这也是推断，并非图纸明确给出的加工尺寸。

校准要求板宽、高、厚度有明确单位及可靠尺寸，两个方向的比例差不超过 5%。明确标为 NOT TO SCALE、单位未知、槽标注被拒绝、部分位置尺寸链或未解决冲突时，不用图形估算覆盖现有证据。已完整解析的槽标注仍走原来的尺寸约束路径。独立测试见 `test_slot_only.py`：100×25×6 板及两个水平槽、改变尺寸/数量/偏心位置、缺失证据及导出实体检查。

## 当前样例与验收证据

提供的图纸解析出外径 **8.8 mm**、内径 **4.4 mm**；厚度标注为上限 **0.9 mm**、下限 **0.7 mm**。图纸没有独立的名义厚度，默认策略选取区间中值 **0.8 mm**，明确写入 `status=inferred`、`limits_mm`、`nominal_mm=null`、`selection_policy=midpoint` 和原因。该数值是可追溯的选择，不是识别出的名义尺寸。

如不接受自动选择中值，运行：

```cmd
python drawing2cad.py "C:\Users\86155\Downloads\95610A014_Nylon Plastic Washers.pdf" --limit-policy require-nominal --output "outputs\drawing2cad_strict"
```

这会保留解析结果并返回 `needs_review`，不生成 STEP/STL；不会用默认厚度兜底。

已运行的验收覆盖：

- 多组独立合成图纸的不同外径、孔径、厚度、无孔圆盘、横向侧视图。
- 修改尺寸文字而保持绘图轮廓不变，模型仍按标注尺寸生成，而非按像素比例猜尺寸。
- 原图 PDF、匿名重命名 PDF、由原图生成的 PNG 和 JPG。
- 仅将原图全部显式尺寸标注乘以 1.5，得到外径 13.2 mm、内径 6.6 mm、中值厚度 1.2 mm；源图纸未改动。
- mm/cm/in 换算、单位注释、极限尺寸策略、缺失尺寸、低置信度、冲突标注、错误孔径、偏心/多个孔、空白/不支持输入，以及防止残留旧模型误导用户。
- 原有 Panda 和简化机械臂的实际物理抓取/松手反例测试；原有代码、模型及测试的 SHA256 不变。

测试命令（CMD / PyCharm Terminal 均可）：

```cmd
.venv-drawing2cad\Scripts\python.exe -m pytest tests\drawing2cad -q
python -m unittest discover -s tests -p "test_*.py" -v
.venv-drawing2cad\Scripts\python.exe scripts\validate_drawing2cad_sample.py "C:\Users\86155\Downloads\95610A014_Nylon Plastic Washers.pdf"
```

第二条使用原有 MuJoCo 解释器。验收脚本会在 `outputs/drawing2cad_acceptance/` 生成输入变体、对应 CAD 及 `acceptance_report.json`，并核验源 PDF 哈希不变。变体尺寸由输入标注与测试倍率计算，生产模块没有样例尺寸。

## CLI 与错误处理

| 选项 | 含义 |
| --- | --- |
| `--output "目录"` | 必须是空目录或已有本功能标记的目录；拒绝覆盖其他非空目录 |
| `--page 2` | 选择 PDF 页；默认只处理第 1 页，非整本自动合并 |
| `--dpi 240` | PDF 栅格化分辨率；范围 72–400，默认 180，最长边预处理上限 2800 px |
| `--ocr auto` | 默认；有原生文字的 PDF 优先使用原生文字，否则本地 OCR |
| `--ocr always` | 用 OCR 替代文字层；混合扫描/错误文字层 PDF 可尝试此项 |
| `--ocr off` | 禁用 OCR；无文字层的图纸将缺少尺寸而停止建模 |
| `--units mm` | 用户明确给无单位标注指定 mm/cm/in；不会覆盖标注本身的显式单位 |
| `--limit-policy midpoint` | 对可靠配对的上下极限尺寸取中值，并记录推断 |
| `--limit-policy require-nominal` | 禁止为只有极限尺寸的参数自动选名义值 |
| `--open` | 成功后打开本地 HTML |

退出码：`0` = 已导出但仍有明确记录的几何推断；`2` = 缺少可靠证据，需人工复核；`1` = 读取、依赖或 CAD 导出错误。`parsed.json` 的 `blocking_reasons` / `error` 给出原因。重新运行失败时，本次输出目录中的旧 STEP/STL/预览会清除，防止把上次成功文件当成本次结果。不要在本功能管理的输出文件名下存放其他文件。

## 真实能力与限制

- 这是明确限定几何组合的 V0.1，不是任意机械图纸的通用理解系统。圆形轮廓仍需矩形侧视图，允许一个同心内轮廓；B2 需上方和右方视图，只支持一个圆角和一个有两个定位标注的圆孔。矩形板路径仍要求宽大于高两倍、至少一个水平长圆槽，以及同尺度、同轴对齐的唯一薄侧视图；圆孔可为零，不限制板宽占页面的比例。侧视候选厚度跨度至少 2 px，小于主视图短边；超过短边 20% 时还须有完整闭合轮廓证据。扫描断线/延长线交叠可能无法配对。孔和槽数量来自检测，支持多排和非完整网格；所有孔共用一组直径及可选沉孔参数（暂不混用普通孔和沉孔），显式槽标注仍共用一组宽度/长度，未标注槽可分别估算长宽。沉孔仍需要独立水平剖面的两径与夹角，角度标注需在剖面附近，沉孔开口面仍按正 Z 面解释。尺寸约束路径仍依赖可关联的水平/竖直相邻尺寸链或中心线；过近坐标可能被容差聚为同轴，稠密标注可能无法可靠关联。任意多边形、多个圆角、旋转槽/阵列、不同孔径或多组槽标注混用、盲孔、圆柱沉孔、台阶、弯槽、变厚、斜孔、曲面和一般复杂剖面仍不支持。
- 文字尺寸必须能通过水平/竖直尺寸界线及共同尺寸线关联。当前识别十进制数字、点/逗号小数、mm/cm/in、直径前缀、THRU、单个 ± 对称公差、上下叠排极限尺寸、单圆角短 R 引线，以及 B3 的链式尺寸、数量与槽尺寸标注、沉孔剖面夹角。分数、一般角度定位、一般曲线半径、多分支长引线、GD&T、螺纹、复杂公差表、双单位尺寸尚未实现。
- 除上述明确标记的未标注槽推断模式及已有中心线/冲突候选推断外，没有可靠标注就返回 unknown；单位不从外观或像素尺度猜测。识别到的非尺寸数字（如年份）可能以未关联 unknown 保存在 JSON。
- OCR 置信度低于 0.90 的普通尺寸不会用于建模；B3 组合标注也可采用字符级数字置信度加引线/剖面证据。高分 OCR 仍可能误识别，特别是细线邻近数字或直径符号。全部 confidence 是启发式证据分数，不是经过统计校准的正确率。使用前可检查 `evidence.png` 和 `parsed.json`。
- 圆形内轮廓解释为通孔、侧视图解释为等截面拉伸均是显式推断，分别记录较低置信度；仅凭这两种视图不能证明不存在盲孔、隐藏台阶、未标倒角/圆角。这些未知几何不会自动补齐，生成模型不代表已完成制造图纸审签。
- PDF 可读取原生直线/Bezier 曲线及文字；PNG/JPG 使用图像检测和 OCR。支持 EXIF 方向、透明底归白和小角度扫描倾斜校正，不支持任意透视照片、严重压缩、重叠线条和任意 90° 旋转图像。PNG/JPG 的斜视图暂不单独恢复；PDF 中椭圆曲线可记为斜视候选，但不据此取尺寸。
- 小圆检测阈值约为页面短边的 1.8%；过小孔、很细或断裂的线可能漏检。图像质量差时仍需人工检查，不能宣称任意同拓扑图纸都必定成功。已验证的同结构、不同尺寸、清晰标注图纸无需改代码。
- 交互预览采用 Canvas 三角面深度排序，适合当前简单实体的检查；STEP/STL 和内核校验是几何交付依据。预览不包含物理仿真。

## 模块与扩展点

```text
drawing2cad.py                 独立环境启动器
drawing_cad/
  ingest.py                   输入适配器注册表；PDF/PNG/JPG
  preprocess.py               坐标一致的归一化、灰度/二值化、轻度纠偏
  ocr.py                      本地 OCR
  geometry.py                 圆、椭圆候选、线段合并、矩形闭环
  profile_geometry.py         四直边与单个相切圆角的闭合轮廓检测
  features.py                 特征识别器注册表；视图配对及拓扑推断
  profile_features.py         主/上/侧视图配对、定位孔与线/圆弧拉伸特征
  dimensions.py               尺寸语法、界线关联、单位/公差/置信度策略
  leader_dimensions.py        R 前缀与相连圆角引线证据
  cad.py                      参数门禁、构造器注册表、STEP/STL 与校验
  profile_cad.py              参数化直线/圆弧拉伸与定位通孔
  pattern_geometry.py        小圆阵列、长圆槽候选、薄侧视图
  pattern_features.py        矩形板基础几何组合及尺寸链需求
  feature_callouts.py         数量引线、槽尺寸、沉孔直径和角度证据
  pattern_constraints.py     保留原始尺寸链冲突、派生位置及候选
  slot_inference.py          未标注槽的比例校准、逐槽参数及推断证据
  pattern_cad.py             矩形拉伸、槽切除与锥形沉孔
  preview.py                  证据叠加图和 3D 预览
  preview_template.html       离线交互预览
  pipeline.py / cli.py         编排、JSON、失败处理、命令行
  types.py                    InputAdapter / FeatureRecognizer / ExportAdapter 协议
schemas/drawing2cad.schema.json JSON 字段契约（Draft 2020-12）
tests/drawing2cad/             独立测试及参数化工程图生成器
scripts/validate_drawing2cad_sample.py  用户输入驱动的验收脚本
scripts/validate_drawing2cad_profile.py 第二张图、改尺寸和匿名输入回归
scripts/validate_drawing2cad_pattern.py 第三张图、尺寸链冲突和同结构变体回归
```

扩展基础几何组合时，在 `RECOGNIZERS` 注册从轮廓、视图和标注证据生成参数需求的识别器，并在 `BUILDERS` 添加 CAD 构造器及对应的尺寸/拓扑/导出校验，不按零件名称分派。新输入格式在 `ADAPTERS` 适配为统一证据坐标及来源；DXF/DWG 当前只有接口契约，没有声称已实现转换。未来 MuJoCo 导出器可接收验证后的实体与显式 mm 单位，负责单位换算、网格碰撞分解和物理属性；本轮未将新模块接入或修改现有 Panda 代码。
