import asyncio
import base64
import io
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from novus_wechat.paths import ROOT
from novus_wechat.store import Store
from PIL import Image


def test_real_stdio_and_streamable_http(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "NOVUS_WECHAT_DATA": str(tmp_path), "PYTHONIOENCODING": "utf-8"}
    process = subprocess.Popen([sys.executable, str(ROOT / "boot.py"), "serve", "--port", str(port)],
                                env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        for _ in range(100):
            try:
                with urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise AssertionError("Server did not start")

        async def verify():
            parameters = StdioServerParameters(command=sys.executable,
                args=[str(ROOT / "boot.py"), "mcp", "--port", str(port)], env=env)
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    assert {t.name for t in tools.tools} >= {"query", "create", "update", "delete", "get_media", "send"}
                    result = await session.call_tool("create", {"resource": "chats", "data": {"name": "MCP测试群"}})
                    assert not result.isError, result
                    chat = json.loads(result.content[0].text)
                    result = await session.call_tool("create", {"resource": "messages", "data": {
                        "chat_id": chat["id"], "kind": "image", "content": "合成图片验收"}})
                    assert not result.isError, result
                    message = json.loads(result.content[0].text)
                    buffer = io.BytesIO()
                    Image.new("RGB", (2, 2), "green").save(buffer, "PNG")
                    media = tmp_path / "media/test.png"
                    media.parent.mkdir(exist_ok=True)
                    media.write_bytes(buffer.getvalue())
                    with Store(tmp_path).db() as db:
                        db.execute("UPDATE messages SET media_path=? WHERE id=?", (str(media), message["id"]))
                    result = await session.call_tool("get_media", {"message_id": message["id"]})
                    assert not result.isError, result
                    block = next(item for item in result.content if item.type == "image")
                    assert block.mimeType == "image/png"
                    assert base64.b64decode(block.data) == buffer.getvalue()
            async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as (reader, writer, _):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    result = await session.call_tool("query", {"resource": "chats", "id": chat["id"]})
                    assert not result.isError, result
                    assert json.loads(result.content[0].text)["name"] == "MCP测试群"
                    result = await session.call_tool("delete", {"resource": "chats", "id": chat["id"]})
                    assert not result.isError
        asyncio.run(verify())
    finally:
        process.terminate()
        process.wait(timeout=15)
