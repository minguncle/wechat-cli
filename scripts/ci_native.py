"""Native-platform verification in the CI checkout only."""
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
os.chdir(root)
print("NATIVE_HOST", sys.platform, platform.mac_ver()[0], platform.machine(), flush=True)
os.environ["UV_PYTHON_BIN_DIR"] = str(root / ".build/python-bin")
previous_link = Path.home() / ".local/bin/python3.12"
if previous_link.is_symlink() and previous_link.resolve(strict=False).is_relative_to(root / ".build/python"):
    print("REMOVE_PREVIOUS_CI_LINK", previous_link, "->", previous_link.resolve(strict=False), flush=True)
    previous_link.unlink()
uv = shutil.which("uv")
if not uv:
    for candidate in (Path.home() / ".local/bin/uv", Path.home() / ".cargo/bin/uv", Path("/opt/homebrew/bin/uv")):
        if candidate.is_file():
            uv = str(candidate)
            break
if not uv:
    tools = root / ".build/ci-tools"
    subprocess.run([sys.executable, "-m", "pip", "install", "--target", str(tools), "uv"], check=True)
    uv = str(tools / "bin/uv")
target_arch = os.environ.get("NOVUS_TEST_ARCH", platform.machine())
requested_python = "3.12.11"
if sys.platform == "darwin":
    requested_python = "cpython-3.12.11-macos-" + ("aarch64" if target_arch == "arm64" else "x86_64") + "-none"
    subprocess.run([uv, "python", "install", requested_python, "--install-dir", str(root / ".build/python")], check=True)
    requested_python = str(root / ".build/python" / requested_python / "bin/python3.12")
subprocess.run([uv, "venv", ".venv", "--clear", "--python", requested_python], check=True)
python = root / (".venv/Scripts/python.exe" if sys.platform == "win32" else ".venv/bin/python")
if sys.platform == "darwin":
    lock = "requirements-macos.lock" if target_arch == "arm64" else "requirements-macos-x64.lock"
else:
    lock = "requirements-windows.lock"
subprocess.run([uv, "pip", "install", "--python", str(python), "-r", lock, "pytest"], check=True)
if sys.platform == "darwin":
    snapshot = ["--snapshot"] if os.environ.get("NOVUS_MAC_SNAPSHOT") == "1" and target_arch == "arm64" else []
    subprocess.run([str(python), "scripts/inspect_macos_client.py", *snapshot], check=True)
subprocess.run([str(python), "-m", "pytest", "-q", "--junitxml=.build/native-tests.xml"], check=True)
if sys.platform == "darwin":
    subprocess.run(["osacompile", "-o", ".build/send.scpt", "platform/macos/send.applescript"], check=True)
actual_arch = subprocess.check_output([str(python), "-c", "import platform; print(platform.machine())"], text=True).strip()
assert actual_arch == target_arch, (actual_arch, target_arch)
print("NATIVE_PLATFORM_PASS", sys.platform, actual_arch)
