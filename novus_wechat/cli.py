import json
import os
import plistlib
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from .paths import ROOT, data_root


def probe(port):
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as response:
            value = json.load(response)
            if value.get("version") and value.get("data_dir"):
                return value
    except (OSError, URLError, ValueError):
        return None


def start(port, open_browser=True):
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    status = probe(port)
    if not status:
        cmd = [sys.executable, str(ROOT / "boot.py"), "serve", "--port", str(port)]
        kwargs = {"cwd": ROOT, "stdin": subprocess.DEVNULL}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        with (root / "service.log").open("a", encoding="utf-8") as log:
            process = subprocess.Popen(cmd, stdout=log, stderr=log, **kwargs)
        (root / "service.pid").write_text(str(process.pid))
        for _ in range(80):
            time.sleep(0.25)
            status = probe(port)
            if status:
                break
            if process.poll() is not None:
                raise RuntimeError(f"服务退出，请查看 {root / 'service.log'}")
        if not status:
            raise RuntimeError("服务启动超时，请查看 service.log")
    if Path(status["data_dir"]).resolve() != root.resolve():
        raise RuntimeError("该端口属于另一个数据目录，请换用 --port")
    if open_browser:
        webbrowser.open(f"http://127.0.0.1:{port}")
    return status


def autostart(mode, port):
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(ROOT / "boot.py"), "serve", "--port", str(port)]
    if sys.platform == "win32":
        startup = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup/NovusWeChat.vbs"
        if mode == "remove":
            startup.unlink(missing_ok=True)
        else:
            startup.parent.mkdir(parents=True, exist_ok=True)
            quoted = subprocess.list2cmdline(command).replace('"', '""')
            directory = str(root.resolve()).replace('"', '""')
            working = str(ROOT).replace('"', '""')
            startup.write_text(f'Set shell = CreateObject("WScript.Shell")\n'
                f'shell.Environment("PROCESS")("NOVUS_WECHAT_DATA") = "{directory}"\n'
                f'shell.CurrentDirectory = "{working}"\n'
                f'shell.Run "{quoted}", 0, False\n', encoding="utf-16")
        return {"autostart": mode, "path": str(startup), "effective": "next_login"}
    if sys.platform == "darwin":
        plist = Path.home() / "Library/LaunchAgents/com.novus.wechat.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        if mode == "remove":
            subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(plist)], capture_output=True)
            plist.unlink(missing_ok=True)
        else:
            plist.write_bytes(plistlib.dumps({"Label": "com.novus.wechat", "ProgramArguments": command,
                "RunAtLoad": True, "KeepAlive": True, "WorkingDirectory": str(ROOT),
                "EnvironmentVariables": {"NOVUS_WECHAT_DATA": str(root)},
                "StandardOutPath": str(root / "service.log"), "StandardErrorPath": str(root / "service.log")}))
            # Registration takes effect on next login; avoid launching a duplicate server now.
        return {"autostart": mode, "path": str(plist), "effective": "next_login"}
    raise ValueError("常驻安装仅支持 Windows 和 macOS")


def main(argv=None):
    from .cli_commands import Client, HTTP_COMMANDS, execute, parser
    args = parser().parse_args(argv)
    args.command = args.command or "start"
    if args.data_dir:
        os.environ["NOVUS_WECHAT_DATA"] = str(Path(args.data_dir).expanduser().resolve())
    try:
        if args.command == "doctor":
            from .macos import doctor
            print(json.dumps(doctor(data_root(), probe(args.port)), ensure_ascii=False, indent=2))
        elif args.command in HTTP_COMMANDS:
            start(args.port, False)
            result = execute(args, Client(args.port))
            print(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2))
            if args.command == "send" and result.get("state") not in {"native_success_unconfirmed", "sent"}:
                sys.exit(1)
        elif args.command == "serve":
            import uvicorn
            from .api import create_app
            uvicorn.run(create_app(port=args.port), host="127.0.0.1", port=args.port, log_level="warning")
        elif args.command == "start":
            start(args.port, not args.no_browser)
            print(f"http://127.0.0.1:{args.port}")
        elif args.command == "status":
            print(json.dumps(probe(args.port) or {"state": "stopped"}, ensure_ascii=False, indent=2))
        elif args.command == "mcp":
            start(args.port, False)
            from .mcp import make_mcp
            make_mcp(f"http://127.0.0.1:{args.port}").run()
        elif args.command == "stop":
            # Graceful shutdown verifies this application's PID via its own state endpoint.
            from urllib.request import Request
            status = probe(args.port)
            if not status:
                print("服务未运行")
                return
            if Path(status["data_dir"]).resolve() != data_root().resolve():
                raise RuntimeError("端口属于另一个数据目录")
            req = Request(f"http://127.0.0.1:{args.port}/api/shutdown", method="POST", headers={"X-Novus-WeChat": "1"})
            with urlopen(req, timeout=3):
                pass
            print("已请求停止服务")
        elif args.command == "config":
            print(json.dumps({"mcpServers": {"novus-wechat": {"command": sys.executable,
                "args": [str(ROOT / "boot.py"), "mcp", "--port", str(args.port)],
                "env": {"NOVUS_WECHAT_DATA": str(data_root())}}}}, ensure_ascii=False, indent=2))
            print("\n# Codex config.toml\n[mcp_servers.novus-wechat]\ncommand = " + json.dumps(sys.executable) +
                  "\nargs = " + json.dumps([str(ROOT / "boot.py"), "mcp", "--port", str(args.port)]) + "\nstartup_timeout_sec = 60" +
                  "\n[mcp_servers.novus-wechat.env]\nNOVUS_WECHAT_DATA = " + json.dumps(str(data_root())))
        elif args.command == "autostart":
            if args.argument not in {"install", "remove"}:
                raise ValueError("请使用 autostart install 或 autostart remove")
            print(json.dumps(autostart(args.argument, args.port), ensure_ascii=False))
        elif args.command == "init-image-key":
            from .image_keys import capture
            from .service import Service
            print("正在从本机图片缓存推导密钥，请稍候。微信无需退出。", flush=True)
            service = Service(data_root())
            service.configure(capture(service.root))
            print("图片密钥已保存到个人设置，可以查看已缓存图片。")
        elif args.command == "init-key":
            if not args.restart_wechat:
                raise ValueError("此步骤会关闭并重新启动微信。明确执行时添加 --restart-wechat；Windows 需要你从桌面手动重启微信。")
            from .service import Service
            service = Service(data_root())
            if not service.settings().get("source_dir"):
                raise ValueError("请先在 Web 中配置微信 db_storage 目录")
            print("请配合微信重新登录。Windows 请在提示后从桌面手动启动微信。")
            result = subprocess.run([sys.executable, str(ROOT / "boot.py"), "key-worker", str(data_root())])
            if result.returncode:
                raise RuntimeError("密钥初始化未完成，请检查上方诊断")
            service.configure({"raw_key_file": str(data_root() / "raw-key.txt")})
            print("密钥已保存并配置，可以在 Web 中开始同步。")
    except Exception as exc:
        print(json.dumps({"error": str(exc), "command": args.command}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
