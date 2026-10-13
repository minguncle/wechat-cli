import argparse
import base64
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import frida

from config import load_config
from key_scan_common import (
    collect_db_files,
    save_results,
    scan_raw_key_candidates_for_any_salt,
    scan_memory_for_keys,
)


OFFSETS = [
    0x336B882, 0x336B8BE, 0x336C67F, 0x336C9E1, 0x336CA36, 0x336CB0F,
    0x336D169, 0x336D2D7, 0x336D41A, 0x33A5AE0, 0x33A5E32, 0x33A5ED3,
    0x33B5733, 0x33B5886, 0x33B6144, 0x33B61CA, 0x5105348, 0x510545A,
]


SCRIPT_TEMPLATE = r"""
const weixin = Process.getModuleByName("Weixin.dll");
const offsets = OFFSETS_JSON;
const maxHitsPerOffset = MAX_HITS;
const bytesPerPointer = BYTES_PER_POINTER;
const counts = {};
const pointerMin = ptr("0x10000");
const pointerMax = ptr("0x00007fffffffffff");

function rel(addr) {
  const p = ptr(addr);
  if (p.compare(weixin.base) >= 0 && p.compare(weixin.base.add(weixin.size)) < 0) {
    return "Weixin.dll+" + p.sub(weixin.base);
  }
  const m = Process.findModuleByAddress(p);
  if (m) return m.name + "+" + p.sub(m.base);
  return p.toString();
}

function isPointer(v) {
  const p = ptr(v);
  return p.compare(pointerMin) >= 0 && p.compare(pointerMax) <= 0 && p.and(7).toUInt32() === 0;
}

function addPtr(list, label, value) {
  try {
    if (!isPointer(value)) return;
    const p = ptr(value);
    const key = p.toString();
    if (list.some(x => x.address === key)) return;
    list.push({ label, address: key });
  } catch (e) {
  }
}

function collectPointers(ctx) {
  const out = [];
  ["rax", "rbx", "rcx", "rdx", "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15", "rsi", "rdi"].forEach(name => {
    addPtr(out, name, ctx[name]);
  });
  try {
    const rsp = ctx.rsp;
    for (let i = 0; i < 16; i++) {
      addPtr(out, "stack+" + (i * 8), rsp.add(i * 8).readPointer());
    }
  } catch (e) {
  }
  return out.slice(0, 24);
}

function readWindow(address) {
  try {
    const p = ptr(address);
    const base = p.sub(128);
    return {
      base: base.toString(),
      data: base.readByteArray(bytesPerPointer)
    };
  } catch (e) {
    try {
      const p = ptr(address);
      return {
        base: p.toString(),
        data: p.readByteArray(bytesPerPointer)
      };
    } catch (e2) {
      return null;
    }
  }
}

offsets.forEach(off => {
  const address = weixin.base.add(off);
  counts[off] = 0;
  try {
    Interceptor.attach(address, {
      onEnter(args) {
        counts[off] += 1;
        if (counts[off] > maxHitsPerOffset) return;
        const pointers = collectPointers(this.context);
        const stack = Thread.backtrace(this.context, Backtracer.ACCURATE).slice(0, 16).map(rel);
        send({ kind: "hit", offset: "0x" + off.toString(16), count: counts[off], pointers, stack });
        pointers.forEach(item => {
          const win = readWindow(item.address);
          if (win && win.data) {
            send({ kind: "window", offset: "0x" + off.toString(16), label: item.label, pointer: item.address, base: win.base }, win.data);
          }
        });
      }
    });
  } catch (e) {
    send({ kind: "hook_error", offset: "0x" + off.toString(16), error: String(e) });
  }
});

send({ kind: "ready", pid: Process.id, weixinBase: weixin.base.toString(), offsets: offsets.length });
"""


def get_biggest_weixin_pid():
    cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-Process Weixin | Sort-Object WorkingSet64 -Descending | Select-Object -First 1 -ExpandProperty Id",
    ]
    out = subprocess.check_output(cmd, text=True, encoding="utf-8", errors="replace").strip()
    return int(out)


def make_script(max_hits, bytes_per_pointer):
    return (
        SCRIPT_TEMPLATE
        .replace("OFFSETS_JSON", json.dumps(OFFSETS))
        .replace("MAX_HITS", str(max_hits))
        .replace("BYTES_PER_POINTER", str(bytes_per_pointer))
    )


def scan_window(data, base_addr, pid, db_files, salt_to_dbs, key_map, remaining_salts, diagnostics):
    before = len(key_map)
    hex_re = re.compile(b"x'([0-9a-fA-F]{64,192})'")
    scan_memory_for_keys(
        data, hex_re, db_files, salt_to_dbs, key_map, remaining_salts,
        base_addr, pid, print, diagnostics=diagnostics,
    )
    scan_raw_key_candidates_for_any_salt(
        data, db_files, salt_to_dbs, key_map, remaining_salts,
        base_addr, pid, print, "frida sqlcipher pointer window",
        diagnostics=diagnostics, stride=1,
    )
    return len(key_map) > before


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, default=None)
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--max-hits", type=int, default=12)
    parser.add_argument("--bytes", type=int, default=512)
    parser.add_argument("--out", default="frida_sqlcipher_probe.jsonl")
    args = parser.parse_args(argv)

    cfg = load_config()
    db_files, salt_to_dbs = collect_db_files(cfg["db_dir"])
    key_map = {}
    remaining_salts = set(salt_to_dbs)
    diagnostics = {"salt_context_sample_limit": 0, "pointer_scan_target_limit": 0}
    pid = args.pid or get_biggest_weixin_pid()
    out_path = Path(args.out).resolve()

    print(f"[+] attaching Weixin.exe PID={pid}")
    print(f"[+] DB salts: {len(salt_to_dbs)}")
    print(f"[+] writing {out_path}")

    session = frida.attach(pid)
    script = session.create_script(make_script(args.max_hits, args.bytes))
    active = True

    with out_path.open("w", encoding="utf-8") as f:
        def on_message(message, data):
            nonlocal key_map, remaining_salts
            if not active or f.closed:
                return
            payload = message.get("payload", {}) if message.get("type") == "send" else {}
            record = {"ts": time.time(), "message": message}
            if payload:
                record = {"ts": time.time(), **payload}
            if payload.get("kind") == "ready":
                print(f"[ready] base={payload.get('weixinBase')} offsets={payload.get('offsets')}")
            elif payload.get("kind") == "hook_error":
                print(f"[hook_error] {payload.get('offset')} {payload.get('error')}")
            elif payload.get("kind") == "hit":
                if int(payload.get("count", 0)) <= 2:
                    print(f"[hit] {payload.get('offset')} count={payload.get('count')} ptrs={len(payload.get('pointers', []))}")
            elif payload.get("kind") == "window" and data:
                raw = bytes(data)
                base_addr = int(payload["base"], 16)
                found = scan_window(raw, base_addr, pid, db_files, salt_to_dbs, key_map, remaining_salts, diagnostics)
                record["data_sha256"] = __import__("hashlib").sha256(raw).hexdigest()
                record["data_len"] = len(raw)
                record["found_after_window"] = found
                if found:
                    print(f"[FOUND_WINDOW] {payload.get('offset')} {payload.get('label')} {payload.get('pointer')}")
                    save_results(db_files, salt_to_dbs, key_map, cfg["db_dir"], cfg["keys_file"], print)
            elif message.get("type") != "send":
                print(message)
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()

        script.on("message", on_message)
        script.load()
        deadline = time.time() + args.seconds
        while time.time() < deadline and remaining_salts:
            time.sleep(0.5)

        active = False
        script.unload()
        session.detach()
    print("[+] done")
    print(f"[+] matched {len(key_map)}/{len(salt_to_dbs)} salts")
    print(f"[+] diagnostics: {diagnostics}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
