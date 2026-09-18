# 配置说明

[返回首页](../README.md)

配置文件名为 `config.json`，位置取决于运行方式：

| 运行方式 | 配置文件位置 |
|---|---|
| Windows EXE、直接运行源码 | 启动文件所在目录的 `CNKIBug-data/config.json` |
| Linux AppImage、Linux 的 Python 包安装 | `~/.local/share/CNKIBug-data/config.json` |
| Windows 的 Python 包安装 | `%LOCALAPPDATA%/CNKIBug-data/config.json` |

Linux 设置了 `XDG_DATA_HOME` 时，用户数据保存在该目录下的 `CNKIBug-data/`。日志、登录状态、断点和任务报告与配置文件保存在同一用户数据目录中。

GUI 可通过任务设置页右上角的设置按钮管理外观、抓取、会话、日志、运行环境和更新选项，保存后立即生效，无需重启。设置入口仅在任务设置页显示。

手动编辑配置文件后，可在设置窗口点击重新读取配置；GUI 开始抓取前也会读取配置文件。终端版需重新启动程序。

恢复默认只填写设置窗口中的选项，点击保存后才会写入文件。重新读取配置会立即应用文件中的设置；读取失败时保留当前设置。

```json
{
  "version": 2,
  "timeout_goto_ms": 30000,
  "timeout_load_ms": 20000,
  "timeout_selector_ms": 15000,
  "verify_wait_timeout_sec": 180,
  "verify_notice_interval_sec": 15,
  "max_advance_fail": 2,
  "session_cache_enabled": true,
  "session_cache_ttl_hours": 12,
  "download_auth_wait_sec": 60,
  "log_level": "INFO",
  "log_save_path": true,
  "log_keywords": false,
  "log_scraped_records": false,
  "detail_txt_export": false,
  "gui_theme": "litera",
  "update_source": "auto",
  "linux_setup_completed": false,
  "output_dir": ""
}
```

| 参数                           | 默认值      | 可填值                                | 作用                                   |
|------------------------------|----------|------------------------------------|--------------------------------------|
| `version`                    | `2`      | 正整数                                | 配置文件版本号，不建议手动修改                      |
| `timeout_goto_ms`            | `30000`  | 正整数，毫秒                             | 打开 CNKI 页面时的最长等待时间                   |
| `timeout_load_ms`            | `20000`  | 正整数，毫秒                             | 等待页面加载的最长时间                          |
| `timeout_selector_ms`        | `15000`  | 正整数，毫秒                             | 等待搜索框、结果表格、翻页按钮等元素的最长时间              |
| `verify_wait_timeout_sec`    | `180`    | 正整数，秒                              | 等待用户完成滑块或安全验证的最长时间                   |
| `verify_notice_interval_sec` | `15`     | 正整数，秒                              | 验证等待期间的提醒间隔                          |
| `max_advance_fail`           | `2`      | 正整数                                | 连续翻页失败多少次后结束当前关键词                    |
| `session_cache_enabled`      | `true`   | `true` / `false`                   | 是否复用抓取和 PDF 下载各自保存的浏览器会话 |
| `session_cache_ttl_hours`    | `12`     | 正整数，小时                             | Cookie 会话缓存的有效期                      |
| `download_auth_wait_sec`    | `60`     | 非负整数，秒                             | 下载前在知网首页等待机构授权的时间，可点“立即继续”提前开始 |
| `log_level`                  | `"INFO"` | `"INFO"` / `"WARNING"` / `"ERROR"` | 日志级别                                 |
| `log_save_path`              | `true`   | `true` / `false`                   | 是否在日志中记录导出文件路径                       |
| `log_keywords`               | `false`  | `true` / `false`                   | 是否在日志中记录关键词                          |
| `log_scraped_records`        | `false`  | `true` / `false`                   | 是否记录详细的抓取统计                          |
| `detail_txt_export`          | `false`  | `true` / `false`                   | 抓取论文详情时是否额外导出关键词 TXT                 |
| `gui_theme`                  | `"litera"` | `"litera"` / `"darkly"`          | GUI 浅色或暗色主题，随设置保存并记忆                 |
| `update_source`              | `"auto"` | `"auto"` / `"ghproxy.net"` / `"ghfast.top"` / `"gh-proxy.org"` / `"direct"` | GUI 更新下载线路 |
| `linux_setup_completed`      | `false` | `true` / `false` | Linux AppImage 是否已完成初始化，由初始化设置保存 |
| `output_dir`                 | `""` | 目录路径或空字符串 | GUI 论文保存目录；空字符串使用默认桌面目录 |

抓取会话保存在用户数据目录的 `cache/cookies`，PDF 下载会话保存在 `cache/download_cookies`，两者使用相同的有效期设置。下载缓存为空时可读取有效的抓取会话，下载后的状态单独保存。

## 初始化与运行环境

Linux AppImage 在初始化完成前显示初始化设置，可选择论文保存目录并检查运行环境。需要 Chromium 时，点击“安装 Chromium”；缺少系统组件时，页面列出缺失项和安装命令，在终端执行后再检查。检查通过后点击“开始使用”保存设置。点击“稍后设置”时，下次启动仍会显示初始化设置。

Windows 直接进入主窗口。Windows 和 Linux 都可在“设置 → 运行环境”中手动检查程序组件、系统组件、浏览器和目录写入权限。浏览器检查会短暂打开一个空白窗口。

Chromium 默认保存在 Linux 的 `~/.cache/ms-playwright/`，设置 `XDG_CACHE_HOME` 时使用该目录下的 `ms-playwright/`；`PLAYWRIGHT_BROWSERS_PATH` 可指定浏览器安装位置。程序会检查当前所需版本，已安装时可以直接使用。完成初始化后，浏览器检查和安装仍可从设置中操作。

## GUI 更新

- 自动：依次尝试 `ghproxy.net`、`ghfast.top`、`gh-proxy.org`，最后使用原始 GitHub 地址；网络错误时换源，每条线路一次。
- 指定加速源：只使用选中的下载线路。
- 无加速（系统代理）：更新信息和文件均使用 GitHub 原始地址，遵循 Python urllib 支持的系统及环境 HTTP/HTTPS 代理设置，不强制直连。

加速模式优先从 JSDMirror 读取更新信息，不可用时尝试 GitHub API。下载固定到检查结果中的版本；换源时重新下载，文件校验失败会停止更新。

测试连接检查当前模式下的更新信息和对应平台的下载地址，只读取文件开头的一小段。自动模式检查全部候选下载线路。

Windows GUI EXE 和 Linux x86-64 AppImage 支持下载并替换后重启。AppImage 原文件所在目录可写时，更新保留原文件名和位置；无法直接替换时，可选择位置保存新版本。配置、登录状态和论文文件继续保留。

源码和 Python 包安装方式通过发布页或 pip 手动更新。

## 常见调整

- 网络慢：把 `timeout_goto_ms`、`timeout_load_ms`、`timeout_selector_ms` 适当调大
- 验证码来不及处理：把 `verify_wait_timeout_sec` 调大
- 会话状态异常：抓取会话可删除用户数据目录中的 `cache/cookies`，PDF 下载会话可删除 `cache/download_cookies`；也可在 GUI 设置中关闭复用浏览器会话。终端版将 `session_cache_enabled` 改为 `false` 后重启
- 不想日志记录本机路径：把 `log_save_path` 改为 `false`
- 需要把论文关键词重新导入软件：把 `detail_txt_export` 改为 `true` 后重启
