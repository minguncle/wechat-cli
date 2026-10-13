# 第三方来源与许可证

顶层 LICENSE 仅覆盖独立编写的整合代码、Web、CLI/MCP 和原生桥接组件。第三方源码及其修改保留原版权和许可证。

| 目录 | 来源 | 许可证 |
| --- | --- | --- |
| vendor/wechat-decrypt | https://github.com/pronii/wechat-decrypt | MIT |
| vendor/key-tools | https://github.com/tzwkb/wechat-decrypt | MIT |
| vendor/pywechat | https://github.com/Hello-Mr-Crab/pywechat | 上游 LGPL 2.1 文本 |
| vendor/wechatauto | https://github.com/fanyuantaier/wechatauto-replica | Apache-2.0 |

许可证原文位于各目录 LICENSE；锁定提交见 upstreams.lock.json，本地修改见 patches。pywechat 未作本地修改。

自行构建的完整包还包含 Python 和锁定依赖，保留运行时 LICENSE 及依赖 dist-info 中的许可证。两个桥接 DLL 的对应 C 源码位于 native/windows，不包含微信客户端代码。

## 完整二进制包的发布状态

本次公开发布源码，不上传完整离线二进制包。输入包使用 PyAV 15.1.0，其 Python 绑定的 BSD 许可证不能代替随附 FFmpeg 及外部编解码库的许可证。

2026-10-13 检查 Windows 包时，`avcodec_license()` 返回 `LGPL version 3 or later`，而编译配置包含 `--enable-libx264` 和 `--enable-libx265`，包内也包含 x265 DLL。因此目前不能仅依据这个返回值确定全部原生库的分发条件。

公开上传完整离线包前，需要确认两个平台各原生库的实际构建来源、适用许可证、对应源码及构建方法，并提供所需材料。参考 [FFmpeg 官方许可与分发检查清单](https://ffmpeg.org/legal.html)。当前 CI 不上传完整运行时包，源码安装由使用者直接从依赖上游获取对应版本。

历史桌面自动化组件仍保留源码；Windows MCP 发送使用本项目原生实现。许可证原文优先于本摘要。
