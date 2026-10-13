"""Interactive platform bootstrap; never invoked automatically by service polling."""
import json
import os
import re
import runpy
import subprocess
import sys
import time
from pathlib import Path

from .paths import ROOT


def capture(root):
    settings = json.loads((root / "settings.json").read_text(encoding="utf-8"))
    keyfile = root / "raw-key.txt"
    if sys.platform == "win32":
        os.environ["NOVUS_WECHAT_DB_DIR"] = settings["source_dir"]
        os.environ["NOVUS_WECHAT_RAW_KEY_FILE"] = str(keyfile)
        script = ROOT / "vendor/key-tools/scripts/windows/extract_raw_key.py"
        sys.argv = [str(script), "120"]
        runpy.run_path(str(script), run_name="__main__")
    elif sys.platform == "darwin":
        import frida
        device = frida.get_local_device()
        subprocess.run(["osascript", "-e", 'tell application "WeChat" to quit'], capture_output=True)
        time.sleep(1)
        session = None
        try:
            pid = device.spawn(["/Applications/WeChat.app/Contents/MacOS/WeChat"])
            session = device.attach(pid)
            source = (ROOT / "vendor/key-tools/scripts/macos/hook_pbkdf.js").read_text(encoding="utf-8")
            hook = session.create_script(source)
            found = []
            def message(value, data):
                text = str(value.get("payload", ""))
                match = re.search(r"RAW_KEY=([0-9a-fA-F]{64})", text)
                if match and match[1] not in found:
                    found.append(match[1])
            hook.on("message", message)
            # The upstream hook uses console.log; Frida dispatches it separately.
            # Consume all log messages here so key material never reaches stdout.
            hook.set_log_handler(lambda level, text: message({"payload": text}, None))
            hook.load()
            device.resume(pid)
            print("请在微信窗口扫码登录，等待密钥初始化（最多 120 秒）。")
            for _ in range(240):
                if found:
                    break
                time.sleep(0.5)
            if not found:
                raise RuntimeError("没有捕获到密钥，请确认重新登录和微信版本")
            if len(found) != 1:
                raise RuntimeError("捕获到多个密钥，无法确定当前账号，请先退出其他账号")
            keyfile.write_text(found[0], encoding="ascii")
            keyfile.chmod(0o600)
        except (frida.PermissionDeniedError, frida.NotSupportedError, frida.ProcessNotFoundError) as exc:
            raise RuntimeError("macOS 不允许附加微信进程。请按上游 references/macos.md 检查签名与授权；程序不会自动更改微信签名。" + str(exc)) from None
        finally:
            if session:
                session.detach()
    else:
        raise RuntimeError("密钥初始化仅支持 Windows 和 macOS")
    if not keyfile.is_file():
        raise RuntimeError("未生成密钥文件")


if __name__ == "__main__":
    try:
        capture(Path(sys.argv[1]))
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
