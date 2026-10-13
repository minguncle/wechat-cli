import hashlib
import sqlite3
import struct
import time

from novus_wechat.service import Service
from novus_wechat.source_worker import committed_wal


def test_source_sync_and_incremental_update(tmp_path, wechat_fixture):
    source, shard, table = wechat_fixture
    before = hashlib.sha256(shard.read_bytes()).hexdigest()
    service = Service(tmp_path / "application")
    service.configure({"source_dir": str(source)})
    result = service.synchronize(background=False)
    assert result["state"] == "complete", result
    assert len(service.store.messages()["items"]) == 3
    assert hashlib.sha256(shard.read_bytes()).hexdigest() == before
    m = service.store.messages(query="设计稿")["items"][0]
    assert m["sender"] == "小林"
    alice = service.store.chats(query="Alice")["items"]
    assert len(alice) == 1 and alice[0]["name"] == "小林"
    assert service.store.chats(query="小林")["items"][0]["id"] == alice[0]["id"]
    service.store.patch_message(m["id"], {"content": "本地修订", "deleted": True})
    with sqlite3.connect(shard) as db:
        db.execute(f"INSERT INTO {table} VALUES(?,?,?,?,?,?,?)", (4, 1, int(time.time()) - 1, 3, "wxid_alice:\n新增内容", 0, "10004"))
    result = service.synchronize(background=False)
    assert result["state"] == "complete", result
    assert service.store.messages(query="新增内容")["items"]
    assert service.store.chats(query="Alice")["items"][0]["id"] == alice[0]["id"]
    assert service.store.message(m["id"])["content"] == "本地修订"
    assert service.store.message(m["id"])["deleted"] == 1


def test_contact_search_uses_alias_and_keeps_duplicate_names(tmp_path, wechat_fixture):
    source, _, _ = wechat_fixture
    with sqlite3.connect(source / "contact/contact.db") as db:
        db.execute("UPDATE contact SET alias='alice-work' WHERE username='wxid_alice'")
        db.execute("INSERT INTO contact VALUES(?,?,?,?,?)", ('wxid_other', 'Alice', '另一个同事', 'other-work', 0))
    service = Service(tmp_path / 'application')
    service.configure({'source_dir': str(source)})
    assert service.synchronize(background=False)['state'] == 'complete'
    assert service.store.chats(query='ALICE')['total'] == 2
    assert service.store.chats(query='alice-work')['items'][0]['name'] == '小林'
    with sqlite3.connect(source / "contact/contact.db") as db:
        db.execute("UPDATE contact SET alias='new-alias',nick_name='NewName' WHERE username='wxid_alice'")
    assert service.synchronize(background=False)['state'] == 'complete'
    assert service.store.chats(query='alice-work')['total'] == 0
    assert service.store.chats(query='new-alias')['items'][0]['name'] == '小林'


def test_wal_only_applies_committed_frames(tmp_path):
    salt = b"12345678"
    header = bytearray(32)
    header[16:24] = salt
    def frame(page, commit, content):
        return struct.pack(">II", page, commit) + salt + bytes(8) + content * 4096
    wal = tmp_path / "db-wal"
    wal.write_bytes(header + frame(1, 1, b"a") + frame(1, 0, b"b"))
    out = tmp_path / "out.db"
    out.write_bytes(b"0" * 4096)
    class Upstream:
        @staticmethod
        def decrypt_page(key, data, page):
            return data
    committed_wal(Upstream, wal, out, b"key")
    assert out.read_bytes() == b"a" * 4096
