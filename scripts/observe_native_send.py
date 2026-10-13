"""Short-lived diagnostic instrumentation for the exact researched Weixin build.

Does not call native send functions. Records metadata only, never message text.
Frida hooks temporarily modify function entry instructions; this is not a pure
read-only memory scan. Use with a cooperating account owner during acceptance.
"""
import argparse
import hashlib
import json
import time
import threading
from pathlib import Path

import frida
import pefile


EXPECTED_SHA256 = "8f7406a8a465e851ee10eaecccb572d0e5b4e00d480c770938c714c396097b74"
ENTRIES = {
    "coroutine_context": 0x43AB0,
    "message_send_candidate": 0x17A3620,
    "image_prepare_candidate": 0x17908B0,
    "image_commit_candidate": 0x17A68E0,
    "image_helper_candidate": 0x2D02BE0,
}

AGENT = r"""
const spec = SPEC;
const module = Process.getModuleByName('Weixin.dll');
if (Process.arch !== 'x64' || module.path.toLowerCase() !== spec.path.toLowerCase())
  throw new Error('Unexpected module or architecture');
// Verify every site before installing the first hook.
for (const item of spec.entries) {
  const bytes = new Uint8Array(module.base.add(item.rva).readByteArray(32));
  const actual = Array.from(bytes, n => n.toString(16).padStart(2, '0')).join('');
  if (actual !== item.prefix) throw new Error('Entry bytes differ: ' + item.name);
}
let count = 0;
const seenThreads = new Set();
const hooks = [];
function emit(event) { if (count++ < 200) send(event); }
function stringShape(p) {
  try {
    const length = p.add(16).readU64().toString();
    const capacity = p.add(24).readU64().toString();
    return {length, capacity};
  } catch (_) { return {readable: false}; }
}
function messageShapes(vector) {
  try {
    const begin = vector.readPointer();
    const end = vector.add(Process.pointerSize).readPointer();
    const bytes = Number(end.sub(begin).toString());
    if (!Number.isSafeInteger(bytes) || bytes < 0 || bytes > 128 || bytes % 16 !== 0)
      return {bounded: false};
    const items = [];
    for (let offset = 0; offset < bytes; offset += 16) {
      const object = begin.add(offset).readPointer();
      items.push({type118: object.add(0x118).readU32(),
        typeD8: object.add(0xd8).readU32(), type9C: object.add(0x9c).readU32(),
        recipientShape: stringShape(object.add(0xb0)),
        contentShape: stringShape(object.add(0x758))});
    }
    return {count: items.length, items};
  } catch (_) { return {readable: false}; }
}
try {
  for (const item of spec.entries) {
    hooks.push(Interceptor.attach(module.base.add(item.rva), {
      onEnter(args) {
        const thread = this.threadId;
        if (item.name === 'coroutine_context') {
          if (!seenThreads.has(thread) && seenThreads.size < 16) {
            seenThreads.add(thread); emit({kind: item.name, thread});
          }
          return;
        }
        const event = {kind: item.name, thread};
        if (item.name === 'message_send_candidate') event.messages = messageShapes(args[2]);
        if (item.name === 'image_helper_candidate') {
          event.arg2 = stringShape(args[2]); event.arg3 = stringShape(args[3]);
          this.resultPointer = args[1];
        }
        emit(event);
      },
      onLeave() {
        if (!this.resultPointer) return;
        try {
          emit({kind: 'image_helper_result', thread: this.threadId,
            successBit: this.resultPointer.readU8() & 1,
            errorString: stringShape(this.resultPointer.add(24))});
        } catch (_) { emit({kind: 'result_unreadable'}); }
      }
    }));
  }
  send({kind: 'ready', hooks: hooks.length});
} catch (error) {
  for (const hook of hooks) hook.detach();
  throw error;
}
rpc.exports = { stop() { for (const hook of hooks) hook.detach(); return true; } };
"""


def build_agent(path):
    with path.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != EXPECTED_SHA256:
            raise ValueError("Unrecognized Weixin.dll build")
    pe = pefile.PE(str(path), fast_load=True)
    spec = {"path": str(path.resolve()), "entries": [
        {"name": name, "rva": rva, "prefix": pe.get_data(rva, 32).hex()}
        for name, rva in ENTRIES.items()
    ]}
    pe.close()
    return AGENT.replace("SPEC", json.dumps(spec), 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dll", type=Path, required=True)
    parser.add_argument("--pid", type=int)
    parser.add_argument("--seconds", type=int, default=120, choices=range(10, 301))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    agent = build_agent(args.dll)
    if args.check_only:
        print(json.dumps({"build_verified": True, "sites": len(ENTRIES), "attached": False}))
        return
    if args.pid is None:
        parser.error("--pid is required unless --check-only")
    with args.output.open("a", encoding="utf-8") as output:
        session = frida.attach(args.pid)
        script = None
        try:
            initialized = threading.Event()
            ready = threading.Event()
            def on_message(message, _data):
                # Frida errors contain diagnostics; no arbitrary target buffers are sent.
                event = message.get("payload", {"kind": "agent_error", "description": message.get("description", "unknown")})
                output.write(json.dumps({"time": time.time(), "event": event}) + "\n")
                output.flush()
                print(json.dumps(event), flush=True)
                if event.get("kind") == "ready":
                    ready.set()
                    initialized.set()
                elif event.get("kind") == "agent_error":
                    initialized.set()
            script = session.create_script(agent)
            script.on("message", on_message)
            script.load()
            if not initialized.wait(5) or not ready.is_set():
                raise RuntimeError("Observer failed to initialize; detaching")
            deadline = time.monotonic() + args.seconds
            while time.monotonic() < deadline:
                time.sleep(0.25)
            script.exports_sync.stop()
        finally:
            try:
                if script is not None:
                    script.unload()
            finally:
                session.detach()


if __name__ == "__main__":
    main()
