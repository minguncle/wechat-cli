"""Relocatable entrypoint for bundled runtimes and all child workers."""
import os
import platform
import runpy
import site
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
target = "windows-x64" if sys.platform == "win32" else "macos-arm64" if platform.machine() in {"arm64", "aarch64"} else "macos-x64"
libraries = root / "libraries" / target
if libraries.is_dir():
    site.addsitedir(str(libraries))
    os.environ["PYTHONPATH"] = os.pathsep.join([str(root), str(libraries)])
    if sys.platform == "win32":
        dll_path = libraries / "pywin32_system32"
        if dll_path.exists():
            dll_handle = os.add_dll_directory(str(dll_path))
            os.environ["PATH"] = str(dll_path) + os.pathsep + os.environ.get("PATH", "")
sys.path.insert(0, str(root))
if len(sys.argv) > 1 and sys.argv[1] in {"worker", "sender", "key-worker", "native-preflight", "native-image-once", "native-sender"}:
    command = sys.argv.pop(1)
    modules = {"worker": "source_worker", "sender": "sender", "key-worker": "key_capture",
               "native-preflight": "native_preflight", "native-image-once": "native_image_once",
               "native-sender": "native_sender"}
    runpy.run_module("novus_wechat." + modules[command], run_name="__main__")
else:
    from novus_wechat.cli import main
    main()
