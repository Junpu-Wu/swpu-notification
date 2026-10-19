# 第三方项目引用与致谢

## Bark

- 项目：Bark（iOS 自定义通知应用）
- 开发者 / 上游账号：Finb
- 客户端源码：https://github.com/Finb/Bark
- 服务端源码：https://github.com/Finb/bark-server
- 文档：https://bark.day.app/
- API 教程：https://github.com/Finb/Bark/blob/master/docs/tutorial.md
- 客户端许可证：MIT，原文见 https://github.com/Finb/Bark/blob/master/LICENSE
- 客户端许可证版权声明：Copyright (c) 2018 Feng

本项目通过 Bark 的 HTTP API 发送 JSON 请求，使用 device_key、title、body、url、group 等参数。Bark 及苹果 APNs 提供手机推送能力，网页解析、去重、数据库和调度由本项目实现。本项目不隶属于 Bark 官方，未捆绑、复制或修改其客户端 / 服务端源码。若以后引入上游源码或其他实质内容，应另行保留对应版权声明与许可证；服务端授权请以该服务端仓库为准。

## ntfy

- 项目：https://github.com/binwiederhier/ntfy
- API 文档：https://docs.ntfy.sh/publish/
- 上游许可证：https://github.com/binwiederhier/ntfy/blob/main/LICENSE

本项目仅调用 ntfy HTTP API，未捆绑其服务端或客户端代码。ntfy 服务及客户端的授权以对应上游项目和版本为准。

## Python

本项目仅使用 Python 标准库。Python 本身的授权见 https://docs.python.org/3/license.html 。

## WxPusher

- 官方文档：https://wxpusher.zjiecode.com/docs/
- 极简推送 SPT：https://wxpusher.zjiecode.com/docs/spt.html

本项目调用 WxPusher 极简推送 HTTP API，未捆绑其客户端或服务端源码。消息送达及使用规则以平台说明为准。

## DeepSeek

- 官方 API 文档：https://api-docs.deepseek.com/
- JSON 模式：https://api-docs.deepseek.com/guides/json_mode/
- 模型与价格：https://api-docs.deepseek.com/quick_start/pricing/

本项目可选调用 DeepSeek 托管 API，使用 JSON 输出选择原文段落，未捆绑模型权重或 SDK。服务可用性、数据处理、费用和使用规则以 DeepSeek 官方条款与说明为准；此处引用不构成对模型输出准确性的保证。
