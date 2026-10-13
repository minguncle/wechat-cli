import json
import mimetypes
import hashlib
import os
import signal
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Body, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .mcp import make_mcp
from . import __version__
from .paths import ROOT, data_root
from .service import Service


def create_app(root=None, port=18765, polling=True):
    service = Service(root or data_root())
    mcp = make_mcp(f"http://127.0.0.1:{port}")

    @asynccontextmanager
    async def lifespan(app):
        if polling:
            service.poll()
        async with mcp.session_manager.run():
            yield
        service.stop_event.set()

    app = FastAPI(title="Novus WeChat", version=__version__, lifespan=lifespan)
    app.state.service = service
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def local_request(request: Request, call_next):
        if request.method in {"POST", "PATCH", "PUT", "DELETE"} and request.url.path.startswith("/api/"):
            if request.headers.get("x-novus-wechat") != "1":
                return JSONResponse({"detail": "缺少本地应用请求标识"}, 403)
        origin = request.headers.get("origin")
        if origin and origin not in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}:
            return JSONResponse({"detail": "不允许跨站访问"}, 403)
        return await call_next(request)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, 400)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": str(exc.args[0])}, 404)

    @app.get("/api/status")
    def status():
        return service.status()

    @app.get("/api/settings")
    def settings():
        return {**service.public_settings(), "candidates": service.discover()}

    @app.put("/api/settings")
    def configure(changes: dict = Body(...)):
        return service.configure(changes)

    @app.post("/api/sync")
    def sync(full: bool = False):
        return service.synchronize(full=full)

    @app.post("/api/shutdown")
    def shutdown():
        service.stop_event.set()
        threading.Timer(0.3, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
        return {"state": "stopping"}

    @app.get("/api/chats")
    def chats(query: str = "", kind: str = "", limit: int = Query(100, ge=1, le=500),
              offset: int = Query(0, ge=0), include_deleted: bool = False):
        return service.store.chats(query, kind, limit, offset, include_deleted)

    @app.get("/api/chats/{chat_id}")
    def chat(chat_id: str):
        return service.store.chat(chat_id)

    @app.post("/api/chats")
    def create_chat(data: dict = Body(...)):
        return service.store.create_chat(data.get("name", ""), data.get("kind", "private"))

    @app.patch("/api/chats/{chat_id}")
    def patch_chat(chat_id: str, changes: dict = Body(...)):
        return service.store.patch_chat(chat_id, changes)

    @app.delete("/api/chats/{chat_id}")
    def delete_chat(chat_id: str):
        return service.store.patch_chat(chat_id, {"deleted": True})

    @app.get("/api/messages")
    def messages(chat_id: str = "", query: str = "", start: int = 0, end: int = 0, kind: str = "",
                 limit: int = Query(100, ge=1, le=500), cursor: str = "", include_deleted: bool = False):
        return service.store.messages(chat_id, query, start, end, kind, limit, cursor, include_deleted)

    @app.get("/api/messages/{message_id}")
    def message(message_id: str):
        return service.store.message(message_id)

    @app.post("/api/messages")
    def create_message(data: dict = Body(...)):
        return service.store.create_message(data["chat_id"], data.get("content", ""), data.get("sender", "我"),
                                            data.get("timestamp", 0), data.get("kind", "text"))

    @app.patch("/api/messages/{message_id}")
    def patch_message(message_id: str, changes: dict = Body(...)):
        return service.store.patch_message(message_id, changes)

    @app.delete("/api/messages/{message_id}")
    def delete_message(message_id: str):
        return service.store.patch_message(message_id, {"deleted": True})

    @app.get("/api/messages/{message_id}/media")
    def media(message_id: str):
        path = service.media(message_id)
        return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream")

    @app.post("/api/import")
    def import_records(payload: dict = Body(...)):
        return service.import_records(payload)

    @app.post("/api/import-file")
    async def import_file(file: UploadFile = File(...)):
        data = await file.read(100 * 1024 * 1024 + 1)
        if len(data) > 100 * 1024 * 1024:
            raise HTTPException(413, "单文件上限为 100 MB；更大数据请使用本机导入命令")
        return service.import_records(json.loads(data.decode("utf-8-sig")))

    @app.post("/api/attachments")
    async def attachment(file: UploadFile = File(...)):
        content = await file.read(100 * 1024 * 1024 + 1)
        if not content or len(content) > 100 * 1024 * 1024:
            raise HTTPException(413, "附件必须在 1 字节到 100 MB 之间")
        name = Path((file.filename or "file").replace("\\", "/")).name
        directory = service.root / "uploads" / hashlib.sha256(content).hexdigest()[:24]
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_bytes(content)
        return {"path": str(path), "name": name, "is_image": path.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}}

    @app.get("/api/export/{chat_id}")
    def export(chat_id: str, format: Literal["json", "csv", "markdown", "xlsx"] = "json", start: int = 0, end: int = 0):
        result = service.export(chat_id, format, start, end)
        mime = {"json": "application/json", "csv": "text/csv", "markdown": "text/markdown",
                "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}[format]
        ext = "md" if format == "markdown" else format
        return Response(result, media_type=mime, headers={"Content-Disposition": f'attachment; filename="wechat-export.{ext}"'})

    @app.post("/api/send")
    def send(data: dict = Body(...)):
        return service.send(data["chat_id"], data.get("content", ""), data.get("files"), data.get("request_id", ""), data.get("images", False))

    @app.get("/api/send/{request_id}")
    def send_status(request_id: str):
        return service.store.send_status(request_id)

    @app.get("/")
    def index():
        return FileResponse(ROOT / "web/index.html")

    app.mount("/assets", StaticFiles(directory=ROOT / "web"), name="assets")
    app.mount("/", mcp.streamable_http_app())
    return app
