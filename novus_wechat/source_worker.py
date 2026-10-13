"""Isolated upstream process. stdout is NDJSON; diagnostics use stderr."""
import contextlib
import hashlib
import importlib
import json
import os
import re
import shutil
import sqlite3
import struct
import sys
import time
from pathlib import Path

from .paths import ROOT


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def connect_ro(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def committed_wal(upstream, wal_path, out_path, key):
    """Apply only frames through the last commit in the current WAL generation."""
    data = Path(wal_path).read_bytes()
    if len(data) < 32:
        return
    salts, frames, committed, db_size = data[16:24], [], 0, 0
    for offset in range(32, len(data) - 24 - 4096 + 1, 24 + 4096):
        header = data[offset:offset + 24]
        if header[8:16] != salts:
            break
        page, size = struct.unpack(">II", header[:8])
        if page < 1 or page > 10000000:
            break
        frames.append((page, data[offset + 24:offset + 24 + 4096]))
        if size:
            committed, db_size = len(frames), size
    with open(out_path, "r+b") as out:
        for page, data in frames[:committed]:
            out.seek((page - 1) * 4096)
            out.write(upstream.decrypt_page(key, data, page))
        if committed:
            out.truncate(db_size * 4096)


class SnapshotCache:
    def __init__(self, upstream, config, root):
        self.upstream, self.config, self.root = upstream, config, root
        self.source = Path(config["source_dir"]).expanduser().resolve()
        self.snapshots = root / "snapshots"
        self.snapshots.mkdir(exist_ok=True)
        self.keys = {}
        keys_file = config.get("keys_file")
        if keys_file and Path(keys_file).is_file():
            self.keys = json.loads(Path(keys_file).read_text(encoding="utf-8-sig"))
        raw_file = config.get("raw_key_file")
        self.raw = Path(raw_file).read_text().strip() if raw_file and Path(raw_file).is_file() else None
        self.cached = {}

    def get(self, relative):
        relative = relative.replace("\\", "/")
        source = (self.source / relative).resolve()
        if not source.is_relative_to(self.source):
            raise ValueError("无效的数据库路径")
        if not source.is_file():
            return None
        if relative in self.cached:
            return self.cached[relative]
        target = self.snapshots / (hashlib.sha256(str(source).encode()).hexdigest()[:24] + ".db")
        with source.open("rb") as stream:
            header = stream.read(16)
        if header == b"SQLite format 3\x00":
            with contextlib.closing(connect_ro(source)) as src, contextlib.closing(sqlite3.connect(target)) as dst:
                src.backup(dst)
        else:
            from key_utils import get_key_info
            info = get_key_info(self.keys, relative)
            if info:
                key = bytes.fromhex(info["enc_key"])
            elif self.raw:
                key = hashlib.pbkdf2_hmac("sha512", bytes.fromhex(self.raw), header, 256000, 32)
            else:
                raise ValueError(f"数据库尚未解锁：{relative}。请导入密钥或完成初始化。")
            wal = Path(str(source) + "-wal")
            encrypted, wal_copy = target.with_suffix(".encrypted"), target.with_suffix(".wal-copy")
            signature = lambda: (source.stat().st_mtime_ns, source.stat().st_size,
                                 wal.stat().st_mtime_ns if wal.exists() else 0,
                                 wal.stat().st_size if wal.exists() else 0)
            for attempt in range(4):
                before = signature()
                shutil.copyfile(source, encrypted)
                if wal.exists():
                    shutil.copyfile(wal, wal_copy)
                else:
                    wal_copy.unlink(missing_ok=True)
                if before == signature():
                    break
                if attempt == 3:
                    raise RuntimeError("微信正在持续写入，未取得一致快照，请稍后同步")
                time.sleep(0.1)
            self.upstream.full_decrypt(str(encrypted), str(target), key)
            if wal_copy.exists():
                committed_wal(self.upstream, wal_copy, target, key)
            encrypted.unlink(missing_ok=True)
            wal_copy.unlink(missing_ok=True)
            with contextlib.closing(connect_ro(target)) as db:
                if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("数据库快照校验失败，请检查密钥和客户端版本")
        self.cached[relative] = str(target)
        return str(target)


def load_upstream(config, root):
    adapter = root / "adapter"
    adapter.mkdir(exist_ok=True)
    (adapter / "config.json").write_text(json.dumps({
        "db_dir": config["source_dir"], "keys_file": config.get("keys_file") or str(adapter / "missing-keys.json"),
        "decrypted_dir": str(root / "snapshots"), "decoded_image_dir": str(root / "media"),
    }), encoding="utf-8")
    os.environ["WECHAT_DECRYPT_APP_DIR"] = str(adapter)
    os.environ["NOVUS_WECHAT_CACHE_DIR"] = str(adapter / "cache")
    sys.path.insert(0, str(ROOT / "vendor/wechat-decrypt"))
    with contextlib.redirect_stdout(sys.stderr):
        upstream = importlib.import_module("mcp_server")
        helpers = importlib.import_module("chat_export_helpers")
    upstream._cache = SnapshotCache(upstream, config, root)
    return upstream, helpers


def sync(config, root, full=False):
    upstream, helpers = load_upstream(config, root)
    cache = upstream._cache
    names = {}
    contact_names = {}
    contact = cache.get("contact/contact.db")
    if contact:
        with contextlib.closing(connect_ro(contact)) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(contact)")}
            alias_column = "alias" if "alias" in columns else "''"
            for username, nick, remark, alias in db.execute(f"SELECT username,nick_name,remark,{alias_column} FROM contact"):
                if username:
                    names[username] = remark or nick or username
                    contact_names[username] = {"nickname": nick or "", "remark": remark or "", "wechat_alias": alias or ""}
    upstream._contact_names = names
    upstream.get_contact_names = lambda: names
    upstream.get_contact_full = lambda: [{"username": k, "nick_name": v} for k, v in names.items()]
    account = config.get("account_id") or cache.source.parent.name
    account_key = hashlib.sha256(str(cache.source).encode()).hexdigest()[:12]
    prefix = f"wechat:{account_key}:"
    my_wxid = config.get("self_wxid") or re.sub(r"_[0-9a-fA-F]{4,}$", "", account)
    upstream._self_username = my_wxid
    for username, name in names.items():
        emit({"entity": "chat", "id": prefix + username, "name": name,
              "kind": "group" if username.endswith("@chatroom") else "private", "source": "wechat",
              "contact_names": contact_names[username]})
    stamp_file = root / "adapter/sync-stamps.json"
    stamps = json.loads(stamp_file.read_text()) if stamp_file.exists() else {}
    next_stamps, messages, failures = {}, 0, []
    sources = [p for p in sorted(cache.source.glob("message/message_*.db")) if re.fullmatch(r"message_\d+\.db", p.name)]
    if not sources:
        raise ValueError("所选目录中没有 message/message_N.db，请选择正确的 db_storage 目录")
    for source in sources:
        if not re.fullmatch(r"message_\d+\.db", source.name):
            continue
        relative = source.relative_to(cache.source).as_posix()
        wal = Path(str(source) + "-wal")
        signature = [source.stat().st_mtime_ns, wal.stat().st_mtime_ns if wal.exists() else 0]
        stamp_key = str(source)
        if not full and stamps.get(stamp_key, {}).get("signature") == signature:
            next_stamps[stamp_key] = stamps[stamp_key]
            continue
        try:
            path = cache.get(relative)
            last_time = 0 if full else max(0, stamps.get(stamp_key, {}).get("time", 0) - 120)
            newest = last_time
            with contextlib.closing(connect_ro(path)) as db:
                id_map = upstream._load_name2id_maps(db)
                contexts = upstream._load_search_contexts_from_db(db, path, names)
                for ctx in contexts:
                    username = ctx["username"]
                    if not username:
                        failures.append(f"无法识别会话：{relative}/{ctx['table_name']}")
                        continue
                    chat_id = prefix + username
                    emit({"entity": "chat", "id": chat_id, "name": names.get(username, username),
                          "kind": "group" if ctx["is_group"] else "private", "source": "wechat"})
                    table = ctx["table_name"]
                    if not re.fullmatch(r"Msg_[0-9a-fA-F]{32}", table):
                        raise ValueError("无法识别消息表")
                    columns = {row[1] for row in db.execute(f"PRAGMA table_info([{table}])")}
                    server_column = next((x for x in ("server_id", "svr_id", "msg_svr_id") if x in columns), None)
                    sid = f"[{server_column}]" if server_column else "0"
                    rows = db.execute(f"""SELECT local_id,local_type,create_time,real_sender_id,
                      message_content,WCDB_CT_message_content,{sid} FROM [{table}]
                      WHERE create_time>=? ORDER BY create_time,local_id""", (last_time,))
                    for row in rows:
                        local_id, local_type, timestamp, sender_id, content, ct, server_id = row
                        if timestamp is None:
                            continue
                        rendered, extras = helpers._extract_content(local_id, local_type, content, ct, username, ctx["display_name"])
                        sender = helpers._resolve_sender(row[:6], ctx, names, id_map)
                        identity = str(server_id) if server_id else f"{relative}:{local_id}:{timestamp}"
                        message_id = hashlib.sha256(f"{chat_id}:{identity}".encode()).hexdigest()
                        kind = (extras or {}).get("type") or helpers._msg_type_str(local_type)
                        emit({"entity": "message", "id": message_id, "chat_id": chat_id,
                              "timestamp": timestamp, "sender": sender, "sender_id": id_map.get(sender_id, ""),
                              "kind": kind, "content": rendered or ("[图片]" if kind == "image" else f"[{kind}]"),
                              "source": "wechat", "source_ref": {"username": username, "local_id": local_id,
                              "timestamp": timestamp, "database": relative, "source_dir": str(cache.source)}})
                        newest = max(newest, timestamp)
                        messages += 1
            next_stamps[stamp_key] = {"signature": signature, "time": newest}
        except Exception as exc:
            failures.append(f"{relative}: {exc}")
    if failures:
        emit({"entity": "result", "state": "partial", "messages": messages, "errors": failures})
        return 2
    # Parent saves stamps only after committing all emitted records.
    emit({"entity": "result", "state": "complete", "messages": messages, "stamps": next_stamps,
          "source_dir": str(cache.source)})
    return 0


def media(config, root, reference):
    from types import MethodType
    upstream, _ = load_upstream(config, root)
    from decode_image import ImageResolver, extract_md5_from_packed_info
    resolver = ImageResolver(str(Path(config["source_dir"]).parent), str(root / "media"), upstream._cache,
                             aes_key=config.get("image_key") or None, xor_key=int(config.get("image_xor", 136)))
    def exact_md5(self, username, local_id):
        path = self.cache.get("message/message_resource.db")
        if not path:
            return None
        with contextlib.closing(connect_ro(path)) as db:
            row = db.execute("""SELECT r.packed_info FROM MessageResourceInfo r
              JOIN ChatName2Id c ON c.rowid=r.chat_id WHERE c.user_name=? AND r.message_local_id=?
              AND r.message_create_time=? AND (r.message_local_type % 4294967296)=3 LIMIT 1""",
              (username, local_id, reference["timestamp"])).fetchone()
            return extract_md5_from_packed_info(row[0]) if row and row[0] else None
    resolver.get_image_md5 = MethodType(exact_md5, resolver)
    result = resolver.decode_image(reference["username"], reference["local_id"])
    if result.get("success") and result.get("format") == "hevc":
        import av
        original = Path(result["path"])
        preview = original.with_suffix(".jpg")
        with av.open(str(original), format="hevc") as container:
            frame = next(container.decode(video=0), None)
            if frame is None:
                raise ValueError("微信 HEVC 图片中没有可解码的画面")
            frame.to_image().save(preview, format="JPEG", quality=90)
        result.update(path=str(preview), format="jpg", size=preview.stat().st_size)
    emit(result)
    return 0 if result.get("success") else 2


def main():
    root = Path(sys.argv[1]).resolve()
    config = json.loads((root / "settings.json").read_text(encoding="utf-8"))
    if not config.get("source_dir"):
        raise ValueError("请先配置微信数据目录")
    if sys.argv[2] == "media":
        return media(config, root, json.loads(sys.stdin.read()))
    return sync(config, root, full="--full" in sys.argv)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        emit({"entity": "result", "state": "failed", "errors": [str(exc)]})
        sys.exit(1)
