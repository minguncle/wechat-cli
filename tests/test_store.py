import time

import pytest

from novus_wechat.service import Service
from novus_wechat.store import Store


def test_chat_page_uses_latest_visible_message(tmp_path):
    s = Store(tmp_path)
    for name in ['a','b','c']:
        s.create_chat(name,chat_id=name)
    old = s.create_message('a','older',timestamp=10)
    hidden = s.create_message('a','hidden',timestamp=30)
    s.patch_message(hidden['id'],{'deleted':True})
    s.create_message('b','recent',timestamp=20)
    first = s.chats(limit=1)
    second = s.chats(limit=1,offset=1)
    last = s.chats(limit=1,offset=2)
    assert first['items'][0]['id'] == 'b'
    assert second['items'][0]['id'] == 'a'
    assert second['items'][0]['last_time'] == old['timestamp']
    assert second['items'][0]['preview'] == 'older'
    assert second['items'][0]['message_count'] == 1
    assert last['items'][0]['id'] == 'c'
    assert last['items'][0]['last_time'] is None
    assert last['items'][0]['message_count'] == 0


def test_pagination_stable_and_query_bound(tmp_path):
    s = Store(tmp_path)
    chat = s.create_chat("群聊")
    now = int(time.time()) - 10
    for i in range(7):
        s.create_message(chat["id"], str(i), timestamp=now)
    first = s.messages(chat_id=chat["id"], limit=3)
    # Inserting a later message must not shift the next page.
    s.create_message(chat["id"], "新消息", timestamp=int(time.time()) + 1)
    second = s.messages(chat_id=chat["id"], limit=3, cursor=first["next_cursor"])
    third = s.messages(chat_id=chat["id"], limit=3, cursor=second["next_cursor"])
    ids = [r["id"] for p in (first, second, third) for r in p["items"]]
    assert len(ids) == len(set(ids)) == 7
    with pytest.raises(ValueError):
        s.messages(query="another", cursor=first["next_cursor"])


def test_sync_keeps_edits_and_tombstones(tmp_path):
    s = Store(tmp_path)
    chat = {"entity": "chat", "id": "wx", "name": "会话"}
    msg = {"entity": "message", "id": "m1", "chat_id": "wx", "timestamp": 1, "content": "原文"}
    s.ingest([chat, msg])
    s.patch_message("m1", {"content": "修订", "note": "备注", "deleted": True})
    s.ingest([chat, {**msg, "content": "来源变化"}])
    assert s.message("m1")["content"] == "修订"
    assert s.message("m1")["original_content"] == "来源变化"
    assert s.messages()["items"] == []
    s.patch_message("m1", {"deleted": False, "restore_original": True})
    assert s.message("m1")["content"] == "来源变化"
    assert s.message("m1")["note"] == "备注"


def test_import_idempotency_and_export_all(tmp_path):
    service = Service(tmp_path)
    payload = {"chat": "外部会话", "messages": [{"local_id": i, "timestamp": i + 1, "content": f"记录{i}"} for i in range(1100)]}
    first = service.import_records(payload)
    service.import_records(payload)
    import json
    exported = json.loads(service.export(first["chat_id"]))
    assert len(exported["messages"]) == 1100
    assert exported["messages"][0]["timestamp"] == 1
    assert len(service.export(first["chat_id"], "csv").splitlines()) == 1101


def test_send_idempotency_and_conflicting_reuse(tmp_path):
    s = Store(tmp_path)
    payload = {"chat": "x", "content": "hello"}
    assert s.reserve_send("r1", payload)[0]
    assert not s.reserve_send("r1", payload)[0]
    s.finish_send("r1", "unknown", {"reason": "timeout"})
    assert not s.reserve_send("r1", payload)[0]
    with pytest.raises(ValueError):
        s.reserve_send("r1", {**payload, "content": "changed"})


def test_csv_formula_escaped(tmp_path):
    service = Service(tmp_path)
    chat = service.store.create_chat("表格")
    service.store.create_message(chat["id"], "=HYPERLINK(\"x\")")
    assert "'=HYPERLINK" in service.export(chat["id"], "csv")
