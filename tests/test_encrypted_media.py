import hashlib
import errno
import io
import importlib.util
import json
import sqlite3
import struct
from contextlib import closing
from pathlib import Path

import pytest
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from fastapi.testclient import TestClient
from PIL import Image

from novus_wechat.api import create_app
from novus_wechat.service import Service


def test_settings_on_storage_rejecting_sibling_rename(tmp_path, monkeypatch):
    def cross_device(*args):
        raise OSError(errno.EXDEV, "synthetic cross-device rename")
    monkeypatch.setattr("novus_wechat.service.os.replace", cross_device)
    service = Service(tmp_path / "app")
    assert service.settings()["sync_interval"] == 30
    service.configure({"sync_interval": 90})
    assert Service(tmp_path / "app").settings()["sync_interval"] == 90
    assert not (tmp_path / "app/settings.tmp").exists()


def test_image_on_storage_rejecting_sibling_rename(tmp_path, monkeypatch):
    module_path = Path(__file__).resolve().parent.parent / "vendor/wechat-decrypt/decode_image.py"
    spec = importlib.util.spec_from_file_location("image_storage_test", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), "green").save(buffer, "PNG")
    content = buffer.getvalue()
    digest = hashlib.md5(content).hexdigest()
    directory = tmp_path / "msg/attach" / hashlib.md5(b"test@chatroom").hexdigest() / "2026-09/Img"
    directory.mkdir(parents=True)
    (directory / (digest + ".dat")).write_bytes(bytes(b ^ 0x88 for b in content))
    resolver = module.ImageResolver(str(tmp_path), str(tmp_path / "decoded"), None)
    resolver.get_image_md5 = lambda *args: digest
    def cross_device(*args):
        raise OSError(errno.EXDEV, "synthetic cross-device rename")
    monkeypatch.setattr(module.os, "replace", cross_device)
    result = resolver.decode_image("test@chatroom", 1)
    assert result["success"]
    assert Path(result["path"]).read_bytes() == content


@pytest.mark.parametrize("key_mode", ["raw", "derived", "wrong", "missing"])
def test_encrypted_source_with_independent_database_salts(tmp_path, key_mode):
    source = tmp_path / "wxid_public/db_storage"
    paths = {"contact/contact.db": "contact", "message/message_10.db": "message_10"}
    raw = hashlib.sha256(b"novus-public-synthetic-test-key-not-an-account-key").digest()
    keys, originals = {}, {}
    for relative, name in paths.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        content = (Path(__file__).parent / "fixtures/cipher" / (name + ".enc")).read_bytes()
        path.write_bytes(content)
        originals[path] = hashlib.sha256(content).hexdigest()
        keys[relative] = {"enc_key": hashlib.pbkdf2_hmac("sha512", raw, content[:16], 256000, 32).hex()}
    service = Service(tmp_path / "app")
    settings = {"source_dir": str(source)}
    if key_mode in {"raw", "wrong"}:
        key = tmp_path / "public-fixture-key.txt"
        key.write_text((raw if key_mode == "raw" else bytes(32)).hex())
        settings["raw_key_file"] = str(key)
    elif key_mode == "derived":
        key = tmp_path / "public-fixture-keys.json"
        key.write_text(json.dumps(keys))
        settings["keys_file"] = str(key)
    service.configure(settings)
    result = service.synchronize(background=False)
    if key_mode in {"raw", "derived"}:
        assert result["state"] == "complete", result
        messages = service.store.messages()["items"]
        assert len(messages) == 1
        assert messages[0]["content"] == "加密数据库验证记录"
        assert messages[0]["sender"] == "小林"
    else:
        assert result["state"] in {"failed", "partial"}, result
        assert service.store.messages()["items"] == []
        assert not service.status()["capabilities"]["source_connection_verified"]
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in originals.items())


@pytest.mark.parametrize("encoding", ["xor", "aes-v2", "aes-v2-hevc"])
@pytest.mark.parametrize("root_form", ["absolute", "relative"])
def test_image_resource_resolution_and_http_bytes(tmp_path, wechat_fixture, encoding, root_form, monkeypatch):
    source, shard, table = wechat_fixture
    with closing(sqlite3.connect(shard)) as db:
        timestamp = db.execute(f"SELECT create_time FROM {table} WHERE local_id=3").fetchone()[0]
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (27, 122, 65)).save(buffer, "PNG")
    image = buffer.getvalue()
    if encoding == "aes-v2-hevc":
        image = (Path(__file__).parent / "fixtures/public-image.wxgf").read_bytes()
    digest = hashlib.md5(image).hexdigest()
    marker = bytes([0x12, 0x22, 0x0a, 0x20])
    with closing(sqlite3.connect(source / "message/message_resource.db")) as db:
        db.execute("CREATE TABLE ChatName2Id(user_name TEXT)")
        db.execute("INSERT INTO ChatName2Id VALUES('test@chatroom')")
        db.execute("CREATE TABLE MessageResourceInfo(chat_id INTEGER,message_local_id INTEGER,message_create_time INTEGER,message_local_type INTEGER,packed_info BLOB)")
        db.executemany("INSERT INTO MessageResourceInfo VALUES(1,3,?,3,?)", [
            (timestamp, marker + digest.encode()),
            (timestamp + 1, marker + b"0" * 32),  # Same local ID: must not select the newer, unrelated image.
        ])
        db.commit()
    directory = source.parent / "msg/attach" / hashlib.md5(b"test@chatroom").hexdigest() / "2026-09/Img"
    directory.mkdir(parents=True)
    key = "public-test-key!"
    if encoding == "xor":
        content = bytes(b ^ 0x88 for b in image)
    else:
        head, tail = image[:32], image[-12:]
        content = bytes.fromhex("070856320807") + struct.pack("<II", len(head), len(tail)) + b"\x00"
        content += AES.new(key.encode(), AES.MODE_ECB).encrypt(pad(head, 16))
        content += image[32:-12] + bytes(b ^ 0x88 for b in tail)
    (directory / (digest + ".dat")).write_bytes(content)
    monkeypatch.chdir(tmp_path)
    app = create_app(tmp_path / "app" if root_form == "absolute" else Path("app"), polling=False)
    service = app.state.service
    service.configure({"source_dir": str(source), "image_key": key})
    assert service.synchronize(background=False)["state"] == "complete"
    message = service.store.messages(kind="image")["items"][0]
    with TestClient(app) as client:
        response = client.get("/api/messages/" + message["id"] + "/media")
        assert response.status_code == 200, response.text
        if encoding == "aes-v2-hevc":
            assert response.headers["content-type"] == "image/jpeg"
            with Image.open(io.BytesIO(response.content)) as decoded:
                assert decoded.size == (64, 64)
                assert all(abs(a-b) <= 12 for a,b in zip(decoded.getpixel((32,32)), (27,122,65)))
        else:
            assert response.headers["content-type"] == "image/png"
            assert response.content == image
