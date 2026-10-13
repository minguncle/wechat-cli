import sys
from types import SimpleNamespace

import pytest
from PIL import Image

from novus_wechat import sender, windows_accessibility


@pytest.mark.parametrize("switch_target", [False, True])
def test_image_uses_send_button_and_rechecks_target(monkeypatch, tmp_path, switch_target):
    path = tmp_path / "test.png"
    Image.new("RGB", (8, 8), "white").save(path)
    calls = []
    editor = SimpleNamespace(element_info=SimpleNamespace(name="文件传输助手"), exists=lambda **kw: True, window_text=lambda: "")
    def type_keys(keys, **kwargs):
        assert keys == "^v"
        calls.append("paste")
        if switch_target:
            editor.element_info.name = "另一个会话"
    editor.type_keys = type_keys
    button = SimpleNamespace(wait=lambda *a, **kw: calls.append("ready"), click_input=lambda: calls.append("send"))
    window = SimpleNamespace(child_window=lambda **kw: button if kw.get("title") == "发送" else editor)
    monkeypatch.setitem(sys.modules, "pyweixin", SimpleNamespace(Messages=None, Files=None, Navigator=SimpleNamespace(open_dialog_window=lambda **kw: window)))
    monkeypatch.setitem(sys.modules, "pyweixin.Uielements", SimpleNamespace(Edits=SimpleNamespace(CurrentChatEdit={})))
    monkeypatch.setitem(sys.modules, "win32clipboard", SimpleNamespace(CF_DIB=8, OpenClipboard=lambda: None, EmptyClipboard=lambda: None, SetClipboardData=lambda *a: None, CloseClipboard=lambda: None))
    monkeypatch.setattr(windows_accessibility, "ensure_accessibility", lambda: None)
    monkeypatch.setattr(sender.time, "sleep", lambda seconds: None)
    payload = {"target":"文件传输助手", "images":True, "files":[str(path)], "content":""}
    if switch_target:
        with pytest.raises(RuntimeError, match="会话已变化"):
            sender.windows(payload)
        assert calls == ["paste", "ready"]
    else:
        assert sender.windows(payload)["state"] == "executed"
        assert calls == ["paste", "ready", "send"]
