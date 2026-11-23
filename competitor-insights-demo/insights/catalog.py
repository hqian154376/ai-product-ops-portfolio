"""Versioned observations of a fixed design task and immutable report snapshots."""

import csv
import io
import json
import re
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLUMNS = "product name version date step material observation handoff".split()
STEPS = {
    "generate": "生成首图", "iterate": "继续迭代", "edit": "局部编辑",
    "mask": "选区精度", "reference": "参考约束", "history": "版本恢复", "export": "交付导出",
}
RESULTS_BY_STEP = {
    "generate": {"start"},
    "iterate": {"direct", "upload", "unknown"},
    "edit": {"direct", "upload", "unknown"},
    **{step: {"pass", "fail", "unknown"} for step in ("mask", "reference", "history", "export")},
}
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,31}")
SCHEMA = """
CREATE TABLE IF NOT EXISTS tools (product TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS walks (
 product TEXT NOT NULL REFERENCES tools(product), version TEXT NOT NULL, date TEXT NOT NULL,
 step TEXT NOT NULL, material TEXT NOT NULL, observation TEXT NOT NULL, handoff TEXT NOT NULL,
 PRIMARY KEY(product,date,step));
CREATE TABLE IF NOT EXISTS reports (id INTEGER PRIMARY KEY, snapshot TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS research_reports (id INTEGER PRIMARY KEY, snapshot TEXT NOT NULL);
"""


class InputError(ValueError):
    pass


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def parse_csv(payload):
    try:
        reader = csv.DictReader(
            io.StringIO(payload.decode("utf-8-sig"), newline=""), strict=True
        )
        if reader.fieldnames != COLUMNS:
            raise InputError("表头与样例不符。")
        rows = []
        seen = set()
        for raw in reader:
            if None in raw or None in raw.values():
                raise InputError(f"第 {reader.line_num} 行列数有误。")
            row = {key: value.strip() for key, value in raw.items()}
            try:
                stamp = date.fromisoformat(row["date"]).isoformat()
                if stamp != row["date"] or not all(
                    IDENTIFIER.fullmatch(row[key])
                    for key in ("product", "material", "version")
                ):
                    raise ValueError
                if (
                    not 0 < len(row["name"]) <= 80
                    or not 0 < len(row["observation"]) <= 500
                ):
                    raise ValueError
                if row["handoff"] not in RESULTS_BY_STEP.get(row["step"], ()):
                    raise ValueError
            except ValueError:
                raise InputError(
                    f"第 {reader.line_num} 行格式无效，请核对日期、编号、步骤与衔接方式。"
                ) from None
            key = (row["product"], row["date"], row["step"])
            if key in seen or len(rows) >= 1000:
                raise InputError("同一工具、日期和步骤重复，或超过 1000 行。")
            seen.add(key)
            rows.append(row)
    except (UnicodeDecodeError, csv.Error):
        raise InputError("请导入有效的 UTF-8 CSV。") from None
    if not rows:
        raise InputError("至少提供一条走查记录。")
    return rows


def apply_rows(db, rows):
    for row in rows:
        db.execute("INSERT OR IGNORE INTO tools VALUES (:product,:name)", row)
        existing = db.execute(
            "SELECT name FROM tools WHERE product=:product", row
        ).fetchone()
        if existing["name"] != row["name"]:
            raise InputError("已有产品编号与名称不一致。")
        conflict = db.execute(
            "SELECT 1 FROM walks WHERE product=:product AND date=:date AND version<>:version",
            row,
        ).fetchone()
        if conflict:
            raise InputError("同一工具同一天的走查须使用同一版本。")
        db.execute(
            """INSERT INTO walks VALUES (:product,:version,:date,:step,:material,:observation,:handoff)
                      ON CONFLICT(product,date,step) DO UPDATE SET material=excluded.material,
                      observation=excluded.observation, handoff=excluded.handoff""",
            row,
        )


def initialize(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(path)) as db, db:
        db.executescript(SCHEMA)
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM tools LIMIT 1").fetchone():
            apply_rows(db, parse_csv((ROOT / "samples/catalog.csv").read_bytes()))


def import_csv(path, payload):
    rows = parse_csv(payload)
    with closing(connect(path)) as db, db:
        apply_rows(db, rows)
    return {"rows": len(rows)}


def products(path):
    with closing(connect(path)) as db:
        return [dict(row) for row in db.execute("SELECT * FROM tools ORDER BY product")]


def selected(path, left, right):
    if left == right:
        raise InputError("请选择两款不同的设计工具。")
    with closing(connect(path)) as db:
        rows = [
            dict(r)
            for r in db.execute(
                """SELECT * FROM walks JOIN tools USING(product)
                    WHERE product IN (?,?) ORDER BY date, product, step""",
                (left, right),
            )
        ]
    names = {r["product"]: r["name"] for r in rows}
    if len(names) != 2:
        raise InputError("未找到选定工具。")
    for r in rows:
        r["reference"] = f"{r['product']}@{r['date']}/{r['step']}"
    return {
        "products": [{"sku": key, "name": names[key]} for key in (left, right)]
    }, rows


def save_report(path, report):
    with closing(connect(path)) as db, db:
        cursor = db.execute(
            "INSERT INTO reports(snapshot) VALUES (?)",
            (json.dumps(report, ensure_ascii=False),),
        )
    return {"id": cursor.lastrowid, **report}


def list_reports(path):
    with closing(connect(path)) as db:
        return [
            dict(r)
            for r in db.execute("""SELECT id, json_extract(snapshot,'$.title') AS title,
                  json_extract(snapshot,'$.created_at') AS created_at FROM reports ORDER BY id DESC""")
        ]


def get_report(path, report_id):
    with closing(connect(path)) as db:
        row = db.execute(
            "SELECT snapshot FROM reports WHERE id=?", (report_id,)
        ).fetchone()
    return {"id": report_id, **json.loads(row[0])} if row else None


def save_research(path, report):
    with closing(connect(path)) as db, db:
        row = db.execute("INSERT INTO research_reports(snapshot) VALUES (?)", (json.dumps(report, ensure_ascii=False),))
    return {"id": row.lastrowid, **report}


def research_history(path, before=None, limit=30):
    with closing(connect(path)) as db:
        rows = db.execute("""SELECT id, json_extract(snapshot,'$.title') AS title,
                  json_extract(snapshot,'$.created_at') AS created_at, json_extract(snapshot,'$.as_of') AS as_of,
                  json_extract(snapshot,'$.intent') AS intent, json_extract(snapshot,'$.mode') AS mode
                FROM research_reports WHERE (? IS NULL OR id < ?) ORDER BY id DESC LIMIT ?""",
                          (before, before, limit + 1)).fetchall()
    page = [dict(r) for r in rows[:limit]]
    return {"reports": page, "next_before": page[-1]["id"] if len(rows) > limit else None}


def get_research(path, report_id):
    with closing(connect(path)) as db:
        row = db.execute("SELECT snapshot FROM research_reports WHERE id=?", (report_id,)).fetchone()
    return {"id": report_id, **json.loads(row[0])} if row else None
