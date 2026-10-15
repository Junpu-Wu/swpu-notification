# 常驻部署

Python 3.10+，无需第三方 Python 包。先分别在 gs/ 和 sgy/ 中完成一次真实网页检查及指定接收人的文章直达测试。请合理控制请求频率；默认 30–60 分钟，推送重试单独为 30–180 秒。

## 石工院：系统级 systemd

参考 `sgy/swpu-notice-monitor.service`。以下为部署路径约定，不包含实际服务器信息：

- 程序 `/opt/swpu-notice-monitor/`
- 配置 `/etc/swpu-notice-monitor/config.json`
- 状态 `/var/lib/swpu-notice-monitor/`
- 专用服务账号 `swpu-monitor`

管理员创建非登录服务账号和上述目录，将 sgy/ 的程序放入程序目录，将示例配置复制到配置目录，调整所有者和权限。程序归 root 所有；配置与凭据归服务账号所有、权限 600，配置目录建议 root:swpu-monitor 权限 750。使用 configure.py 时以管理员执行并加 `--owner swpu-monitor`，避免凭据归属错误。

安装 service 到 /etc/systemd/system/ 后执行：

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now swpu-notice-monitor.service
sudo systemctl status swpu-notice-monitor.service --no-pager
sudo journalctl -u swpu-notice-monitor.service -n 30 --no-pager
```

## 研究生院：用户级部署

复制 gs/ 中的程序及后台脚本到 `~/.local/opt/swpu-gs-notice-monitor/`。复制示例为 `~/.config/swpu-gs-notice-monitor/config.json`，配置接收人并将实际 key 文件放入私有配置目录。`~/.local/state/swpu-gs-notice-monitor/` 保存状态。

```sh
chmod +x ~/.local/opt/swpu-gs-notice-monitor/*-background.sh
~/.local/opt/swpu-gs-notice-monitor/start-background.sh
~/.local/opt/swpu-gs-notice-monitor/stop-background.sh
```

后台脚本可在退出 SSH 后继续运行；这不代表设备重启后会自动恢复。

需要重启后自动运行时，将 `gs/swpu-gs-notice-monitor.service` 安装到 `~/.config/systemd/user/`，由管理员为服务用户启用 linger：

```sh
sudo loginctl enable-linger "$USER"
```

然后以该服务用户登录，停止手动后台进程，再启用用户服务：

```sh
~/.local/opt/swpu-gs-notice-monitor/stop-background.sh
systemctl --user daemon-reload
systemctl --user enable --now swpu-gs-notice-monitor.service
systemctl --user status swpu-gs-notice-monitor.service --no-pager
loginctl show-user "$USER" -p Linger
```

应确认 Linger=yes，并实际验证退出 SSH 及重启后的服务状态。虚拟机需要宿主机和客户机均保持运行，宿主机睡眠会中断监测。若无法取得管理员权限，应明确记录尚未完成开机自启。

## 状态与停用

两套监测使用独立配置、状态目录和锁。用对应 monitor.py 的 --status 查看基线、最后成功时间、下次检查时间和投递状态；--once 只抓取并写入记录，不发推送。不要删除数据库来处理暂时网络错误。

系统服务用 systemctl disable --now 停用，用户服务加 --user；手动后台进程使用其 stop-background.sh。不要同时让两个进程使用同一数据库。
