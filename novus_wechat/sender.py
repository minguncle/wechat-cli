"""Desktop send adapters. Executed in a separate process, not the web process."""
import contextlib
import io
import json
import subprocess
import sys
import time
import tempfile
from pathlib import Path

from .paths import ROOT


def windows(payload):
    from .windows_accessibility import ensure_accessibility
    ensure_accessibility()
    sys.path.insert(0, str(ROOT / "vendor/pywechat/src"))
    from pyweixin import Messages, Files
    target = payload["target"]
    if payload["images"]:
        import win32clipboard
        from PIL import Image
        from pyweixin import Navigator
        # Resolve the destination through upstream UI automation before pasting.
        from pyweixin.Uielements import Edits
        window = Navigator.open_dialog_window(friend=target)
        editor = window.child_window(**Edits.CurrentChatEdit)
        if not editor.exists(timeout=1):
            raise RuntimeError("未找到当前聊天输入区域")
        if editor.window_text().strip():
            raise RuntimeError("聊天输入框存在草稿，请先处理草稿后发送")
        send_button = window.child_window(title="发送", control_type="Button")
        for path in payload["files"]:
            if editor.element_info.name != target:
                raise RuntimeError("当前微信会话已变化，停止发送")
            buffer = io.BytesIO()
            with Image.open(path) as image:
                image.convert("RGB").save(buffer, "BMP")
            win32clipboard.OpenClipboard()
            try:
                win32clipboard.EmptyClipboard()
                win32clipboard.SetClipboardData(win32clipboard.CF_DIB, buffer.getvalue()[14:])
            finally:
                win32clipboard.CloseClipboard()
            editor.type_keys("^v", set_foreground=True)
            send_button.wait("enabled", timeout=5)
            if editor.element_info.name != target:
                raise RuntimeError("当前微信会话已变化，停止发送")
            send_button.click_input()
            time.sleep(0.5)
    elif payload["files"]:
        Files.send_files_to_friend(friend=target, files=payload["files"], close_weixin=False)
    if payload["content"]:
        Messages.send_messages_to_friend(friend=target, messages=[payload["content"]], close_weixin=False)
    return {"state": "executed", "message": "桌面发送操作已执行，请以微信发送记录为准", "delivery_confirmed": False}


def macos(payload):
    script = ROOT / "platform/macos/send.applescript"
    # All variable input is argv, never AppleScript source interpolation.
    with tempfile.TemporaryDirectory(prefix="novus-send-") as temporary:
        files = payload["files"]
        if payload["images"]:
            from PIL import Image
            converted = []
            for index, file in enumerate(files):
                path = Path(temporary) / f"{index}.png"
                with Image.open(file) as image:
                    image.save(path, "PNG")
                converted.append(str(path))
            files = converted
        result = subprocess.run(["osascript", str(script), payload["target"], payload["content"],
                                 "images" if payload["images"] else "files", *files],
                                capture_output=True, text=True, timeout=150)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return {"state": "executed", "message": result.stdout.strip(), "delivery_confirmed": False}


if __name__ == "__main__":
    try:
        payload = json.loads(sys.stdin.read())
        with contextlib.redirect_stdout(sys.stderr):
            if sys.platform == "win32":
                result = windows(payload)
            elif sys.platform == "darwin":
                result = macos(payload)
            else:
                raise RuntimeError("桌面发送仅支持 Windows 和 macOS")
    except Exception as exc:
        result = {"state": "unknown", "error": str(exc), "message": "无法确认发送结果；请检查微信后再决定是否新建发送请求"}
    print(json.dumps(result, ensure_ascii=False))
