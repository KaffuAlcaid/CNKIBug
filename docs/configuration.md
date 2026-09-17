# 配置说明

[返回首页](../README.md)

首次运行后，程序会在启动文件所在目录创建：

```text
CNKIBug-data/config.json
```

GUI 可通过任务设置页右上角的设置按钮管理外观、抓取、会话、日志和更新选项，保存后立即生效，无需重启。设置入口仅在任务设置页显示。

手动编辑配置文件后，可在设置窗口点击重新读取配置；GUI 开始抓取前也会读取配置文件。终端版需重新启动程序。`config.json` 是标准 JSON 文件，不支持 `//` 或 `#` 注释。

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
  "update_source": "auto"
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
| `session_cache_enabled`      | `true`   | `true` / `false`                   | 是否复用 `CNKIBug-data/cache/cookies` 中的浏览器会话 |
| `session_cache_ttl_hours`    | `12`     | 正整数，小时                             | Cookie 会话缓存的有效期                      |
| `download_auth_wait_sec`    | `60`     | 非负整数，秒                             | 下载前在知网首页等待机构授权的时间，可点“立即继续”提前开始 |
| `log_level`                  | `"INFO"` | `"INFO"` / `"WARNING"` / `"ERROR"` | 日志级别                                 |
| `log_save_path`              | `true`   | `true` / `false`                   | 是否在日志中记录导出文件路径                       |
| `log_keywords`               | `false`  | `true` / `false`                   | 是否在日志中记录关键词                          |
| `log_scraped_records`        | `false`  | `true` / `false`                   | 是否记录详细的抓取统计                          |
| `detail_txt_export`          | `false`  | `true` / `false`                   | 抓取论文详情时是否额外导出关键词 TXT                 |
| `gui_theme`                  | `"litera"` | `"litera"` / `"darkly"`          | GUI 浅色或暗色主题，随设置保存并记忆                 |
| `update_source`              | `"auto"` | `"auto"` / `"ghproxy.net"` / `"ghfast.top"` / `"gh-proxy.org"` / `"direct"` | GUI 更新下载线路 |

## GUI 更新线路

- 自动：依次尝试 `ghproxy.net`、`ghfast.top`、`gh-proxy.org`，最后使用原始 GitHub 地址；网络错误时换源，每条线路一次。
- 指定加速源：只使用选中的下载线路。
- 无加速（系统代理）：更新信息和文件均使用 GitHub 原始地址，遵循 Python urllib 支持的系统及环境 HTTP/HTTPS 代理设置，不强制直连。

加速模式优先从 JSDMirror 读取更新信息，不可用时尝试 GitHub API。下载固定到检查结果中的版本；换源时重新下载，文件校验失败会停止更新。

测试连接检查当前模式下的更新信息和实际 EXE 下载地址，只读取文件开头的一小段。自动模式检查全部候选下载线路；指定模式只检查选中的线路。界面显示延迟，不代表完整文件的下载速度。

Windows GUI EXE 支持下载并替换后重启；源码和 Python 包安装方式通过发布页或 pip 手动更新。

## 常见调整

- 网络慢：把 `timeout_goto_ms`、`timeout_load_ms`、`timeout_selector_ms` 适当调大
- 验证码来不及处理：把 `verify_wait_timeout_sec` 调大
- 会话状态异常：删除 `CNKIBug-data/cache/cookies`，或在 GUI 设置中关闭复用浏览器会话；终端版将 `session_cache_enabled` 改为 `false` 后重启
- 不想日志记录本机路径：把 `log_save_path` 改为 `false`
- 需要把论文关键词重新导入软件：把 `detail_txt_export` 改为 `true` 后重启
