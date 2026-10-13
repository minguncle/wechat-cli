"""Check pinned upstreams and apply updates on a review branch."""
import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def git(*args, capture=False):
    result = subprocess.run(["git", *args], cwd=ROOT, check=True,
                            stdout=subprocess.PIPE if capture else None)
    return result.stdout if capture else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    path = ROOT / "upstreams.lock.json"
    lock = json.loads(path.read_text(encoding="utf-8"))
    changes = []
    for name, upstream in lock.items():
        rows = git("ls-remote", upstream["url"], "refs/heads/" + upstream["branch"], capture=True).split()
        if not rows:
            raise RuntimeError(f"Upstream unavailable: {name}; pinned source retained")
        head = rows[0].decode()
        if head != upstream["commit"]:
            changes.append({"name": name, "old": upstream["commit"], "new": head})
    print(json.dumps({"updates": changes}, indent=2))
    if not changes or not args.apply:
        return
    if git("status", "--porcelain", capture=True):
        raise RuntimeError("Commit local changes before applying updates")
    branch = "chore/upstream-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    git("switch", "-c", branch)
    for change in changes:
        upstream = lock[change["name"]]
        for revision in (change["old"], change["new"]):
            git("fetch", "--no-tags", upstream["url"], revision)
        patch = git("diff", "--binary", change["old"], change["new"], capture=True)
        if patch:
            subprocess.run(["git", "apply", "--3way", "--directory=" + upstream["prefix"], "-"],
                           input=patch, cwd=ROOT, check=True)
        upstream["commit"] = change["new"]
        tree = git("write-tree", capture=True).decode().strip()
        local_patch = git("diff", "--binary", change["new"] + "^{tree}",
                          tree + ":" + upstream["prefix"], capture=True)
        patch_path = ROOT / "patches" / (change["name"] + ".patch")
        patch_path.parent.mkdir(exist_ok=True)
        if local_patch:
            patch_path.write_bytes(local_patch)
        elif patch_path.exists():
            patch_path.unlink()
    path.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    git("add", "upstreams.lock.json", "vendor", "patches")
    git("commit", "-m", "chore: 更新锁定上游并保留本地修改")
    subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ROOT, check=True)
    print("Review and open a pull request for " + branch)


if __name__ == "__main__":
    main()
