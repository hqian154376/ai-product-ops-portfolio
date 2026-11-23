import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Literal

import httpx
from dotenv import dotenv_values
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import analysis, catalog, collection, materials, radar_api, research, strategy_agent


class RequestReport(BaseModel):
    left: str = Field(min_length=1, max_length=32)
    right: str = Field(min_length=1, max_length=32)
    question: str = Field(min_length=1, max_length=2000)


Intent = Literal["opportunity", "benchmark", "change"]


class ResearchRequest(RequestReport):
    intent: Intent = "opportunity"
    as_of: date | None = None
    window_days: radar_api.Window = 7


def create_app(db_path=None, config=None, transport=None):
    db_path = (
        Path(db_path)
        if db_path is not None
        else catalog.ROOT / "runtime" / "walkthroughs.sqlite3"
    )
    if config is None:
        config = {**dotenv_values(catalog.ROOT / ".env"), **os.environ}
    config = {
        k: str(v).strip() for k, v in config.items() if k.startswith("LLM_") and v
    }

    @asynccontextmanager
    async def lifespan(app):
        catalog.initialize(db_path)
        materials.initialize(db_path)
        radar_api.initialize(db_path)
        collection.initialize(db_path)
        with httpx.Client(transport=transport, follow_redirects=False) as client:
            app.state.model_client = client
            yield

    app = FastAPI(title="竞品分析", lifespan=lifespan)
    app.include_router(materials.make_router(db_path))
    app.include_router(radar_api.make_router(db_path))
    app.include_router(collection.make_router(db_path))
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1"])

    @app.middleware("http")
    async def protect_local_writes(request, call_next):
        origin = request.headers.get("origin")
        local_origin = f"{request.url.scheme}://{request.headers.get('host', '')}"
        if origin and request.method in {"POST", "PUT", "PATCH", "DELETE"} and origin != local_origin:
            return JSONResponse({"detail": "请从本机演示页面发起操作。"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(catalog.InputError)
    async def on_input_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=422)

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request, error):
        field = next((str(e["loc"][-1]) for e in error.errors() if e["loc"]), "")
        details = {
            "window_days": "窗口可选 7、14 或 30 天。",
            "as_of": "截止日请使用 YYYY-MM-DD。",
            "intent": "请选择机会优先级、能力对标或版本复验。",
            "question": "请填写 1 到 2000 字的研究问题。",
            "review_refs": "一次最多选择 10 条复核走查。",
            "status": "跟进状态可选 open、reviewed 或 archived。",
            "note": "复核说明最长 2000 字。",
        }
        return JSONResponse({"detail": details.get(field, "请求参数格式不正确，请核对选项及输入。")}, status_code=422)

    @app.exception_handler(analysis.ModelError)
    async def on_model_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=502)

    @app.get("/")
    def home():
        return FileResponse(catalog.ROOT / "static/index.html")

    @app.get("/api/status")
    def status():
        mode = analysis.setting(config)
        return {
            "mode": mode,
            "model": config.get("LLM_MODEL") if mode == "model" else None,
        }

    @app.post('/api/collection/agent-run')
    def run_collection_agent(data: collection.CollectionTask):
        return collection.agent_collect(db_path,data.question,config,app.state.model_client)

    @app.get("/api/products")
    def list_products():
        return {"products": catalog.products(db_path)}

    @app.get("/api/compare")
    def compare(left: str, right: str):
        pair, rows = catalog.selected(db_path, left, right)
        return {**pair, "observations": rows}

    @app.get("/sample.csv")
    def sample():
        return FileResponse(
            catalog.ROOT / "samples/import-example.csv",
            media_type="text/csv",
            filename="import-example.csv",
        )

    @app.post("/api/import")
    async def import_observations(request: Request):
        if (
            request.headers.get("content-type", "").split(";")[0].strip().lower()
            != "text/csv"
        ):
            raise HTTPException(415, "请以 text/csv 提交 UTF-8 CSV 文件。")
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > 1_048_576:
                raise HTTPException(413, "CSV 文件不能超过 1 MiB。")
            data.extend(chunk)
        return await run_in_threadpool(catalog.import_csv, db_path, bytes(data))

    @app.post("/api/reports", status_code=201)
    def create_report(data: RequestReport):
        question = data.question.strip()
        if not question:
            raise catalog.InputError("请填写要分析的问题。")
        pair, evidence = catalog.selected(db_path, data.left, data.right)
        result = analysis.generate(
            pair, evidence, question, config, app.state.model_client
        )
        return catalog.save_report(db_path, result)

    @app.get("/api/reports")
    def history():
        return {"reports": catalog.list_reports(db_path)}

    @app.get("/api/reports/{report_id}")
    def detail(report_id: int):
        result = catalog.get_report(db_path, report_id)
        if result is None:
            raise HTTPException(404, "报告不存在。")
        return result

    @app.get("/api/reports/{report_id}/export")
    def export(report_id: int):
        report = detail(report_id)
        method = report["model"] if report["mode"] == "model" else "本地规则分析"
        lines = [
            f"# {report['title']}",
            "",
            f"问题：{report['question']}",
            f"分析方式：{method}",
            f"生成时间：{report['created_at']}",
            "",
            report["markdown"],
            "",
            "## 引用观测",
            "",
        ]
        counts = report["counts"]
        lines.append(
            f"读取 {counts['materials']} 份材料 / {counts['observations']} 条观测，引用 {counts['citations']} 条。"
        )
        for point in report["evidence"]:
            lines.append(
                f"- [{point['reference']}] {point['name']} {point['version']} · {point['material']} · {point['observation']}"
            )
        return PlainTextResponse(
            "\n".join(lines) + "\n",
            media_type="text/markdown",
            headers={
                "Content-Disposition": f'attachment; filename="comparison-{report_id}.md"'
            },
        )

    def research_snapshot(left, right, intent, as_of):
        pair, rows = catalog.selected(db_path, left, right)
        return research.build(pair, rows, intent=intent, as_of=as_of.isoformat() if as_of else None,
                              signals=materials.load_all(db_path))

    @app.get("/api/research")
    def workspace(left: str, right: str, intent: Intent = "opportunity", as_of: date | None = None):
        return research_snapshot(left, right, intent, as_of)

    @app.post("/api/research/reports", status_code=201)
    def create_research(data: ResearchRequest):
        question = data.question.strip()
        if not question:
            raise catalog.InputError("请填写研究问题。")
        snapshot = research_snapshot(data.left, data.right, data.intent, data.as_of)
        snapshot["feedback_radar"] = radar_api.report_radar(snapshot, data.window_days)
        report = strategy_agent.generate(snapshot, question, config, app.state.model_client)
        return catalog.save_research(db_path, report)

    @app.get("/api/research/reports")
    def research_history(before: int | None = Query(None, gt=0), limit: int = Query(30, ge=1, le=100)):
        return catalog.research_history(db_path, before, limit)

    @app.get("/api/research/reports/{report_id}")
    def research_detail(report_id: int):
        found = catalog.get_research(db_path, report_id)
        if found is None:
            raise HTTPException(404, "研究报告不存在。")
        return found

    @app.get("/api/research/reports/{report_id}/export")
    def research_export(report_id: int):
        return PlainTextResponse(
            strategy_agent.export(research_detail(report_id)), media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="research-{report_id}.md"'},
        )

    app.mount("/static", StaticFiles(directory=catalog.ROOT / "static"), name="static")
    return app
