# 开发和打包

[返回首页](../README.md)

## 手动打包 Windows exe

```bash
pip install -e ".[gui]" -r requirements-build.txt
python generate_version_info.py version.txt
pyinstaller --onefile --console --version-file=version.txt --copy-metadata cnkibug --name CNKIBug run.py
pyinstaller --onefile --windowed --icon=icon.ico --version-file=version.txt --copy-metadata cnkibug --copy-metadata ttkbootstrap --collect-all ttkbootstrap --add-data "icon.ico:." --name CNKIBug-GUI run_gui.py
```

生成文件位于 `dist/CNKIBug.exe` 和 `dist/CNKIBug-GUI.exe`。

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
├── CNKIBug/                # 运行时数据目录
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
