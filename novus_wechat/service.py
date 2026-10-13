import csv
import errno
import hashlib
import io
import json
import os
import subprocess
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

from . import __version__
from .paths import ROOT
from .store import Store


class Service:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self.store = Store(self.root)
        self.settings_path = self.root / "settings.json"
        self.lock = threading.Lock()
        self.send_lock = threading.Lock()
        self.job = {"state": "idle"}
        self.stop_event = threading.Event()
        if not self.settings_path.exists():
            self.configure({"source_dir": "", "sync_interval": 30, "send_enabled": False})

    def settings(self):
        return json.loads(self.settings_path.read_text(encoding="utf-8"))

    def configure(self, changes):
        if self.lock.locked():
            raise ValueError("正在同步或解析媒体，请完成后再修改设置")
        settings = self.settings() if self.settings_path.exists() else {}
        allowed = {"source_dir", "keys_file", "raw_key_file", "image_key", "image_xor", "self_wxid", "sync_interval", "send_enabled"}
        for key, value in changes.items():
            if key not in allowed:
                raise ValueError(f"未知设置：{key}")
            if key in {"source_dir", "keys_file", "raw_key_file"} and value:
                value = str(Path(value).expanduser().resolve())
                if not Path(value).exists():
                    raise ValueError(f"路径不存在：{value}")
            settings[key] = value
        if not 5 <= int(settings.get("sync_interval", 30)) <= 86400:
            raise ValueError("同步间隔必须在 5 秒到 24 小时之间")
        tmp = self.settings_path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as output:
            if sys.platform != "win32":
                os.fchmod(output.fileno(), 0o600)
            json.dump(settings, output, ensure_ascii=False, indent=2)
        try:
            os.replace(tmp, self.settings_path)
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
            # Some redirected/encrypted Windows folders reject even sibling renames.
            # Keep the complete temporary copy until the destination write succeeds.
            shutil.copyfile(tmp, self.settings_path)
            tmp.unlink()
        if sys.platform != "win32":
            self.settings_path.chmod(0o600)
        return self.public_settings()

    def public_settings(self):
        value = self.settings()
        value["image_key_configured"] = bool(value.pop("image_key", ""))
        return value

    def discover(self):
        roots = [Path.home() / "Documents/xwechat_files", Path.home() / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"]
        if sys.platform == "win32":
            config = Path(os.environ.get("APPDATA", "")) / "Tencent/xwechat/config"
            for ini in config.glob("*.ini"):
                for encoding in ("utf-8", "gbk"):
                    try:
                        candidate = ini.read_text(encoding=encoding).strip()
                        if not any(c in candidate for c in "\n\r\0") and Path(candidate).is_dir():
                            roots.append(Path(candidate) / "xwechat_files")
                        break
                    except (UnicodeError, OSError):
                        continue
        return sorted({str(p.resolve()) for base in roots for p in base.glob("*/db_storage") if p.is_dir()})

    def status(self):
        last = self.store.meta("last_sync")
        with self.store.db() as db:
            chat_count = db.execute("SELECT count(*) FROM chats WHERE deleted=0").fetchone()[0]
        return {"version": __version__, "platform": sys.platform, "data_dir": str(self.root),
                "settings": self.public_settings(), "sync": self.job,
                "last_sync": self.store.meta("last_sync"),
                "chats": chat_count,
                "capabilities": {"read": True, "local_crud": True, "export": True,
                  "send_enabled": bool(self.settings().get("send_enabled")),
                  "native_send": "not_implemented" if sys.platform == "darwin" else "version_restricted" if sys.platform == "win32" else "unsupported_platform",
                  "source_write": False, "source_connection_verified": bool(last and last.get("source_dir") == self.settings().get("source_dir"))}}

    def synchronize(self, full=False, background=True):
        if not self.settings().get("source_dir"):
            raise ValueError("请先选择微信数据目录")
        if not self.lock.acquire(blocking=False):
            return self.job
        self.job = {"state": "running", "started": int(time.time()), "messages": 0}
        def work():
            try:
                command = [sys.executable, str(ROOT / "boot.py"), "worker", str(self.root), "sync"]
                if full:
                    command.append("--full")
                with (self.root / "sync.log").open("w", encoding="utf-8") as log:
                    process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=log,
                                               text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                    rows, result = [], None
                    for line in process.stdout:
                        item = json.loads(line)
                        if item.get("entity") == "result":
                            result = item
                        else:
                            rows.append(item)
                            if len(rows) >= 200:
                                self.job["messages"] += self.store.ingest(rows)
                                rows = []
                    if rows:
                        self.job["messages"] += self.store.ingest(rows)
                    code = process.wait()
                if result is None:
                    raise RuntimeError(f"同步进程退出（{code}），请查看 sync.log")
                self.job.update({k: v for k, v in result.items() if k not in {"entity", "stamps", "messages"}})
                if code == 0 and result["state"] == "complete":
                    (self.root / "adapter/sync-stamps.json").write_text(json.dumps(result["stamps"]), encoding="utf-8")
                    self.store.meta("last_sync", {"time": int(time.time()), "source_dir": result["source_dir"]})
            except Exception as exc:
                self.job.update(state="failed", errors=[str(exc)])
            finally:
                self.job["finished"] = int(time.time())
                self.lock.release()
        if background:
            threading.Thread(target=work, daemon=True).start()
        else:
            work()
        return self.job

    def poll(self):
        def loop():
            while not self.stop_event.wait(int(self.settings().get("sync_interval", 30))):
                if self.settings().get("source_dir"):
                    self.synchronize()
        threading.Thread(target=loop, daemon=True).start()

    def media(self, message_id):
        item = self.store.message(message_id)
        if item["deleted"] or self.store.chat(item["chat_id"])["deleted"]:
            raise ValueError("消息已在本地删除，请先恢复")
        if item.get("media_path"):
            path = Path(item["media_path"]).resolve()
            if path.is_relative_to(self.root / "media") and path.is_file():
                return path
        if item["kind"] != "image" or not item["source_ref"]:
            raise ValueError("此消息没有已下载的图片")
        if item["source_ref"]["source_dir"] != self.settings().get("source_dir"):
            raise ValueError("此消息属于另一数据目录，请在设置中切换来源")
        with self.lock:
            process = subprocess.run([sys.executable, str(ROOT / "boot.py"), "worker", str(self.root), "media"],
                cwd=ROOT, input=json.dumps(item["source_ref"]), capture_output=True, text=True,
                encoding="utf-8", timeout=180, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        result = json.loads(process.stdout.strip().splitlines()[-1])
        if not result.get("success"):
            raise ValueError(result.get("error") or "; ".join(result.get("errors", ["图片未下载或密钥不可用"])))
        path = Path(result["path"]).resolve()
        if not path.is_relative_to(self.root / "media") or not path.is_file():
            raise ValueError("图片解析返回了无效路径")
        with self.store.db() as db:
            db.execute("UPDATE messages SET media_path=? WHERE id=?", (str(path), message_id))
        return path

    def import_records(self, payload):
        # Accept our export and the upstream's single-chat JSON format.
        messages = payload.get("messages", [])
        if not isinstance(messages, list) or len(messages) > 500000:
            raise ValueError("导入格式无效或单次超过 500000 条")
        chat = payload.get("chat", "导入会话")
        if isinstance(chat, str):
            chat = {"id": payload.get("username") or hashlib.sha256(chat.encode()).hexdigest()[:16], "name": chat}
        chat_id = "import:" + str(chat.get("id") or uuid.uuid4().hex)
        rows = [{"entity": "chat", "id": chat_id, "name": chat.get("name", "导入会话"),
                 "kind": chat.get("kind", "group" if payload.get("is_group") else "private"), "source": "import"}]
        for index, original in enumerate(messages):
            timestamp = int(original.get("timestamp", 0))
            content = str(original.get("content", ""))
            identity = str(original.get("id") or original.get("local_id") or index)
            mid = hashlib.sha256(f"{chat_id}:{identity}:{timestamp}".encode()).hexdigest()
            rows.append({"entity": "message", "id": mid, "chat_id": chat_id, "timestamp": timestamp,
                         "content": content, "sender": str(original.get("sender", "")),
                         "kind": original.get("kind", original.get("type", "text")), "source": "import"})
        count = self.store.ingest(rows)
        return {"chat_id": chat_id, "messages": count}

    def export(self, chat_id, format="json", start=0, end=0):
        if start < 0 or end < 0 or (end and end < start):
            raise ValueError("导出时间范围无效")
        chat = self.store.chat(chat_id)
        messages, cursor = [], ""
        while True:
            page = self.store.messages(chat_id=chat_id, start=start, end=end, cursor=cursor, limit=500)
            messages.extend(page["items"])
            cursor = page["next_cursor"]
            if not cursor:
                break
        messages.reverse()
        if format == "xlsx":
            from .excel_export import build_xlsx
            return build_xlsx(chat, messages, self.media)
        if format == "json":
            return json.dumps({"chat": chat, "messages": messages}, ensure_ascii=False, indent=2)
        if format == "csv":
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["消息ID", "时间戳", "发送者", "类型", "内容", "原始内容", "备注"])
            def cell(value):
                text = str(value)
                return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text
            for m in messages:
                writer.writerow([cell(m[k]) for k in ("id", "timestamp", "sender", "kind", "content", "original_content", "note")])
            return "\ufeff" + output.getvalue()
        if format == "markdown":
            lines = [f"# {chat.get('alias') or chat['name']}", ""]
            for m in messages:
                lines += [f"## {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(m['timestamp']))} · {m['sender']}",
                          f"消息 ID：{m['id']}", "", m["content"], ""]
            return "\n".join(lines)
        raise ValueError("导出格式仅支持 xlsx、json、csv、markdown")

    def send(self, chat_id, content="", files=None, request_id="", images=False):
        if not self.settings().get("send_enabled"):
            raise ValueError("发送功能尚未在设置中开启")
        chat = self.store.chat(chat_id)
        if chat["deleted"] or chat["source"] != "wechat":
            raise ValueError("只能向有效的微信来源会话发送")
        source = self.settings().get("source_dir", "")
        account_prefix = "wechat:" + hashlib.sha256(source.encode()).hexdigest()[:12] + ":"
        if not source or not chat_id.startswith(account_prefix):
            raise ValueError("该会话不属于当前配置的数据目录，请先切换正确账号来源")
        files = [str(Path(p).expanduser().resolve()) for p in (files or [])]
        if not content.strip() and not files:
            raise ValueError("发送内容不能为空")
        if any(not Path(p).is_file() for p in files):
            raise ValueError("发送文件不存在")
        if images and any(Path(p).suffix.lower() not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"} for p in files):
            raise ValueError("图片类型不支持")
        payload = {"username": chat_id[len(account_prefix):], "chat_id": chat_id, "content": content,
                   "files": files, "images": images, "source_dir": source, "data_dir": str(self.root)}
        from .native_sender import validate
        validate(payload)
        request_id = request_id or uuid.uuid4().hex
        with self.send_lock:
            new, previous = self.store.reserve_send(request_id, payload)
            if not new:
                return self.store.send_status(request_id)
            try:
                process = subprocess.run([sys.executable, str(ROOT / "boot.py"), "native-sender"], input=json.dumps(payload),
                    cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=180,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                result = json.loads(process.stdout.strip().splitlines()[-1])
                state = result.get("state", "unknown")
            except Exception as exc:
                state, result = "unknown", {"error": str(exc), "message": "不能确认发送结果，请检查微信，勿自动重试"}
            return self.store.finish_send(request_id, state, result)
