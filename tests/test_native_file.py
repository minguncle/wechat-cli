import json
import shutil
import subprocess

import pytest

from novus_wechat.native_file import AGENT


@pytest.mark.parametrize('scenario',['success','failure','wrong_thread','account_changed','locked','no_context','no_service','no_download','helper_error'])
def test_file_call_and_owned_reference_cleanup(scenario):
    node=shutil.which('node')
    if not node:
        pytest.skip('Node required for isolated agent execution')
    spec={'path':'fixture.dll','bridge':'release.dll','cache':'cache-path','thread':7,'sites':[],
          'recipient':'123@chatroom','recipientBytes':12,'directory':'novus-'+'a'*32,'directoryBytes':38,
          'filename':'test.txt','filenameBytes':8}
    harness=r'''
const vm=require('node:vm');
const input=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
let hook=null,calls=0,releases=0,closes=0;const events=[];
function p(offset=0,store={}){return {offset,store,add(n){return p(offset+n,store);},
writeByteArray(){},writeUtf8String(s){store[offset]=s;},writePointer(v){store[offset]=v;},writeU64(){},
readPointer(){return typeof store[offset]==='object'?store[offset]:p(99);},
readU64(){return offset===40?0:offset===48?15:10;},
readU8(){return input.scenario==='failure'?0:1;},
readUtf8String(){return '';},
readUtf16String(){return input.scenario==='account_changed'?'other-account':'cache-path';},
equals(other){return offset===other.offset;},isNull(){return offset===0;}};}
function text(v){return typeof v.store[v.offset]==='string'?v.store[v.offset]:text(v.readPointer());}
const context={
Process:{arch:'x64',enumerateThreads(){return [{id:7}];},getModuleByName(name){return name==='kernel32.dll'?{getExportByName(n){return {name:n};}}:{path:'fixture.dll',base:p()};}},
Module:{load(){return {path:'release.dll',getExportByName(n){return {name:n};}};}},
Memory:{alloc(){return p();},allocUtf8String(s){const v=p();v.writeUtf8String(s);return v;},allocUtf16String(){return p();}},
NativeFunction:function(a){
 if(a.name==='novus_shared_release')return ()=>{releases++;};
 if(a.name==='CreateFileW')return ()=>p(input.scenario==='locked'?-1:10);
 if(a.name==='CloseHandle')return ()=>{closes++;return true;};
 if(a.offset===0x4811c30||a.offset===0x7af0||a.offset===0x7165a30)return ()=>{};
 if(a.offset===0x43ab0)return out=>out.writePointer(p(input.scenario==='no_context'?0:99));
 if(a.offset===0x3426c0)return (v,out)=>out.writePointer(p(input.scenario==='no_service'?0:99));
 if(a.offset===0xecac00)return (v,out)=>out.writePointer(p(input.scenario==='no_download'?0:99));
 if(a.offset===0x2d04250)return (...args)=>{
  calls++;
  if(args.length!==7||text(args[2])!=='123@chatroom'||text(args[3])!==''||text(args[4])!==''||text(args[5])!=='test.txt'||text(args[6])!=='novus-'+'a'.repeat(32))throw new Error('wrong file arguments');
  if(input.scenario==='helper_error')throw new Error('helper failed');
 };
 throw new Error('unexpected native function');
},ptr:p,Interceptor:{attach(a,h){hook=h;return {detach(){}};}},rpc:{exports:{}},send(e){events.push(e);}};
vm.runInNewContext(input.agent,context);
const ctx={threadId:input.scenario==='wrong_thread'?8:7};
hook.onLeave.call(ctx);hook.onLeave.call(ctx);
process.stdout.write(JSON.stringify({calls,releases,closes,events}));
'''
    proc=subprocess.run([node,'-e',harness],input=json.dumps({'scenario':scenario,'agent':AGENT.replace('SPEC',json.dumps(spec),1)}),capture_output=True,text=True,check=True,timeout=10)
    out=json.loads(proc.stdout)
    assert out['calls']==(1 if scenario in {'success','failure','helper_error'} else 0)
    assert out['releases']==(0 if scenario=='wrong_thread' else 3)
    assert out['closes']==(0 if scenario in {'wrong_thread','account_changed','locked'} else 1)
    results=[e for e in out['events'] if e['kind']=='probe_result']
    assert len(results)==(1 if scenario in {'success','failure'} else 0)
    if results:
        assert results[0]['successBit']==(1 if scenario=='success' else 0)
