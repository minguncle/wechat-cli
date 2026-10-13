# 第三方来源与许可证

顶层 LICENSE 仅覆盖独立编写的整合代码、Web、CLI/MCP 和原生桥接组件。第三方源码及其修改保留原版权和许可证。

| 目录 | 来源 | 许可证 |
| --- | --- | --- |
| vendor/wechat-decrypt | https://github.com/pronii/wechat-decrypt | MIT |
| vendor/key-tools | https://github.com/tzwkb/wechat-decrypt | MIT |
| vendor/pywechat | https://github.com/Hello-Mr-Crab/pywechat | 上游 LGPL 2.1 文本 |
| vendor/wechatauto | https://github.com/fanyuantaier/wechatauto-replica | Apache-2.0 |

许可证原文位于各目录 LICENSE；锁定提交见 upstreams.lock.json，本地修改见 patches。pywechat 未作本地修改。

完整包还包含 Python 和锁定依赖，保留运行时 LICENSE 及依赖 dist-info 中的许可证。PyAV 的二进制分发包含 FFmpeg 组件，相关许可按分发物保留。两个桥接 DLL 的对应 C 源码位于 native/windows，不包含微信客户端代码。

历史桌面自动化组件仍保留源码；Windows MCP 发送使用本项目原生实现。许可证原文优先于本摘要。
