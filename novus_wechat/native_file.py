"""Pinned Windows file sender; stages a unique account-bound cache copy."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

from .native_runtime import verified_entries
from .native_file_paths import validate_names

BRIDGE_SHA256='f87f626db4d9bf4eca700ff99103251af6dcb50e8171f1e0d2e844aecb8f8d76'


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source,'sha256').hexdigest()

AGENT=r"""
const spec=SPEC;
const mod=Process.getModuleByName('Weixin.dll');
if(Process.arch!=='x64'||mod.path.toLowerCase()!==spec.path.toLowerCase())throw new Error('Unexpected module');
if(!Process.enumerateThreads().some(t=>t.id===spec.thread))throw new Error('Observed thread gone');
for(const site of spec.sites){
 const bytes=new Uint8Array(mod.base.add(site.rva).readByteArray(32));
 if(Array.from(bytes,n=>n.toString(16).padStart(2,'0')).join('')!==site.prefix)throw new Error('Entry mismatch');
}
const bridge=Module.load(spec.bridge);
if(bridge.path.toLowerCase()!==spec.bridge.toLowerCase())throw new Error('Unexpected release module');
const releaseSP=new NativeFunction(bridge.getExportByName('novus_shared_release'),'void',['pointer']);
const keep=[];
function zero(size){const p=Memory.alloc(size);p.writeByteArray(new Uint8Array(size));return p;}
function str(text,length){
 const p=zero(32);
 if(length<16){p.writeUtf8String(text);p.add(24).writeU64(15);}
 else{const data=Memory.allocUtf8String(text);keep.push(data);p.writePointer(data);p.add(24).writeU64(length);}
 p.add(16).writeU64(length);return p;
}
const recipient=str(spec.recipient,spec.recipientBytes),filename=str(spec.filename,spec.filenameBytes);
const directory=str(spec.directory,spec.directoryBytes),empty=str('',0);
const resolve=new NativeFunction(mod.base.add(0x4811c30),'pointer',['pointer','int','pointer','pointer','pointer']);
const destroyPath=new NativeFunction(mod.base.add(0x7af0),'void',['pointer']);
const getCoro=new NativeFunction(mod.base.add(0x43ab0),'pointer',['pointer']);
const getSvc=new NativeFunction(mod.base.add(0x3426c0),'pointer',['pointer','pointer']);
const getDownload=new NativeFunction(mod.base.add(0xecac00),'pointer',['pointer','pointer']);
const helper=new NativeFunction(mod.base.add(0x2d04250),'pointer',['pointer','pointer','pointer','pointer','pointer','pointer','pointer']);
const free=new NativeFunction(mod.base.add(0x7165a30),'void',['pointer','uint64']);
const kernel=Process.getModuleByName('kernel32.dll');
const open=new NativeFunction(kernel.getExportByName('CreateFileW'),'pointer',['pointer','uint32','uint32','pointer','uint32','uint32','pointer']);
const close=new NativeFunction(kernel.getExportByName('CloseHandle'),'bool',['pointer']);
const cacheWide=Memory.allocUtf16String(spec.cache);
let attempted=false;
const hook=Interceptor.attach(mod.base.add(0x43ab0),{onLeave(){
 if(attempted||this.threadId!==spec.thread)return;
 attempted=true;
 const context=zero(16),service=zero(16),download=zero(16);
 let handle=ptr(-1);
 try{
  const path=zero(32);resolve(path,1,directory,filename,empty);
  const length=Number(path.add(16).readU64().toString()),capacity=Number(path.add(24).readU64().toString());
  if(!Number.isSafeInteger(length)||!Number.isSafeInteger(capacity)||length>capacity||length>32767)throw new Error('Unexpected file path layout');
  let actual;
  try{actual=(capacity<8?path:path.readPointer()).readUtf16String(length);}finally{destroyPath(path);}
  if(actual.toLowerCase()!==spec.cache.toLowerCase())throw new Error('Account file cache changed');
  // Keep the verified cache entry present and immutable throughout the call.
  handle=open(cacheWide,0x80000000,1,ptr(0),3,0x80,ptr(0));
  if(handle.equals(ptr(-1)))throw new Error('Cannot hold file cache for reading');
  getCoro(context);
  if(context.readPointer().isNull())throw new Error('No coroutine context');
  getSvc(context.readPointer(),service);
  if(service.readPointer().isNull())throw new Error('No message service');
  getDownload(service.readPointer(),download);
  if(download.readPointer().isNull())throw new Error('No file service');
  const result=zero(64);
  send({kind:'call_started',thread:this.threadId});
  helper(download.readPointer(),result,recipient,empty,empty,filename,directory);
  const success=result.readU8()&1,string=result.add(24);
  const errorLength=Number(string.add(16).readU64().toString()),errorCapacity=Number(string.add(24).readU64().toString());
  if(!Number.isSafeInteger(errorLength)||!Number.isSafeInteger(errorCapacity)||errorLength>errorCapacity||errorCapacity>=4095)throw new Error('Unexpected file result layout');
  const error=(errorCapacity<16?string:string.readPointer()).readUtf8String(errorLength);
  if(errorCapacity>=16)free(string.readPointer(),errorCapacity+1);
  send({kind:'probe_result',successBit:success,errorLength,error});
 }catch(error){send({kind:'probe_error',reason:String(error)});}
 finally{
  releaseSP(download);releaseSP(service);releaseSP(context);
  if(!handle.equals(ptr(-1)))close(handle);
 }
}});
rpc.exports={stop(){hook.detach();return true;}};
send({kind:'ready',thread:spec.thread});
"""


def build_agent(path,thread,*,recipient,directory,filename,cache,bridge):
    validate_names(directory,filename)
    if not recipient or '\0' in recipient:
        raise ValueError('A recipient identifier is required')
    bridge=Path(bridge).resolve(strict=True)
    if hashlib.sha256(bridge.read_bytes()).hexdigest()!=BRIDGE_SHA256:
        raise ValueError('Shared release binary changed')
    cache=Path(cache).resolve(strict=True)
    if not cache.is_file():
        raise ValueError('Prepared file cache is missing')
    spec={'path':str(Path(path).resolve()),'thread':thread,'bridge':str(bridge),'cache':str(cache),
          'recipient':recipient,'directory':directory,'filename':filename,
          'sites':verified_entries(path,[0x43ab0,0x3426c0,0xecac00,0x2d04250,0x4811c30,0x7af0,0x7165a30])}
    spec.update({name+'Bytes':len(spec[name].encode('utf-8')) for name in ('recipient','directory','filename')})
    return AGENT.replace('SPEC',json.dumps(spec),1)


def send(payload):
    from .native_preflight import preflight
    from .native_file_paths import build_agent as paths, verify_cache
    from .native_runtime import verify_identity
    from .native_probe import run_agent
    from .paths import ROOT
    folder=Path(payload['data_dir']).resolve()/'native-outbox'/uuid.uuid4().hex
    folder.mkdir(parents=True)
    original=Path(payload['files'][0])
    staged=folder/'payload'/original.name
    staged.parent.mkdir()
    shutil.copyfile(original,staged)
    file_digest=digest(staged)
    check=preflight(Path(payload['source_dir']),folder/'preflight.json')
    runtime,thread=check['runtime'],check['candidate_thread']
    dll=Path(runtime['dll']);directory='novus-'+folder.name
    verify_identity(runtime)
    events=run_agent(paths(dll,thread,directory,staged.name),runtime['pid'],folder/'path.jsonl')
    resolved=next((e for e in events if e.get('kind')=='probe_result'),None)
    if resolved is None:
        raise ValueError('未能确认文件缓存路径')
    cache=verify_cache(payload['source_dir'],directory,staged.name,resolved['resolved_path'])
    cache.parent.mkdir(parents=True,exist_ok=True)
    with staged.open('rb') as source,cache.open('xb') as output:
        shutil.copyfileobj(source,output);output.flush();os.fsync(output.fileno())
    if digest(cache)!=file_digest:
        raise ValueError('文件缓存内容校验失败')
    agent=build_agent(dll,thread,recipient=payload['username'],directory=directory,filename=staged.name,
                      cache=cache,bridge=ROOT/'platform/windows/novus-shared-release-v1.dll')
    verify_identity(runtime)
    with (folder/'attempt.json').open('x',encoding='utf-8') as output:
        json.dump({'target':payload['username'],'file_sha256':file_digest,'cache':str(cache),'state':'attempted'},output)
        output.flush();os.fsync(output.fileno())
    events=run_agent(agent,runtime['pid'],folder/'events.jsonl')
    returned=next((e for e in events if e.get('kind')=='probe_result'),None)
    state='unknown' if returned is None else ('native_success_unconfirmed' if returned['successBit'] else 'native_failure')
    result={'state':state,'delivery_confirmed':False,'backend':'native','attempt_id':folder.name}
    if returned and returned.get('error'):
        result['error']=returned['error']
    (folder/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result
