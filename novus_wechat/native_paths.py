"""Resolve one resource identifier through the client; never invoke sending."""
import argparse
import json
from pathlib import Path

from .native_runtime import verified_entries
from .native_probe import run_agent


AGENT = r"""
const spec = SPEC;
const mod = Process.getModuleByName('Weixin.dll');
if (Process.arch !== 'x64' || mod.path.toLowerCase() !== spec.path.toLowerCase()) throw new Error('Unexpected module');
if (!Process.enumerateThreads().some(t => t.id === spec.thread)) throw new Error('Observed thread gone');
for (const site of spec.sites) {
  const bytes = new Uint8Array(mod.base.add(site.rva).readByteArray(32));
  if (Array.from(bytes,n=>n.toString(16).padStart(2,'0')).join('') !== site.prefix) throw new Error('Entry mismatch');
}
const resolve = new NativeFunction(mod.base.add(0xa520b0),'pointer',['pointer','pointer','pointer']);
const destroy = new NativeFunction(mod.base.add(0x7af0),'void',['pointer']);
const exists = new NativeFunction(mod.base.add(0x3639f0),'bool',['pointer']);
const input = Memory.alloc(32), empty = Memory.alloc(32), result = Memory.alloc(32);
for (const value of [input,empty,result]) value.writeByteArray(new Uint8Array(32));
const data = Memory.allocUtf8String(spec.resource);
if (spec.resourceBytes < 16) { input.writeUtf8String(spec.resource); input.add(24).writeU64(15); }
else { input.writePointer(data); input.add(24).writeU64(spec.resourceBytes); }
input.add(16).writeU64(spec.resourceBytes); empty.add(24).writeU64(15);
let attempted = false;
const hook = Interceptor.attach(mod.base.add(0x43ab0), {
  onLeave() {
    if (attempted || this.threadId !== spec.thread) return;
    attempted = true;
    try {
      resolve(result,input,empty);
      const length = Number(result.add(16).readU64().toString());
      const capacity = Number(result.add(24).readU64().toString());
      if (!Number.isSafeInteger(length) || !Number.isSafeInteger(capacity) || length > capacity || length > 32767)
        throw new Error('Unexpected path object');
      let resolved, present;
      try { resolved = (capacity < 8 ? result : result.readPointer()).readUtf16String(length); present = exists(result); }
      finally { destroy(result); }
      send({kind:'probe_result', resolved_path:resolved, pathLength:length, cache_exists:present, sending_called:false});
    } catch(error) { send({kind:'probe_error',reason:String(error)}); }
  }
});
rpc.exports={stop(){hook.detach();return true;}};
send({kind:'ready',thread:spec.thread});
"""


def build_agent(path, thread, resource):
    if not resource or '\0' in resource:
        raise ValueError("Resource identifiers must be nonempty and contain no NUL")
    path = path.resolve()
    sites = verified_entries(path, (0x43ab0,0xa520b0,0x7af0,0x3639f0))
    return AGENT.replace("SPEC",json.dumps({"path":str(path),"thread":thread,"resource":resource,"resourceBytes":len(resource.encode("utf-8")),"sites":sites}),1)


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dll",type=Path,required=True)
    parser.add_argument("--pid",type=int,required=True)
    parser.add_argument("--thread",type=int,required=True)
    parser.add_argument("--resource",required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--check-only",action="store_true")
    args=parser.parse_args()
    agent=build_agent(args.dll,args.thread,args.resource)
    if args.check_only:
        print(json.dumps({"build_verified":True,"attached":False}))
    else:
        run_agent(agent,args.pid,args.output)
