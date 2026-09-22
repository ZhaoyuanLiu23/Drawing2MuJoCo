# 在 PyCharm 中运行 Panda 抓球动画

运行入口是 **`panda_grasp.py`**。它读取你电脑上的 Franka Emika Panda 原模型，完成靠近、下降、夹球、抬起、搬运到绿色托盘、松开和归位，完整过程约 23.5 秒。

## 启动步骤

1. 在 PyCharm 中打开 `C:\Users\86155\Documents\ChatGPT\仿真` 文件夹。
2. 选择已经安装 `mujoco`、`numpy`、`matplotlib` 的解释器。本机已验证的解释器是 `C:\Users\86155\Desktop\python\python.exe`。
3. 在左侧项目列表里右键 **`panda_grasp.py` → Run 'panda_grasp'**，不需要设置运行参数。
4. 等待几秒规划轨迹，随后会打开 Panda 抓球动画窗口。

窗口中：**空格**暂停/继续，**R**重新开始，鼠标左键拖动旋转视角，滚轮缩放。关闭窗口后保存最后一次动作的结果和轨迹图。

也可以在 PyCharm 的 Terminal 中执行：

```powershell
cd "C:\Users\86155\Documents\ChatGPT\仿真"
python panda_grasp.py
```

## 模型路径和文件关系

脚本顶部已设置你的实际模型路径：

```python
MODEL_PATH = Path(r"C:\Users\86155\Desktop\mujoco_menagerie\franka_emika_panda\scene.xml")
```

请保留该目录下的 `scene.xml`、`panda.xml` 和整个 `assets` 文件夹。脚本还会导入本项目的 `pick_and_place.py` 以复用兼容窗口和结果输出，因此两个 Python 文件需放在同一个目录中。

模型文件通过 Python 读取，网格交给 MuJoCo 虚拟文件系统加载，支持中文路径。小球、工作台和托盘在内存中加入，桌面上的原模型文件不作修改。

## 可选命令

```powershell
python panda_grasp.py --speed 0.5
python panda_grasp.py --headless
python panda_grasp.py --gif
python panda_grasp.py --plot
```

分别用于慢速播放、无窗口验证、导出 GIF，以及关闭动画后显示 Matplotlib 轨迹图。结果保存在 `outputs/panda_grasp/`，包括 `result.json`、`trajectory.csv` 和 `trajectory.png`；导出 GIF 后还有 `pick_and_place.gif` 与 `preview.png`。

## 控制方式

使用原模型的 7 个机械臂关节、双指夹爪、网格和质量惯量。前 7 个执行器分别接收逆运动学求得的关节角，第 8 个执行器单独控制夹爪：**255 张开，0 闭合**。

演示为机器人各运动刚体启用理想重力补偿，并为原指垫调整接触摩擦参数。小球始终是受重力、碰撞和摩擦作用的自由刚体；没有球与夹爪之间的 weld 约束，也不在运行中改写球的位置。原模型已有的两指同步关节约束保留。

默认使用 MuJoCo 离屏渲染与 Tk 兼容窗口，避开本机原生 Viewer 的显示异常。该窗口仍需要可用的 OpenGL；本机已经验证离屏渲染可用。

参考：[Menagerie Panda 模型](https://github.com/google-deepmind/mujoco_menagerie/tree/main/franka_emika_panda)、[MuJoCo Python API](https://mujoco.readthedocs.io/en/3.3.7/python.html)。
