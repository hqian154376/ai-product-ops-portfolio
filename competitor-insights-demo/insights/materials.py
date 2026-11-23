"""Import attributable research excerpts. An ID always denotes the same evidence."""
import csv
import io
import re
from contextlib import closing
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from . import catalog, research

FIELDS = "id product version date capability source_type origin source position excerpt".split()
CHANNELS = {"interview", "community", "support", "unknown"}
SAMPLES = Path(__file__).resolve().parents[1] / "samples"
EVIDENCE_ID = re.compile(r"SIG-[A-Z0-9]+(?:-[A-Z0-9]+)*")
SCHEMA = """
CREATE TABLE IF NOT EXISTS research_materials (
 id TEXT PRIMARY KEY, product TEXT NOT NULL REFERENCES tools(product), version TEXT NOT NULL,
 date TEXT NOT NULL, capability TEXT NOT NULL, source_type TEXT NOT NULL,
 origin TEXT NOT NULL, source TEXT NOT NULL, position TEXT NOT NULL, excerpt TEXT NOT NULL,
 channel TEXT NOT NULL DEFAULT 'unknown'
)"""
ADD = """INSERT INTO research_materials
(id,product,version,date,capability,source_type,origin,source,position,excerpt,channel)
VALUES (:id,:product,:version,:date,:capability,:source_type,:origin,:source,:position,:excerpt,:channel)"""


def parse(payload):
    try:
        reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig"), newline=""), strict=True)
        if reader.fieldnames not in (FIELDS, [*FIELDS, "channel"]):
            raise catalog.InputError("来源材料 CSV 表头与样例不符。")
        rows = []
        for record in reader:
            line = reader.line_num
            if None in record or None in record.values():
                raise catalog.InputError(f"第 {line} 行列数不正确。")
            row = {key: value.strip() for key, value in record.items()}
            row["channel"] = row.get("channel") or "unknown"
            if row["channel"] not in CHANNELS:
                raise catalog.InputError(f"第 {line} 行渠道应填 interview、community、support 或 unknown。")
            try:
                if date.fromisoformat(row["date"]).isoformat() != row["date"]:
                    raise ValueError
            except ValueError:
                raise catalog.InputError(f"第 {line} 行日期需为 YYYY-MM-DD。") from None
            if len(row["id"]) > 64 or not EVIDENCE_ID.fullmatch(row["id"]):
                raise catalog.InputError(f"第 {line} 行证据编号需形如 SIG-TEAM-01。")
            for key in ("product", "version", "origin"):
                if not catalog.IDENTIFIER.fullmatch(row[key]):
                    raise catalog.InputError(f"第 {line} 行 {key} 编号无效。")
            if row["capability"] not in research.CAPABILITIES or row["source_type"] not in {"release","feedback","documentation"}:
                raise catalog.InputError(f"第 {line} 行能力或来源类型无效。")
            if row["position"] not in {"support","friction","unknown"}:
                raise catalog.InputError(f"第 {line} 行证据倾向无效。")
            if not (0 < len(row["source"]) <= 160 and 0 < len(row["excerpt"]) <= 1200):
                raise catalog.InputError(f"第 {line} 行需填写来源和摘录，长度分别至多 160 和 1200 字。")
            rows.append((line, row))
            if len(rows) > 1000:
                raise catalog.InputError("一次最多导入 1000 条材料摘录。")
        if not rows:
            raise catalog.InputError("至少提供一行来源材料。")
        return rows
    except (UnicodeDecodeError, csv.Error):
        raise catalog.InputError("请提交有效的 UTF-8 CSV 文件。") from None


def initialize(path):
    # Built-in records may grow between demo versions; preserve imported records
    # on reopen and keep the original citation immutable on an ID collision.
    with closing(catalog.connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(SCHEMA)
        if "channel" not in {r["name"] for r in db.execute("PRAGMA table_info(research_materials)")}:
            db.execute("ALTER TABLE research_materials ADD COLUMN channel TEXT NOT NULL DEFAULT 'unknown'")
        db.executemany(ADD.replace("INSERT INTO", "INSERT OR IGNORE INTO"),
                       [row for _, row in parse((SAMPLES / "research-signals.csv").read_bytes())])


def load_all(path):
    with closing(catalog.connect(path)) as db:
        return [dict(r) for r in db.execute("SELECT * FROM research_materials ORDER BY date,id")]


def import_materials(path, payload):
    rows = parse(payload)
    inserted = 0
    with closing(catalog.connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        known = {r[0] for r in db.execute("SELECT product FROM tools")}
        for line, row in rows:
            if row["product"] not in known:
                raise catalog.InputError(f"第 {line} 行产品未知，请先导入该产品的任务走查。")
            existing = db.execute("SELECT * FROM research_materials WHERE id=?", (row["id"],)).fetchone()
            if existing:
                if dict(existing) != row:
                    raise HTTPException(409, f"第 {line} 行证据编号已有不同内容；请为新材料另设编号。")
                continue
            db.execute(ADD, row)
            inserted += 1
    return {"inserted": inserted, "unchanged": len(rows) - inserted}


def make_router(path):
    router = APIRouter(prefix="/api/research/materials")

    @router.get("/sample")
    def sample():
        return FileResponse(SAMPLES / "source-import-example.csv",
                            media_type="text/csv", filename="source-import-example.csv")

    @router.get("/verification-sample")
    def verification_sample():
        return FileResponse(SAMPLES / "verification-example.csv", media_type="text/csv",
                            filename="verification-example.csv")

    @router.post("/import")
    async def receive(request: Request):
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "text/csv":
            raise HTTPException(415, "请以 text/csv 提交来源材料。")
        data = bytearray()
        async for chunk in request.stream():
            if len(data) + len(chunk) > 1_048_576:
                raise HTTPException(413, "来源材料 CSV 不得超过 1 MiB。")
            data.extend(chunk)
        return await run_in_threadpool(import_materials, path, bytes(data))

    return router
