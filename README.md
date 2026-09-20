# CNKIBug

> 中国知网（CNKI）论文信息批量抓取工具。Windows 使用单文件 GUI `.exe`；Linux GUI 支持实验性 AppImage 和源码运行，也可运行终端版；macOS 可尝试通过源码运行终端版，当前不在正式支持范围。

![Python](https://img.shields.io/badge/Python-3.10--3.14-blue?logo=python)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux-lightgrey)
![License](https://img.shields.io/badge/License-MIT-green)
![Version](https://img.shields.io/github/v/release/KaffuAlcaid/CNKIBug?color=orange&label=Version)

---

## 功能

- GUI 和终端版都支持单个或批量检索
- 高级检索（实验性）：在图形界面中组合多组检索条件，可与普通检索混合执行
- TXT 导入自动去重，开始前会显示任务规模和预计耗时
- 可导出 Excel 或 CSV，包含标题、作者、来源、日期、DOI 等信息，也可选采集引文、关键词和摘要
- GUI 提供独立论文结果窗口，可搜索、筛选、勾选论文，打开历史 Excel/CSV 文件
- 可保存和载入检索方案，对所选论文补抓缺失详情，并关联手动下载的 PDF
- 所选论文可导出为 Excel、CSV 或 Zotero 文献文件，保存到主窗口设置的目录
- Zotero 直接发送、期刊信息查询属于实验性功能；发送结果和期刊信息可在论文结果窗口查看
- PDF 下载（实验性）：下载所选论文的 PDF，需具备对应文献的机构或个人全文访问权限
- 自动保存配置、浏览器会话、日志和任务报告
- 中途可安全停止，并从最近完成页继续
- GUI 可手动检查更新、选择下载加速线路并测试连接

## 实验性功能说明

高级检索、PDF 下载、Zotero 直接发送、期刊信息查询和 Linux AppImage 属于实验性功能。建议先进行小规模尝试，核对结果后再扩大任务范围。

### Linux AppImage

不同 Linux 发行版的浏览器和系统组件可能存在兼容差异。程序优先使用系统 Chrome、Chromium，也可下载所需浏览器；遇到启动问题时，可在“设置 → 运行环境”查看具体原因和安装指引。

### 高级检索

支持组合多个检索条件。使用前请核对条件设置，并检查知网页面上的检索结果是否符合预期。

### PDF 下载

下载需要对应文献的有效全文访问权限。机构用户请按学校或机构提供的方式完成授权。WebVPN 下载目前支持 `www-cnki-net-443.<学校域名>`、`kns-cnki-net-443.<学校域名>` 形式的知网入口，需填写完整网址并完成登录。对于地址中含 `/https/编码/` 的门户及其他 WebVPN 类型，请在学校 WebVPN 浏览器页面中手动下载 PDF。

下载过程中可能出现安全验证、权限提示或下载中断。请留意浏览器窗口，及时手动处理验证，并按程序提示继续。遇到反复验证时，建议停止本批任务，稍后再试。

PDF 保存在主窗口设置的目录，每篇论文的下载结果可在论文列表中查看。

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

1. 前往 [Releases](../../releases) 页面下载 `CNKIBug-GUI.exe`
2. 确保电脑已安装 **Microsoft Edge**（Win10/11 通常已预装）
3. 在任务列表中直接输入关键词；批量检索可点击“+ 普通检索项”或导入 TXT，然后点击“检查并开始检索”
4. 手动通过知网滑块验证
5. 抓取完成后，选择是否展示论文详情；Excel 或 CSV 会保存到主窗口设置的目录

### 查看、导出与下载论文

点击主窗口的“论文结果”可查看已抓取的论文，也可通过“打开文件”读取以前保存的 Excel 或 CSV。上方列表用于搜索、筛选和勾选，下方显示所选论文的摘要、关键词、DOI 等信息。

勾选论文后，可同时勾选 **Excel、CSV、RIS**，再点击“导出所选”。各格式文件自动保存在主窗口设置的目录，RIS 文件可通过 Zotero 的“文件 → 导入”添加到文献库。

下载 PDF 时，先连接学校或机构提供的访问网络，或使用具有全文权限的个人账号。点击“下载 PDF”后默认等待 **60 秒**，准备好后可点击“立即继续”。PDF 自动保存到主窗口设置的目录，下载结果可在论文列表中查看。

首页等待时间可在“设置 → 会话 → 下载前首页等待（秒）”中设置。

使用上述网址形式的学校 WebVPN 时，在下载确认框点击“使用机构 WebVPN 登录”，填入学校提供的完整知网访问网址。完成网页登录后，回到论文结果窗口点击“登录完成，继续”。

### 方式二：Linux AppImage（实验性，x86-64）

在 [Releases](../../releases) 中选择附带 `CNKIBug-GUI-x86_64.AppImage` 的版本，下载后在文件所在目录运行：

```bash
chmod +x CNKIBug-GUI-x86_64.AppImage
./CNKIBug-GUI-x86_64.AppImage
```

首次启动时，在初始化设置中选择论文保存目录。程序优先检查系统 Chrome、Chromium；需要下载浏览器时，可点击“安装 Chromium”。若检查发现缺少系统组件，请按页面给出的发行版安装指引处理，再点击“开始检查”。检查通过后点击“开始使用”。完成初始化后，后续启动直接进入主窗口。

AppImage 自带 Python 运行环境。配置、登录状态和任务记录保存在 `~/.local/share/CNKIBug-data/`，通过程序下载的浏览器保存在 `~/.cache/ms-playwright/`；论文文件保存在主窗口设置的目录。

Windows 和 Linux 都可通过“设置 → 运行环境 → 开始检查”查看浏览器、程序组件和保存目录的检查结果。

### 方式三：源码运行（Linux / macOS 用户或开发者）

先取得完整源码并进入项目根目录：

```bash
git clone https://github.com/KaffuAlcaid/CNKIBug.git
cd CNKIBug
```

也可下载 GitHub 提供的 Source code 压缩包，解压后进入项目根目录。

#### Linux 终端版

```bash
pip install -e .
python run.py
```

#### Linux GUI 版

```bash
pip install -e ".[gui]"
python run_gui.py
```

Linux 可使用已安装的系统 Chrome、Chromium。需要下载浏览器时，可在 GUI 的运行环境中点击“安装 Chromium”，或在终端执行 `playwright install chromium`。

#### macOS 终端版（非正式支持）

```bash
pip install -e .
playwright install chromium
python run.py
```

macOS 当前未纳入正式测试和支持范围。Release 中单独提供的 `CNKIBug-<版本>-source-tui.tar.gz` 只包含终端版源码，不包含 `run_gui.py` 和 `cnkibug/gui/`。

浏览器启动并完成滑块验证后，程序会将 Excel 或 CSV 写入任务中选择的保存目录；配置、日志和任务报告保存在项目根目录的 `CNKIBug-data/`。

> 源码运行需要可用的图形桌面完成滑块验证，Linux 使用 X11 / Wayland；无法在纯无头服务器运行。

### 更多说明

- [使用说明](docs/usage.md)：导入、输出、进度和完整截图
- [配置说明](docs/configuration.md)：`config.json` 和常见调整
- [开发和打包](docs/development.md)：项目结构、GUI 模块职责、打包和测试

## 系统要求

| 平台            | GUI          | 终端版          |
|---------------|--------------|--------------|
| Windows 10/11 | `.exe` 或源码运行 | 源码运行 |
| Linux         | 实验性 x86-64 AppImage 或源码运行 | 源码运行 |
| macOS         | 不支持          | 源码运行（非正式支持）  |

| 项目     | 要求                                                                                      |
|--------|-----------------------------------------------------------------------------------------|
| 浏览器    | Windows：Microsoft Edge；Linux：系统 Chrome、Chromium 或由 Playwright 下载的 Chromium |
| Python | 3.10–3.14（仅源码运行需要）                                                                      |
| 图形桌面   | 所有抓取方式均需要人工通过知网滑块验证，无法在纯无头服务器运行                                                         |

---

## 使用环境

CNKIBug 用于中国知网的论文检索与资料整理。使用前，请确认浏览器能够正常访问知网；下载全文需要对应文献的有效访问权限。机构用户按学校或机构提供的方式接入访问网络，并给知网首页留出完成授权的时间。

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
