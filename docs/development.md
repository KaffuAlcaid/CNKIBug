# 开发和打包

[返回首页](../README.md) · [使用说明](usage.md)

快速导航：[源码运行](#从源码运行) · [项目结构](#项目结构) · [测试](#测试与启动检查) · [Windows 打包](#windows-打包) · [AppImage](#linux-appimage) · [更新清单](#发布与更新清单)

## 从源码运行

需要 Python 3.10–3.14，GUI 还需要 Tk，Linux 的 Debian/Ubuntu 可通过系统包管理器安装 `python3-tk`

```bash
git clone https://github.com/KaffuAlcaid/CNKIBug.git
cd CNKIBug
python -m venv .venv
```

激活虚拟环境，Windows PowerShell 使用：

```powershell
.\.venv\Scripts\Activate.ps1
```

Linux 使用：

```bash
source .venv/bin/activate
```

安装 GUI 和开发依赖并启动：

```bash
python -m pip install -e ".[gui]" -r requirements-dev.txt
python run_gui.py
```

终端入口为 `python run.py`，浏览器选择和安装方法见[运行环境](configuration.md#运行环境)

### 更新源码

结束当前任务后，在项目目录拉取代码并更新依赖：

```bash
git pull --ff-only
python -m pip install -e ".[gui]" -r requirements-dev.txt
```

如果工作区有本地修改，先处理这些修改，再拉取远端代码

## 项目结构

| 位置 | 负责的内容 |
| --- | --- |
| `run.py` / `run_gui.py` | 终端版和 GUI 启动入口 |
| `cnkibug/app/` | 终端菜单、运行配置和应用初始化 |
| `cnkibug/gui/` | 主窗口、任务表单、结果窗口、设置和更新 |
| `cnkibug/browser/` | 浏览器选择、启动和会话保存 |
| `cnkibug/cnki/` | 检索、页面解析、翻页、详情、引文和下载 |
| `cnkibug/core/` | 检索条件、设置、事件、耗时估算和内存统计 |
| `cnkibug/fileio/` | 文件导入导出、检索方案和 Zotero 发送 |
| `cnkibug/workflow/` | 任务执行、断点、结果保存和报告 |
| `tests/` | 自动化测试 |
| `.github/workflows/` | 测试、构建和发布流程 |

GUI 的主要入口是 `cnkibug/gui/app.py`，任务表单位于 `task_form.py`，进度与日志位于 `task_progress.py`，文献列表位于 `results.py`

耗时操作在工作线程中执行，通过事件队列通知界面，Tk 控件由主线程更新

## 测试与启动检查

项目使用 pytest，部分测试需要 Playwright 浏览器，安装开发依赖后可运行：

```bash
python -m playwright install chromium
python -m pytest -q
```

Linux 如缺少浏览器系统组件，可按 Playwright 提示安装，Ubuntu/Debian 可使用 `python -m playwright install --with-deps chromium`

启动检查命令分别检查终端版和 GUI 所需的导入与资源：

```bash
python run.py --self-check
python run_gui.py --self-check
```

`run_gui.py --self-check-browser` 会实际创建 Tk 窗口、启动浏览器、打开本地页面并退出，需要可用的图形桌面

## Windows 打包

在项目根目录运行：

```powershell
python -m pip install -e ".[gui]" -r requirements-build.txt
python generate_version_info.py version.txt
pyinstaller --onefile --windowed --icon=icon.ico --version-file=version.txt --copy-metadata cnkibug --copy-metadata ttkbootstrap --collect-all ttkbootstrap --add-data "icon.ico:." --add-data "cnkibug/gui/apply_update.ps1:cnkibug/gui" --name CNKIBug-GUI run_gui.py
```

产物为 `dist/CNKIBug-GUI.exe`

## Linux AppImage

构建环境使用 Ubuntu 22.04 x86-64、Python 3.12 和 appimagetool 1.9.1

```bash
sudo apt-get install python3-tk libfuse2 squashfs-tools desktop-file-utils
python -m pip install ".[gui]" -r requirements-build.txt
curl -fL https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage -o /tmp/cnkibug-appimagetool
chmod +x /tmp/cnkibug-appimagetool
APPIMAGETOOL=/tmp/cnkibug-appimagetool bash scripts/build_appimage.sh
```

产物为 `dist/CNKIBug-GUI-x86_64.AppImage`，包含 Python、Tk、程序依赖和浏览器安装器，浏览器本体使用系统安装或按需下载

构建采用 PyInstaller 目录模式和 zstd 压缩，脚本会输出主要目录大小和最终体积，超过 100 MiB 时给出提示

| 参数 | 用途 |
| --- | --- |
| `--self-check` | 检查导入和打包资源 |
| `--self-check-browser` | 检查 Tk 窗口及真实浏览器启动 |
| `--install-browser` | 安装 Chromium |
| `--install-system-deps` | Ubuntu/Debian 安装浏览器系统组件，Fedora 显示系统浏览器安装指引 |

AppImage 通过 `APPIMAGE` 定位自身，运行数据位置见[配置说明](configuration.md#配置与数据位置)

## 发布与更新清单

版本号由 `pyproject.toml` 管理，Windows 版本资源通过 `generate_version_info.py` 生成，发布标签采用 `vX.Y.Z`

`.github/workflows/build.yml` 构建 Windows EXE、Linux AppImage 和终端版源码包，并检查打包资源与导入

Release 附件上传后，`scripts/publish_update.py` 将正式版本号、说明、附件地址、大小和 SHA-256 写入 `updates` 分支的 `latest.json`

GUI 通过 GitHub Release API 获取最新正式发布，与本地版本比较后按平台选择附件；安装包使用所选下载线路，下载地址固定到本次检查到的版本，校验失败时停止更新

仓库 Secret `JSDMIRROR_API_KEY` 用于请求 CDN 缓存刷新，更新清单地址为：

```text
https://cdn.jsdmirror.com/gh/KaffuAlcaid/CNKIBug@updates/latest.json
```

程序更新后，主窗口成功启动时清理对应的旧程序备份和已完成的更新临时文件
