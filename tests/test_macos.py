import json
import plistlib
from pathlib import Path
from types import SimpleNamespace

from novus_wechat import macos
from novus_wechat.service import Service
import os
import pytest


@pytest.mark.skipif(os.name == "nt", reason="POSIX private file mode")
def test_settings_with_image_key_are_private(tmp_path):
    service = Service(tmp_path)
    service.configure({"image_key": "private-fixture-key"})
    assert (tmp_path / "settings.json").stat().st_mode & 0o777 == 0o600
    assert "private-fixture-key" not in json.dumps(service.public_settings())


def test_doctor_without_setup_does_not_create_data(tmp_path):
    root = tmp_path / "not-created"
    result = macos.doctor(root)
    assert not root.exists()
    assert not result["source_connection_verified"]
    assert not result["native_send_verified"]


def test_doctor_does_not_leak_key_or_claim_a_different_service(tmp_path):
    key = tmp_path / "key.txt"
    key.write_text("secret-database-key")
    (tmp_path / "settings.json").write_text(json.dumps({"raw_key_file": str(key), "image_key": "secret-image-key"}))
    result = macos.doctor(tmp_path, {"data_dir": str(tmp_path / "other"), "capabilities": {"source_connection_verified": True}})
    assert not result["source_connection_verified"]
    assert "secret" not in json.dumps(result)
    assert next(c for c in result["checks"] if c["name"] == "service")["state"] == "other_data_dir"


def test_mac_inspector_handles_old_main_executable_and_new_module(tmp_path, monkeypatch):
    app = tmp_path / "WeChat.app/Contents"
    (app / "MacOS").mkdir(parents=True)
    (app / "MacOS/WeChat").write_bytes(b"synthetic client")
    (app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "WeChat", "CFBundleShortVersionString": "4.1.7"}))
    monkeypatch.setattr(macos.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout="arm64 x86_64"))
    old = macos.inspect_clients(tmp_path)[0]
    assert Path(old["native_binary"]["path"]).parts[-2:] == ("MacOS", "WeChat")
    (app / "Resources").mkdir()
    (app / "Resources/wechat.dylib").write_bytes(b"synthetic module")
    new = macos.inspect_clients(tmp_path)[0]
    assert Path(new["native_binary"]["path"]).parts[-2:] == ("Resources", "wechat.dylib")
    assert old["native_binary"]["sha256"] != new["native_binary"]["sha256"]


def test_inspector_rejects_executable_outside_bundle(tmp_path):
    app = tmp_path / "WeChat.app/Contents"
    app.mkdir(parents=True)
    (app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleExecutable": "/tmp/outside"}))
    assert "超出应用目录" in macos.inspect_clients(tmp_path)[0]["error"]
