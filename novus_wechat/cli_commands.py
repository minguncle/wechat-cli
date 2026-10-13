"""Terminal commands using the same local HTTP service as Web and MCP."""
import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


def timestamp(value):
    try:
        number = int(value)
    except ValueError:
        try:
            number = int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
        except (ValueError, OverflowError):
            raise argparse.ArgumentTypeError("时间须为 Unix 秒或 ISO 日期，如 2026-09-16T09:00:00+08:00") from None
    if number < 0:
        raise argparse.ArgumentTypeError("时间不能为负数")
    return number


def bounded_int(low, high):
    def parse(value):
        try:
            number = int(value)
        except ValueError:
            raise argparse.ArgumentTypeError("请输入整数") from None
        if not low <= number <= high:
            raise argparse.ArgumentTypeError(f"数值须在 {low} 到 {high} 之间")
        return number
    return parse


def parser():
    def common():
        options = argparse.ArgumentParser(add_help=False)
        options.add_argument("--port", type=bounded_int(1, 65535), default=argparse.SUPPRESS, help="本地端口，默认 18765")
        options.add_argument("--data-dir", default=argparse.SUPPRESS, help="独立个人数据目录")
        return options
    root = argparse.ArgumentParser(description="微信 CLI：本地会话、消息、图片、导出与 MCP", parents=[common()])
    root.set_defaults(command="start", argument=None, no_browser=False,
                      port=int(os.environ.get("NOVUS_WECHAT_PORT", 18765)), data_dir=None)
    commands = root.add_subparsers(dest="command")

    def command(name, help):
        return commands.add_parser(name, help=help, description=help, parents=[common()])

    start = command("start", "启动本地服务和 Web")
    start.add_argument("--no-browser", action="store_true", default=argparse.SUPPRESS)
    # Preserve the previous no-command launch convention.
    root.add_argument("--no-browser", action="store_true")
    for name, help in [("serve", "前台运行服务"), ("stop", "停止本地服务"),
                       ("status", "查看服务及同步状态"), ("mcp", "运行 stdio MCP"),
                       ("config", "输出 MCP 配置"), ("doctor", "只读诊断 Mac 环境和初始化条件"),
                       ("discover", "列出微信账号数据目录，不自动选账号"),
                       ("init-image-key", "从本机图片缓存初始化图片密钥")]:
        command(name, help)
    login = command("autostart", "管理下次系统登录时启动")
    login.add_argument("argument", choices=["install", "remove"])
    key = command("init-key", "重新启动微信并初始化数据库密钥")
    key.add_argument("--restart-wechat", action="store_true")
    settings = command("settings", "查看设置，或配置明确指定的数据来源和已有密钥文件")
    for name in ["source-dir", "keys-file", "raw-key-file", "self-wxid"]:
        settings.add_argument("--" + name)
    settings.add_argument("--sync-interval", type=bounded_int(5, 86400))
    sync = command("sync", "同步来源；--wait 等待完成并以退出码表示成败")
    sync.add_argument("--full", action="store_true")
    sync.add_argument("--wait", action="store_true")
    sync.add_argument("--timeout", type=bounded_int(1, 86400), default=240)

    def filters(p, messages=False):
        p.add_argument("--keyword", default="", help="匹配关键词")
        p.add_argument("--kind", default="", help="会话或消息类型")
        p.add_argument("--limit", type=bounded_int(1, 500), default=100)
        p.add_argument("--include-deleted", action="store_true")
        p.add_argument("--id", default="", help="查询一条稳定 ID")
        if messages:
            p.add_argument("--chat-id", default="")
            p.add_argument("--cursor", default="", help="上一页返回的 next_cursor")
            p.add_argument("--start", type=timestamp, default=0)
            p.add_argument("--end", type=timestamp, default=0)
        else:
            p.add_argument("--offset", type=bounded_int(0, 2**31 - 1), default=0)
    filters(command("chats", "查询联系人与群聊，使用 offset 分页"))
    filters(command("messages", "查询消息，使用 next_cursor 分页"), messages=True)
    query = command("query", "统一查询入口，与 MCP query 对齐")
    query.add_argument("resource", choices=["chats", "messages"])
    filters(query, messages=True)
    query.add_argument("--offset", type=bounded_int(0, 2**31 - 1), default=0)
    export = command("export", "导出会话全部分页或指定时间范围")
    export.add_argument("chat_id", help="chats 返回的完整稳定 ID")
    export.add_argument("--format", choices=["json", "csv", "markdown", "xlsx"], default="json")
    export.add_argument("--start", type=timestamp, default=0)
    export.add_argument("--end", type=timestamp, default=0)
    export.add_argument("--output", type=Path, help="保存文件；默认写 stdout")
    export.add_argument("--force", action="store_true", help="允许覆盖已有输出文件")
    export.add_argument("--highlights", type=Path, help="Excel 重点摘要 JSON，每项含 topic、summary、message_ids")
    media = command("media", "将消息图片保存到本地文件")
    media.add_argument("message_id")
    media.add_argument("--output", type=Path, required=True)
    media.add_argument("--force", action="store_true")
    importer = command("import", "导入 JSON 为本地管理副本")
    importer.add_argument("argument", type=Path)
    send = command("send", "发送一个消息项目；Mac 原生发送尚未适配")
    send.add_argument("chat_id", help="已确认的完整稳定 ID，不接受昵称自动匹配")
    send.add_argument("--request-id", required=True, help="唯一请求 ID；结果未知时查询原 ID")
    payload = send.add_mutually_exclusive_group(required=True)
    payload.add_argument("--text", dest="content")
    payload.add_argument("--file", type=Path)
    payload.add_argument("--image", type=Path)
    pending = command("send-status", "查询原请求状态，不重新发送")
    pending.add_argument("request_id")
    return root


class Client:
    def __init__(self, port):
        self.base = f"http://127.0.0.1:{port}/api/"

    def call(self, method, path, data=None, params=None, raw=False, timeout=240):
        url = self.base + path
        if params:
            url += "?" + urlencode({k: str(v).lower() if isinstance(v, bool) else v for k, v in params.items()})
        req = Request(url, method=method, data=json.dumps(data).encode("utf-8") if data is not None else None,
                      headers={"Content-Type": "application/json", "X-Novus-WeChat": "1"})
        try:
            with urlopen(req, timeout=timeout) as response:
                body = response.read()
                return body if raw else json.loads(body)
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(body).get("detail", body)
            except (ValueError, AttributeError):
                detail = body
            raise ValueError(str(detail)) from None
        except (URLError, TimeoutError, OSError) as exc:
            if method == "POST" and path == "send":
                raise RuntimeError(f"发送结果未知；请用 send-status {data['request_id']} 查询，勿换 ID 重发") from None
            raise RuntimeError(f"本地服务请求失败：{exc}") from None


def save_output(path, body, force=False):
    path = path.expanduser().resolve()
    # Exclusive creation protects existing files and creates private exports.
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if force else os.O_EXCL)
    with os.fdopen(os.open(path, flags, 0o600), "wb") as target:
        target.write(body)
    return {"path": str(path), "bytes": len(body)}


HTTP_COMMANDS = {"settings", "discover", "sync", "query", "chats", "messages", "export", "media", "import", "send", "send-status"}


def execute(args, client):
    command = args.command
    if getattr(args, "end", 0) and args.end < args.start:
        raise ValueError("结束时间不能早于开始时间")
    if command in {"settings", "discover"}:
        changes = {key: getattr(args, key) for key in ["source_dir", "keys_file", "raw_key_file", "self_wxid", "sync_interval"]
                   if getattr(args, key, None) is not None}
        if changes:
            return client.call("PUT", "settings", changes)
        value = client.call("GET", "settings")
        return {"candidates": value["candidates"], "source_dir": value.get("source_dir")} if command == "discover" else value
    if command in {"query", "chats", "messages"}:
        resource = args.resource if command == "query" else command
        if args.id:
            return client.call("GET", resource + "/" + quote(args.id, safe=""))
        params = {"query": args.keyword, "kind": args.kind, "limit": args.limit, "include_deleted": args.include_deleted}
        params.update({"offset": args.offset} if resource == "chats" else
                      {"chat_id": args.chat_id, "cursor": args.cursor, "start": args.start, "end": args.end})
        return client.call("GET", resource, params=params)
    if command == "sync":
        value = client.call("POST", "sync", params={"full": args.full})
        deadline = time.monotonic() + args.timeout
        while args.wait and value.get("state") == "running":
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("等待同步超时，后台同步仍可能继续；使用 status 查询")
            time.sleep(min(0.25, remaining))
            value = client.call("GET", "status", timeout=min(5, max(0.1, deadline - time.monotonic())))["sync"]
        if args.wait and value.get("state") != "complete":
            raise RuntimeError("同步未完成：" + json.dumps(value, ensure_ascii=False))
        return value
    if command in {"media", "export"}:
        if command == "export" and args.format == "xlsx" and not args.output:
            raise ValueError("Excel 导出必须指定 --output 文件.xlsx")
        if command == "export" and args.highlights and args.format != "xlsx":
            raise ValueError("--highlights 仅适用于 xlsx")
        if args.output and args.output.expanduser().exists() and not args.force:
            raise FileExistsError(f"输出文件已存在：{args.output}；可用 --force 覆盖")
        if command == "media":
            body = client.call("GET", "messages/" + quote(args.message_id, safe="") + "/media", raw=True)
        elif args.format == "xlsx":
            from .excel_export import build_xlsx
            payload = client.call("GET", "export/" + quote(args.chat_id, safe=""),
                                  params={"format": "json", "start": args.start, "end": args.end})
            highlights = json.loads(args.highlights.expanduser().read_text(encoding="utf-8-sig")) if args.highlights else None
            body = build_xlsx(payload["chat"], payload["messages"],
                              lambda mid: client.call("GET", "messages/" + quote(mid, safe="") + "/media", raw=True), highlights)
        else:
            body = client.call("GET", "export/" + quote(args.chat_id, safe=""), raw=True,
                               params={"format": args.format, "start": args.start, "end": args.end})
        return save_output(args.output, body, args.force) if args.output else body.decode("utf-8")
    if command == "import":
        return client.call("POST", "import", json.loads(args.argument.expanduser().read_text(encoding="utf-8-sig")))
    if command == "send-status":
        return client.call("GET", "send/" + quote(args.request_id, safe=""))
    if command == "send":
        if not args.request_id.strip():
            raise ValueError("request_id 不能为空")
        attachment = args.file or args.image
        return client.call("POST", "send", {"chat_id": args.chat_id, "request_id": args.request_id,
                           "content": args.content or "", "files": [str(attachment.expanduser().resolve())] if attachment else [],
                           "images": bool(args.image)})
    raise ValueError(f"未知命令：{command}")
