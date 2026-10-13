"""Enable the verified WeChat Qt accessibility flag without changing OS settings."""
import ctypes
import importlib.util
import json
from ctypes import wintypes
from pathlib import Path

from .paths import ROOT, data_root


def ensure_accessibility():
    # Load only the UIA driver; the upstream package entrypoint initializes unrelated services.
    spec = importlib.util.spec_from_file_location(
        "novus_wechat_uia", ROOT / "vendor/wechatauto/wechatauto/uia_driver.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    driver = module.WeChatUIA()
    if driver.is_materialized():
        return
    windows = driver._wechat_hwnds()
    if not windows:
        raise RuntimeError("未找到已登录的微信窗口，请先打开微信")
    pids = {driver._pid_from_hwnd(hwnd) for hwnd in windows}
    if len(pids) != 1:
        raise RuntimeError("检测到多个微信进程，无法确定发送账号")
    pid = pids.pop()
    base, size, dll_path = driver._weixin_dll_module(pid)
    version = Path(dll_path).parent.name
    expected = module.QACCESSIBLE_ACTIVE_RVA_BY_VERSION.get(version)
    candidates = driver._qaccessible_candidate_rvas(dll_path)
    if expected is None or candidates != [expected] or not 0 <= expected < size:
        raise RuntimeError("当前微信控件不可见，且版本特征尚未验证；未修改微信进程")
    kernel = ctypes.windll.kernel32
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x410, False, pid)
    if not handle:
        raise RuntimeError("无法读取当前微信的控件访问状态")
    try:
        original = driver._read_process_byte(handle, base + expected)
    finally:
        kernel.CloseHandle(handle)
    if original not in (0, 1):
        raise RuntimeError("微信控件访问状态异常，未修改进程")
    root = data_root().resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "uia-original-state.json").write_text(json.dumps({
        "pid": pid, "version": version, "address": base + expected,
        "original": original, "rva": expected,
    }, indent=2), encoding="utf-8")
    if not driver._hot_activate_accessibility(windows[0], verify=True, max_candidates=1):
        raise RuntimeError("微信控件访问未恢复；未执行发送，请查看兼容性说明")
