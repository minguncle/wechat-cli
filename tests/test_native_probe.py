"""Isolated negative-probe flow; no native client or messages are involved."""
import ast
import json
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("scenario", ["normal", "present", "race", "wrong_thread", "real_file", "real_disappeared", "account_match", "account_changed"])
def test_fixed_negative_probe(scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node required for isolated agent execution")
    path = Path(__file__).parents[1] / "novus_wechat/native_probe.py"
    tree = ast.parse(path.read_text())
    agent = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "AGENT" for t in n.targets))
    spec = {"path":"fixture.dll", "thread":7, "missing":"absent.png", "sites":[],
            "recipient":"filehelper", "recipientBytes":10}
    if scenario.startswith("real_"):
        spec["negative"] = False
    if scenario.startswith("account_"):
        spec["expectedCache"] = "cache-path"
    harness = r"""
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0,'utf8'));
let helperCalls=0, frees=0, checks=0, hook=null; const events=[];
function p(offset=0) { return {
  offset, add(n) {return p(offset+n);},
  writeByteArray(){}, writeUtf8String(){}, writeU64(){}, writePointer(){},
  readU8(){return input.scenario==='real_file'?1:0;}, readU64(){return {toString(){return input.scenario==='real_file'?(offset===40?'0':'15'):(offset===40?'16':'31');}};},
  readPointer(){return p();}, readUtf8String(){return 'image not exist:';}, readUtf16String(){return input.scenario==='account_changed'?'other-account':'cache-path';}
}; }
const context = {
  Process:{arch:'x64',enumerateThreads(){return [{id:7}];},
    getModuleByName(){return {path:'fixture.dll',base:p(),getExportByName(){return p(-1);}};}},
  Memory:{alloc:p,allocUtf16String(){return p();},allocUtf8String(){return p();}},
  NativeFunction:function(address){
    if(address.offset===-1)return ()=>{checks++;if(input.scenario.startsWith('real_'))return input.scenario==='real_disappeared'&&checks>1?0xffffffff:0;return input.scenario==='present'||(input.scenario==='race'&&checks>1)?0:0xffffffff;};
    if(address.offset===0x2d02be0)return ()=>{helperCalls++;};
    if(address.offset===0xa520b0 || address.offset===0x7af0)return ()=>{};
    if(address.offset===0x7165a30)return ()=>{frees++;};
    throw new Error('unexpected native function');
  },
  ptr:p, Interceptor:{attach(address,callbacks){hook=callbacks;return {detach(){}};}},
  send(event){events.push(event);}, rpc:{exports:{}}
};
// Every fresh allocation has its own offset-zero base in the simulated layout.
context.Memory.alloc=()=>p();
let error=null;
try {
 vm.runInNewContext(input.agent,context);
 const call={threadId:input.scenario==='wrong_thread'?8:7};
 hook.onLeave.call(call);hook.onLeave.call(call);
} catch(e){error=e.message;}
process.stdout.write(JSON.stringify({helperCalls,frees,events,error}));
"""
    result = subprocess.run([node, "-e", harness], input=json.dumps({"scenario":scenario,
        "agent":agent.replace("SPEC", json.dumps(spec), 1)}), capture_output=True, text=True, check=True, timeout=10)
    out = json.loads(result.stdout)
    if scenario in {"normal", "account_match"}:
        assert out["error"] is None
        assert out["helperCalls"] == out["frees"] == 1
        assert out["events"][-1]["missingFileError"] is True
        assert out["events"][-1]["successBit"] == 0
    elif scenario == "real_file":
        assert out["error"] is None
        assert out["helperCalls"] == 1
        assert out["frees"] == 0
        assert out["events"][-1]["successBit"] == 1
    else:
        assert out["helperCalls"] == out["frees"] == 0
