from pathlib import Path
import plistlib

from novus_wechat import cli


def test_windows_login_keeps_custom_data_directory(monkeypatch,tmp_path):
    root = tmp_path / '个人目录 with spaces'
    monkeypatch.setattr(cli.sys,'platform','win32')
    monkeypatch.setenv('APPDATA',str(tmp_path / 'roaming'))
    monkeypatch.setenv('NOVUS_WECHAT_DATA',str(root))
    result = cli.autostart('install',18767)
    path = Path(result['path'])
    script = path.read_text(encoding='utf-16')
    assert f'shell.Environment("PROCESS")("NOVUS_WECHAT_DATA") = "{root.resolve()}"' in script
    assert 'shell.CurrentDirectory = ' in script
    assert '--port 18767' in script
    assert ', 0, False' in script
    assert result['effective'] == 'next_login'
    cli.autostart('remove',18767)
    assert not path.exists()


def test_macos_login_keeps_custom_data_directory(monkeypatch,tmp_path):
    root = tmp_path / '个人目录 with spaces'
    monkeypatch.setattr(cli.sys,'platform','darwin')
    monkeypatch.setattr(cli.Path,'home',lambda:tmp_path / 'home')
    monkeypatch.setenv('NOVUS_WECHAT_DATA',str(root))
    result = cli.autostart('install',18767)
    value = plistlib.loads(Path(result['path']).read_bytes())
    assert value['EnvironmentVariables']['NOVUS_WECHAT_DATA'] == str(root.resolve())
    assert value['ProgramArguments'][-2:] == ['--port','18767']
    assert value['RunAtLoad'] and value['KeepAlive']
