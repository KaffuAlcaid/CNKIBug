# CNKIBug

面向中国知网的本地文献检索与整理工具，支持批量检索、文献筛选、Excel/CSV/RIS 导出和 Zotero 发送

[项目首页](https://github.com/KaffuAlcaid/CNKIBug) · [使用说明](https://github.com/KaffuAlcaid/CNKIBug/blob/main/docs/usage.md) · [Windows 下载](https://github.com/KaffuAlcaid/CNKIBug/releases/latest/download/CNKIBug-GUI.exe)

## 安装

推荐使用图形界面完成检索、筛选和导出

需要 Python 3.10–3.14、图形桌面和可用浏览器，建议在虚拟环境中安装

### 图形界面（推荐）

```bash
python -m pip install "cnkibug[gui]"
cnkibug-gui
```

### 终端版（可选）

```bash
python -m pip install cnkibug
cnkibug
```

Windows 优先使用 Microsoft Edge，Linux 优先使用系统 Chrome 或 Chromium；Linux GUI 需要 Tk，Debian/Ubuntu 可安装 `python3-tk`

浏览器检查和安装方法见[配置说明](https://github.com/KaffuAlcaid/CNKIBug/blob/main/docs/configuration.md)，macOS 目前不在正式支持范围

## 更新

```bash
python -m pip install --upgrade "cnkibug[gui]"
```

仅使用终端版时，将命令中的 `"cnkibug[gui]"` 换为 `cnkibug`

## 使用与数据

知网安全验证需要手动完成，PDF 下载需要相应的全文访问权限

文献保存目录由用户选择，配置和登录状态的存放位置见[配置说明](https://github.com/KaffuAlcaid/CNKIBug/blob/main/docs/configuration.md#配置与数据位置)

本项目采用 MIT License，与中国知网及其关联方无隶属或合作关系，请遵守适用法律、网站协议和所在机构规定
