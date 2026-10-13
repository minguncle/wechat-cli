from types import SimpleNamespace
import sys

import pytest

from novus_wechat import native_runtime as native


def setup_client(monkeypatch, tmp_path, count=1, supported=True):
    dll = tmp_path / 'Weixin.dll'
    dll.write_bytes(b'client')
    class Missing(Exception):
        pass
    class Denied(Exception):
        pass
    class Process:
        def __init__(self, pid):
            self.pid = pid
            self.info = {'name':'Weixin.exe','cmdline':[]}
            self.created = 123
        def create_time(self): return self.created
        def memory_maps(self): return [SimpleNamespace(path=str(dll))]
        def is_running(self): return True
    processes = [Process(i + 1) for i in range(count)]
    fake = SimpleNamespace(process_iter=lambda fields: processes, Process=lambda pid:processes[0],
                           NoSuchProcess=Missing, AccessDenied=Denied)
    monkeypatch.setitem(sys.modules, 'psutil', fake)
    monkeypatch.setattr(native.sys, 'platform', 'win32')
    if supported:
        monkeypatch.setattr(native, 'WINDOWS_IMAGE_BUILD', native.hashlib.sha256(b'client').hexdigest())
    return processes


def test_discovery_and_pid_reuse(monkeypatch, tmp_path):
    processes = setup_client(monkeypatch, tmp_path)
    runtime = native.discover()
    native.verify_identity(runtime)
    processes[0].created += 1
    with pytest.raises(ValueError, match='变化'):
        native.verify_identity(runtime)


@pytest.mark.parametrize('count', [0, 2])
def test_ambiguous_instance_rejected(monkeypatch, tmp_path, count):
    setup_client(monkeypatch, tmp_path, count)
    with pytest.raises(ValueError, match='恰好一个'):
        native.discover()


def test_unknown_build_rejected(monkeypatch, tmp_path):
    setup_client(monkeypatch, tmp_path, supported=False)
    with pytest.raises(ValueError, match='未适配'):
        native.discover()


def test_renderer_ignored(monkeypatch, tmp_path):
    processes = setup_client(monkeypatch, tmp_path, count=2)
    processes[1].info['cmdline'] = ['--type=renderer']
    assert native.discover()['pid'] == 1


def test_inaccessible_other_instance_rejected(monkeypatch, tmp_path):
    processes = setup_client(monkeypatch, tmp_path, count=2)
    def denied():
        raise sys.modules['psutil'].AccessDenied()
    processes[1].memory_maps = denied
    with pytest.raises(ValueError, match='所有微信主进程'):
        native.discover()


def test_exit_during_discovery_rejected(monkeypatch, tmp_path):
    processes = setup_client(monkeypatch, tmp_path)
    processes[0].is_running = lambda:False
    with pytest.raises(ValueError, match='恰好一个'):
        native.discover()


def test_account_cache_match(tmp_path):
    source = tmp_path / 'account' / 'db_storage'
    source.mkdir(parents=True)
    cache = source.parent / 'cache'
    cache.mkdir()
    resource = '个人目录/测试😀.png'
    path = cache / '2026-09' / 'HttpResource' / native.hashlib.md5(resource.encode()).hexdigest()
    assert native.verify_resource_account(source,resource,path) == path.resolve()
    assert not path.exists()
    with pytest.raises(ValueError, match='不一致'):
        native.verify_resource_account(source,resource,tmp_path / 'other' / path.name)
    with pytest.raises(ValueError, match='不匹配'):
        native.verify_resource_account(source,'different-resource',path)
    with pytest.raises(ValueError, match='不匹配'):
        native.verify_resource_account(source,resource,cache / '2026-13' / 'HttpResource' / path.name)


def test_verified_entry_mapping(monkeypatch, tmp_path):
    import struct
    data = bytearray(1024)
    struct.pack_into('<I',data,0x3c,0x80)
    data[0x80:0x84] = b'PE\0\0'
    struct.pack_into('<H',data,0x86,1)
    struct.pack_into('<H',data,0x94,0)
    struct.pack_into('<8sIIII16x',data,0x98,b'.text',256,0x1000,256,0x200)
    data[0x220:0x240] = bytes(range(32))
    path = tmp_path / 'fixture.dll'
    path.write_bytes(data)
    monkeypatch.setattr(native,'WINDOWS_IMAGE_BUILD',native.hashlib.sha256(data).hexdigest())
    assert native.verified_entries(path,[0x1020]) == [{'rva':0x1020,'prefix':bytes(range(32)).hex()}]
    with pytest.raises(ValueError,match='contained'):
        native.verified_entries(path,[0x10f0])
    path.write_bytes(data + b'changed')
    with pytest.raises(ValueError,match='Unrecognized'):
        native.verified_entries(path,[0x1020])
