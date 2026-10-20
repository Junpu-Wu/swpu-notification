# 常驻部署

Python 3.10+，无需第三方 Python 包。先分别在 `gs/`、`sgy/` 和 `bgw/` 中完成一次真实网页检查；真实推送测试只面向已获授权的指定接收人。部署程序时同时保留 `monitor.py` 与 `ai_content.py`，正文处理模块与主程序需要来自同一版本。请合理控制请求频率；默认 30–60 分钟，推送重试单独为 30–180 秒。

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

## 办公网重要通知：独立用户服务

`bgw/` 监测学校办公网重要通知，列表分类为 `1312`。可与研究生院监测运行在同一设备，但使用以下独立路径：

- 程序 `~/.local/opt/swpu-bgw-notice-monitor/`
- 配置 `~/.config/swpu-bgw-notice-monitor/config.json`
- 状态 `~/.local/state/swpu-bgw-notice-monitor/`
- 用户服务 `~/.config/systemd/user/swpu-bgw-notice-monitor.service`

将 `bgw/` 中的 `monitor.py`、`ai_content.py` 和 `configure.py` 安装到程序目录，复制该目录下的示例配置到私有配置目录，安装同目录提供的 service 文件。配置和凭据保持权限 `600`；需要复用同一用户已有的 Bark 或 DeepSeek 凭据时，只在 BGW 配置中引用现有私有文件路径，勿将凭据复制进仓库。BGW 仍使用独立配置、数据库、缓存和锁，不与 GS 共用数据库。

首次部署先运行一次静默检查：

```sh
python3 ~/.local/opt/swpu-bgw-notice-monitor/monitor.py \
  --config ~/.config/swpu-bgw-notice-monitor/config.json \
  --state-dir ~/.local/state/swpu-bgw-notice-monitor --once
```

首次成功检查建立当前首页基线，不会批量发送历史公告。核对状态和接收范围后，启用独立服务：

```sh
systemctl --user daemon-reload
systemctl --user enable --now swpu-bgw-notice-monitor.service
systemctl --user status swpu-bgw-notice-monitor.service --no-pager
loginctl show-user "$USER" -p Linger
```

开机自启需要服务用户已启用 linger，要求与研究生院用户服务相同。试运行保留 `delivery_allowlist: ["my-iphone"]`，只对原始测试手机启用发送；其他目标的凭据可保留。需要测试最新公告时，明确运行 `--test-latest my-iphone`；程序按发布日期选择最新公告，置顶旧公告不代表最新发布。

办公网正文可能指向 `Office/Xbgw_contentx.jsp`，部分内容仅限校园网或特定权限。部署前从运行设备验证列表与原文的实际访问能力；服务器能访问原文不代表校外手机也能打开。正文访问受限时，程序回退为明确的不可读取说明、标题和原文链接，并保留 AI 提醒尾注；访问提示或登录页面不应作为正文送入 AI 或推送。部署本身不提供校园网访问权限。

## 状态与停用

三套监测使用独立配置、状态目录和锁。用对应 monitor.py 的 --status 查看基线、最后成功时间、下次检查时间和投递状态；--once 只抓取并写入记录，不发推送。不要删除数据库来处理暂时网络错误。

系统服务用 systemctl disable --now 停用，用户服务加 --user；手动后台进程使用其 stop-background.sh。不要同时让两个进程使用同一数据库。

## 启用正文与 DeepSeek

正文会按网页原有顺序提取，过滤样式、脚本等干扰。图片内容需要打开原文查看；附件仅提供入口。AI 使用 DeepSeek JSON 模式筛选已有段落，不生成替换文字。启用 AI 后，设备可读取的校内通知段落也会提交至配置的 AI 服务，不能假定这些内容在校外公开可读。

1. 升级前停止对应服务，备份程序、私有配置和数据库；SQLite 数据库应使用其备份接口或在进程已停止后复制，保留原基线与投递记录。
2. 将同版本 `monitor.py` 和 `ai_content.py` 放入对应程序目录。无需清空或重建状态目录，程序会在现有数据库中创建正文缓存。
3. 在私有配置目录保存 DeepSeek key，文件权限 `600`、所有者为服务账号；系统服务的配置目录还需要允许该账号遍历。
4. 在配置中加入以下设置。路径替换为当前服务器的私有文件路径；不要将实际配置提交到仓库。

```json
"ai": {
  "enabled": true,
  "model": "deepseek-flash",
  "key_file": "/path/to/private/deepseek.key",
  "timeout_seconds": 30
},
"delivery_allowlist": ["my-iphone"]
```

启动服务前确认 `my-iphone` 是已配置的原始测试接收人。白名单会同时限制后台与测试，只填写 ID 不会自动启用目标。可先运行 `--once` 检查网页，再执行 `--test-latest my-iphone` 验证一条公告；测试会抓取正文并按当前 AI 配置处理，但不改变正常投递的已发送记录。三套程序应分别验证。

AI 请求超时、接口失败或返回内容无法验证时，程序发送规则提取的内容，并保留原文链接和“内容含AI处理，请关注原文。”。可从服务日志中的处理状态区分 AI 筛选与回退，日志不应含密钥或公告全文。Bark 的正文长度受限，较长文章会提示截断；不能以手机通知是否包含全文来判断提取是否成功。

密钥轮换不需要重启：在原文件所在目录准备新文件，写入新 key，设置正确的所有者和 `600` 权限，再原子替换 `ai.key_file` 指定的文件。程序下一次需要处理新文章时会读取新 key，已缓存的成功结果继续复用。若新 key 不可用，回退机制仍维持基础通知发送。

## 结束指定手机试运行

保留 `delivery_allowlist` 时，其他接收人的配置与凭据仍在，但没有发送资格。移除字段后，所有已启用目标恢复资格，可能补发试运行期间新发现的公告。

若需要保留并补发积压，核对目标后再移除限制。若只希望从恢复时开始接收，应先停止服务，将相应目标的 `since` 设为恢复时的 Unix 时间戳，并检查数据库中该目标已有的待投递记录，再解除限制。配置工具对新目标提供 `--future-only`；不要删除整个数据库或既有已接受记录。当前程序也会用 `since` 筛选已有 pending 记录；记录仍保留，但早于该时间的公告不会投递。恢复前先备份并确认投递范围。

升级后可在三套目录分别运行离线验证：

```sh
python3 -m unittest discover -v -p 'test_*.py'
```

离线测试使用模拟接口，不会向任何真实设备发消息。真实投递是否到达、原文链接是否能打开，仍需要在获授权的测试手机上确认。
