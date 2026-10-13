"""Context observation must stay bounded and must not expose addresses."""
import ast
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_context_metadata_and_bounds():
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for isolated agent execution')
    tree = ast.parse((Path(__file__).parents[1] / 'novus_wechat/native_threads.py').read_text())
    agent = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == 'AGENT' for t in n.targets))
    agent = agent.replace('SPEC', json.dumps({'path':'fixture.dll','prefix':'00'*32}), 1)
    harness = r"""
const vm=require('node:vm'), fs=require('node:fs');
let hook,detached=false;
const c={Process:{arch:'x64',getModuleByName(){return {path:'fixture.dll',base:{add(){return {readByteArray(){return new Uint8Array(32);}};}}};},enumerateThreads(){return [{id:7}];}},
Interceptor:{attach(p,h){hook=h;return {detach(){detached=true;}};}},rpc:{exports:{}}};
vm.runInNewContext(fs.readFileSync(0,'utf8'),c);
for(let i=0;i<30;i++)hook.onLeave.call({threadId:7},{isNull(){return false;},toString(){return 'private-address-'+i;}});
hook.onLeave.call({threadId:8},{isNull(){return true;}});
for(let i=100;i<400;i++)hook.onLeave.call({threadId:i},{isNull(){return true;}});
process.stdout.write(JSON.stringify({rows:c.rpc.exports.finish(),detached}));
"""
    result = subprocess.run([node,'-e',harness],input=agent,text=True,capture_output=True,check=True)
    assert 'private-address' not in result.stdout
    value = json.loads(result.stdout)
    assert value['detached']
    assert len(value['rows']) == 256
    assert value['rows'][0] == {'thread':7,'calls':30,'nonNull':30,'distinctContextsCapped':16,'alive':True}
    assert value['rows'][1]['nonNull'] == 0
    assert not value['rows'][1]['alive']
