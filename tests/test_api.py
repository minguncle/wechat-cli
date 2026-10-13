import pytest
from fastapi.testclient import TestClient
from novus_wechat.api import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, polling=False)) as client:
        client.headers["X-Novus-WeChat"] = "1"
        yield client


def test_web_and_crud_same_store(client):
    assert client.get("/").status_code == 200
    assert client.get("/assets/app.js").status_code == 200
    c = client.post("/api/chats", json={"name": "项目群", "kind": "group"}).json()
    m = client.post("/api/messages", json={"chat_id": c["id"], "content": "下午确认设计稿"}).json()
    assert client.get("/api/messages", params={"query": "设计稿"}).json()["items"][0]["id"] == m["id"]
    assert client.patch("/api/messages/" + m["id"], json={"note": "周五前", "content": "确认"}).json()["original_content"] == "下午确认设计稿"
    assert client.delete("/api/messages/" + m["id"]).status_code == 200
    assert client.get("/api/messages").json()["items"] == []
    client.patch("/api/messages/" + m["id"], json={"deleted": False})
    assert len(client.get("/api/messages").json()["items"]) == 1
    assert client.get("/api/export/" + c["id"]).json()["messages"][0]["note"] == "周五前"


def test_unconfigured_source_is_actionable(client):
    assert client.post("/api/sync").status_code == 400
    assert client.get("/api/status").json()["capabilities"]["source_connection_verified"] is False
    assert client.post("/api/send", json={"chat_id": "missing", "content": "x"}).status_code == 400


def test_bounds_and_origin(client):
    assert client.get("/api/messages?limit=0").status_code == 422
    assert client.get("/api/messages?limit=100000").status_code == 422
    assert client.post("/api/chats", json={"name": "x"}, headers={"Origin": "http://other.example"}).status_code == 403
    assert client.post("/api/chats", json={"name": "x"}, headers={"X-Novus-WeChat": ""}).status_code == 403


def test_import_and_attachment(client):
    content = '{"chat":"导入","messages":[{"timestamp":1,"content":"测试"}]}'.encode()
    assert client.post("/api/import-file", files={"file": ("test.json", content)}).json()["messages"] == 1
    assert client.post("/api/attachments", files={"file": ("x.png", b"test")}).json()["name"] == "x.png"
