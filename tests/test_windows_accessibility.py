import json
from types import SimpleNamespace

import pytest

from novus_wechat import windows_accessibility as access


def setup_driver(monkeypatch, tmp_path, *, visible=False, version="4.1.13.65", candidates=None, original=0):
    calls = []
    driver = SimpleNamespace(
        is_materialized=lambda: visible,
        _wechat_hwnds=lambda: [1],
        _pid_from_hwnd=lambda hwnd: 7,
        _weixin_dll_module=lambda pid: (1000, 500, f"/Weixin/{version}/Weixin.dll"),
        _qaccessible_candidate_rvas=lambda path: [20] if candidates is None else candidates,
        _read_process_byte=lambda handle, address: original,
        _hot_activate_accessibility=lambda *a, **kw: calls.append("activate") or True,
    )
    module = SimpleNamespace(WeChatUIA=lambda: driver, QACCESSIBLE_ACTIVE_RVA_BY_VERSION={"4.1.13.65": 20})
    monkeypatch.setattr(access.importlib.util, "spec_from_file_location", lambda *a: SimpleNamespace(loader=SimpleNamespace(exec_module=lambda m: None)))
    monkeypatch.setattr(access.importlib.util, "module_from_spec", lambda spec: module)
    kernel = SimpleNamespace(OpenProcess=lambda *a: 8, CloseHandle=lambda h: calls.append("close"))
    monkeypatch.setattr(access.ctypes, "windll", SimpleNamespace(kernel32=kernel), raising=False)
    monkeypatch.setattr(access, "data_root", lambda: tmp_path)
    return driver, calls


def test_visible_controls_do_not_modify_process(monkeypatch, tmp_path):
    _, calls = setup_driver(monkeypatch, tmp_path, visible=True)
    access.ensure_accessibility()
    assert not calls
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("changes", [{"version": "unknown"}, {"candidates": [20, 30]}, {"original": 12}])
def test_unverified_process_state_never_activates(monkeypatch, tmp_path, changes):
    _, calls = setup_driver(monkeypatch, tmp_path, **changes)
    with pytest.raises(RuntimeError):
        access.ensure_accessibility()
    assert "activate" not in calls


def test_verified_activation_saves_original_before_write(monkeypatch, tmp_path):
    driver, calls = setup_driver(monkeypatch, tmp_path)
    def activate(*args, **kwargs):
        saved = json.loads((tmp_path / "uia-original-state.json").read_text())
        assert saved["original"] == 0 and saved["address"] == 1020 and saved["pid"] == 7
        assert kwargs == {"verify": True, "max_candidates": 1}
        calls.append("activate")
        return True
    driver._hot_activate_accessibility = activate
    access.ensure_accessibility()
    assert calls == ["close", "activate"]


def test_multiple_wechat_processes_are_rejected(monkeypatch, tmp_path):
    driver, calls = setup_driver(monkeypatch, tmp_path)
    driver._wechat_hwnds = lambda: [1, 2]
    driver._pid_from_hwnd = lambda hwnd: hwnd
    with pytest.raises(RuntimeError, match="多个微信进程"):
        access.ensure_accessibility()
    assert not calls
