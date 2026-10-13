import json
import subprocess
import sys

from scripts import upstream_sync


def test_update_preserves_local_patch_and_refreshes_provenance(tmp_path, monkeypatch):
    upstream = tmp_path / "upstream"
    checkout = tmp_path / "checkout"
    for directory in (upstream, checkout):
        directory.mkdir()
        subprocess.run(["git", "init", "-b", "main", str(directory)], check=True, capture_output=True)
        for key, value in (("user.name", "Fixture"), ("user.email", "fixture@example.invalid"),
                           ("core.autocrlf", "false")):
            subprocess.run(["git", "-C", str(directory), "config", key, value], check=True)

    def commit(directory, message):
        subprocess.run(["git", "-C", str(directory), "add", "."], check=True)
        subprocess.run(["git", "-C", str(directory), "commit", "-m", message], check=True, capture_output=True)
        return subprocess.check_output(["git", "-C", str(directory), "rev-parse", "HEAD"], text=True).strip()

    original = "\n".join(["first"] + [str(i) for i in range(20)] + ["last"]) + "\n"
    (upstream / "module.txt").write_text(original, encoding="utf-8")
    before = commit(upstream, "baseline")
    (upstream / "module.txt").write_text(original.replace("last", "upstream-change"), encoding="utf-8")
    after = commit(upstream, "upstream change")
    vendor = checkout / "vendor/demo"
    vendor.mkdir(parents=True)
    (vendor / "module.txt").write_text(original.replace("first", "local-change"), encoding="utf-8")
    lock = {"demo": {"url": str(upstream), "branch": "main", "commit": before, "prefix": "vendor/demo"}}
    (checkout / "upstreams.lock.json").write_text(json.dumps(lock), encoding="utf-8")
    commit(checkout, "local baseline")
    actual_run = subprocess.run
    tested = []

    def run(command, **kwargs):
        if command[:3] == [sys.executable, "-m", "pytest"]:
            tested.append(True)
            return subprocess.CompletedProcess(command, 0)
        return actual_run(command, **kwargs)

    monkeypatch.setattr(upstream_sync, "ROOT", checkout)
    monkeypatch.setattr(sys, "argv", ["upstream_sync.py", "--apply"])
    monkeypatch.setattr(upstream_sync.subprocess, "run", run)
    upstream_sync.main()
    content = (vendor / "module.txt").read_text()
    assert "local-change" in content and "upstream-change" in content
    assert json.loads((checkout / "upstreams.lock.json").read_text())["demo"]["commit"] == after
    patch = (checkout / "patches/demo.patch").read_text()
    assert "+local-change" in patch and "+upstream-change" not in patch
    assert tested
