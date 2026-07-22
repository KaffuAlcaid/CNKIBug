# CNKIBug

> 中国知网（CNKI）论文信息批量抓取工具。Windows 提供 GUI 和终端两个独立 `.exe`；Linux 可通过源码启动 GUI 或终端版本，macOS 继续支持源码终端版。

![Python](https://img.shields.io/badge/Python-3.10--3.14-blue?logo=python)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)
![License](https://img.shields.io/badge/License-MIT-green)
![Version](https://img.shields.io/github/v/release/KaffuAlcaid/CNKIBug?color=orange&label=Version)

---

## 功能

- GUI 和终端版都支持单个或批量检索
- TXT 导入自动去重，开始前会显示任务规模和预计耗时
- 可导出 Excel 或 CSV，也可选抓取引文、关键词和摘要
- 自动保存配置、浏览器会话、日志和任务报告
- 中途可安全停止，并从最近完成页继续

---

## 运行截图

<table style="border: none;">
  <tr>
    <td style="text-align: center; vertical-align: top; width: 50%;">
      <img src="docs/1.png" alt="GUI 任务设置" style="width: 100%; aspect-ratio: 25 / 18; object-fit: contain; border: 1px solid #ddd; border-radius: 4px;"/>
      <br /><sub><b>输入关键词与设置</b></sub>
    </td>
    <td style="text-align: center; vertical-align: top; width: 50%;">
      <img src="docs/3.png" alt="GUI 抓取过程" style="width: 100%; aspect-ratio: 25 / 18; object-fit: contain; border: 1px solid #ddd; border-radius: 4px;"/>
      <br /><sub><b>GUI 抓取过程</b></sub>
    </td>
  </tr>
</table>

完整截图和操作说明见 [使用说明](docs/usage.md)。


---


## 快速开始


### 方式一：Windows 直接运行（推荐）

1. 前往 [Releases](../../releases) 页面下载 `CNKIBug-GUI.exe`，喜欢终端交互也可下载 `CNKIBug.exe`
2. 确保电脑已安装 **Microsoft Edge**（Win10/11 通常已预装）
3. 启动后输入检索项，按界面提示确认任务
4. 手动通过知网滑块验证

### 方式二：源码运行（Linux / macOS 用户或开发者）

终端版：

```bash
pip install -e .
playwright install chromium
python run.py
```

GUI 版（当前用于 Linux 源码运行和开发）：

```bash
pip install -e ".[gui]"
playwright install chromium
python run_gui.py
```

Release 中单独提供的 `source-tui.tar.gz` 只包含终端版源码，不包含 `run_gui.py` 和 `cnkibug/gui/`。

> 必须有图形桌面（X11 / Wayland）完成滑块验证，无法在纯无头服务器运行。

### 更多说明

- [使用说明](docs/usage.md)：导入、输出、进度和完整截图
- [配置说明](docs/configuration.md)：`config.json` 和常见调整
- [开发和打包](docs/development.md)：手动打包、项目结构和测试

## 系统要求

| 平台            | GUI          | 终端版          |
|---------------|--------------|--------------|
| Windows 10/11 | `.exe` 或源码运行 | `.exe` 或源码运行 |
| Linux         | 源码运行         | 源码运行         |
| macOS         | 不作为正式支持范围    | 源码运行         |

| 项目     | 要求                                                                                      |
|--------|-----------------------------------------------------------------------------------------|
| 浏览器    | Windows：Microsoft Edge（已预装或手动安装）；Linux / macOS：`playwright install chromium` 的 Chromium |
| Python | 3.10–3.14（仅源码运行需要）                                                                      |
| 图形桌面   | 所有抓取方式均需要人工通过知网滑块验证，无法在纯无头服务器运行                                                         |

---

## 不支持范围

CNKIBug 仅面向中国知网（CNKI）基础检索结果及公开详情页信息抓取，不支持 Web of Science / SCI 数据库，也不支持通过校园 WebVPN、统一认证网关或代管账号密码的方式抓取机构资源。

遇到这类访问环境时，请改用浏览器手动访问对应平台。

---

## 免责声明

CNKIBug 是独立开发的开源工具，与中国知网（CNKI）及其关联方不存在隶属、授权、合作或背书关系。

本软件仅提供自动化操作能力。使用者应确保其访问和使用行为符合所在国家和地区合法的适用的法律法规、CNKI 用户协议及所在机构的相关规定，并自行确认对相关内容访问和处理权限。

请合理控制任务规模和访问频率，本项目不以绕过任何网站的技术或商业限制为目的，不鼓励或支持任何违反服务协议或适用法律法规的使用方式。

本软件按“现状”提供，不保证 CNKI 页面长期兼容，也不保证结果完整、准确或持续可用。

因网络异常、网站变更、验证码、账号或 IP 限制、数据处理及使用本软件产生的风险，由使用者依法承担；作者在所在国家和地区合法的适用法律允许的范围内不承担相关责任。

本免责声明作为项目文档的一部分，与仓库中的 MIT License 共同适用于本项目；如两者存在冲突，以适用法律规定为准。

软件会在本地保存配置、日志、任务状态和浏览器会话信息。请妥善保管运行数据目录，并在分享日志或任务报告前检查其中是否包含敏感信息。

本软件不会主动修改、去除或规避内容版权标识，也不会授予用户访问任何受版权保护内容的权利。

用户应自行妥善保管账号凭据，不建议在非受信任环境运行本软件。

下载并使用本软件，视作您同意本免责声明和 MIT License。

---
## 致谢 / Contributors

<table style="border: none;">
  <tr>
    <td style="text-align: center; vertical-align: top; width: 200px;">
      <a href="https://github.com/KaffuAlcaid">
        <img src="https://github.com/KaffuAlcaid.png" width="80px" alt="KaffuAlcaid"/>
        <br /><sub><b>Kaffu_Alcaid</b></sub>
      </a><br />核心开发
    </td>
    <td style="text-align: center; vertical-align: top; width: 200px;">
      <a href="https://github.com/Speechlessyc">
        <img src="https://github.com/Speechlessyc.png" width="80px" alt="Speechlessyc"/>
        <br /><sub><b>Speechlessyc</b></sub>
      </a><br />图标设计 & 测试
    </td>
    <td style="text-align: center; vertical-align: top; width: 200px;">
      <a href="https://github.com/cloudw233">
        <img src="https://github.com/cloudw233.png" width="80px" alt="cloudw233"/>
        <br /><sub><b>cloudw233</b></sub>
      </a><br />自动化构建(CI/CD)
    </td>
  </tr>
  <tr>
     <td style="text-align: center; vertical-align: top; width: 200px;">
      <a href="https://github.com/zirend666-prog">
        <img src="https://github.com/zirend666-prog.png" width="80px" alt="zirend666-prog"/>
        <br /><sub><b>zirend666-prog</b></sub>
      </a><br />产品经理
    </td>
    <td style="text-align: center; vertical-align: top; width: 200px;">
      <a href="https://github.com/LuisCotton">
        <img src="https://github.com/LuisCotton.png" width="80px" alt="LuisCotton"/>
        <br /><sub><b>LuisCotton</b></sub>
      </a><br />特约吉祥物
    </td>
    <td style="text-align: center; vertical-align: top; width: 200px;">
      <a href="https://github.com/clover1909">
        <img src="https://github.com/clover1909.png" width="80px" alt="clover1909"/>
        <br /><sub><b>clover1909</b></sub>
      </a><br />可爱群友
    </td>
  </tr>
  <tr>
    <td style="text-align: center; vertical-align: top; width: 200px;">
      <img src="./logo.png" width="80px" alt="Placeholder"/>
      <br /><sub><b>虚位以待</b></sub>
      <br />欢迎提交 PR
    </td>
    <td style="text-align: center; vertical-align: top; width: 200px;">
      <img src="./logo.png" width="80px" alt="Placeholder"/>
      <br /><sub><b>虚位以待</b></sub>
      <br />欢迎提交 PR
    </td>
    <td style="text-align: center; vertical-align: top; width: 200px;">
      <img src="./logo.png" width="80px" alt="Placeholder"/>
      <br /><sub><b>虚位以待</b></sub>
      <br />欢迎提交 PR
    </td>
  </tr>
  <tr>
    <td style="text-align: center; vertical-align: top; width: 200px;">
      <a href="https://openai.com/codex/">
        <img src="https://github.com/openai.png" width="80px" alt="ChatGPT / Codex"/>
        <br /><sub><b>ChatGPT / Codex</b></sub>
      </a><br />代码改进
    </td>
      <td style="text-align: center; vertical-align: top; width: 200px;">
      <a href="https://claude.ai">
        <img src="https://github.com/claude.png" width="80px" alt="Claude"/>
        <br /><sub><b>Claude</b></sub>
      </a><br /> 代码改进
    </td>
    <td style="text-align: center; vertical-align: top; width: 200px;">
      <a href="https://gemini.google.com/">
        <img src="https://github.com/google.png" width="80px" alt="Gemini"/>
        <br />
        <sub><b>Gemini</b></sub>
      </a>
      <br />
      代码审查<br/>
    </td>
  </tr>
</table>
