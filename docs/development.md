# 开发和打包

[返回首页](../README.md)

## 手动打包 Windows exe

```bash
pip install -e ".[gui]" -r requirements-build.txt
python generate_version_info.py version.txt
pyinstaller --onefile --windowed --icon=icon.ico --version-file=version.txt --copy-metadata cnkibug --copy-metadata ttkbootstrap --collect-all ttkbootstrap --add-data "icon.ico:." --add-data "cnkibug/gui/apply_update.ps1:cnkibug/gui" --name CNKIBug-GUI run_gui.py
```

生成文件位于 `dist/CNKIBug-GUI.exe`。

## Linux AppImage

构建环境为 Ubuntu 22.04 x86-64、Python 3.12，使用 appimagetool 1.9.1。

```bash
sudo apt-get install python3-tk libfuse2 squashfs-tools desktop-file-utils
python -m pip install ".[gui]" -r requirements-build.txt
curl -fL https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage -o /tmp/cnkibug-appimagetool
chmod +x /tmp/cnkibug-appimagetool
APPIMAGETOOL=/tmp/cnkibug-appimagetool bash scripts/build_appimage.sh
```

生成文件位于 `dist/CNKIBug-GUI-x86_64.AppImage`。包内包含 Python、Tk、程序依赖和 Playwright 安装器；Chromium 在初始化设置中下载到用户缓存。使用 PyInstaller 目录模式、移除调试符号和 xz 压缩，脚本输出主要目录大小及最终体积，超过 100 MiB 时给出提示。

AppImage 通过 `APPIMAGE` 确定原文件位置，用户数据使用 `XDG_DATA_HOME` 或 `~/.local/share/CNKIBug-data/`。`--install-system-deps` 调用包内 Playwright 的系统组件安装器，由用户在终端执行。

`.github/workflows/build.yml` 构建 Windows GUI EXE、Linux AppImage 和终端版源码包，完成入口检查后上传到对应 Release。Linux 在 Ubuntu 22.04 上构建。

## 更新清单

`build.yml` 在 Release 附件上传完成后运行 `scripts/publish_update.py`，将最新正式发布的版本、说明、Windows EXE 和 Linux AppImage 的下载地址、大小和 SHA-256 写入 `updates` 分支的 `latest.json`。GUI 按当前平台选择附件。

GUI 通过 `https://cdn.jsdmirror.com/gh/KaffuAlcaid/CNKIBug@updates/latest.json` 读取清单。仓库 Secret `JSDMIRROR_API_KEY` 用于请求主动缓存刷新。

## 项目结构

```text
CNKIBug/
├── run.py                  # 终端版入口
├── run_gui.py              # GUI 版入口
├── cnkibug/
│   ├── app/                # 菜单、配置、运行环境和控制台界面
│   ├── gui/                # ttkbootstrap 图形界面
│   ├── browser/            # 浏览器生命周期与会话缓存
│   ├── cnki/               # CNKI 页面操作与单关键词抓取
│   ├── core/               # 界面无关的核心模型与接口
│   ├── fileio/             # 文件输入输出
│   └── workflow/           # 多关键词任务编排与收尾
├── CNKIBug-data/           # 运行时数据目录
│   ├── config.json         # 用户配置
│   ├── cache/              # 会话与断点缓存
│   ├── log/                # 运行日志
│   └── status/             # JSON 任务报告
├── tests/
├── pyproject.toml
└── .github/workflows/
```

主要文件：

- `cnkibug/app/cli.py`：终端菜单和任务循环
- `cnkibug/gui/app.py`：GUI 窗口、表单和任务控制
- `cnkibug/gui/environment.py`：Linux 初始化设置和设置中的运行环境检查
- `cnkibug/browser/environment.py`：浏览器选择、运行环境检查和 Chromium 安装
- `cnkibug/browser/`：浏览器启动、上下文和会话缓存
- `cnkibug/cnki/`：搜索、解析、翻页、详情和引文
- `cnkibug/core/`：设置、事件、耗时估算、内存采样和运行路径
- `cnkibug/fileio/`：导入、导出和输出目录
- `cnkibug/workflow/`：任务状态、断点、保存和报告

## 测试

```bash
pytest -q
python run.py --self-check
python run_gui.py --self-check
```

测试覆盖浏览器启动、页面解析、翻页、导出、断点、GUI 事件、CLI 进度、内存采样、配置和分层依赖。
