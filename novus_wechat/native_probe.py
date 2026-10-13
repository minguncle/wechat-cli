"""CLI negative ABI probe: a fixed nonexistent image, never a real send.

Runs once when the previously observed coroutine thread naturally returns from
GetCoroCtx. The shared builder also serves the separately authorized one-shot
image acceptance command. Neither command is a production sender.
"""
import argparse
import json
from pathlib import Path
import threading
import uuid

import frida
from .native_runtime import verified_entries


AGENT = r"""
const spec = SPEC;
const mod = Process.getModuleByName('Weixin.dll');
if (Process.arch !== 'x64' || mod.path.toLowerCase() !== spec.path.toLowerCase())
  throw new Error('Unexpected module');
if (!Process.enumerateThreads().some(t => t.id === spec.thread))
  throw new Error('Previously observed thread no longer exists');
for (const site of spec.sites) {
  const bytes = new Uint8Array(mod.base.add(site.rva).readByteArray(32));
  if (Array.from(bytes, n => n.toString(16).padStart(2,'0')).join('') !== site.prefix)
    throw new Error('Entry mismatch');
}
const attributes = new NativeFunction(Process.getModuleByName('kernel32.dll').getExportByName('GetFileAttributesW'), 'uint32', ['pointer']);
const missingWide = Memory.allocUtf16String(spec.missing);
const negative = spec.negative !== false;
function pathMatches() { return (attributes(missingWide) === 0xffffffff) === negative; }
if (!pathMatches()) throw new Error('Unexpected image path state');
const helper = new NativeFunction(mod.base.add(0x2d02be0), 'pointer', ['pointer','pointer','pointer','pointer']);
const release = new NativeFunction(mod.base.add(0x7165a30), 'void', ['pointer','uint64']);
const keep = [];
function inputString(text, length = text.length) {
  const structure = Memory.alloc(32);
  structure.writeByteArray(new Uint8Array(32));
  if (length < 16) {
    structure.writeUtf8String(text); structure.add(24).writeU64(15);
  } else {
    const data = Memory.allocUtf8String(text); keep.push(data);
    structure.writePointer(data); structure.add(24).writeU64(length);
  }
  structure.add(16).writeU64(length); keep.push(structure); return structure;
}
const recipient = inputString(spec.recipient, spec.recipientBytes);
const filename = inputString(spec.missing, spec.missingBytes);
const result = Memory.alloc(64); result.writeByteArray(new Uint8Array(64));
function accountMatches() {
  if (!spec.expectedCache) return true;
  const resolve = new NativeFunction(mod.base.add(0xa520b0),'pointer',['pointer','pointer','pointer']);
  const destroy = new NativeFunction(mod.base.add(0x7af0),'void',['pointer']);
  const empty = inputString('');
  const path = Memory.alloc(32); path.writeByteArray(new Uint8Array(32));
  resolve(path,filename,empty);
  const length = Number(path.add(16).readU64().toString());
  const capacity = Number(path.add(24).readU64().toString());
  if (!Number.isSafeInteger(length) || !Number.isSafeInteger(capacity) || length > capacity || length > 32767)
    throw new Error('Unexpected account path layout');
  try {
    const actual = (capacity < 8 ? path : path.readPointer()).readUtf16String(length);
    return actual.toLowerCase() === spec.expectedCache.toLowerCase();
  } finally { destroy(path); }
}
let attempted = false;
const hook = Interceptor.attach(mod.base.add(0x43ab0), {
  onLeave() {
    if (attempted || this.threadId !== spec.thread) return;
    attempted = true;
    if (!pathMatches()) {
      send({kind:'probe_error', reason:'image path state changed'}); return;
    }
    try {
      if (!accountMatches()) throw new Error('Account resource path changed; sending refused');
      send({kind:'call_started', thread:this.threadId});
      helper(ptr(0), result, recipient, filename);
      const success = result.readU8() & 1;
      const string = result.add(24);
      const length = Number(string.add(16).readU64().toString());
      const capacity = Number(string.add(24).readU64().toString());
      if (!Number.isSafeInteger(length) || !Number.isSafeInteger(capacity) || length > capacity || capacity >= 4095)
        throw new Error('Unexpected result string layout; no deallocation attempted');
      const chars = capacity < 16 ? string : string.readPointer();
      const prefixMatches = length >= 16 && chars.readUtf8String(16) === 'image not exist:';
      if (capacity >= 16) release(chars, capacity + 1);
      send({kind:'probe_result', thread:this.threadId, successBit:success,
        missingFileError:prefixMatches, errorLength:length, released:capacity >= 16});
    } catch (error) { send({kind:'probe_error', reason:String(error)}); }
  }
});
rpc.exports = { stop() { hook.detach(); return true; } };
send({kind:'ready', thread:spec.thread});
"""


def build_agent(path, thread, missing, *, negative=True, expected_cache=None, recipient="filehelper"):
    if not isinstance(recipient, str) or not recipient.strip() or "\0" in recipient:
        raise ValueError("Recipient must be a nonempty identifier without NUL")
    path = path.resolve()
    if missing.exists() == negative:
        raise ValueError("Unexpected image path state")
    if "\0" in str(missing):
        raise ValueError("Image path contains NUL")
    rvas = [0x43ab0, 0x2d02be0, 0x7165a30]
    if expected_cache is not None:
        rvas += [0xa520b0, 0x7af0]
    sites = verified_entries(path, rvas)
    return AGENT.replace("SPEC", json.dumps({"path":str(path), "thread":thread, "missing":str(missing), "missingBytes":len(str(missing).encode("utf-8")),
        "recipient":recipient, "recipientBytes":len(recipient.encode("utf-8")),
        "sites":sites, "negative":negative, "expectedCache":str(expected_cache) if expected_cache else None}), 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dll", type=Path, required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--thread", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    missing = Path(__file__).resolve().parents[1] / ".build" / ("absent-native-probe-" + uuid.uuid4().hex + ".png")
    agent = build_agent(args.dll, args.thread, missing)
    if args.check_only:
        print(json.dumps({"build_verified":True, "path_absent":True, "attached":False})); return
    run_agent(agent, args.pid, args.output)


def run_agent(agent, pid, output_path):
    complete = threading.Event()
    events = []
    with output_path.open("a", encoding="utf-8") as output:
        session = frida.attach(pid)
        script = None
        try:
            def message(value, _):
                event = value.get("payload", {"kind":"probe_error", "reason":value.get("description", "agent error")})
                events.append(event)
                output.write(json.dumps(event) + "\n"); output.flush()
                print(json.dumps(event), flush=True)
                if event.get("kind") in ("probe_result", "probe_error"):
                    complete.set()
            script = session.create_script(agent)
            script.on("message", message); script.load()
            if not complete.wait(45):
                print(json.dumps({"kind":"timeout", "result":"NOT_VERIFIED"}), flush=True)
            # unload removes the interceptor after the callback has returned.
        finally:
            try:
                if script is not None:
                    script.unload()
            finally:
                session.detach()
    return events


if __name__ == "__main__":
    main()
