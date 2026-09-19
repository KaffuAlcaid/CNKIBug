# CNKIBug

中国知网论文信息抓取工具，提供 GUI 和终端入口，支持普通检索、GUI 高级检索、Excel/CSV 导出、引文及详情提取、停止和断点续抓。

## 安装和启动

Python 3.10 至 3.14，需要图形桌面和浏览器完成知网的人工验证。

```bash
python -m pip install "cnkibug[gui]"
cnkibug-gui
```

Windows 优先使用 Microsoft Edge，Linux 优先使用系统 Chrome、Chromium。需要下载浏览器时，可在设置的运行环境中操作，或运行 `python -m playwright install chromium`。

Linux 需要 Python Tk 支持。Debian/Ubuntu 可通过系统包管理器安装 `python3-tk`；建议在虚拟环境中安装 Python 依赖。Fedora 可通过 `sudo dnf install chromium` 安装系统浏览器及其组件。

终端入口：

```bash
python -m pip install cnkibug
cnkibug
```

Windows 用户可直接从 [GitHub Releases](https://github.com/KaffuAlcaid/CNKIBug/releases) 下载单文件 GUI EXE。

## 数据和更新

通过安装命令启动时，数据存放位置为：

- Linux：`$XDG_DATA_HOME/CNKIBug-data`，默认 `~/.local/share/CNKIBug-data`
- Windows：`%LOCALAPPDATA%/CNKIBug-data`
- macOS：`~/Library/Application Support/CNKIBug-data`，当前不在正式支持范围

导出目录由用户选择。通过源码入口或便携 EXE 启动时，仍使用启动文件旁的 `CNKIBug-data`。

Python 包通过 `python -m pip install --upgrade "cnkibug[gui]"` 升级；GUI 中的 EXE 自动替换仅适用于 Windows 打包版。

## 使用范围

项目与中国知网不存在隶属、授权或合作关系。抓取需要可直接访问知网的网络和图形桌面，验证码由用户手动完成。请遵守网站协议和所在机构规定，合理控制访问频率。

GUI 的 PDF 下载为实验性功能，需要对应文献的全文访问权限。WebVPN 下载目前支持 `www-cnki-net-443.<学校域名>`、`kns-cnki-net-443.<学校域名>` 形式的知网入口；对于地址中含 `/https/编码/` 的门户和其他 WebVPN 类型，请在学校 WebVPN 浏览器页面中手动下载 PDF。

项目按 MIT License 提供，不保证网站长期兼容或结果完整。完整说明见 [项目仓库](https://github.com/KaffuAlcaid/CNKIBug)。
