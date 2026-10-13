import ast
from pathlib import Path
from types import SimpleNamespace
import json

import pytest


@pytest.mark.parametrize('samples,expected_calls', [([[7],[7]],1), ([[7,8]]*6,0), ([[]]*6,0), ([[7],[8]]*3,0)])
def test_preflight_candidate_gate(tmp_path, samples, expected_calls):
    path = Path(__file__).parents[1] / 'novus_wechat/native_preflight.py'
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'preflight')
    module = ast.Module(body=[function],type_ignores=[])
    observations = iter(samples)
    calls = []
    runtime = {'pid':1,'dll':'fixture.dll'}
    def run(*args):
        calls.append(args)
        return [{'kind':'probe_result','sending_called':False,'resolved_path':'account/cache/resource'}]
    namespace = {'Path':Path,'json':json,'uuid':SimpleNamespace(uuid4=lambda:SimpleNamespace(hex='fixed')),
                 'discover':lambda:runtime,'verify_identity':lambda value:None,
                 'observe':lambda *args:[{'thread':i,'alive':True,'nonNull':1} for i in next(observations)],
                 'build_agent':lambda *args:'resolver-only','run_agent':run,
                 'verify_resource_account':lambda *args:Path(args[2])}
    exec(compile(module,str(path),'exec'),namespace)
    output = tmp_path / 'report.json'
    if expected_calls:
        result = namespace['preflight'](tmp_path,output)
        assert result['account_cache_matches']
        assert result['sending_called'] is False
        assert result['send_ready'] is False
        assert output.exists()
    else:
        with pytest.raises(ValueError):
            namespace['preflight'](tmp_path,output)
        assert not output.exists()
    assert len(calls) == expected_calls
