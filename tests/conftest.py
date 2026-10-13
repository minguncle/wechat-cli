import hashlib
import sqlite3
import time

import pytest


@pytest.fixture
def wechat_fixture(tmp_path):
    root = tmp_path / "wxid_self_a123/db_storage"
    (root / "contact").mkdir(parents=True)
    (root / "message").mkdir()
    with sqlite3.connect(root / "contact/contact.db") as db:
        db.execute("CREATE TABLE contact(username TEXT,nick_name TEXT,remark TEXT,alias TEXT,local_type INTEGER)")
        db.executemany("INSERT INTO contact VALUES(?,?,?,?,?)", [
            ("wxid_self", "我", "", "", 0), ("test@chatroom", "项目群", "项目群", "", 0),
            ("wxid_alice", "Alice", "小林", "", 0)])
    table = "Msg_" + hashlib.md5(b"test@chatroom").hexdigest()
    path = root / "message/message_10.db"  # More than nine shards must be supported.
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE Name2Id(user_name TEXT)")
        db.executemany("INSERT INTO Name2Id VALUES(?)", [("wxid_self",), ("test@chatroom",), ("wxid_alice",)])
        db.execute(f"CREATE TABLE {table}(local_id INTEGER,local_type INTEGER,create_time INTEGER,real_sender_id INTEGER,message_content TEXT,WCDB_CT_message_content INTEGER,server_id TEXT)")
        now = int(time.time()) - 100
        db.executemany(f"INSERT INTO {table} VALUES(?,?,?,?,?,?,?)", [
            (1, 1, now, 3, "wxid_alice:\n明天下午确认设计稿", 0, "10001"),
            (2, 1, now, 1, "wxid_self:\n收到，我来确认", 0, "10002"),
            (3, 3, now + 1, 3, "wxid_alice:\n<msg><img/></msg>", 0, "10003")])
    return root, path, table
