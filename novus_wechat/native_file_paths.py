"""Resolve the client's file-send cache path without sending a message."""
import json
from pathlib import Path
import re

from .native_runtime import verified_entries


AGENT = r"""
const spec=SPEC;
const mod=Process.getModuleByName('Weixin.dll');
if(Process.arch!=='x64'||mod.path.toLowerCase()!==spec.path.toLowerCase())throw new Error('Unexpected module');
if(!Process.enumerateThreads().some(t=>t.id===spec.thread))throw new Error('Observed thread gone');
for(const site of spec.sites){
 const bytes=new Uint8Array(mod.base.add(site.rva).readByteArray(32));
 if(Array.from(bytes,n=>n.toString(16).padStart(2,'0')).join('')!==site.prefix)throw new Error('Entry mismatch');
}
const keep=[];
function str(text,length){
 const value=Memory.alloc(32);value.writeByteArray(new Uint8Array(32));
 if(length<16){value.writeUtf8String(text);value.add(24).writeU64(15);}
 else{const data=Memory.allocUtf8String(text);keep.push(data);value.writePointer(data);value.add(24).writeU64(length);}
 value.add(16).writeU64(length);return value;
}
const filename=str(spec.filename,spec.filenameBytes),directory=str(spec.directory,spec.directoryBytes),empty=str('',0);
const resolve=new NativeFunction(mod.base.add(0x4811c30),'pointer',['pointer','int','pointer','pointer','pointer']);
const destroy=new NativeFunction(mod.base.add(0x7af0),'void',['pointer']);
const exists=new NativeFunction(mod.base.add(0x3639f0),'bool',['pointer']);
let attempted=false;
const hook=Interceptor.attach(mod.base.add(0x43ab0),{onLeave(){
 if(attempted||this.threadId!==spec.thread)return;
 attempted=true;
 try{
  const result=Memory.alloc(32);result.writeByteArray(new Uint8Array(32));
  resolve(result,1,directory,filename,empty);
  const length=Number(result.add(16).readU64().toString()),capacity=Number(result.add(24).readU64().toString());
  if(!Number.isSafeInteger(length)||!Number.isSafeInteger(capacity)||length>capacity||length>32767)throw new Error('Unexpected file path layout');
  let path,present;
  try{path=(capacity<8?result:result.readPointer()).readUtf16String(length);present=exists(result);}
  finally{destroy(result);}
  send({kind:'probe_result',resolved_path:path,cache_exists:present,sending_called:false});
 }catch(error){send({kind:'probe_error',reason:String(error)});}
}});
rpc.exports={stop(){hook.detach();return true;}};
send({kind:'ready',thread:spec.thread});
"""


def validate_names(directory,filename):
    if not re.fullmatch(r'novus-[0-9a-f]{32}',directory):
        raise ValueError('A unique request cache directory is required')
    if not filename or filename in {'.','..'} or any(c in filename for c in '\\/:\0'):
        raise ValueError('Expected a filename without path components')


def build_agent(path,thread,directory,filename):
    validate_names(directory,filename)
    path=Path(path).resolve()
    spec={'path':str(path),'thread':thread,'directory':directory,'filename':filename,
          'directoryBytes':len(directory.encode()),'filenameBytes':len(filename.encode()),
          'sites':verified_entries(path,[0x43ab0,0x4811c30,0x7af0,0x3639f0])}
    return AGENT.replace('SPEC',json.dumps(spec),1)


def verify_cache(source_dir,directory,filename,resolved_path):
    validate_names(directory,filename)
    source=Path(source_dir).resolve(strict=True)
    if not source.is_dir() or source.name.lower()!='db_storage':
        raise ValueError('Expected the configured account database directory')
    cache=(source.parent/'cache').resolve(strict=True)
    resolved=Path(resolved_path).resolve()
    try:
        parts=resolved.relative_to(cache).parts
    except ValueError as exc:
        raise ValueError('File cache belongs to another account') from exc
    if (len(parts)!=5 or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',parts[0])
            or parts[1:]!=('chatbot','file',directory,filename)):
        raise ValueError('File cache does not match the request')
    return resolved
