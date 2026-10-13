import ast
import json
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('value', ['汉'*5,'汉'*6,'😀'*4])
def test_native_string_uses_byte_length(value):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node required for isolated string layout test')
    tree = ast.parse((Path(__file__).parents[1] / 'novus_wechat/native_probe.py').read_text(encoding='utf-8'))
    agent = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
                 and any(isinstance(t,ast.Name) and t.id=='AGENT' for t in n.targets))
    function = agent[agent.index('function inputString('):agent.index('const recipient =')]
    harness = r"""
const vm=require('node:vm'),fs=require('node:fs');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const writes={};let heap=false;
function p(offset=0){return {writeByteArray(){},writeUtf8String(t){if(Buffer.byteLength(t)>15)throw Error('inline overflow');},
add(n){return p(offset+n);},writeU64(n){writes[offset]=n;},writePointer(){heap=true;}};}
const context={keep:[],Memory:{alloc(){return p();},allocUtf8String(t){return Buffer.from(t);}}};
vm.runInNewContext(input.function,context);
context.inputString(input.value,Buffer.byteLength(input.value));
process.stdout.write(JSON.stringify({writes,heap}));
"""
    result = subprocess.run([node,'-e',harness],input=json.dumps({'function':function,'value':value}),
                            text=True,capture_output=True,check=True)
    row = json.loads(result.stdout)
    size = len(value.encode('utf-8'))
    assert row['writes']['16'] == size
    assert row['writes']['24'] == (15 if size < 16 else size)
    assert row['heap'] == (size >= 16)
