"""Build a fixed, offline Skill bundle for Windows x64 and Mac Apple Silicon."""
import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tarfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLATFORMS = {
    "windows-x64": "cpython-3.12.11-windows-x86_64-none",
    "macos-arm64": "cpython-3.12.11-macos-aarch64-none",
}


def copy(source, destination):
    shutil.copytree(source, destination, dirs_exist_ok=True, symlinks=True,
                    ignore=shutil.ignore_patterns("__pycache__", ".git", ".pytest_cache", "*.pyc"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", default="0.2.0a6")
    parser.add_argument("--prepare", action="store_true", help="download pinned runtimes/dependencies before building")
    args = parser.parse_args()
    if args.prepare:
        os.environ["UV_PYTHON_BIN_DIR"] = str(ROOT / ".build/python-bin")
        subprocess.run(["uv", "python", "install", *PLATFORMS.values(), "--install-dir", str(ROOT / ".build/python")], check=True)
        for target in PLATFORMS:
            target_env = {**os.environ, "MACOSX_DEPLOYMENT_TARGET": "13.0"}
            lock = "requirements-windows.lock" if target == "windows-x64" else "requirements-macos.lock" if target == "macos-arm64" else "requirements-macos-x64.lock"
            uv_target = {"windows-x64": "x86_64-pc-windows-msvc", "macos-arm64": "aarch64-apple-darwin", "macos-x64": "x86_64-apple-darwin"}[target]
            subprocess.run(["uv", "pip", "install", "--target", str(ROOT / ".build/site" / target),
                "--python-version", "3.12", "--python-platform", uv_target, "-r", str(ROOT / lock)], check=True, env=target_env)
    output = ROOT / "dist" / f"novus-wechat-{args.version}"
    bundle = output / "wechat-cli"
    if output.exists():
        raise RuntimeError(f"版本目录已存在，请使用新版本号：{output}")
    bundle.mkdir(parents=True)
    for relative in ("novus_wechat", "web", "platform", "vendor", "native"):
        copy(ROOT / relative, bundle / relative)
    for path in (ROOT / "skill/novus-wechat").iterdir():
        if path.is_file():
            shutil.copy2(path, bundle / path.name)
        elif path.is_dir():
            copy(path, bundle / path.name)
    for name in ("boot.py", "README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "CURRENT_STATUS.md", "upstreams.lock.json", "requirements-windows.lock", "requirements-macos.lock", "requirements-macos-x64.lock"):
        shutil.copy2(ROOT / name, bundle / name)
    copy(ROOT / "docs", bundle / "references")
    readme = (bundle / "README.md").read_text(encoding="utf-8")
    (bundle / "README.md").write_text(readme.replace("](docs/", "](references/").replace("](skill/novus-wechat/SKILL.md)", "](SKILL.md)"), encoding="utf-8")
    for target, python in PLATFORMS.items():
        runtime = ROOT / ".build/python" / python
        libraries = ROOT / ".build/site" / target
        if not runtime.is_dir() or not libraries.is_dir():
            raise RuntimeError(f"缺少平台组件：{target}，先执行 --prepare")
        copy(runtime, bundle / "runtime" / target)
        copy(libraries, bundle / "libraries" / target)
    info = {"name": "novus-wechat", "version": args.version,
            "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "platforms": list(PLATFORMS), "python": "3.12.11",
            "validation": json.loads((ROOT / "validation.json").read_text())}
    (bundle / "BUILD_INFO.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    files = {}
    for path in sorted(bundle.rglob("*")):
        if path.is_file():
            files[path.relative_to(bundle).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    (bundle / "SHA256SUMS.json").write_text(json.dumps(files, indent=2), encoding="utf-8")
    # ZIP stores executable modes explicitly; tar is supplied for Unix symlink/mode fidelity.
    archive = ROOT / "dist" / f"novus-wechat-{args.version}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=5) as z:
        for path in sorted(bundle.rglob("*")):
            if not path.is_file():
                continue
            if path.relative_to(bundle).as_posix() not in files and path.name != "SHA256SUMS.json":
                continue
            name = path.relative_to(output).as_posix()
            executable = path.suffix in {".command", ".sh"} or "/bin/" in name or path.suffix == ".exe"
            data = zipfile.ZipInfo(name)
            data.create_system = 3
            data.external_attr = ((stat.S_IFREG | (0o755 if executable else 0o644)) << 16)
            data.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(data, path.read_bytes())
    tar_path = ROOT / "dist" / f"novus-wechat-{args.version}.tar.gz"
    def mode(info):
        relative = info.name.removeprefix("wechat-cli/")
        if "__pycache__" in info.name.split("/") or info.name.endswith(".pyc"):
            return None
        if info.isfile() and relative not in files and relative != "SHA256SUMS.json":
            return None
        if info.name.endswith((".command", ".sh")) or "/bin/" in info.name:
            info.mode |= 0o111
        return info
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(bundle, arcname="wechat-cli", filter=mode)
    manifest = {path.name: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
                for path in (archive, tar_path)}
    (ROOT / "dist" / f"novus-wechat-{args.version}.checksums.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
