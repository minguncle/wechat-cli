"""Derive the image key from local cache metadata without restarting WeChat."""
import json
import queue
import re
import sys
from pathlib import Path

from .paths import ROOT


def capture(root):
    config = json.loads((Path(root) / "settings.json").read_text(encoding="utf-8"))
    if not config.get("source_dir"):
        raise ValueError("请先在 Web 中选择微信 db_storage 目录")
    account = Path(config["source_dir"]).parent
    match = re.fullmatch(r"(.+)_([0-9a-fA-F]{4})", account.name)
    if not match:
        raise ValueError("账号目录名无法用于图片密钥推导，请选择微信原始数据目录")
    sys.path.insert(0, str(ROOT / "vendor/wechat-decrypt"))
    from find_all_keys import find_xor_key, find_v2_ciphertext, _brute_worker
    attach = str(account / "msg/attach")
    xor = find_xor_key(attach)
    ciphertext, _ = find_v2_ciphertext(attach)
    if xor is None or not ciphertext:
        raise ValueError("图片缓存不足，请先在微信中打开 2–3 张图片，再执行初始化")
    result = queue.SimpleQueue()
    # One bounded local scan avoids multiprocessing bootstrap in the packaged entrypoint.
    _brute_worker(0, 1 << 24, xor, bytes.fromhex(match[2]), match[1].encode("ascii"), ciphertext, result)
    if result.empty():
        raise ValueError("未找到有效图片密钥，请检查原始账号目录和本地图片缓存")
    _, key = result.get()
    return {"image_key": key, "image_xor": xor}
