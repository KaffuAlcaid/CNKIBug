# CNKIBug 

> 中国知网（CNKI）论文标题批量爬取工具。Windows 下可打包为独立 `.exe` 开箱即用，无需安装任何环境；Linux / macOS 可通过源码运行。

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)
![License](https://img.shields.io/badge/License-MIT-green)
![Version](https://img.shields.io/badge/Version-0.2.1-orange)

---

##  功能特性

-  输入关键词，自动批量抓取知网论文标题，支持单关键词和多关键词抓取模式
-  结果自动导出为 `.xlsx` Excel 文件，保存至桌面，用户可以选择多关键词保存策略
-  优先调用系统自带的 **Microsoft Edge**（Windows）；找不到时自动回退到 Playwright 的 Chromium，故 Linux / macOS 亦可运行
-  首次运行会在程序旁创建 `CNKIBug/config.json`、`CNKIBug/cache/`、`CNKIBug/log/`，用于保存配置、会话缓存和日志
-  支持复用 `CNKIBug/cache/cookies` 中的浏览器会话状态，默认 12 小时有效，可降低重复验证码概率
-  抓取过程记录关键行为日志，结束时输出任务摘要、失败原因和字段完整性统计
-  完善的错误提示，缺少环境时弹出友好的引导窗口
-  抓取中途按 `Ctrl+C` 或关闭浏览器可安全中止，已抓取数据不丢失
-  可打包为单文件 `.exe`，双击即用，无需 Python 环境

---

##  运行截图 

<table style="border: none;">
  <tr>
    <td style="text-align: center; vertical-align: top; width: 50%;">
      <img src="docs/1.png" alt="演示" style="max-width: 100%; border: 1px solid #ddd; border-radius: 4px;"/>
      <br /><sub><b>启动演示1</b></sub>
    </td>
    <td style="text-align: center; vertical-align: top; width: 50%;">
      <img src="docs/2.png" alt="演示" style="max-width: 100%; border: 1px solid #ddd; border-radius: 4px;"/>
      <br /><sub><b>2.输入关键词与设置</b></sub>
    </td>
  </tr>
  <tr>
    <td style="text-align: center; vertical-align: top; width: 50%;">
      <img src="docs/3.png" alt="演示" style="max-width: 100%; border: 1px solid #ddd; border-radius: 4px;"/>
      <br /><sub><b>3. 抓取完成</b></sub>
    </td>
    <td style="text-align: center; vertical-align: top; width: 50%;">
      <img src="docs/4.png" alt="演示" style="max-width: 100%; border: 1px solid #ddd; border-radius: 4px;"/>
      <br /><sub><b>4. 抓取完成，结果保存至桌面</b></sub>
    </td>
  </tr>
</table>


---


##  快速开始


### 方式一：直接运行（推荐）

1. 前往 [Releases](../../releases) 页面下载最新的 `CNKIBug.exe`
2. 确保电脑已安装 **Microsoft Edge**（Win10/11 通常已预装）
3. 双击 `CNKIBug.exe`，按提示输入关键词和页数即可
4. 请注意：**一定要手动通过知网的滑块人机验证**

首次运行后，程序会在 `CNKIBug.exe` 同目录创建 `CNKIBug/` 运行数据目录。`config.json` 可调整超时、日志和会话缓存参数；`cache/cookies` 保存浏览器会话状态，默认 12 小时后过期并重建；`log/` 保存运行日志。

> 如提示未找到 Edge，请访问 https://www.microsoft.com/zh-cn/edge/download 下载安装。

### 方式二：源码运行（Linux / macOS 用户，或开发者）

> Windows 用户建议直接用方式一的 `.exe`；**Linux / macOS 用户请使用本方式**。

```bash
# 1. 安装依赖（pywin32 已标记为仅 Windows 安装，Linux / macOS 会自动跳过）
pip install -r requirements.txt
# 或手动：pip install playwright openpyxl rich

# 2. 安装浏览器内核
playwright install chromium

# 3. 从仓库根目录运行入口脚本
python run.py
```

> **必须有图形桌面**（X11 / Wayland）：知网会弹出滑块验证，需要人工手动通过，
> 因此**无法在纯无头（headless）服务器上运行**。
> 结果 `.xlsx` 保存到当前用户桌面目录（中文桌面会正确识别为 `~/桌面`）。

### 方式三：自行打包为 exe

```bash
pip install pyinstaller
# 入口为 run.py，PyInstaller 会自动跟随 import 把整个 cnkibug/ 包收进单 exe
pyinstaller --onefile --console --name CNKIBug run.py
# 生成文件在 dist/CNKIBug.exe
```

---

## 配置文件说明

首次运行后，程序会在运行目录旁创建：

```text
CNKIBug/config.json
```

修改配置后请重新启动程序。`config.json` 是标准 JSON 文件，不支持 `//` 或 `#` 注释。

```json
{
  "version": 1,
  "timeout_goto_ms": 30000,
  "timeout_load_ms": 20000,
  "timeout_selector_ms": 15000,
  "verify_wait_timeout_sec": 180,
  "verify_notice_interval_sec": 15,
  "max_advance_fail": 2,
  "session_cache_enabled": true,
  "session_cache_ttl_hours": 12,
  "log_level": "INFO",
  "log_save_path": true,
  "log_keywords": false,
  "log_scraped_records": false
}
```

| 参数                           | 默认值      | 可填值                                | 作用                                   | 什么时候改                                     |
|------------------------------|----------|------------------------------------|--------------------------------------|-------------------------------------------|
| `version`                    | `1`      | 正整数                                | 配置文件版本号，程序内部用于识别配置结构                 | 不建议手动改                                    |
| `timeout_goto_ms`            | `30000`  | 正整数，毫秒                             | 打开 CNKI 页面时最多等待多久                    | 网络慢、页面经常打不开时调大，例如 `60000`                 |
| `timeout_load_ms`            | `20000`  | 正整数，毫秒                             | 等页面加载状态时最多等待多久                       | 页面能打开但加载慢时调大                              |
| `timeout_selector_ms`        | `15000`  | 正整数，毫秒                             | 等搜索框、结果表格、下一页按钮等页面元素出现的时间            | 经常提示找不到结果或按钮时调大                           |
| `verify_wait_timeout_sec`    | `180`    | 正整数，秒                              | 出现知网滑块/安全验证后，程序最多等你操作多久              | 来不及处理验证码时调大 例如 `300`                      |                     |
| `verify_notice_interval_sec` | `15`     | 正整数，秒                              | 等验证码期间，每隔多久提醒一次                      | 想少刷提示就调大                                  |
| `max_advance_fail`           | `2`      | 正整数                                | 翻页后连续几次没确认页面变化，就提前结束当前关键词            | 网络不稳可调大；不建议太大，避免重复空转                      |
| `session_cache_enabled`      | `true`   | `true` / `false`                   | 是否复用 `CNKIBug/cache/cookies` 中的浏览器会话 | 验证码太频繁建议保持开启；会话异常时可改为 `false` 或删除 cookies |
| `session_cache_ttl_hours`    | `12`     | 正整数，小时                             | Cookie 会话缓存多久后过期                     | 想更久复用会话可调大；想更频繁刷新会话可调小                    |
| `log_level`                  | `"INFO"` | `"INFO"` / `"WARNING"` / `"ERROR"` | 日志详细程度                               | 反馈问题时用 `INFO`；只想少写日志可用 `WARNING`          |
| `log_save_path`              | `true`   | `true` / `false`                   | 日志里是否记录导出文件路径                        | 不想在日志里暴露用户名或目录结构时改为 `false`               |
| `log_keywords`               | `false`  | `true` / `false`                   | 日志里是否记录关键词                           | 需要排查具体关键词问题时临时开启                          |
| `log_scraped_records`        | `false`  | `true` / `false`                   | 日志里是否记录更详细的抓取记录统计                    | 深度排查抓取异常时临时开启，不建议长期打开                     |

### 常见调整

- 网络慢：把 `timeout_goto_ms`、`timeout_load_ms`、`timeout_selector_ms` 适当调大。
- 验证码来不及处理：把 `verify_wait_timeout_sec` 调大。
- 会话状态异常：删除 `CNKIBug/cache/cookies`，或把 `session_cache_enabled` 改为 `false` 后重启。
- 不想日志记录本机路径：把 `log_save_path` 改为 `false`。

---

## 系统要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows 10 / 11；或带图形桌面的 Linux / macOS（源码运行） |
| 浏览器 | Windows：Microsoft Edge（预装或手动安装）；Linux / macOS：`playwright install chromium` 的 Chromium |
| Python | 3.10+（源码运行需要） |
| 图形界面 | 必需 —— 需人工通过知网滑块验证，无法在纯无头服务器运行 |

---

## 不支持范围

CNKIBug 仅面向中国知网（CNKI）基础检索结果标题抓取，不支持 Web of Science / SCI 数据库，也不支持通过校园 WebVPN、统一认证网关或代管账号密码的方式抓取机构资源。遇到这类访问环境时，请改用浏览器手动访问对应平台。

---

##  项目结构

```
CNKIBug/
├── run.py                  # 程序入口（PyInstaller 打包入口、依赖守卫、主菜单循环）
├── cnkibug/                # 核心包
│   ├── __init__.py
│   ├── errors.py           # 错误弹窗（仅依赖标准库）
│   ├── ui.py               # 共享的 rich Console 单例
│   ├── environment.py      # 平台/环境检测（Edge 检查、桌面路径）
│   ├── exporter.py         # 结果导出（xlsx、文件名清洗、三种保存模式）
│   ├── estimate.py         # 抓取耗时估算
│   ├── runtime.py          # 运行数据目录、config.json、文件日志初始化
│   ├── settings.py         # 抓取配置映射
│   ├── session_cache.py    # 浏览器会话缓存（cache/cookies）
│   ├── scrape_logging.py   # 抓取日志辅助与字段缺失统计
│   ├── scrape_report.py    # 任务摘要、失败报告与字段完整性统计
│   ├── window.py           # 浏览器窗口置顶（主要用于验证码提示）
│   └── scraper.py          # 核心抓取与多关键词编排
├── requirements.txt        # 依赖清单（playwright / openpyxl / rich）
├── icon.ico                # 打包图标
├── version.txt             # exe 版本信息
├── README.md
├── .github/
│   └── workflows/
│       └── build.yml       # CI：在 Windows 上构建并发布 exe
└── dist/
    └── CNKIBug.exe         # 打包产物（不纳入版本管理）
```

> 0.1.6 起代码由单文件重构为上述多文件包结构，行为保持不变；打包入口随之由
> `CNKIBug.py` 改为 `run.py`。

---

##  版本规划
**0.1.x阶段：**
- [x] 1.无限续杯:当前检索并保存完毕后，程序直接结束，需重新双击运行才能进行下一次检索 
- [x] 2.强退防丢:用户检索中途（如手抖填了200页）想终止，直接点浏览器红叉或按 Ctrl+C 会导致程序崩溃，已抓取数据全部丢失
- [x] 3.超大页数拦截警告
- [x] 4.首页重定向修复:首次启动无 Cookie 时，知网大概率会重定向到科普/低质文章推荐页，导致检索目标错误

**0.2.x阶段：**

- [x] 5.运行配置、日志与浏览器会话缓存: 程序旁创建 `CNKIBug/` 运行数据目录，支持 `config.json`、`cache/cookies` 和任务摘要日志
- [ ] 6.复合关键词查询:高级检索页面?解析用户输入的逻辑符（空格、+、AND），自动在知网基础搜索框触发复合检索?(未定)

**0.3.x阶段：**

- [ ] 7.参考文献/引证文献抓取(耗时、技术难度大大增加)（考虑中）
- [ ] 8.SCI (Web of Science) 与校园 WebVPN 支持（不支持，见“不支持范围”）
- [ ] 9.Web UI界面


---

##  免责声明

本工具仅供个人学习、代码研究与非商业用途使用。请严格遵守知网（CNKI）的用户协议及相关法律法规。高频爬取极易触发 IP 封禁与验证码，请合理设置抓取页数，切勿滥用。

---

##  作者

**Kaffu_Alcaid** — 非科班出身的业余开发者，全凭兴趣写码。欢迎提交Issue探讨或发起PR！

---
## ♥️ 致谢 / Contributors

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
      <img src="./logo.png" width="80px" alt="Placeholder"/>
      <br /><sub><b>虚位以待</b></sub>
      <br />欢迎提交 PR
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
