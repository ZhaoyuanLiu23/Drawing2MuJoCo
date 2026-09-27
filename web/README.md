# Drawing2MuJoCo Web V0.1

独立的本地工作台。**工程图上传 → Drawing2CAD → STEP/STL → 交互 3D 预览 → 真实 Panda 视觉抓取放置 → 视觉复核结果和 MP4 录像。** Web 只衔接原有管线，不新增机器人算法；完成后播放录像，不使用实时流。

## 页面

- 顶部：项目名称、副标题、实际项目 GitHub 链接。
- 上传图纸 / 更换图纸：点击或拖拽上传一个 PDF / PNG / JPG / JPEG，单文件最多 10 MiB。上传时显示“上传中...”，成功后显示文件名和 job_id。
- 2D Drawing：显示图片或 PDF 第一页，可点击打开大图。
- 生成 3D：调用原有 Drawing2CAD CLI，显示“生成中...”；成功后 3D CAD 卡片使用 Three.js 展示真实 STL，自动居中适配，支持鼠标拖动旋转、滚轮缩放。
- 保存 STL / 保存 STEP / 保存解析 JSON：下载当前 job 的真实生成文件。出现 needs_review 或失败时只显示原管线原因；有 parsed.json 时可下载，不展示伪造模型。
- 仿真设置：物体 X / Y 质心坐标、绕世界 Z 轴偏航角与目标区域中心 X / Y。全部必填，界面单位为米、度，不把空值或非法位置替换为默认值。
- 运行仿真：CAD 成功后调用真实后端；运行期间显示“正在运行仿真并录制...”，完成后 Robot Simulation 显示本次真实录像和成功/失败状态。“保存视频”下载 MP4，“保存仿真结果”下载 JSON。任务失败但有有效录像时仍可播放；无录像或加载失败时显示“视频不可用”。
- 运行结果：抓取/放置显示阶段是否完成，最终任务成功来自视觉复核。位置误差是释放后视觉包围盒中心到目标 XY 的距离，单位毫米；原管线不提供独立姿态误差，因此为 null，界面显示“—”并解释原因，不用零值冒充。不使用真值补齐指标。

页面外壳可单独打开；上传和 CAD 生成需要启动后端。前端通过 Next.js 同源转发访问 `/api/*`，不需要开放 CORS。当前 job 对象和 CAD 状态保存在页面 React state 中；刷新页面会清空当前选择，但文件仍留在磁盘。替换失败保留上一个成功 job；替换成功后清空上一份 CAD 预览。

仿真中禁用更换图纸、再次生成和重复运行。HTTP 请求同步等待结果（最多 600 秒），页面没有后台任务轮询。关闭/刷新网页不会取消已经启动的工作进程；网络中断时不能把前端连接错误当作物理失败，需查看磁盘 `simulation/result.json`。本地 V0.1 不提供云调度或并发队列。

## 目录

```text
web/
├── .gitignore
├── README.md
├── frontend/
│   ├── package.json
│   ├── pnpm-lock.yaml
│   ├── next.config.mjs
│   ├── lib/api.js
│   ├── app/
│   │   ├── layout.js
│   │   ├── page.js
│   │   ├── icon.svg
│   │   └── globals.css
│   └── components/
│       ├── workbench.js
│       ├── pipeline-preview.js
│       ├── stl-viewer.js
│       ├── simulation-results.js
│       └── icon.js
└── backend/
    ├── requirements.txt
    ├── requirements-dev.txt
    ├── simulation_worker.py
    ├── app/
    │   ├── __init__.py
    │   ├── main.py
    │   ├── settings.py
    │   ├── errors.py
    │   ├── routes/jobs.py
    │   ├── routes/cad.py
    │   ├── routes/simulation.py
    │   └── services/
    │       ├── preview.py
    │       ├── cad_generation.py
    │       ├── simulation.py
    │       ├── simulation_request.py
    │       └── upload_storage.py
    └── tests/
        ├── test_health.py
        ├── test_cad.py
        ├── test_simulation.py
        ├── robot_worker_checks.py
        └── test_upload.py
```

## 当前电脑启动（Windows PowerShell）

本机已有 Codex 提供的 Node.js / pnpm，Web 依赖安装在本目录中。打开两个 PowerShell 终端。更新代码后先停止旧进程，再启动新版本；生产模式需要重新 build。

终端一：前端

```powershell
cd "C:\Users\86155\Documents\ChatGPT\仿真\web\frontend"
$webNode = "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\node"
$env:Path = "$webNode\bin;$env:Path"
node "$webNode\node_modules\pnpm\bin\pnpm.cjs" dev
```

终端二：后端（使用独立 `.venv`，不必激活环境）

```powershell
cd "C:\Users\86155\Documents\ChatGPT\仿真\web\backend"
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- 页面：<http://127.0.0.1:3000>
- 健康接口：<http://127.0.0.1:8000/api/health>，返回 `{"status":"ok"}`。
- 用 `Ctrl+C` 分别停止两端。只绑定本机回环地址。
- 本仓库 GitHub 链接可能需要有权限的 GitHub 账号登录。

## 新电脑首次安装

使用 Node.js 24 和 pnpm 11.19.0，以及 Python 3.12。前端依赖由 `pnpm-lock.yaml` 固定。后端使用独立虚拟环境，不向现有 CAD / 仿真解释器安装 Web 依赖。

```powershell
# 在项目根目录执行；前提是 Node.js / npm 和 Python 3.12 已安装
npm install --global pnpm@11.19.0
cd web\frontend
pnpm install --frozen-lockfile
pnpm dev
```

另一个终端，在项目根目录执行：

```powershell
cd web\backend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

运行时依赖见 `requirements.txt`；`requirements-dev.txt` 额外包含 pytest 和 HTTPX。复用项目已有的 Pillow / PyMuPDF 库，安装在独立 Web 环境；不导入 `drawing_cad`。FastAPI 不开放文档或 OpenAPI 路由，健康接口只表示 Web 进程可用，不表示机器人管线就绪。

CAD 生成额外依赖项目根目录已有的 `.venv-drawing2cad` 及其依赖（按主项目 Drawing2CAD 安装说明准备）。Web 后端使用该环境的 Python 调用根目录 `drawing2cad.py`，不向 Web 环境安装 CadQuery。缺少 CAD 环境时返回明确的 503 错误，不切换其他解释器。

仿真使用已有 MuJoCo/perception 解释器（本机 `C:\Users\86155\Desktop\python\python.exe`）及本机 Panda 资产。后端启动前可通过服务端环境变量配置，不允许请求传路径：

```powershell
$env:WEB_SIMULATION_PYTHON = 'C:\Users\86155\Desktop\python\python.exe'
$env:CAD_MUJOCO_PANDA_SCENE = 'C:\Users\86155\Desktop\mujoco_menagerie\franka_emika_panda\scene.xml'
```

Web 虚拟环境不安装 MuJoCo，也不弹出 viewer。RGB-D 离屏渲染仍需要原环境可用的图形驱动；缺环境/渲染失败不会回退真值。项目路径过长时，旧 Python 3.9 可能无法写入哈希命名的机器人资产，建议保持当前项目路径深度。

## Upload API

`POST /api/jobs`：multipart/form-data，恰好一个名为 `file` 的文件。成功返回 HTTP 201：

```json
{
  "job_id": "<32-character UUID4 hex>",
  "original_filename": "drawing.pdf",
  "file_type": "pdf",
  "preview_url": "/api/jobs/<job_id>/preview"
}
```

`file_type` 为 `pdf`、`png` 或 `jpeg`（JPG/JPEG 统一为 jpeg）。`GET /api/jobs/{job_id}/preview` 返回 `image/png`。原始 PDF/图片不通过静态目录对外开放。

失败统一为 JSON：`{"error":{"code":"file_too_large","message":"The maximum file size is 10 MiB."}}`。400 表示文件字段/名称/表单错误，413 表示超限，415 表示格式或 MIME 不符，422 表示文件损坏/空文件/加密 PDF，404 表示预览不存在，500 表示存储失败。

固定存储结构（不由上传参数选择）：

```text
outputs/web/jobs/<uuid4-hex>/
├── input/drawing.pdf   # 或 drawing.png / drawing.jpg / drawing.jpeg；保留原始字节
├── preview.png
├── job.json           # 原文件名、类型、job_id、输入相对路径、字节数
├── cad_result.json    # Web 生成状态与允许访问的文件 URL
├── cad.log            # CLI 标准输出和错误
└── cad/               # Drawing2CAD 自行写入的产物
    ├── parsed.json
    ├── model.step
    ├── model.stl
    └── ...            # 原管线其他证据、预览等；不额外公开
```

- UUID 随机分配目录，文件名固定；客户端名称只保存为元数据。拒绝路径片段、控制字符、冒号；multipart 库可能先把旧式 Windows 完整文件名规范化为 basename，该名称同样不能决定存储路径。
- 扩展名、MIME 与解码内容分别验证，只接受 PDF、PNG、JPEG。拒绝空文件、损坏文件、加密 PDF、动画图片。
- 文件限制 10 MiB；读取请求流时另限制 10 MiB + 64 KiB（multipart 开销），包括无 Content-Length 的分块上传。Next.js 转发上限留出开销为 11 MiB，后端仍强制文件的 10 MiB 上限。
- 图片上限 2000 万像素。预览最长边 1600 px；图片解码后按 EXIF 方向重新编码为不含原始元数据的 PNG；PDF 仅第一页栅格化。预览不执行脚本、不打开 PDF 链接、不提取附件、不进行 OCR 或尺寸解析。参考：[PyMuPDF 图片渲染](https://pymupdf.readthedocs.io/en/latest/recipes-images.html)、[Pillow 图像安全](https://pillow.readthedocs.io/en/stable/handbook/security.html)。
- 格式/大小/解码失败不会创建 job，写盘失败尝试清理本次文件；若磁盘连清理也拒绝，可能遗留未完成目录，不能作为成功 job 使用。成功 job 本轮不自动清理。无数据库、登录或外部存储。当前是本机单进程同步预览服务，没有生产级并发队列或独立解码沙箱。

## 验证

```powershell
# 在 web/backend 中：上传、真实 CAD CLI、下载和拒绝路径测试
.\.venv\Scripts\python.exe -m pytest tests -q

# 在 web/frontend 中：生产构建
pnpm build
# 可选：运行刚构建的页面
pnpm start
```

本机没有全局 pnpm 时，将上述 `pnpm` 替换为前面的 `node "…\pnpm.cjs"` 调用即可。

框架文档：[Next.js 本地安装](https://nextjs.org/docs/app/getting-started/installation)、[FastAPI TestClient](https://fastapi.tiangolo.com/tutorial/testing/)。

## 手工验证

1. 打开首页，点击“上传图纸”，选择 `examples/bracket/input.pdf`；观察上传状态、文件名、job_id 和 PDF PAGE 1 预览。
2. 将任意 PNG/JPG/JPEG 拖入上传区；确认预览更新、job_id 改变，旧 job 的文件保留。
3. 尝试拖入 TXT 或超过 10 MiB 的文件；页面应显示错误，保留原有成功预览。损坏的 `.png` 应被后端拒绝。
4. 点击“生成 3D”，观察生成状态；成功后拖动 3D 模型旋转，使用滚轮缩放。点击“保存 STL”“保存 STEP”“保存解析 JSON”，确认下载到浏览器下载目录（或浏览器提示的保存位置）。
5. 上传无法解析的空白图并生成：应显示 needs_review/失败原因，不显示 3D 模型；若产出了 parsed.json，可保存检查。
6. 使用成功生成的 `examples/bracket/input.pdf`，填写物体 X=0.50、Y=0、偏航角=0；目标 X=0.46、Y=-0.09，点击“运行仿真”。等待任务结果，成功应显示抓取/放置阶段完成、视觉复核通过和实际位置误差。点击“保存仿真结果”下载 JSON。
7. 将目标 X 改为 2，重新运行：应返回 `target_zone_outside_table`，本次记录 `physics_started=false`，不得启动落体或 Agent 执行。可改为其他合法参数继续运行，之前每次结果保留。

测试使用临时目录隔离磁盘结果；真实运行始终使用仓库 `outputs/web/jobs/`。CAD 测试包含真实 PDF 上传→CLI→STEP/STL 验证及空白图失败流程。仿真测试包括 HTTP 上传→CAD→Panda→视觉任务、改变起点/偏航角/目标后的真实执行，以及非法位置在物理前拒绝。其他错误注入测试只模拟超时、失败响应等边界。缺少 CAD/MuJoCo/Panda 环境时真实集成测试失败，不 skip。

第 4 步本机验证：Web 87 项通过（157.96 秒），既有 viewer 5 项通过（11.674 秒），均无跳过；前端 production build 通过。两次自动化完整任务视觉位置误差分别约 0.165 / 0.054 mm；变位姿测试逐步监测 99,600 个实时物理步，无物体状态外写、weld 或真值抓取输入。浏览器真实上传→生成→运行→保存结果也通过。此记录不是整仓 full validation，其他未修改模块本轮未全部重跑。

## CAD API

`POST /api/jobs/{job_id}/generate-cad`：空请求体，无查询参数。客户端只能提供上传返回的 32 位十六进制 job_id，不能指定输入路径、输出路径或 CLI 选项。

```json
{
  "status": "generated",
  "job_id": "<job_id>",
  "stl_url": "/api/jobs/<job_id>/cad/model.stl",
  "step_url": "/api/jobs/<job_id>/cad/model.step",
  "parsed_json_url": "/api/jobs/<job_id>/cad/parsed.json",
  "pipeline_status": "generated_with_assumptions",
  "error": null
}
```

- 实际调用：`.venv-drawing2cad/Scripts/python.exe drawing2cad.py <job>/input/drawing.<ext> --output <job>/cad`。Linux 使用该环境的 `bin/python`。通过参数列表启动子进程，`shell=False`，180 秒超时；默认页码、单位、OCR 和推断策略完全沿用原 CLI，不根据文件名或尺寸添加规则。
- `status` 为 `generated`、`needs_review` 或 `error`。只有 CLI 正常完成、parsed 状态为 `generated_with_assumptions` 且三个文件齐全时才返回模型 URL。原始解析 JSON 保留不改写；网页提醒结果含推断信息。单位未知/不支持的图纸按原管线拒绝。
- 失败同样返回完整字段（不可用 URL 为 null）和 `error: {code, message}`。400：非法 ID/请求/存储元数据；404：job 不存在；409：同 job 正在生成；422：需要复核/生成失败；503：缺少 CAD 环境；504：超时；500：存储或无效输出。
- `GET /api/jobs/{job_id}/cad/{artifact}` 只允许 `model.stl`、`model.step`、`parsed.json`，以附件响应；STL 也由网页读取显示。拒绝重定向文件系统路径和任意路径。生成中或失败后的部分模型不得下载；诊断 JSON 可在实际生成且记录授权后下载。
- 同 job 使用独占文件锁，成功后重复点击返回已有文件；重新上传产生新 job。服务异常终止可能遗留锁，需要确认进程结束后人工清理该 job 的 `.cad-generation.lock`。本地服务无跨 job 队列、持久任务调度或自动清理机制。
- Web 仅衔接既有 CLI，不修改 `drawing_cad`、CAD2MuJoCo、perception、manipulation、pick_place 或 embodied_agent。

## Simulate API

`POST /api/jobs/{job_id}/simulate`，`Content-Type: application/json`：

```json
{
  "object_start": {"x": 0.5, "y": 0.0, "yaw_deg": 0.0},
  "target": {"x": 0.46, "y": -0.09}
}
```

- 只接受这些字段和有限数值。严格验证 job、CAD 成功状态、STL/JSON 与路径；输入坐标不更正、不补默认。请求体上限 4 KiB。
- 在隔离的 MuJoCo Python 子进程中，先复用 `cad_mujoco` 的网格/质量/场景函数形成**没有执行 mj_step 的**校验场景，再调用现有 `pick_place_task.scene.add_target_zone` 检查目标区域和旋转后的起始外包矩形。复用 `inside_zone` 检查物体能放进目标区。边界来自实际场景桌面，保持原 1 mm 桌边余量和放置余量。
- 预检通过后才调用原 `cad_mujoco.pipeline.convert`，保留其真实落体验证；落体失败直接返回失败。随后在新 MJCF 中配置初始 body pose 及 home keyframe（执行前），保存 `scene.xml`。不在运行中写目标 qpos/qvel/xpos，不 weld、teleport，不用真值做抓取输入。
- 目标 XY 进入原 `TaskPlan.build()` 的 goal/place/verify；`run_agent(..., viewer=False)` 完成原 observe→locate→pick→place→verify。Web 仅增加保存实际相机/目标区域场景的构造器包装，不覆盖机器人 run/IK/control/感知/验证。
- 服务端测试配置：均匀密度 **7800 kg/m³**（不是材料识别）、目标区域完整尺寸 **0.16 × 0.16 m**。桌面/释放间隙/求解器沿用 CAD2MuJoCo 默认值。起始 X/Y 是质心；初始 Z 由原落体场景的桌高、网格厚度、释放间隙计算，再通过真实重力和接触落到桌上。这些假设写入结果并在页面提示。
- HTTP 200 仅对应 `succeeded` 且原视觉任务判定通过；422 为参数拒绝或真实执行失败，错误带原 code/message；400/404 为非法/缺失 job；409 为无成功 CAD 或同 job 运行中；503 为环境缺失；504 为超时（杀死并等待工作进程）；500 为工作进程/存储错误。
- 返回 `status/job_id/run_id/steps/error/perception_success/grasp_execution_completed/place_execution_completed/visual_task_success/position_error_mm/orientation_error_deg/result_json_url`，并保留参数、物性来源、预检和视觉检查细节。抓取完成不等于独立持物成功，place 完成也不代替 verify。
- `GET /api/jobs/{job_id}/simulation/{run_id}/result.json` 下载指定运行的 JSON。其他 scene/mesh 任意路径不提供下载接口；URL 只接受两个严格随机 ID。每次运行产生独立目录，不把旧结果当作本次结果，不覆盖前次下载 URL。

```text
outputs/web/jobs/<job_id>/simulation/
├── result.json                 # 最近一次运行结果
└── runs/<run_id>/
    ├── request.json            # 用户参数 + 服务端固定环境配置
    ├── preflight.json          # 原场景边界检查证据
    ├── preflight/              # 未执行物理的预检场景/资产
    ├── cad2mujoco/             # 原 convert 输出及 drop_test.json
    ├── scene.xml               # 用户起始配置
    ├── task_scene.xml          # 实际使用的相机/目标区域场景
    ├── scene_assets/
    ├── agent/                  # TaskPlan、transitions、RGB-D 帧及 pipeline_result
    ├── worker.log
    └── result.json
```

失败时保存已产生的证据，未到达阶段没有对应文件。预检保证桌面包含关系，不保证所有合法桌面位置的 IK/路径可达；其余失败由原完整路径预检和控制保护明确返回。保留单物体、已知 CAD、理想分割、水平桌面、矩形板外缘抓取、凸包碰撞、有限时窗视觉稳定等原限制。桌面 CLI `run_embodied_agent.py ... --viewer` 不变。

## 同次执行 MP4 录像

本轮文件范围（均位于 `web/`）：

- 新增 `backend/simulation_video.py`、`frontend/components/simulation-preview.js`。
- 修改后端 `backend/simulation_worker.py`、`backend/app/services/simulation.py`、`backend/app/services/simulation_request.py`、`backend/app/routes/simulation.py`。
- 修改前端 `frontend/components/pipeline-preview.js`、`frontend/components/workbench.js`、`frontend/lib/api.js`、`frontend/app/globals.css`、`frontend/package.json`。
- 新增测试 `backend/tests/test_video_observer.py`、`frontend/tests/simulation-video.test.cjs`；扩展 `backend/tests/test_simulation.py`、`backend/tests/robot_worker_checks.py`、`backend/tests/test_health.py`。
- 更新本文；运行证据保存至 `web/artifacts/video-*`。未修改核心管线。

- `web/backend/simulation_video.py` 是只读观察器。Web 的 `ArchivedTask` 在原有 `step_observer` 后采样，使用执行中的同一个 `MjModel/MjData`。仍然只有一次 `run_agent` / `PickPlaceTask.run()`；不回放轨迹、不启动第二次机器人任务，不写物体状态。CAD2MuJoCo 原有落体验证保持不变，视频录制的是随后唯一一次 Agent 任务（含其初始落体/稳定阶段）。
- 独立 `mujoco.Renderer` 在机器人工作线程创建、渲染和释放；固定相机根据桌面与机器人基座区域取景，覆盖 Panda、桌面、零件和目标区。没有 viewer 窗口。960×540、15 FPS、H.264/yuv420p、CRF 25、veryfast、无音轨、MP4 faststart。按仿真时间抽帧，编码速度不改变控制步长和判定。
- 复用已有 `.video_dependencies/imageio_ffmpeg/binaries/ffmpeg*.exe` 或 PATH 的 FFmpeg；也支持仿真 Python 中已安装的 `imageio_ffmpeg`。本机没有新增依赖。如其他机器缺少编码器，可仅在仿真 Python 中安装 `imageio-ffmpeg`。编码器缺失记录在 `video.error`，不会将原任务改判失败或成功。
- 输出 `simulation/runs/<run_id>/simulation.mp4`、`video_encoding.log`。先写 `simulation.partial.mp4`，关闭编码器并完整解码验证后才发布最终文件。失败任务的有效录像照常发布；不完整/不可解码文件不能下载。编码/渲染资源在成功、任务失败或录像异常后关闭，编码超时会杀死并等待编码进程。
- `result.json` 与 simulate 响应新增 `video_available: bool`、`video_url: string|null`，并包含 `video` 元数据（尺寸、FPS、帧数、仿真时间、录制阶段、文件字节、错误、完整性）。`video.complete` 仅指录像是否完整，不表示机器人任务成功。
- `GET /api/jobs/{job_id}/simulation/{run_id}/simulation.mp4`：只访问当前严格 ID 对应的最终文件，检查结果记录授权及路径，拒绝重定向路径和任意文件。`video/mp4`，支持 HTTP Range/206 进度跳转。无有效录像返回 404；前端不将旧录像或视频可用性当成任务成功。
- 浏览器使用原生 HTML5 video controls/autoplay/muted/playsinline，支持暂停、进度拖动和保存；自动播放可能受浏览器策略影响，可手动点击播放。重新运行期间隐藏上一段录像。

验证：Web 回归 94 项通过，录制异常/清理测试 3 项通过；前端组件/API 测试 7 项通过（`pnpm test`）；原 embodied_agent viewer 回归 5 项通过；前端 production build 通过。无 skipped/xfail。真实测试覆盖完整成功录像、换位姿执行的同一状态对象检查，以及感知故障后保留有效执行录像；其余失败注入测试标注为接口/异常边界测试，不冒充真实物理成功。

手工播放：上传 `examples/bracket/input.pdf` → 生成 3D → 物体 `(0.50, 0, 0°)`、目标 `(0.46, -0.09)` → 运行仿真 → 等待 MP4 显示 → 播放/暂停、拖动进度、点击“保存视频”。目标 X 改为 2 应在物理前拒绝，显示真实拒绝原因且视频不可用。
