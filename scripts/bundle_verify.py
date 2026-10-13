"""Run with the packaged Python; uses only libraries inside the supplied bundle."""
import asyncio
import hashlib
import json
import os
import platform
import site
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

bundle = Path(sys.argv[1]).resolve()
target = "windows-x64" if sys.platform == "win32" else "macos-arm64" if platform.machine() == "arm64" else "macos-x64"
libraries = bundle / "libraries" / target
site.addsitedir(str(libraries))
sys.path.insert(0, str(bundle))
if sys.platform == "win32":
    dll_handle = os.add_dll_directory(str(libraries / "pywin32_system32"))
    sys.path.insert(0, str(bundle / "vendor/pywechat/src"))
    import pyweixin
    import uiautomation
    assert Path(uiautomation.__file__).is_relative_to(libraries)
import frida
import av
import zstandard
from Crypto.Cipher import AES
from PIL import Image
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from novus_wechat.service import Service
from novus_wechat.paths import ROOT

assert ROOT == bundle
assert sys.version_info[:3] == (3, 12, 11), sys.version
assert Path(frida.__file__).is_relative_to(libraries)
assert Path(av.__file__).is_relative_to(libraries)
with av.open(str(Path(__file__).resolve().parents[1] / "tests/fixtures/public-image.wxgf"), format="hevc") as video:
    frame = next(video.decode(video=0))
    assert (frame.width, frame.height) == (64, 64)
with tempfile.TemporaryDirectory(prefix="novus-bundle-test-") as temporary:
    temporary = Path(temporary)
    source = temporary / "wxid_self/db_storage"
    (source / "message").mkdir(parents=True)
    (source / "contact").mkdir()
    with sqlite3.connect(source / "contact/contact.db") as db:
        db.execute("CREATE TABLE contact(username TEXT,nick_name TEXT,remark TEXT,alias TEXT,local_type INTEGER)")
        db.execute("INSERT INTO contact VALUES('test@chatroom','包验收群','','',0)")
    db.close()
    shard = source / "message/message_10.db"
    table = "Msg_" + hashlib.md5(b"test@chatroom").hexdigest()
    with sqlite3.connect(shard) as db:
        db.execute("CREATE TABLE Name2Id(user_name TEXT)")
        db.execute("INSERT INTO Name2Id VALUES('test@chatroom')")
        db.execute(f"CREATE TABLE {table}(local_id INTEGER,local_type INTEGER,create_time INTEGER,real_sender_id INTEGER,message_content TEXT,WCDB_CT_message_content INTEGER,server_id TEXT)")
        db.execute(f"INSERT INTO {table} VALUES(1,1,?,1,'离线组件验证',0,'90001')", (int(time.time()) - 10,))
    db.close()
    before = hashlib.sha256(shard.read_bytes()).hexdigest()
    service = Service(temporary / "data")
    service.configure({"source_dir": str(source)})
    result = service.synchronize(background=False)
    assert result["state"] == "complete", (result, (temporary / "data/sync.log").read_text())
    assert service.store.messages(query="离线组件")["items"]
    assert hashlib.sha256(shard.read_bytes()).hexdigest() == before
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "NOVUS_WECHAT_DATA": str(temporary / "data"), "PYTHONIOENCODING": "utf-8"}
    env.pop("PYTHONPATH", None)
    with (temporary / "server.log").open("w") as log:
        process = subprocess.Popen([sys.executable, str(bundle / "boot.py"), "serve", "--port", str(port)], env=env, stdout=log, stderr=log)
        try:
            for _ in range(150):
                try:
                    with urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1) as response:
                        assert json.load(response)["capabilities"]["source_connection_verified"]
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                raise AssertionError((temporary / "server.log").read_text())
            with urlopen(f"http://127.0.0.1:{port}/") as response:
                assert "微信cli" in response.read().decode("utf-8")

            async def verify():
                params = StdioServerParameters(command=sys.executable, args=[str(bundle / "boot.py"), "mcp", "--port", str(port)], env=env)
                async with stdio_client(params) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        assert len((await session.list_tools()).tools) == 11
                        result = await session.call_tool("create", {"resource": "chats", "data": {"name": "离线包 MCP"}})
                        assert not result.isError, result
                        chat = json.loads(result.content[0].text)
                async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as (reader, writer, _):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        result = await session.call_tool("query", {"resource": "chats", "id": chat["id"]})
                        assert not result.isError, result
                        assert json.loads(result.content[0].text)["name"] == "离线包 MCP"
                        result = await session.call_tool("delete", {"resource": "chats", "id": chat["id"]})
                        assert not result.isError, result
            asyncio.run(verify())
        finally:
            process.terminate()
            process.wait(timeout=15)
print("BUNDLE_PASS", target, sys.version.split()[0], "native dependencies / source worker / Web / stdio MCP / HTTP MCP")
