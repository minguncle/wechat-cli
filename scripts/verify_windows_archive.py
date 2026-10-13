"""Extract the published ZIP locally and verify using its own Python executable."""
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parent.parent
version = sys.argv[1]
archive = root / "dist" / f"novus-wechat-{version}.zip"
checksums = json.loads((root / "dist" / f"novus-wechat-{version}.checksums.json").read_text())
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
assert digest == checksums[archive.name]["sha256"]
stage = root / ".build" / ("zip-verification-" + version)
stage.mkdir()
with zipfile.ZipFile(archive) as z:
    for item in z.infolist():
        parts = Path(item.filename).parts
        assert ".." not in parts and not Path(item.filename).is_absolute()
        if len(parts) >= 3 and parts[1] in {"runtime", "libraries"} and parts[2] != "windows-x64":
            continue
        z.extract(item, stage)
bundle = stage / "wechat-cli"
result = subprocess.run([str(bundle / "runtime/windows-x64/python.exe"), "-X", "utf8",
    str(root / "scripts/bundle_verify.py"), str(bundle)], capture_output=True, text=True, encoding="utf-8")
print(result.stdout)
if result.returncode:
    print(result.stderr)
    raise SystemExit(result.returncode)
build = json.loads((bundle / "BUILD_INFO.json").read_text(encoding="utf-8"))
evidence = {"status": "PASS", "source_commit": build["source_commit"], "archive_sha256": digest,
            "output": result.stdout.strip()}
output = root / ".build" / f"windows-{version}.json"
output.write_text(json.dumps(evidence, indent=2))
print("EVIDENCE", output)
