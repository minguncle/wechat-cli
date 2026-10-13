import hashlib
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from novus_wechat.api import create_app
from novus_wechat import native_sender, service


@pytest.mark.parametrize('worker_state',['native_success_unconfirmed','unknown'])
@pytest.mark.parametrize('recipient',['filehelper','wxid_test_person','123456@chatroom'])
def test_api_native_route_and_no_retry(tmp_path,monkeypatch,worker_state,recipient):
    source = tmp_path / 'db_storage'
    source.mkdir()
    picture = tmp_path / 'test.png'
    Image.new('RGB',(8,8)).save(picture)
    app = create_app(tmp_path / 'app',polling=False)
    instance = app.state.service
    instance.configure({'source_dir':str(source),'send_enabled':True})
    chat = 'wechat:'+hashlib.sha256(str(source.resolve()).encode()).hexdigest()[:12]+':'+recipient
    instance.store.ingest([{'entity':'chat','id':chat,'name':'任意显示名','source':'wechat'}])
    calls = []
    def execute(command,**kwargs):
        calls.append((command,json.loads(kwargs['input'])))
        return SimpleNamespace(stdout=json.dumps({'state':worker_state,'delivery_confirmed':False}))
    monkeypatch.setattr(service.subprocess,'run',execute)
    monkeypatch.setattr(native_sender.sys,'platform','win32')
    with TestClient(app) as client:
        payload = {'chat_id':chat,'request_id':'single','files':[str(picture)],'images':True}
        headers = {'X-Novus-WeChat':'1'}
        first = client.post('/api/send',json=payload,headers=headers)
        second = client.post('/api/send',json=payload,headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert first.json()['state'] == worker_state
    assert len(calls) == 1
    assert calls[0][0][-1] == 'native-sender'
    assert calls[0][1]['username'] == recipient


def test_unsupported_native_payloads(monkeypatch,tmp_path):
    monkeypatch.setattr(native_sender.sys,'platform','win32')
    base = {'username':'filehelper','content':'','images':True,'files':['x.png']}
    for change in ({'username':''},{'username':'a\0b'},{'username':None},{'content':'text'},{'files':['a.png','b.png']},{'images':False}):
        with pytest.raises(ValueError):
            native_sender.validate({**base,**change})
    picture = tmp_path / 'fake.png'
    Image.new('RGB',(8,8)).save(picture,format='TIFF')
    with pytest.raises(ValueError,match='PNG'):
        native_sender.validate({**base,'files':[str(picture)]})


@pytest.mark.parametrize('recipient',['wxid_test_person','123456@chatroom'])
@pytest.mark.parametrize('kind',['text','file'])
def test_text_and_file_use_native_worker_and_request_deduplication(tmp_path,monkeypatch,recipient,kind):
    source=tmp_path/'db_storage'
    source.mkdir()
    app=create_app(tmp_path/'app',polling=False)
    instance=app.state.service
    instance.configure({'source_dir':str(source),'send_enabled':True})
    chat='wechat:'+hashlib.sha256(str(source.resolve()).encode()).hexdigest()[:12]+':'+recipient
    instance.store.ingest([{'entity':'chat','id':chat,'name':'测试目标','source':'wechat'}])
    calls=[]
    def execute(command,**kwargs):
        calls.append((command,json.loads(kwargs['input'])))
        return SimpleNamespace(stdout=json.dumps({'state':'native_success_unconfirmed','delivery_confirmed':False}))
    monkeypatch.setattr(service.subprocess,'run',execute)
    monkeypatch.setattr(native_sender.sys,'platform','win32')
    payload={'chat_id':chat,'request_id':'text-one','content':'文字测试😀'}
    if kind=='file':
        attachment=tmp_path/'附件.txt'
        attachment.write_text('文件测试',encoding='utf-8')
        payload={'chat_id':chat,'request_id':'file-one','files':[str(attachment)]}
    with TestClient(app) as client:
        first=client.post('/api/send',json=payload,headers={'X-Novus-WeChat':'1'})
        again=client.post('/api/send',json=payload,headers={'X-Novus-WeChat':'1'})
    assert first.status_code==200
    assert first.json()==again.json()
    assert len(calls)==1
    assert calls[0][0][-1]=='native-sender'
    assert calls[0][1]['username']==recipient
    assert calls[0][1]['content']==payload.get('content','')
    assert calls[0][1]['files']==payload.get('files',[])
