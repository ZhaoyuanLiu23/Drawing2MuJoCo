# 安装与运行

CAD 推荐 64 位 Python 3.12，运行 `setup_drawing2cad.cmd`，或建立 `.venv-drawing2cad` 后安装 `requirements.txt`。保持原有 Panda 环境独立。

## PyCharm

| 项目 | 配置 |
| --- | --- |
| Script path | 根目录 `drawing2cad.py` |
| Working directory | 仓库根目录 |
| Interpreter | `.venv-drawing2cad\Scripts\python.exe` |
| Parameters | `"examples\bracket\input.pdf" --output "outputs\bracket" --open` |

已有其他解释器可运行根启动器，它会转交 CAD 环境。路径带空格时加引号；Python 路径中的下划线不需要转义。

代码包含 Linux/macOS 的 `bin/python` 路径支持，但本次验收来自 Windows，不能声称其他平台已验证。完整依赖锁定清单来自 Windows/Python 3.12。

## MuJoCo

已有环境可继续使用，或新建环境安装 `requirements-simulation.txt`。`pick_and_place.py --headless` 不需要外部资产。交互、GIF 和视频需要可工作的 OpenGL/图形驱动；纯物理测试成功不等于渲染可用。

Panda 要保留完整 Menagerie 模型目录，在对应仿真解释器运行：

```cmd
python panda_grasp.py --model "D:\models\mujoco_menagerie\franka_emika_panda\scene.xml" --headless
```

生产脚本保留开发机默认路径，以免影响现有运行配置。跨机器使用 `--model`，不要照搬本机路径；模型网格不在本仓库中。

`export_video.py` 另需系统 ffmpeg 或 `imageio-ffmpeg`，用于简化机械臂视频。可选编码器不影响 CAD 或纯物理测试。详细选项见 [DRAWING2CAD.md](../DRAWING2CAD.md)。
