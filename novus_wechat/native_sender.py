"""Windows native text, image and file send worker."""
import contextlib
import json
from pathlib import Path
import sys


def validate(payload):
    if sys.platform != 'win32':
        raise ValueError('本版本尚未适配 Mac 原生发送')
    recipient = payload.get('username')
    if not isinstance(recipient, str) or not recipient.strip() or '\0' in recipient:
        raise ValueError('发送需要有效的会话标识')
    if payload.get('content') and not payload.get('files') and not payload.get('images'):
        if not payload['content'].strip() or '\0' in payload['content']:
            raise ValueError('文字内容不能为空或包含 NUL')
        return
    if not payload.get('content') and not payload.get('images') and len(payload.get('files', [])) == 1:
        path = Path(payload['files'][0])
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError('发送文件必须存在且非空')
        return
    if payload.get('content') or not payload.get('images') or len(payload.get('files', [])) != 1:
        raise ValueError('每个请求发送纯文字、一张静态图片或一个文件；文字和附件请分别提交')
    from PIL import Image
    with Image.open(payload['files'][0]) as image:
        if image.format not in {'PNG','JPEG','WEBP','BMP','GIF'} or getattr(image,'n_frames',1) != 1:
            raise ValueError('图片须为 PNG/JPEG/WebP/BMP/静态 GIF；动画请作为文件发送')
        image.verify()


def send(payload):
    validate(payload)
    if payload.get('content'):
        from .native_text import send as send_text
        return send_text(payload)
    if not payload.get('images'):
        from .native_file import send as send_file
        return send_file(payload)
    from .native_image_once import prepare, stage_cache, claim
    from .native_preflight import preflight
    from .native_paths import build_agent as path_agent
    from .native_probe import build_agent, run_agent
    from .native_runtime import verify_identity, verify_resource_account
    root = Path(payload['data_dir']).resolve()
    recipient = payload['username']
    request = prepare(Path(payload['files'][0]),root / 'native-outbox',recipient=recipient)
    image = request.parent / 'image.png'
    check = preflight(Path(payload['source_dir']),request.parent / 'preflight.json')
    runtime, thread = check['runtime'], check['candidate_thread']
    dll = Path(runtime['dll'])
    verify_identity(runtime)
    events = run_agent(path_agent(dll,thread,str(image)),runtime['pid'],request.parent / 'path.jsonl')
    resolved = next((e for e in events if e.get('kind') == 'probe_result'), None)
    if resolved is None:
        raise ValueError('未能确认当前账号资源路径')
    cache = verify_resource_account(payload['source_dir'],str(image),resolved['resolved_path'])
    stage_cache(request,cache,Path(payload['source_dir']).resolve().parent / 'cache',recipient=recipient)
    verify_identity(runtime)
    agent = build_agent(dll,thread,image,negative=False,expected_cache=cache,recipient=recipient)
    claim(request,recipient=recipient)
    events = run_agent(agent,runtime['pid'],request.parent / 'events.jsonl')
    returned = next((e for e in events if e.get('kind') == 'probe_result'), None)
    state = 'unknown' if returned is None else ('native_success_unconfirmed' if returned['successBit'] else 'native_failure')
    result = {'state':state,'delivery_confirmed':False,'backend':'native','attempt_id':request.parent.name}
    (request.parent / 'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


if __name__ == '__main__':
    try:
        payload = json.loads(sys.stdin.read())
        with contextlib.redirect_stdout(sys.stderr):
            result = send(payload)
    except Exception as exc:
        result = {'state':'unknown','delivery_confirmed':False,'error':str(exc),
                  'message':'未确认投递结果，请查询请求记录，不要自动重试'}
    print(json.dumps(result,ensure_ascii=False))
