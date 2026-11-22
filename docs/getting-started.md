# 在 Windows 上运行源码

需要 64 位 Windows、Git、可在终端使用的 Node.js 22、pnpm 11.15.1、uv 和 Python 3.12。源码预览使用本地 SQLite；Node 和 Python 依赖按仓库中的锁文件安装。

从 PowerShell 执行：

```powershell
git clone https://github.com/CurlyAhao/Quantelune.git
cd Quantelune
pnpm install --frozen-lockfile
uv sync --directory apps/api --frozen --python 3.12
.\Start-Web-Preview.cmd
```

在浏览器打开 `http://127.0.0.1:3146`，预览 API 使用 `127.0.0.1:8146`。预览实例把自己的 SQLite、日志和临时状态放在本次检出的 `q/pv` 下。结束时从同一目录运行 `.\Stop-Workspace.cmd`。桌面系统对话框和 IDE 跳转等依赖桌面壳的交互需在桌面程序中使用。

## 试用材料

[task-time.csv](../examples/task-time/task-time.csv) 有 24 行模拟任务记录。可以只下载这一份 CSV，在已安装的桌面版中打开其所在文件夹。完整操作及可核对的数值见[首页示例](../README.md#用一张表完成第一次分析)。

## 媒体组件

表格统计和人工标注可从基础源码开始。声音测量、转录、视频抽帧和视觉特征需要相应媒体运行环境。代码读取 `media-runtime.json` 以找到本地 Python、FFmpeg 和对应模型。公开仓库提供适配代码与装配脚本；模型权重、FFmpeg 可执行文件及预装 Python 环境由使用者按需准备。

[媒体装配脚本](../scripts/packaging/stage_release_runtime.py) 接受 `--python-home`、`--media-environment`、`--models`、`--ffmpeg` 和 `--output`。按脚本约定准备模型文件和 FFmpeg 的许可证文件后，可将输出放到空的 `.local/release-base`。发布自己的二进制分发前，应为该组合准备真实的第三方许可、声明及其要求的材料。

## 桌面构建

Windows x64 桌面构建还需要 Rust MSVC 工具链、Visual Studio C++ Build Tools 与 WebView2。使用前述前端依赖，再将 PyInstaller 安装到本次检出的后端环境：

```powershell
uv pip install --python apps/api/.venv/Scripts/python.exe -r apps/api/sidecar-build-requirements.txt
.\scripts\packaging\Build-Quantelune.ps1 -Part Web
.\scripts\packaging\Build-Quantelune.ps1 -Part Api
```

完整 NSIS 安装器还需要 `.local/release-base/runtime`、`media-runtime.json`、`THIRD_PARTY.txt` 和前两步的构建输出。输入齐备后运行 `.\scripts\packaging\Build-WindowsInstaller.ps1`。脚本输出安装器路径与 SHA-256。用户的研究项目和运行数据不属于打包输入。
