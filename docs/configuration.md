# 配置说明

[返回首页](../README.md)

首次运行后，程序会在启动文件所在目录创建：

```text
CNKIBug/config.json
```

修改配置后请重新启动程序。`config.json` 是标准 JSON 文件，不支持 `//` 或 `#` 注释。

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
  "log_level": "INFO",
  "log_save_path": true,
  "log_keywords": false,
  "log_scraped_records": false,
  "detail_txt_export": false
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
| `session_cache_enabled`      | `true`   | `true` / `false`                   | 是否复用 `CNKIBug/cache/cookies` 中的浏览器会话 |
| `session_cache_ttl_hours`    | `12`     | 正整数，小时                             | Cookie 会话缓存的有效期                      |
| `log_level`                  | `"INFO"` | `"INFO"` / `"WARNING"` / `"ERROR"` | 日志级别                                 |
| `log_save_path`              | `true`   | `true` / `false`                   | 是否在日志中记录导出文件路径                       |
| `log_keywords`               | `false`  | `true` / `false`                   | 是否在日志中记录关键词                          |
| `log_scraped_records`        | `false`  | `true` / `false`                   | 是否记录详细的抓取统计                          |
| `detail_txt_export`          | `false`  | `true` / `false`                   | 抓取论文详情时是否额外导出关键词 TXT                 |

## 常见调整

- 网络慢：把 `timeout_goto_ms`、`timeout_load_ms`、`timeout_selector_ms` 适当调大
- 验证码来不及处理：把 `verify_wait_timeout_sec` 调大
- 会话状态异常：删除 `CNKIBug/cache/cookies`，或把 `session_cache_enabled` 改为 `false` 后重启
- 不想日志记录本机路径：把 `log_save_path` 改为 `false`
- 需要把论文关键词重新导入软件：把 `detail_txt_export` 改为 `true` 后重启
