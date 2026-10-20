# 学校重要通知监测

监测[西南石油大学办公网重要通知](https://www.swpu.edu.cn/bgw/zytzgg.htm)，默认随机 30–60 分钟检查一次。程序与研究生院监测分别运行，使用独立数据库、服务名称及手机通知分组。

正文提取、DeepSeek 辅助清洗、失败回退、按接收人重试及原文链接/AI 尾注与既有监测一致。测试和常驻推送均遵守 `delivery_allowlist`；示例只允许 `my-iphone`，所有接收人默认关闭。请在私有配置中启用需要的接收人，并将密钥放在权限为 `0600` 的独立文件中。

首页含置顶旧公告，因此 `--test-latest` 按发布日期选择最新一条；同日公告按页面顺序选择。静态文章地址和办公网动态文章地址统一去重。首次启动仅保存当前首页基线，不群发已有公告；后续发现的新公告才加入推送队列。

办公网动态文章可能需要校园网访问；部署前请在服务器实际确认列表和正文均能读取。示例配置未包含任何凭据，部署时应从已有服务的私有配置复用相应密钥文件路径，保留接收范围限制。

用户级服务路径：

- 程序：`~/.local/opt/swpu-bgw-notice-monitor/`
- 配置：`~/.config/swpu-bgw-notice-monitor/config.json`
- 状态：`~/.local/state/swpu-bgw-notice-monitor/`
- 服务：`swpu-bgw-notice-monitor.service`

启用服务前创建以上目录。将服务文件放入 `~/.config/systemd/user/` 后，重新加载并启用用户级服务；用户登出后仍需运行时，由管理员启用该用户的 linger。

```bash
python3 monitor.py --config /path/to/private/config.json --state-dir /path/to/private/state --once
python3 monitor.py --config /path/to/private/config.json --state-dir /path/to/private/state --status
python3 monitor.py --config /path/to/private/config.json --state-dir /path/to/private/state --test-latest my-iphone
```

`--once` 抓取并保存状态，不发送消息；`--test-latest` 会发送测试消息，应明确指定接收人。配置与通用用法见[主说明](../README.md)，常驻运行见[部署说明](../docs/DEPLOYMENT.md)。
