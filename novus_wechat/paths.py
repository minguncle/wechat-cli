import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def data_root():
    if os.environ.get("NOVUS_WECHAT_DATA"):
        return Path(os.environ["NOVUS_WECHAT_DATA"]).expanduser().resolve()
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Novus/WeChat"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Novus/WeChat"
    return Path.home() / ".local/share/novus-wechat"
