# swpu-notification

西南石油大学通知公告监测与手机推送。包含石油与天然气工程学院和研究生院两套独立监测程序，使用 Python 标准库定时读取网页，发现新公告后通过 Bark 或 ntfy 发送通知。

| 目录 | 监测页面 | 文章分类 |
| --- | --- | --- |
| `sgy/` | https://www.swpu.edu.cn/sgy/tzgg.htm | 石工院 1086 |
| `gs/` | https://www.swpu.edu.cn/gs/tzgg.htm | 研究生院 1088 |

## 功能

- 默认两轮检查开始时间之间随机间隔 **30–60 分钟**（1800–3600 秒）。
- 推送失败独立按 **30–180 秒**随机重试，不随网页检查周期延长。
- 首次成功访问建立当前首页基线，不批量推送历史公告。
- 按文章编号识别新公告，SQLite 保存记录及每位接收人的投递状态。
- 通知附具体标题、发布日期和单篇文章链接，点击直达该条通知正文。
- 支持 Bark、ntfy，以及多个接收人；`--future-only` 可避免新接收人收到此前积压。
- 支持普通文章地址与学校动态文章地址；首页全部为未知文章时向后翻页补查。
- 日志不打印设备 key、推送请求体或服务商响应正文。

随机间隔并非到达时限，网络故障、学校缓存及手机通知设置均可能延迟。程序采用至少一次投递，平台已接收但本地未能记录结果时，重试可能重复发送。平台接受请求不等于手机已显示通知。

## 环境与快速开始

需要 Python 3.10+，适用于 Linux / macOS。使用 `fcntl` 文件锁，不直接支持原生 Windows。运行与测试均无需安装第三方 Python 包。

以研究生院为例（石工院改为进入 `sgy/`）：

```sh
cd gs
cp config.example.json config.json
chmod 600 config.json
python3 monitor.py --config config.json --state-dir ./state --once
python3 monitor.py --config config.json --state-dir ./state --status
```

示例接收人默认关闭，以上步骤不会推送。两套程序必须使用各自的配置和状态目录。

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

## ntfy

也支持 [ntfy](https://github.com/binwiederhier/ntfy)。手机订阅独立的随机主题，把主题写入权限为 600 的 `topic.txt`：

```sh
python3 configure.py --config config.json --id friend-android --ntfy-topic-stdin --future-only < topic.txt
```

默认使用 `https://ntfy.sh`，自建服务可加 `--ntfy-server https://你的服务地址`。需要鉴权时在目标配置中增加 `token_file`，指向权限为 600 的 Bearer token 文件。公共未保护主题名本身相当于访问凭据，不应使用易猜名称。手机接收方式及后台权限参见 [ntfy 官方说明](https://docs.ntfy.sh/subscribe/phone/)。

## 运行与测试

```sh
python3 monitor.py --config config.json --state-dir ./state
python3 -m unittest -v test_monitor.py
```

分别在 `sgy/` 和 `gs/` 中测试。测试使用合成凭据和模拟接口，不向真实手机推送。

常驻部署见 [部署说明](docs/DEPLOYMENT.md)，凭据管理见 [安全说明](SECURITY.md)。仓库只收录程序、示例和通用文档，不包含实际接收人、运行数据库、SSH 配置、服务器地址或历史部署报告。

## 内容与授权

公告原文及附件属于学校或相应权利人，本仓库不包含学校公告全文或抓取数据库。请合理设置频率，并遵守学校网站访问要求。

本仓库自身代码暂未指定开源许可证；第三方项目授权独立适用。需要公开分享或授予代码使用许可时，由仓库所有者决定许可证。
