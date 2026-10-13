"""Local management copy. Source databases are never opened for writing."""
import base64
import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "library.sqlite3"
        with self.db() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS chats (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'private',
              source TEXT NOT NULL DEFAULT 'local', alias TEXT, note TEXT NOT NULL DEFAULT '',
              deleted INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS messages (
              id TEXT PRIMARY KEY, chat_id TEXT NOT NULL REFERENCES chats(id),
              timestamp INTEGER NOT NULL, sender TEXT NOT NULL DEFAULT '',
              sender_id TEXT NOT NULL DEFAULT '', kind TEXT NOT NULL DEFAULT 'text',
              content TEXT NOT NULL DEFAULT '', original_content TEXT NOT NULL DEFAULT '',
              source TEXT NOT NULL DEFAULT 'local', edited INTEGER NOT NULL DEFAULT 0,
              deleted INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '',
              media_path TEXT, source_ref TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS messages_time ON messages(timestamp DESC,id DESC);
            CREATE INDEX IF NOT EXISTS messages_chat_time ON messages(chat_id,timestamp DESC,id DESC);
            CREATE INDEX IF NOT EXISTS messages_chat_visible_time ON messages(chat_id,deleted,timestamp DESC,id DESC);
            CREATE TABLE IF NOT EXISTS sends (
              request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, state TEXT NOT NULL,
              payload TEXT NOT NULL, result TEXT NOT NULL DEFAULT '{}', created INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS contact_names (
              chat_id TEXT PRIMARY KEY REFERENCES chats(id), nickname TEXT NOT NULL,
              remark TEXT NOT NULL, wechat_alias TEXT NOT NULL
            );
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def meta(self, key, value=None):
        with self.db() as db:
            if value is not None:
                db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, json.dumps(value)))
                return value
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def chat(self, chat_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
        if not row:
            raise KeyError("会话不存在")
        return dict(row)

    def chats(self, query="", kind="", limit=100, offset=0, include_deleted=False):
        clauses, args = ["1=1"], []
        if not include_deleted:
            clauses.append("c.deleted=0")
        if query:
            clauses.append("""(instr(lower(c.name),lower(?))>0 OR instr(lower(coalesce(c.alias,'')),lower(?))>0
              OR instr(c.id,?)>0 OR EXISTS (SELECT 1 FROM contact_names n WHERE n.chat_id=c.id AND
              (instr(lower(n.nickname),lower(?))>0 OR instr(lower(n.remark),lower(?))>0
               OR instr(lower(n.wechat_alias),lower(?))>0)))""")
            args.extend([query] * 6)
        if kind:
            clauses.append("c.kind=?")
            args.append(kind)
        where = " AND ".join(clauses)
        with self.db() as db:
            total = db.execute(f"SELECT count(*) FROM chats c WHERE {where}", args).fetchone()[0]
            rows = db.execute(f"""WITH page AS MATERIALIZED (
              SELECT c.*, coalesce(c.alias,c.name) display_name,
              (SELECT timestamp FROM messages WHERE chat_id=c.id AND deleted=0
                ORDER BY timestamp DESC,id DESC LIMIT 1) last_time
              FROM chats c WHERE {where} ORDER BY last_time DESC,c.id LIMIT ? OFFSET ?
              ) SELECT c.*,
              (SELECT count(*) FROM messages WHERE chat_id=c.id AND deleted=0) message_count,
              (SELECT substr(content,1,100) FROM messages WHERE chat_id=c.id AND deleted=0 ORDER BY timestamp DESC,id DESC LIMIT 1) preview
              FROM page c ORDER BY last_time DESC,c.id""",
              (*args, limit, offset)).fetchall()
        return {"items": [dict(x) for x in rows], "total": total, "next_offset": offset + limit if offset + limit < total else None}

    def create_chat(self, name, kind="private", chat_id=None):
        if not name.strip():
            raise ValueError("会话名称不能为空")
        chat_id = chat_id or "local:" + uuid.uuid4().hex
        with self.db() as db:
            db.execute("INSERT INTO chats(id,name,kind) VALUES(?,?,?)", (chat_id, name, kind))
        return self.chat(chat_id)

    def patch_chat(self, chat_id, changes):
        self.chat(chat_id)
        allowed = {k: v for k, v in changes.items() if k in {"alias", "note", "deleted"}}
        if allowed:
            with self.db() as db:
                db.execute("UPDATE chats SET " + ",".join(f"{k}=?" for k in allowed) + " WHERE id=?", (*allowed.values(), chat_id))
        return self.chat(chat_id)

    def ingest(self, rows):
        """Idempotent sync; preserve local edits, notes and tombstones."""
        count = 0
        with self.db() as db:
            for item in rows:
                if item["entity"] == "chat":
                    db.execute("""INSERT INTO chats(id,name,kind,source) VALUES(?,?,?,?)
                      ON CONFLICT(id) DO UPDATE SET name=excluded.name,kind=excluded.kind""",
                      (item["id"], item["name"], item.get("kind", "private"), item.get("source", "wechat")))
                    if "contact_names" in item:
                        names = item["contact_names"]
                        db.execute("""INSERT INTO contact_names VALUES(?,?,?,?) ON CONFLICT(chat_id)
                          DO UPDATE SET nickname=excluded.nickname,remark=excluded.remark,
                          wechat_alias=excluded.wechat_alias""",
                          (item["id"], names.get("nickname", ""), names.get("remark", ""), names.get("wechat_alias", "")))
                    continue
                db.execute("""INSERT INTO messages
                  (id,chat_id,timestamp,sender,sender_id,kind,content,original_content,source,media_path,source_ref)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                  original_content=excluded.original_content,
                  content=CASE WHEN messages.edited=0 THEN excluded.content ELSE messages.content END,
                  sender=excluded.sender,sender_id=excluded.sender_id,kind=excluded.kind,
                  media_path=coalesce(excluded.media_path,messages.media_path),source_ref=excluded.source_ref""",
                  (item["id"], item["chat_id"], int(item["timestamp"]), item.get("sender", ""),
                   item.get("sender_id", ""), item.get("kind", "text"), item.get("content", ""),
                   item.get("original_content", item.get("content", "")), item.get("source", "wechat"),
                   item.get("media_path"), json.dumps(item.get("source_ref", {}))))
                count += 1
        return count

    def message(self, message_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
        if not row:
            raise KeyError("消息不存在")
        item = dict(row)
        item["source_ref"] = json.loads(item["source_ref"])
        return item

    def messages(self, chat_id="", query="", start=0, end=0, kind="", limit=100,
                 cursor="", include_deleted=False):
        filters = [chat_id, query, int(start), int(end), kind, bool(include_deleted)]
        signature = hashlib.sha256(json.dumps(filters).encode()).hexdigest()[:16]
        snapshot, boundary = int(end or time.time()), None
        if cursor:
            try:
                page = json.loads(base64.urlsafe_b64decode(cursor))
                if page["query"] != signature:
                    raise ValueError()
                snapshot, boundary = int(page["snapshot"]), page["before"]
                if not isinstance(boundary[0], int) or not isinstance(boundary[1], str):
                    raise ValueError()
            except (ValueError, KeyError, TypeError, IndexError):
                raise ValueError("分页游标无效或查询条件已经变化") from None
        clauses, args = ["m.timestamp<=?"], [snapshot]
        if not include_deleted:
            clauses += ["m.deleted=0", "c.deleted=0"]
        for expression, value in [("m.chat_id=?", chat_id), ("m.kind=?", kind), ("m.timestamp>=?", start)]:
            if value:
                clauses.append(expression)
                args.append(value)
        if query:
            clauses.append("(instr(lower(m.content),lower(?))>0 OR instr(lower(m.note),lower(?))>0)")
            args += [query, query]
        if boundary:
            clauses.append("(m.timestamp,m.id)<(?,?)")
            args += boundary
        with self.db() as db:
            rows = db.execute(f"""SELECT m.*,coalesce(c.alias,c.name) chat_name FROM messages m
              JOIN chats c ON c.id=m.chat_id WHERE {' AND '.join(clauses)}
              ORDER BY m.timestamp DESC,m.id DESC LIMIT ?""", (*args, limit + 1)).fetchall()
        items = [dict(x) for x in rows[:limit]]
        for item in items:
            item["source_ref"] = json.loads(item["source_ref"])
        next_cursor = None
        if len(rows) > limit:
            last = items[-1]
            next_cursor = base64.urlsafe_b64encode(json.dumps({"snapshot": snapshot, "query": signature,
                "before": [last["timestamp"], last["id"]]}).encode()).decode()
        return {"items": items, "next_cursor": next_cursor, "snapshot": snapshot}

    def create_message(self, chat_id, content, sender="我", timestamp=0, kind="text"):
        self.chat(chat_id)
        message_id = "local:" + uuid.uuid4().hex
        self.ingest([{"entity": "message", "id": message_id, "chat_id": chat_id,
                      "content": content, "sender": sender, "timestamp": timestamp or int(time.time()),
                      "kind": kind, "source": "local"}])
        return self.message(message_id)

    def patch_message(self, message_id, changes):
        self.message(message_id)
        allowed = {k: v for k, v in changes.items() if k in {"content", "note", "deleted"}}
        if "content" in allowed:
            allowed["edited"] = 1
        if changes.get("restore_original"):
            allowed.pop("content", None)
            allowed["edited"] = 0
        with self.db() as db:
            if allowed:
                db.execute("UPDATE messages SET " + ",".join(f"{k}=?" for k in allowed) + " WHERE id=?", (*allowed.values(), message_id))
            if changes.get("restore_original"):
                db.execute("UPDATE messages SET content=original_content WHERE id=?", (message_id,))
        return self.message(message_id)

    def reserve_send(self, request_id, payload):
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        fingerprint = hashlib.sha256(encoded.encode()).hexdigest()
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO sends VALUES(?,?,?,?,?,?)",
                       (request_id, fingerprint, "pending", encoded, "{}", int(time.time())))
            new = db.execute("SELECT changes()").fetchone()[0] == 1
            row = dict(db.execute("SELECT * FROM sends WHERE request_id=?", (request_id,)).fetchone())
        if row["fingerprint"] != fingerprint:
            raise ValueError("request_id 已用于另一项发送请求")
        return new, row

    def finish_send(self, request_id, state, result):
        with self.db() as db:
            db.execute("UPDATE sends SET state=?,result=? WHERE request_id=?", (state, json.dumps(result, ensure_ascii=False), request_id))
        return self.send_status(request_id)

    def send_status(self, request_id):
        with self.db() as db:
            row = db.execute("SELECT request_id,state,result,created FROM sends WHERE request_id=?", (request_id,)).fetchone()
        if not row:
            raise KeyError("发送请求不存在")
        return {**dict(row), "result": json.loads(row["result"])}
