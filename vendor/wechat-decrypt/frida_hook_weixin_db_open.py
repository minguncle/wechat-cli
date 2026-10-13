import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import frida


SCRIPT = r"""
const weixin = Process.getModuleByName("Weixin.dll");
const interesting = [
  "db_storage",
  "message_",
  "biz_message_",
  "session.db",
  "contact.db",
  "favorite.db",
  "sns.db"
];
const seen = {};

function rel(addr) {
  const p = ptr(addr);
  if (p.compare(weixin.base) >= 0 && p.compare(weixin.base.add(weixin.size)) < 0) {
    return "Weixin.dll+" + p.sub(weixin.base);
  }
  const m = Process.findModuleByAddress(p);
  if (m) return m.name + "+" + p.sub(m.base);
  return p.toString();
}

function shouldLog(path) {
  const lower = path.toLowerCase();
  return interesting.some(s => lower.indexOf(s) >= 0);
}

function findExport(moduleName, exportName) {
  try {
    const m = Process.getModuleByName(moduleName);
    if (m.findExportByName) return m.findExportByName(exportName);
    if (m.getExportByName) return m.getExportByName(exportName);
  } catch (e) {
  }
  try {
    if (Module.getGlobalExportByName) return Module.getGlobalExportByName(exportName);
  } catch (e) {
  }
  return null;
}

function logOpen(kind, path) {
  if (!path || !shouldLog(path)) return;
  const stack = Thread.backtrace(this.context, Backtracer.ACCURATE).slice(0, 24).map(rel);
  const key = kind + "|" + path + "|" + stack.slice(0, 8).join(";");
  if (seen[key]) return;
  seen[key] = true;
  send({ kind, path, stack });
}

function hookCreateFileW(moduleName) {
  const p = findExport(moduleName, "CreateFileW");
  if (!p) return;
  Interceptor.attach(p, {
    onEnter(args) {
      this.path = args[0].isNull() ? "" : args[0].readUtf16String();
    },
    onLeave(retval) {
      logOpen.call(this, moduleName + "!CreateFileW", this.path);
    }
  });
}

function hookCreateFileA(moduleName) {
  const p = findExport(moduleName, "CreateFileA");
  if (!p) return;
  Interceptor.attach(p, {
    onEnter(args) {
      this.path = args[0].isNull() ? "" : args[0].readAnsiString();
    },
    onLeave(retval) {
      logOpen.call(this, moduleName + "!CreateFileA", this.path);
    }
  });
}

["KERNEL32.dll", "KERNELBASE.dll"].forEach(name => {
  hookCreateFileW(name);
  hookCreateFileA(name);
});

send({
  kind: "ready",
  pid: Process.id,
  weixinBase: weixin.base.toString(),
  weixinSize: weixin.size
});
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


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", type=int, default=None)
    parser.add_argument("--seconds", type=int, default=90)
    parser.add_argument("--out", default="frida_db_open_trace.jsonl")
    args = parser.parse_args(argv)

    pid = args.pid or get_biggest_weixin_pid()
    out_path = Path(args.out).resolve()
    print(f"[+] attaching Weixin.exe PID={pid}")
    print(f"[+] writing {out_path}")

    session = frida.attach(pid)
    script = session.create_script(SCRIPT)

    with out_path.open("w", encoding="utf-8") as f:
        def on_message(message, data):
            record = {"ts": time.time(), "message": message}
            if message.get("type") == "send":
                record = {"ts": time.time(), **message.get("payload", {})}
                if record.get("kind") not in ("ready",):
                    print(f"[{record.get('kind')}] {record.get('path')}")
                    for frame in record.get("stack", [])[:10]:
                        print(f"  {frame}")
            else:
                print(message)
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()

        script.on("message", on_message)
        script.load()
        deadline = time.time() + args.seconds
        while time.time() < deadline:
            time.sleep(0.5)

    script.unload()
    session.detach()
    print("[+] done")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
