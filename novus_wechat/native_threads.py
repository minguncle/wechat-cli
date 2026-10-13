"""Observe coroutine context activity without invoking native functions.

Activity identifies candidates, not proof that a thread can safely send.
Only counts and thread IDs leave the process; context addresses stay private.
"""
import argparse
import json
from pathlib import Path
import time

import frida
from .native_runtime import verified_entries


AGENT = r"""
const spec = SPEC;
const mod = Process.getModuleByName('Weixin.dll');
if (Process.arch !== 'x64' || mod.path.toLowerCase() !== spec.path.toLowerCase())
  throw new Error('Unexpected client');
const entry = mod.base.add(0x43ab0);
const bytes = new Uint8Array(entry.readByteArray(32));
if (Array.from(bytes,n=>n.toString(16).padStart(2,'0')).join('') !== spec.prefix)
  throw new Error('Entry mismatch');
const rows = new Map();
const hook = Interceptor.attach(entry, {
  onLeave(value) {
    let row = rows.get(this.threadId);
    if (!row) {
      if (rows.size >= 256) return;
      row = {thread:this.threadId,calls:0,nonNull:0,contexts:new Set()};
      rows.set(this.threadId,row);
    }
    row.calls++;
    if (!value.isNull()) {
      row.nonNull++;
      if (row.contexts.size < 16) row.contexts.add(value.toString());
    }
  }
});
rpc.exports = {
  finish() {
    hook.detach();
    const live = new Set(Process.enumerateThreads().map(t=>t.id));
    return Array.from(rows.values(),r=>({thread:r.thread,calls:r.calls,
      nonNull:r.nonNull,distinctContextsCapped:r.contexts.size,alive:live.has(r.thread)}));
  }
};
"""


def observe(path, pid, seconds):
    path = path.resolve()
    prefix = verified_entries(path, [0x43ab0])[0]['prefix']
    agent = AGENT.replace('SPEC', json.dumps({'path':str(path), 'prefix':prefix}), 1)
    session = frida.attach(pid)
    script = None
    try:
        script = session.create_script(agent)
        script.load()
        time.sleep(seconds)
        return script.exports_sync.finish()
    finally:
        try:
            if script is not None:
                script.unload()
        finally:
            session.detach()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dll', type=Path, required=True)
    parser.add_argument('--pid', type=int, required=True)
    parser.add_argument('--seconds', type=int, choices=range(1, 61), default=10)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = {'threads':observe(args.dll,args.pid,args.seconds), 'sending_called':False,
              'automatic_selection_verified':False}
    args.output.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result))
