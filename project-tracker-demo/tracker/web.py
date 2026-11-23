import os
import secrets
import threading
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .feishu import Bitable, FeishuError
from .models import ProjectFields, Receipt
from .store import ROOT, Conflict, Store


def local_token(directory):
    configured = os.getenv("TRACKER_RELEASE_TOKEN", "").strip()
    if configured:
        return configured
    path = Path(directory) / "release-token"
    try:
        with os.fdopen(
            os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w"
        ) as f:
            f.write(secrets.token_urlsafe(32))
    except FileExistsError:
        pass
    value = path.read_text().strip()
    if not value:
        raise ValueError("本地令牌文件为空，请移除空文件再启动。")
    return value


def make_app(*, directory=None, adapter_factory=Bitable.from_env, release_token=None):
    store = Store(directory or os.getenv("TRACKER_DATA_DIR") or ROOT / ".local")
    token = release_token or local_token(store.directory)
    sync_lock = threading.Lock()
    app = FastAPI(title="项目进度台账", version="1.0")
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
    )

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
            if (
                urlsplit(origin).netloc != request.headers.get("host")
                or urlsplit(origin).scheme != request.url.scheme
            ):
                return JSONResponse(
                    {"detail": "请从本地台账页面发起操作。"}, status_code=403
                )
        return await call_next(request)

    @app.exception_handler(KeyError)
    async def missing_project(request, exc):
        return JSONResponse({"detail": "项目编号不存在。"}, status_code=404)

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(FeishuError)
    async def external_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=502)

    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    @app.get("/")
    def home():
        return FileResponse(ROOT / "static/index.html")

    @app.get("/api/projects")
    def projects():
        return store.projects()

    @app.get("/api/projects/{project_id}")
    def project(project_id: str):
        return store.detail(project_id)

    @app.patch("/api/projects/{project_id}")
    def edit(project_id: str, fields: ProjectFields):
        store.edit(project_id, fields)
        return store.detail(project_id)

    def sync_project(project_id):
        # 单进程串行对外写入，网络请求期间新的本地回执仍然可以提交。
        with sync_lock:
            with store.connection() as db:
                db.execute("BEGIN")
                pending = db.execute(
                    "SELECT revision FROM outbox WHERE project_id=?", (project_id,)
                ).fetchone()
                if pending is None:
                    return "complete"
                release = store.projection(db, project_id, "production")
            try:
                adapter = adapter_factory()
                if adapter is None:
                    return "offline"
                adapter.write_release(
                    project_id,
                    release["version"],
                    release["result"],
                    release["released_at"],
                )
            except FeishuError as exc:
                with store.connection(write=True) as db:
                    db.execute(
                        "UPDATE outbox SET error=? WHERE project_id=? AND revision=?",
                        (str(exc), project_id, pending["revision"]),
                    )
                return "pending"
            with store.connection(write=True) as db:
                deleted = db.execute(
                    "DELETE FROM outbox WHERE project_id=? AND revision=?",
                    (project_id, pending["revision"]),
                )
                return "complete" if deleted.rowcount else "pending"

    @app.post("/api/releases")
    def record(event: Receipt, authorization: str = Header(default="")):
        if not secrets.compare_digest(
            authorization.encode(), ("Bearer " + token).encode()
        ):
            raise HTTPException(401, "需要有效的发布回执令牌。")
        inserted = store.record(event)
        state = (
            sync_project(event.project_id)
            if event.environment == "production"
            else "local"
        )
        return {"inserted": inserted, "sync": state}

    @app.get("/api/sync")
    def sync_state():
        try:
            configured = adapter_factory() is not None
            config_issue = ""
        except FeishuError as exc:
            configured, config_issue = True, str(exc)
        with store.connection() as db:
            rows = [
                dict(r)
                for r in db.execute(
                    "SELECT project_id, error FROM outbox ORDER BY project_id"
                )
            ]
        return {
            "enabled": configured,
            "error": config_issue,
            "pending": rows if configured else [],
        }

    @app.post("/api/sync/import")
    def import_from_table():
        adapter = adapter_factory()
        if adapter is None:
            raise HTTPException(409, "请先配置飞书应用和表格。")
        try:
            count = store.import_rows(adapter.list_projects())
        except ValidationError as exc:
            err = exc.errors()[0]
            field = str(err["loc"][-1])
            raise HTTPException(
                422, f"飞书行字段格式不符（{field}），请参照字段模板。"
            ) from None
        return {"count": count}

    @app.post("/api/sync/retry")
    def retry():
        if adapter_factory() is None:
            raise HTTPException(409, "请先配置飞书应用和表格。")
        with store.connection() as db:
            ids = [
                r[0]
                for r in db.execute("SELECT project_id FROM outbox ORDER BY project_id")
            ]
        return {"results": {p: sync_project(p) for p in ids}}

    return app
