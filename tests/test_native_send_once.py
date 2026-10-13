import json
import hashlib
from pathlib import Path
import runpy
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image


MODULE = runpy.run_path(str(Path(__file__).parents[1] / "scripts/send_native_image_once.py"))


def prepared(tmp_path):
    image = tmp_path / "fixture.png"
    Image.new("RGB", (2, 2), "white").save(image)
    request = MODULE["prepare"](image, tmp_path / "outbox")
    root = tmp_path / "cache"
    parent = root / "2026-09" / "HttpResource"
    parent.mkdir(parents=True)
    cache = parent / hashlib.md5(str(request.parent / "image.png").encode()).hexdigest()
    MODULE["stage_cache"](request, cache, root)
    return request


def test_concurrent_attempts_consume_request_once(tmp_path):
    request = prepared(tmp_path)
    def attempt():
        try:
            MODULE["claim"](request)
            return "claimed"
        except FileExistsError:
            return "refused"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: attempt(), range(2)))
    assert sorted(outcomes) == ["claimed", "refused"]
    with pytest.raises(FileExistsError):
        MODULE["claim"](request)


def test_changed_image_is_rejected_before_attempt(tmp_path):
    request = prepared(tmp_path)
    (request.parent / "image.png").write_bytes(b"changed")
    with pytest.raises(ValueError, match="image changed"):
        MODULE["claim"](request)
    assert not (request.parent / "attempt.json").exists()


def test_changed_target_is_rejected(tmp_path):
    request = prepared(tmp_path)
    value = json.loads(request.read_text())
    value["target"] = "someone_else"
    request.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="target"):
        MODULE["claim"](request)
    assert not (request.parent / "attempt.json").exists()


def test_changed_cache_is_rejected(tmp_path):
    request = prepared(tmp_path)
    value = json.loads(request.read_text())
    Path(value["cache_path"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="cache"):
        MODULE["claim"](request)
    assert not (request.parent / "attempt.json").exists()


def test_existing_cache_is_not_overwritten(tmp_path):
    request = prepared(tmp_path)
    cache = Path(json.loads(request.read_text())["cache_path"])
    original = cache.read_bytes()
    with pytest.raises(FileExistsError):
        MODULE["stage_cache"](request, cache, tmp_path / "cache")
    assert cache.read_bytes() == original


def test_cache_outside_account_root_is_rejected(tmp_path):
    request = prepared(tmp_path)
    cache = Path(json.loads(request.read_text())["cache_path"])
    with pytest.raises(ValueError, match="cache path"):
        MODULE["stage_cache"](request, cache, tmp_path / "different-account")
