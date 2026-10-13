import hashlib
import io
import os
import struct
import subprocess
import sys

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from PIL import Image

from novus_wechat.paths import ROOT
from novus_wechat.service import Service


def test_image_key_initialization_from_public_cache(tmp_path):
    username, uin = "wxid_public", 7
    suffix = hashlib.md5(str(uin).encode()).digest()[:2].hex()
    key = hashlib.md5((str(uin) + username).encode()).hexdigest()[:16]
    account = tmp_path / (username + "_" + suffix)
    source = account / "db_storage"
    source.mkdir(parents=True)
    images = account / "msg/attach/public/2026-09/Img"
    images.mkdir(parents=True)
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), "green").save(buffer, "JPEG")
    image = buffer.getvalue()
    data = bytes.fromhex("070856320807") + struct.pack("<II", 32, 12) + b"\0"
    data += AES.new(key.encode(), AES.MODE_ECB).encrypt(pad(image[:32], 16))
    data += image[32:-12] + bytes(b ^ uin for b in image[-12:])
    (images / "public_t.dat").write_bytes(data)
    root = tmp_path / "app"
    Service(root).configure({"source_dir": str(source)})
    result = subprocess.run([sys.executable, str(ROOT / "boot.py"), "init-image-key"],
        env={**os.environ, "NOVUS_WECHAT_DATA": str(root), "PYTHONIOENCODING": "utf-8"},
        capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    assert key not in result.stdout + result.stderr
    settings = Service(root).settings()
    assert settings["image_key"] == key and settings["image_xor"] == uin
    assert not (ROOT / "vendor/wechat-decrypt/config.json").exists()
