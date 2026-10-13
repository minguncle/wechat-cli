"""Recipient integrity checks without client attachment or real sends."""
import hashlib
import json

from PIL import Image
import pytest

from novus_wechat.native_image_once import prepare, stage_cache, claim
from novus_wechat import native_probe


def test_prepared_recipient_cannot_change(tmp_path):
    picture = tmp_path / 'source.png'
    Image.new('RGB', (8, 8)).save(picture)
    recipient = '123456@chatroom'
    request = prepare(picture, tmp_path / 'outbox', recipient=recipient)
    image = request.parent / 'image.png'
    cache_root = tmp_path / 'cache'
    cache = cache_root / 'HttpResource' / hashlib.md5(str(image).encode()).hexdigest()
    cache.parent.mkdir(parents=True)
    with pytest.raises(ValueError, match='request changed'):
        stage_cache(request, cache, cache_root, recipient='filehelper')
    assert not cache.exists()
    stage_cache(request, cache, cache_root, recipient=recipient)
    with pytest.raises(ValueError, match='target'):
        claim(request, recipient='filehelper')
    assert not (request.parent / 'attempt.json').exists()
    assert claim(request, recipient=recipient) == image
    with pytest.raises(FileExistsError):
        claim(request, recipient=recipient)


@pytest.mark.parametrize('recipient', ['wxid_test_person', '123456@chatroom', '测试标识😀'])
def test_native_spec_preserves_recipient_and_utf8_length(tmp_path, monkeypatch, recipient):
    monkeypatch.setattr(native_probe, 'verified_entries', lambda *args: [])
    captured = []

    class Template:
        def replace(self, marker, value, count):
            captured.append(json.loads(value))
            return value

    monkeypatch.setattr(native_probe, 'AGENT', Template())
    native_probe.build_agent(tmp_path / 'fixture.dll', 7, tmp_path / 'absent.png', recipient=recipient)
    assert captured[0]['recipient'] == recipient
    assert captured[0]['recipientBytes'] == len(recipient.encode('utf-8'))
