"""Experimental one-image acceptance command, fixed to the owner's filehelper.

Prepare is offline. Execute sends and requires the owner's concrete authorization.
An attempt marker is written before attachment; unknown outcomes are never retried.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def prepare(image, directory, *, recipient="filehelper"):
    if not isinstance(recipient, str) or not recipient.strip() or "\0" in recipient:
        raise ValueError("Recipient must be a nonempty identifier without NUL")
    from PIL import Image, ImageOps
    with Image.open(image) as value:
        source_format = value.format
        if source_format not in {"PNG", "JPEG", "WEBP", "BMP", "GIF"} or getattr(value, "n_frames", 1) != 1:
            raise ValueError("图片须为 PNG/JPEG/WebP/BMP/静态 GIF；动画请作为文件发送")
        value.verify()
    folder = directory / uuid.uuid4().hex
    folder.mkdir(parents=True)
    staged = folder / "image.png"
    if source_format == "PNG":
        shutil.copyfile(image, staged)
    else:
        with Image.open(image) as value:
            normalized = ImageOps.exif_transpose(value)
            if normalized.mode not in {"1", "L", "LA", "P", "RGB", "RGBA", "I", "I;16"}:
                normalized = normalized.convert("RGB")
            normalized.save(staged, format="PNG")
    request = {"target":recipient, "image_sha256":digest(staged), "image":"image.png"}
    path = folder / "request.json"
    path.write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
    return path


def claim(request_path, *, recipient="filehelper"):
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if request.get("target") != recipient or request.get("image") != "image.png":
        raise ValueError("Unexpected acceptance target or image")
    image = request_path.parent / "image.png"
    if digest(image) != request["image_sha256"]:
        raise ValueError("Prepared image changed")
    cache = Path(request.get("cache_path", ""))
    if not cache.is_file() or digest(cache) != request["image_sha256"]:
        raise ValueError("Verified resource cache is missing or changed")
    marker = request_path.parent / "attempt.json"
    # Exclusive creation is also the concurrency gate; even failed attempts stay consumed.
    with marker.open("x", encoding="utf-8") as output:
        json.dump({"state":"attempted", "delivery_confirmed":False}, output)
        output.flush()
        os.fsync(output.fileno())
    return image


def stage_cache(request_path, cache_path, cache_root, *, recipient="filehelper"):
    request_path, cache_path, cache_root = (p.resolve() for p in (request_path, cache_path, cache_root))
    if (request_path.parent / "attempt.json").exists():
        raise ValueError("An attempted request cannot be changed")
    request = json.loads(request_path.read_text(encoding="utf-8"))
    image = request_path.parent / "image.png"
    if request.get("target") != recipient or request.get("image") != "image.png" or digest(image) != request["image_sha256"]:
        raise ValueError("Prepared request changed")
    expected_name = hashlib.md5(str(image).encode()).hexdigest()
    if not cache_path.is_relative_to(cache_root) or cache_path.parent.name != "HttpResource" or cache_path.name != expected_name:
        raise ValueError("Resolved cache path does not match the prepared resource")
    # Do not overwrite an existing client cache entry, even if its bytes match.
    with cache_path.open("xb") as output, image.open("rb") as source:
        shutil.copyfileobj(source, output)
        output.flush()
        os.fsync(output.fileno())
    if digest(cache_path) != request["image_sha256"]:
        raise ValueError("Cache copy did not verify")
    request["cache_path"] = str(cache_path)
    request_path.write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preparation = commands.add_parser("prepare")
    preparation.add_argument("image", type=Path)
    preparation.add_argument("--directory", type=Path, required=True)
    staging = commands.add_parser("stage-cache")
    staging.add_argument("request", type=Path)
    staging.add_argument("cache_path", type=Path)
    staging.add_argument("--cache-root", type=Path, required=True)
    execution = commands.add_parser("execute")
    execution.add_argument("request", type=Path)
    execution.add_argument("--dll", type=Path, required=True)
    execution.add_argument("--pid", type=int, required=True)
    execution.add_argument("--thread", type=int, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        print(prepare(args.image, args.directory.resolve()))
        return
    if args.command == "stage-cache":
        stage_cache(args.request, args.cache_path, args.cache_root)
        print(json.dumps({"cache_staged":True, "sent":False}))
        return
    from .native_probe import build_agent, run_agent
    request = args.request.resolve()
    image = claim(request)
    events = run_agent(build_agent(args.dll, args.thread, image, negative=False), args.pid, request.parent / "events.jsonl")
    returned = next((e for e in events if e.get("kind") == "probe_result"), None)
    state = "unknown" if returned is None else ("native_success_unconfirmed" if returned["successBit"] else "native_failure")
    result = {"state":state, "events":events, "delivery_confirmed":False}
    (request.parent / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"state":state, "delivery_confirmed":False}))


if __name__ == "__main__":
    main()
