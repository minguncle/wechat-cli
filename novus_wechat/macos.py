"""Read-only Mac diagnostics. No process attachment or signing changes."""
import hashlib
import json
import platform
import plistlib
import subprocess
import sys
from pathlib import Path


def inspect_clients(applications=None):
    applications = Path(applications or "/Applications")
    clients = []
    for name in ("WeChat.app", "Weixin.app"):
        app = (applications / name).resolve()
        info = app / "Contents/Info.plist"
        if not info.is_file():
            continue
        entry = {"application": str(app)}
        try:
            metadata = plistlib.loads(info.read_bytes())
            executable = (app / "Contents/MacOS" / metadata["CFBundleExecutable"]).resolve()
            if not executable.is_relative_to(app):
                raise ValueError("客户端可执行文件超出应用目录")
            entry.update(version=metadata.get("CFBundleShortVersionString"), build=metadata.get("CFBundleVersion"),
                         executable=str(executable))
            module = (app / "Contents/Resources/wechat.dylib").resolve()
            if not module.is_relative_to(app):
                raise ValueError("客户端模块超出应用目录")
            # Older Mac builds place native code in the main executable.
            binary = module if module.is_file() else executable
            with binary.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            arch = subprocess.run(["/usr/bin/lipo", "-archs", str(binary)], capture_output=True, text=True, timeout=10)
            entry["native_binary"] = {"path": str(binary), "sha256": digest, "bytes": binary.stat().st_size,
                                      "architectures": arch.stdout.strip().split() if arch.returncode == 0 else []}
        except (OSError, ValueError, KeyError, plistlib.InvalidFileException, subprocess.SubprocessError) as exc:
            entry["error"] = str(exc)
        clients.append(entry)
    return clients


def doctor(root, status=None):
    root = Path(root)
    checks = []
    settings = {}
    path = root / "settings.json"
    if path.is_file():
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(settings, dict):
                raise ValueError("settings.json 必须为对象")
        except (ValueError, OSError) as exc:
            checks.append({"name": "settings", "state": "failed", "detail": str(exc)})
            settings = {}
    source = Path(settings["source_dir"]).expanduser() if settings.get("source_dir") else None
    shards = list(source.glob("message/message_*.db")) if source and source.is_dir() else []
    checks.append({"name": "source", "state": "ready" if shards else "missing",
                   "database_shards": len(shards), "detail": "运行 discover 后用 settings --source-dir 明确选择账号" if not shards else "只读来源存在"})
    keys_present = any(Path(settings[k]).expanduser().is_file() for k in ("raw_key_file", "keys_file") if settings.get(k))
    checks.append({"name": "database_key", "state": "present_unverified" if keys_present else "missing",
                   "detail": "使用 sync --wait 验证真实解密；诊断不会输出密钥"})
    checks.append({"name": "image_key", "state": "configured" if settings.get("image_key") else "missing",
                   "detail": "需要时运行 init-image-key"})
    own_service = bool(status and Path(status["data_dir"]).resolve() == root.resolve())
    checks.append({"name": "service", "state": "running" if own_service else "other_data_dir" if status else "stopped"})
    verified = bool(own_service and status.get("capabilities", {}).get("source_connection_verified"))
    translated = False
    clients = []
    if sys.platform == "darwin":
        clients = inspect_clients()
        try:
            result = subprocess.run(["/usr/sbin/sysctl", "-in", "sysctl.proc_translated"], capture_output=True, text=True, timeout=5)
            translated = result.stdout.strip() == "1"
        except (OSError, subprocess.SubprocessError):
            pass
    checks.append({"name": "native_send", "state": "not_implemented" if sys.platform == "darwin" else "unverified",
                   "detail": "Mac 原生发送尚未适配；不会退回模拟键鼠或剪贴板"})
    return {"platform": sys.platform, "macos": platform.mac_ver()[0], "architecture": platform.machine(),
            "rosetta": translated, "python": platform.python_version(), "data_dir": str(root),
            "clients": clients, "checks": checks, "source_connection_verified": verified,
            "native_send_verified": False}
