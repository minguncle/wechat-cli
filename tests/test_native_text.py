import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from novus_wechat import native_text


def test_text_spec_utf8_and_binary_identity(tmp_path,monkeypatch):
    bridge=tmp_path/'callback.dll'
    bridge.write_bytes(b'fixture')
    monkeypatch.setattr(native_text,'verified_entries',lambda *args:[])
    monkeypatch.setattr(native_text,'AGENT','SPEC')
    args=dict(recipient='测试群@chatroom',content='文字测试😀',resource='request-1',
              expected_cache='account-cache',bridge=bridge,bridge_sha256=hashlib.sha256(b'fixture').hexdigest())
    spec=json.loads(native_text.build_agent(tmp_path/'client.dll',7,**args))
    assert spec['recipient']==args['recipient']
    assert spec['contentBytes']==len(args['content'].encode('utf-8'))
    bridge.write_bytes(b'changed')
    with pytest.raises(ValueError,match='binary changed'):
        native_text.build_agent(tmp_path/'client.dll',7,**args)


@pytest.mark.parametrize('scenario',['success','failure','wrong_thread','account_changed','busy','pending','generation_changed'])
def test_text_call_once_and_callback_completion(scenario):
    node=shutil.which('node')
    if not node:
        pytest.skip('Node required for isolated agent execution')
    spec={'path':'fixture.dll','bridge':'callback.dll','thread':7,'sites':[],
          'recipient':'123@chatroom','recipientBytes':12,'content':'test','contentBytes':4,
          'resource':'request','resourceBytes':7,'expectedCache':'cache-path'}
    harness=r'''
const vm=require('node:vm');
const input=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
let calls=0,prepares=0,hook=null,poll=null;const events=[];
function p(offset=0){return {offset,add(n){return p(offset+n);},isNull(){return offset===-1;},
writeByteArray(){},writeUtf8String(){},writePointer(){},writeU64(){},
readByteArray(){return new Uint8Array(32);},readU64(){return 10;},readPointer(){return p();},
readUtf16String(){return input.scenario==='account_changed'?'other-cache':'cache-path';}};}
const bridge={path:'callback.dll',getExportByName(n){return {name:n};}};
const context={
Process:{arch:'x64',enumerateThreads(){return [{id:7}];},getModuleByName(){return {path:'fixture.dll',base:p()};}},
Module:{load(){return bridge;}},Memory:{alloc(){return p();},allocUtf8String(){return p();}},
NativeFunction:function(a){
 if(a.name==='novus_callback_version')return ()=>1;
 if(a.name==='novus_callback_prepare')return ()=>{prepares++;return p(input.scenario==='busy'?-1:0);};
 if(a.name==='novus_callback_generation')return ()=>1;
 if(a.name==='novus_callback_status')return ()=>input.scenario==='pending'?16:input.scenario==='generation_changed'?-1:input.scenario==='failure'?19:23;
 if(a.offset===0x54d4960)return ()=>{calls++;};
 if(a.offset===0xa520b0||a.offset===0x7af0)return ()=>{};
 throw new Error('unexpected function');
},ptr:p,Interceptor:{attach(a,h){hook=h;return {detach(){}};}},
setInterval(fn){poll=fn;return 1;},clearInterval(){},rpc:{exports:{}},send(e){events.push(e);}};
vm.runInNewContext(input.agent,context);
hook.onLeave.call({threadId:input.scenario==='wrong_thread'?8:7});
hook.onLeave.call({threadId:input.scenario==='wrong_thread'?8:7});
poll();poll();
process.stdout.write(JSON.stringify({calls,prepares,events}));
'''
    proc=subprocess.run([node,'-e',harness],input=json.dumps({'scenario':scenario,
        'agent':native_text.AGENT.replace('SPEC',json.dumps(spec),1)}),capture_output=True,text=True,check=True,timeout=10)
    out=json.loads(proc.stdout)
    assert out['calls']==(0 if scenario in {'wrong_thread','account_changed','busy'} else 1)
    completed=[e for e in out['events'] if e['kind']=='probe_result']
    assert len(completed)==(1 if scenario in {'success','failure'} else 0)
    if completed:
        assert completed[0]['successBit']==(1 if scenario=='success' else 0)
        assert completed[0]['callbackReleased'] is True
    if scenario=='account_changed':
        assert out['prepares']==0
