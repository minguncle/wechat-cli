# 平台连接

## Windows x64

从 Skill 目录运行。PowerShell：`./launch.cmd start`；CMD：`launch.cmd start`。
运行时为 `runtime/windows-x64/python.exe`。个人数据默认 `%LOCALAPPDATA%/Novus/WeChat`。
微信 4.1.13.65 原生发送沿用原实现；其他版本不能复用旧偏移。本次在 Mac 组装，只核对 Windows 组件、PE 架构和归档，未在 Windows 真机执行。

## Mac Apple Silicon

执行 `./launch.command doctor`，再执行 `./launch.command start`。
若 ZIP 解压器丢失执行位，在该目录执行：

```sh
chmod +x launch.command runtime/macos-arm64/bin/python3.12
```

`.tar.gz` 可保留执行位。入口检测 Rosetta 并切换 ARM64；Intel Mac 不受支持。
个人数据默认 `~/Library/Application Support/Novus/WeChat`。程序不自动重签微信，不修改系统保护或辅助功能授权。

## 首次连接

1. 登录本人微信，执行 `discover`。
2. 明确目标账号后在 Web 设置中选择来源；多个账号请用户指定。也可执行：

```text
启动器 settings --source-dir "本人账号/db_storage" --keys-file "已有密钥.json"
启动器 sync --wait --timeout 600
启动器 init-image-key
```

启动器按平台替换。`--raw-key-file` 可替代 `--keys-file` 使用已有原始密钥文件，不打印密钥内容。
没有密钥时，`init-key --restart-wechat` 会关闭微信并要求重新登录；先说明影响并确认是否已有重启授权，不能自动降低系统保护。
图片密钥独立于数据库密钥；缓存不足时让用户在微信打开几张图片再重试。

## 服务管理

`status` 查看已有服务；`start --no-browser` 启动；`stop` 停止。
`autostart install/remove` 管理登录时常驻，只有用户要求时才安装。
`config` 输出 MCP 配置；HTTP MCP 为本机 `/mcp`。
同端口服务属于其他数据目录时换端口，不关闭不明服务。更新时停止本 Skill 的服务再替换程序目录，不删除个人数据。
