"""Exercise the whole worker with native calls replaced; never attach or send."""
import hashlib
import json
from pathlib import Path

from PIL import Image
import pytest

from novus_wechat import native_sender, native_preflight, native_paths, native_probe, native_runtime


@pytest.mark.parametrize('failure', ['none','preflight','resolve','account','build','unknown'])
@pytest.mark.parametrize('recipient', ['filehelper','wxid_test_person','123456@chatroom'])
def test_native_worker_order_and_failures(tmp_path,monkeypatch,failure,recipient):
    source = tmp_path / 'account' / 'db_storage'
    source.mkdir(parents=True)
    cache_dir = source.parent / 'cache' / '2026-09' / 'HttpResource'
    cache_dir.mkdir(parents=True)
    picture = tmp_path / 'source.png'
    Image.new('RGB',(8,8)).save(picture)
    root = tmp_path / '个人目录 😀'
    calls = []
    def check(*args):
        calls.append('preflight')
        if failure == 'preflight':
            raise ValueError('preflight failed')
        return {'runtime':{'pid':1,'dll':'fixture.dll'},'candidate_thread':7}
    def path_agent(dll,thread,resource):
        return ('path',resource)
    def send_agent(dll,thread,image,**kwargs):
        calls.append('build')
        assert kwargs['negative'] is False
        assert kwargs['recipient'] == recipient
        assert json.loads((image.parent / 'request.json').read_text())['target'] == recipient
        assert kwargs['expected_cache'].read_bytes() == image.read_bytes()
        if failure == 'build':
            raise ValueError('build failed')
        return ('send',str(image))
    def run(agent,pid,output):
        calls.append(agent[0])
        if agent[0] == 'path':
            if failure == 'resolve':
                return []
            cache = cache_dir / hashlib.md5(agent[1].encode()).hexdigest()
            if failure == 'account':
                cache = tmp_path / 'wrong-account' / cache.name
            return [{'kind':'probe_result','resolved_path':str(cache),'sending_called':False}]
        assert (Path(agent[1]).parent / 'attempt.json').is_file()
        return [] if failure == 'unknown' else [{'kind':'probe_result','successBit':1}]
    monkeypatch.setattr(native_sender.sys,'platform','win32')
    monkeypatch.setattr(native_preflight,'preflight',check)
    monkeypatch.setattr(native_paths,'build_agent',path_agent)
    monkeypatch.setattr(native_probe,'build_agent',send_agent)
    monkeypatch.setattr(native_probe,'run_agent',run)
    monkeypatch.setattr(native_runtime,'verify_identity',lambda runtime:calls.append('identity'))
    payload = {'username':recipient,'images':True,'files':[str(picture)],'content':'',
               'source_dir':str(source),'data_dir':str(root)}
    if failure in {'none','unknown'}:
        result = native_sender.send(payload)
        assert result['state'] == ('unknown' if failure == 'unknown' else 'native_success_unconfirmed')
        assert result['delivery_confirmed'] is False
        assert calls == ['preflight','identity','path','identity','build','send']
    else:
        with pytest.raises(ValueError):
            native_sender.send(payload)
        assert 'send' not in calls
        assert not list(root.rglob('attempt.json'))
