# 命令与数据边界

下例 `入口` 代表 Windows 的 `./launch.cmd` 或 Mac 的 `./launch.command`。使用本 Skill 目录或用户指定的绝对路径。

```text
入口 --help
入口 status
入口 start --no-browser
入口 sync --wait --timeout 600
入口 chats --keyword "项目群" --limit 20
入口 chats --kind group --offset 20 --limit 20
入口 messages --chat-id "完整会话ID" --start "2026-09-01" --end "2026-09-30T23:59:59+08:00"
入口 messages --chat-id "完整会话ID" --cursor "上一页next_cursor"
入口 messages --chat-id "完整会话ID" --keyword "[文件]"
入口 media "完整图片消息ID" --output "输出图片.jpg"
入口 export "完整会话ID" --format xlsx --output "完整消息整理.xlsx"
入口 export "完整会话ID" --format json --output "聊天记录.json"
入口 export "完整会话ID" --format csv --output "聊天记录.csv"
入口 export "完整会话ID" --format markdown --output "聊天记录.md"
入口 import "导出记录.json"
入口 --data-dir "独立测试目录" --port 18766 status
```

日期替换为用户指定范围；可用 Unix 秒或 ISO 日期，未写时区按运行机器时区解释。标准输出 UTF-8 JSON；xlsx 必须指定 --output，输出为真实 Excel 文件。导出自动读取全部分页；已有文件默认拒绝覆盖，用户要求替换时用 `--force`。

图片只有缓存存在且密钥可用时才能取到；格式以真实返回数据为准，后缀不负责转码。群文件保存 `[文件] 文件名`，类型通常为 `link_or_file`，不要用 `--kind file` 宣称查全文件。尚无文件附件下载、Word/PDF 正文预览或语音转写。

## Windows 发送

```text
./launch.cmd send "完整会话ID" --request-id "唯一请求ID" --text "用户授权正文"
./launch.cmd send "完整会话ID" --request-id "另一请求ID" --image "图片完整路径"
./launch.cmd send "完整会话ID" --request-id "另一请求ID" --file "文件完整路径"
./launch.cmd send-status "原请求ID"
```

一次请求只含纯文字、一个文件或一张静态图片。静态 PNG/JPEG/WebP/BMP/GIF 可作为图片；动画作为文件。发送开关由 Web 设置控制。未知结果先查原 ID，不换 ID 自动重发。Mac 不使用这些发送命令。

## MCP/API

`config` 生成配置，`mcp` 运行 stdio，`/mcp` 提供 Streamable HTTP。
工具：status、query、create、update、delete、get_media、synchronize、import_records、export_chat、send、send_status。
API 参数以本机 `/docs` 为准。修改请求带 `X-Novus-WeChat: 1`。只监听本机，不自行开放公网。

`export_chat(format="xlsx")` 返回本机下载地址，打开后生成；不能把返回地址说成文件已保存。网页“导出”也可选择 Excel（含图片与统计）。
