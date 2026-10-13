"""Check a native image runtime and account without staging or sending a file.

Candidate discovery is experimental; successful preflight is not send approval.
"""
import argparse
import json
from pathlib import Path
import uuid

from novus_wechat.native_runtime import discover, verify_identity, verify_resource_account
from .native_threads import observe
from .native_paths import build_agent
from .native_probe import run_agent


def preflight(source_dir, output):
    runtime = discover()
    dll = Path(runtime['dll'])
    samples = []
    # Client background work can temporarily activate additional contexts. Only
    # repeat observation; never invoke a send while thread selection is ambiguous.
    for _ in range(6):
        verify_identity(runtime)
        rows = observe(dll, runtime['pid'], 3)
        active = [r['thread'] for r in rows if r['alive'] and r['nonNull'] > 0]
        if len(active) != 1:
            samples.clear()
            continue
        if samples and samples[-1] != active[0]:
            samples.clear()
        samples.append(active[0])
        if len(samples) == 2:
            break
    else:
        raise ValueError('未连续确认唯一稳定的上下文线程，未调用发送')
    resource = 'novus-preflight-' + uuid.uuid4().hex
    verify_identity(runtime)
    events = run_agent(build_agent(dll,samples[0],resource),runtime['pid'],output.with_suffix('.events.jsonl'))
    verify_identity(runtime)
    result = next((e for e in events if e.get('kind') == 'probe_result'), None)
    if result is None or result.get('sending_called') is not False:
        raise ValueError('未取得有效的账号资源路径')
    resolved = verify_resource_account(source_dir,resource,result['resolved_path'])
    report = {'runtime':runtime,'candidate_thread':samples[0],'account_cache_matches':True,
              'resolved_path':str(resolved),'sending_called':False,'send_ready':False,
              'limitation':'候选线程与账号检查通过；尚未验证重启后的发送与产品集成'}
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    print(json.dumps(preflight(args.source_dir,args.output),ensure_ascii=False))
