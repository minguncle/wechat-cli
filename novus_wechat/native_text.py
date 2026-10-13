"""Pinned-build native text call with a process-resident callback."""
import hashlib
import json
import os
from pathlib import Path
import uuid

from .native_runtime import verified_entries

BRIDGE_SHA256 = '3144981fe31231ad743aaf4049d8e14c91b89f0321d80b6dd4514389ee09cd97'


AGENT = r"""
const spec = SPEC;
const mod = Process.getModuleByName('Weixin.dll');
if (Process.arch !== 'x64' || mod.path.toLowerCase() !== spec.path.toLowerCase()) throw new Error('Unexpected module');
if (!Process.enumerateThreads().some(t => t.id === spec.thread)) throw new Error('Observed thread gone');
for (const site of spec.sites) {
  const bytes = new Uint8Array(mod.base.add(site.rva).readByteArray(32));
  if (Array.from(bytes,n=>n.toString(16).padStart(2,'0')).join('') !== site.prefix) throw new Error('Entry mismatch');
}
const bridge = Module.load(spec.bridge);
if (bridge.path.toLowerCase() !== spec.bridge.toLowerCase()) throw new Error('Unexpected callback module');
const version = new NativeFunction(bridge.getExportByName('novus_callback_version'),'int',[]);
if (version() !== 1) throw new Error('Unexpected callback ABI');
const prepare = new NativeFunction(bridge.getExportByName('novus_callback_prepare'),'pointer',[]);
const generation = new NativeFunction(bridge.getExportByName('novus_callback_generation'),'int',[]);
const status = new NativeFunction(bridge.getExportByName('novus_callback_status'),'int',['int']);
const helper = new NativeFunction(mod.base.add(0x54d4960),'void',['pointer','pointer','pointer','pointer']);
const resolve = new NativeFunction(mod.base.add(0xa520b0),'pointer',['pointer','pointer','pointer']);
const destroy = new NativeFunction(mod.base.add(0x7af0),'void',['pointer']);
const keep = [];
function stringAt(structure,text,length) {
  structure.writeByteArray(new Uint8Array(32));
  if (length < 16) { structure.writeUtf8String(text); structure.add(24).writeU64(15); }
  else {
    const data = Memory.allocUtf8String(text); keep.push(data);
    structure.writePointer(data); structure.add(24).writeU64(length);
  }
  structure.add(16).writeU64(length);
  return structure;
}
const pair = Memory.alloc(64);
stringAt(pair,spec.recipient,spec.recipientBytes);
stringAt(pair.add(32),spec.content,spec.contentBytes);
const label = stringAt(Memory.alloc(32),spec.resource,spec.resourceBytes);
const empty = stringAt(Memory.alloc(32),'',0);
function accountMatches() {
  const path = Memory.alloc(32); path.writeByteArray(new Uint8Array(32));
  resolve(path,label,empty);
  const length = Number(path.add(16).readU64().toString());
  const capacity = Number(path.add(24).readU64().toString());
  if (!Number.isSafeInteger(length) || !Number.isSafeInteger(capacity) || length > capacity || length > 32767)
    throw new Error('Unexpected account path layout');
  try { return (capacity < 8 ? path : path.readPointer()).readUtf16String(length).toLowerCase() === spec.expectedCache.toLowerCase(); }
  finally { destroy(path); }
}
let attempted=false, finished=false, ticket=0;
function poll() {
  if (!ticket || finished) return;
  const flags=status(ticket);
  if (flags === -1) {
    finished=true; send({kind:'probe_error',reason:'Callback generation changed'});
  } else if (flags & 2) {
    finished=true;
    send({kind:'probe_result',callbackInvoked:!!(flags&1),successBit:(flags&4)?1:0,
          callbackReleased:true,duplicateCallback:!!(flags&8),generation:ticket});
  }
}
const hook=Interceptor.attach(mod.base.add(0x43ab0),{
  onLeave() {
    if (attempted || this.threadId !== spec.thread) return;
    attempted=true;
    try {
      if (!accountMatches()) throw new Error('Account changed; sending refused');
      const holder=prepare();
      if (holder.isNull()) throw new Error('Previous callback still owned by client');
      ticket=generation();
      send({kind:'call_started',thread:this.threadId,generation:ticket});
      helper(ptr(0),pair,label,holder);
      poll();
    } catch(error) { finished=true; send({kind:'probe_error',reason:String(error)}); }
  }
});
const timer=setInterval(poll,100);
rpc.exports={stop(){clearInterval(timer);hook.detach();return true;}};
send({kind:'ready',thread:spec.thread});
"""


def build_agent(path, thread, *, recipient, content, resource, expected_cache, bridge, bridge_sha256):
    values = {'recipient':recipient, 'content':content, 'resource':resource}
    for name, value in values.items():
        if not isinstance(value, str) or not value.strip() or '\0' in value:
            raise ValueError(f'{name} must be nonempty and contain no NUL')
    path, bridge = Path(path).resolve(), Path(bridge).resolve(strict=True)
    if hashlib.sha256(bridge.read_bytes()).hexdigest() != bridge_sha256:
        raise ValueError('Callback binary changed')
    if not expected_cache:
        raise ValueError('Account cache verification is required')
    spec = {'path':str(path), 'thread':thread, 'bridge':str(bridge),
            'expectedCache':str(expected_cache),
            'sites':verified_entries(path, [0x43ab0,0x54d4960,0xa520b0,0x7af0]), **values}
    spec.update({name+'Bytes':len(value.encode('utf-8')) for name,value in values.items()})
    return AGENT.replace('SPEC',json.dumps(spec),1)


def send(payload):
    from .native_preflight import preflight
    from .native_paths import build_agent as paths
    from .native_probe import run_agent
    from .native_runtime import verify_identity, verify_resource_account
    from .paths import ROOT
    folder = Path(payload['data_dir']).resolve() / 'native-outbox' / uuid.uuid4().hex
    folder.mkdir(parents=True)
    check = preflight(Path(payload['source_dir']),folder / 'preflight.json')
    runtime, thread = check['runtime'], check['candidate_thread']
    dll = Path(runtime['dll'])
    resource = 'novus-text-' + folder.name
    verify_identity(runtime)
    events = run_agent(paths(dll,thread,resource),runtime['pid'],folder / 'path.jsonl')
    resolved = next((e for e in events if e.get('kind') == 'probe_result'),None)
    if resolved is None:
        raise ValueError('未能确认当前账号资源路径')
    cache = verify_resource_account(payload['source_dir'],resource,resolved['resolved_path'])
    agent = build_agent(dll,thread,recipient=payload['username'],content=payload['content'],
                        resource=resource,expected_cache=cache,
                        bridge=ROOT / 'platform/windows/novus-text-callback-v1.dll',bridge_sha256=BRIDGE_SHA256)
    verify_identity(runtime)
    with (folder / 'attempt.json').open('x',encoding='utf-8') as output:
        json.dump({'target':payload['username'],'content_sha256':hashlib.sha256(payload['content'].encode()).hexdigest(),
                   'state':'attempted'},output)
        output.flush()
        os.fsync(output.fileno())
    events = run_agent(agent,runtime['pid'],folder / 'events.jsonl')
    returned = next((e for e in events if e.get('kind') == 'probe_result'),None)
    state = 'unknown'
    if returned and returned.get('callbackInvoked') and not returned.get('duplicateCallback'):
        state = 'native_success_unconfirmed' if returned['successBit'] else 'native_failure'
    result = {'state':state,'delivery_confirmed':False,'backend':'native','attempt_id':folder.name}
    (folder / 'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result
