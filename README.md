# 西南石油大学通知推送助手

西南石油大学通知公告监测与手机推送。包含石工院、研究生院和学校办公网重要通知三套独立监测程序，使用 Python 标准库定时读取网页，发现新公告后通过 Bark、ntfy 或 WxPusher 发送通知。

| 目录 | 监测页面 | 通知标题 |
| --- | --- | --- |
| `sgy/` | https://www.swpu.edu.cn/sgy/tzgg.htm | 石工院新通知 |
| `gs/` | https://www.swpu.edu.cn/gs/tzgg.htm | 研究生院新通知 |
| `bgw/` | https://www.swpu.edu.cn/bgw/zytzgg.htm | 学校重要通知 |

## 功能

- 默认两轮检查开始时间之间随机间隔 **30–60 分钟**（1800–3600 秒）。
- 推送失败独立按 **30–180 秒**随机重试，同一目标最多尝试 5 次；同时受下述发送频率限制。
- 首次成功访问建立当前首页基线，不批量推送历史公告。
- 按文章编号识别新公告，SQLite 保存记录及每位接收人的投递状态。
- 通知标题固定显示上表中的来源名称，正文包含文章标题、发布日期和内容；点击通知直达该篇原文。
- 可选接入 DeepSeek 清理正文：模型只选择原文段落，程序保留原文文字及顺序；调用失败自动回退到规则提取结果。
- 支持 Bark、ntfy、WxPusher，以及多个接收人；`--future-only` 可避免新接收人收到此前积压。
- 可用 `delivery_allowlist` 将试运行限制在指定接收人，其他接收人的配置和凭据继续保留。
- 支持普通文章地址与学校动态文章地址；按发布日期水位有限补查，单轮最多读取 3 页，整批检查完成后才写入。
- 办公网重要通知兼容 `Office/Xbgw_contentx.jsp` 原文地址；“最新一条”测试按发布日期选择，避免将顶部置顶的旧公告当作最新公告。
- 日志不打印设备 key、推送请求体或服务商响应正文。

随机间隔并非到达时限，网络故障、学校缓存及手机通知设置均可能延迟。程序采用至少一次投递，平台已接收但本地未能记录结果时，重试可能重复发送。平台接受请求不等于手机已显示通知。

## 历史公告与批量发送防护

三套程序使用相同的保护规则。数据库中“未见过”不再等同于“刚发布”：候选公告日期必须不早于已保存的发布日期水位，且在北京时间当天至此前 3 个自然日内。同一发布日期的新文章仍按独立编号识别；补入的更早公告只保存为历史，不生成推送。网页只有日期、没有精确发布时间，因此无法完全区分同一天补入的旧文章和新文章；后续批量与发送限制提供第二层保护。

一轮检查最多读取 3 页、放行 5 篇新候选；超过上限或发现异常日期时，将候选隔离并持久暂停该来源的自动投递。整轮抓取完成前不写入可投递记录，翻页中途失败不会让后台发送半批数据。旧版归档恢复游标不再续跑。待发积压超过 10 篇也会暂停投递，重启不会解除暂停。

每个来源对每个接收端的自动投递至少间隔 60 秒，滚动一小时最多尝试 6 次；失败与结果不明的请求也计入限额，重启后限额继续有效。同一篇对同一端最多尝试 5 次，超过次数或超出日期窗口的任务保留为隔离记录，等待人工核查。限额按来源独立计算，三套来源并不共用全局限额。

首次升级这些保护时，旧版 `is_new` 标记统一取消，旧 pending 任务转为隔离，已接受投递与公告原始记录保留。升级前须备份；升级不自动补发停用期间的公告，也不全量抓取历史。为了避免旧文误推，这些规则可能隔离真实的大批量新通知、延迟发布的回填公告或较长停机期间的公告，需要查看状态后人工处理。

生产配置可以设置 `expected_delivery_targets` 为正式接收端 ID 列表。实际已启用且在白名单中的集合必须与它一致，否则停止处理并记录 `delivery_target_set_mismatch`，防止测试白名单被误留在正式配置。该字段与 `delivery_allowlist` 都只包含 ID，不包含密钥。

`--status` 会显示 `safety_version`、`publication_watermark`、`delivery_pause_reason` 和隔离记录数。暂停原因不会自动清空；不要通过删除数据库、删除隔离记录或批量修改 `is_new` 绕过保护。核对来源页面、候选批次和接收范围后再决定是否恢复。手动 `--test-latest` 是明确指定的测试动作，不代表自动队列通过了上述保护。

## 环境与快速开始

需要 Python 3.10+，适用于 Linux / macOS。使用 `fcntl` 文件锁，不直接支持原生 Windows。运行与测试均无需安装第三方 Python 包。

以研究生院为例（石工院改为进入 `sgy/`，办公网重要通知改为 `bgw/`）：

```sh
cd gs
cp config.example.json config.json
chmod 600 config.json
python3 monitor.py --config config.json --state-dir ./state --once
python3 monitor.py --config config.json --state-dir ./state --status
```

示例接收人默认关闭，以上步骤不会推送。三套程序必须使用各自的配置和状态目录；首次成功检查只建立当前首页基线，不批量发送已有公告。主动执行 `--test-latest my-iphone` 才会对指定手机发送一条现有公告供测试。

办公网部分原文可能仅限校园网或需要访问权限，校外手机即使收到消息也可能无法直接打开。应将该监测部署在可访问对应页面的设备上；正文权限不足时只发送明确的不可读取说明、标题和原文链接，不将访问提示或登录页面作为公告正文。AI 不能绕过这些访问限制。

## 配置 Bark

感谢 **[Finb/Bark](https://github.com/Finb/Bark)** 提供 iOS 推送通道。本项目通过其 HTTP API 提交消息，由 Bark / Apple APNs 向 iPhone 发送系统通知。本项目是独立的学校公告监测工具，与 Bark 官方无隶属关系，也未在仓库中包含 Bark 客户端或服务端源码。

在手机安装 Bark 并允许通知，选择“复制地址和 key”。用编辑器把完整地址保存到本地私有文件 `bark-url.txt`，不要在命令参数、Git 提交或公开截图中暴露 key。

```sh
chmod 600 bark-url.txt
python3 configure.py --config config.json --id my-iphone --bark-url-stdin < bark-url.txt
python3 monitor.py --config config.json --test-latest my-iphone
```

配置工具从标准输入读取凭据，生成权限为 600 的私有 key 文件，更新配置并备份原配置。测试通知使用现有文章，不修改正常监测记录。完成配置后可移除临时输入文件；实际运行使用配置指定的 key 文件。

添加第二位接收人时使用独立 ID，并按需加 `--future-only`：

```sh
python3 configure.py --config config.json --id friend-iphone --future-only --bark-url-stdin < friend-bark-url.txt
```

程序运行中会重新读取配置，添加接收人无需重启。

Bark 接口与使用说明请查阅 [官方文档](https://bark.day.app/) 与 [HTTP API 教程](https://github.com/Finb/Bark/blob/master/docs/tutorial.md)。Bark 上游使用 [MIT License](https://github.com/Finb/Bark/blob/master/LICENSE)。上游许可证仅描述上游项目，不自动赋予本仓库代码相同许可证，详情见 [第三方说明](THIRD_PARTY_NOTICES.md)。

## 正文与 AI 清理

程序抓取单篇通知后，提取正文并过滤脚本、样式和 Word 导出的 CSS；表格按行保留文字，图片提示前往原文查看，附件保留名称和说明文字，不下载解析附件全文。正文和附件中的其他网页链接从通知文字中移除，下载入口请在原文中查看。纯图片通知无法由文本模型恢复图片内的内容。

AI 默认关闭。需要启用时，将 DeepSeek API key 写入仅服务账号可读的独立文件，权限设为 `600`，在配置中填写：

```json
"ai": {
  "enabled": true,
  "model": "deepseek-flash",
  "key_file": "/path/to/private/deepseek.key",
  "timeout_seconds": 30
}
```

DeepSeek 接收经过初步提取的通知段落，返回 JSON 段落编号 `keep_ids`。启用 AI 时，运行设备可以读取的校内通知段落也会提交至所配置的 AI 服务；内容不一定在校外公开可读。程序验证编号，再按原顺序拼接原文，不采用模型重新生成的标题、日期、数字或链接。模型仍可能误选或漏选段落，因此不能把清理结果视为学校原文；身份、期限、名单和附件等重要信息应核对原文。

每篇推送只设置一篇原文的点击入口：Bark 使用 `url`、ntfy 使用 `click`、WxPusher 使用 `url`，正文不重复显示网址。通知标题以及 WxPusher 摘要都使用上表中的固定来源名称，测试也使用相同标题，不添加“AI 测试”等前缀。

正文末尾固定保留两行纯文本：**“点击通知查看原文。”**、**“内容含AI处理，请关注原文。”**。Bark 正文过长时会明确提示截断，原文点击入口和这两行说明始终保留；手机通知适合快速查看，长篇公告仍需打开原文。其他通道也按各自可接受的长度发送。该显示处理在读取正文缓存后执行，已有 AI 缓存无需重新生成。

正文处理结果保存在各自的 SQLite 数据库中，同篇公告的重试与不同接收人共享缓存。AI 超时、接口错误或返回格式不符时，仍发送规则提取的正文或标题与链接，避免依赖模型才能发送通知；回退结果仅短期缓存，之后处理时可以重新尝试。缓存成功的历史内容不会因更换密钥自动重新生成。

模型参数、JSON 模式及接口使用规则参见 [DeepSeek 官方说明](https://api-docs.deepseek.com/guides/json_mode/)；可用模型和费用以 [官方模型与价格页](https://api-docs.deepseek.com/quick_start/pricing/) 为准。该功能会产生 API 用量，只在处理新公告或缓存需要重新处理时调用，不在每次列表检查时调用。

## 只向指定手机试运行

示例配置包含以下限制，且所有接收人默认关闭：

```json
"delivery_allowlist": ["my-iphone"]
```

启用 `my-iphone` 后，后台投递和命令行测试都只允许白名单内且已启用的接收人；`--test-latest all` 同样受此限制。试运行建议明确指定接收人：

```sh
python3 monitor.py --config config.json --state-dir ./state --test-latest my-iphone
```

需要向多台获授权设备验证显示时，可另建权限为 `600` 的私有临时测试配置，只启用这些接收人并为它们设置测试白名单，再使用现有的 `--test-latest all`。临时测试配置只引用已有私有凭据文件，常驻服务继续读取原配置、保持原接收范围。测试仅发送当前选中的一篇公告，不排空历史积压，也不改写正常投递记录；详情见 [部署说明](docs/DEPLOYMENT.md)。

删除 `delivery_allowlist` 后，所有已启用接收人重新具备投递资格，可能收到试运行期间的积压；需要从恢复时开始接收时，应先将相应目标的 `since` 更新为恢复时的 Unix 时间戳，核对已有待投递记录，再解除限制。不要删除数据库来跳过积压，具体步骤见 [部署说明](docs/DEPLOYMENT.md)。

## ntfy

也支持 [ntfy](https://github.com/binwiederhier/ntfy)。手机订阅独立的随机主题，把主题写入权限为 600 的 `topic.txt`：

```sh
python3 configure.py --config config.json --id friend-android --ntfy-topic-stdin --future-only < topic.txt
```

默认使用 `https://ntfy.sh`，自建服务可加 `--ntfy-server https://你的服务地址`。需要鉴权时在目标配置中增加 `token_file`，指向权限为 600 的 Bearer token 文件。公共未保护主题名本身相当于访问凭据，不应使用易猜名称。手机接收方式及后台权限参见 [ntfy 官方说明](https://docs.ntfy.sh/subscribe/phone/)。

## 运行与测试

```sh
python3 monitor.py --config config.json --state-dir ./state
python3 -m unittest discover -v -p 'test_*.py'
```

分别在 `sgy/`、`gs/` 和 `bgw/` 中测试。测试使用合成凭据和模拟接口，不向真实手机推送。

常驻部署见 [部署说明](docs/DEPLOYMENT.md)，凭据管理见 [安全说明](SECURITY.md)。仓库只收录程序、示例和通用文档，不包含实际接收人、运行数据库、SSH 配置、服务器地址或历史部署报告。

## 内容与授权

公告原文及附件属于学校或相应权利人，本仓库不包含学校公告全文或抓取数据库。请合理设置频率，并遵守学校网站访问要求。

本仓库自身代码暂未指定开源许可证；第三方项目授权独立适用。需要公开分享或授予代码使用许可时，由仓库所有者决定许可证。

## WxPusher 极简推送 SPT

支持 [WxPusher SPT](https://wxpusher.zjiecode.com/docs/spt.html)，使用 POST JSON 接口，令牌保存在私有文件中，不放在请求 URL。

把 SPT 或包含它的完整极简推送地址保存为权限 600 的临时输入文件后，在对应目录配置：

```sh
python3 configure.py --config config.json --id my-wxpusher --wxpusher-spt-stdin --future-only < wxpusher-spt.txt
python3 monitor.py --config config.json --test-latest my-wxpusher
```

新通知包含来源、标题、日期、可点击的单篇原文链接。每个通道有独立投递与重试状态，开启 WxPusher 不会替换 Bark。SPT 需要像密码一样保密，不能上传到仓库。接口成功仅表示接受发送，手机实际收到需要另外确认。
