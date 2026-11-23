"""Feedback windows and human follow-up of reproducible research signals."""

import json
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import AfterValidator, BaseModel, Field

from . import catalog, feedback


def valid_window(value):
    if value not in (7, 14, 30):
        raise ValueError("窗口可选 7、14 或 30 天。")
    return value


Window = Annotated[int, AfterValidator(valid_window)]


class AlertSelection(BaseModel):
    left: str = Field(min_length=1, max_length=32)
    right: str = Field(min_length=1, max_length=32)
    product: str = Field(min_length=1, max_length=32)
    topic_id: str = Field(min_length=1, max_length=160)
    as_of: date
    window_days: Window = 7


class Review(BaseModel):
    status: Literal["open", "reviewed", "archived"]
    note: str = Field(max_length=2000)
    review_refs: list[str] | None = Field(default=None, max_length=10)


def initialize(path):
    with closing(catalog.connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""CREATE TABLE IF NOT EXISTS feedback_followups (
            id INTEGER PRIMARY KEY, pair_a TEXT NOT NULL, pair_b TEXT NOT NULL,
            product TEXT NOT NULL, topic_id TEXT NOT NULL, as_of TEXT NOT NULL,
            window_days INTEGER NOT NULL, snapshot TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open', note TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
            review_evidence TEXT NOT NULL DEFAULT '[]',
            UNIQUE(pair_a,pair_b,product,topic_id,as_of,window_days)
        )""")
        columns = {
            r["name"] for r in db.execute("PRAGMA table_info(feedback_followups)")
        }
        if "review_evidence" not in columns:
            db.execute(
                "ALTER TABLE feedback_followups ADD COLUMN review_evidence TEXT NOT NULL DEFAULT '[]'"
            )
        db.execute(
            "CREATE INDEX IF NOT EXISTS feedback_by_date ON research_materials(product,source_type,date)"
        )


def pair_products(db, left, right):
    if left == right:
        raise catalog.InputError("请选择两款不同产品。")
    rows = db.execute(
        "SELECT product,name FROM tools WHERE product IN (?,?)", (left, right)
    )
    names = {r["product"]: r["name"] for r in rows}
    if len(names) != 2:
        raise catalog.InputError("所选产品不存在。")
    return [{"sku": p, "name": names[p]} for p in (left, right)]


def period_start(cutoff, window):
    try:
        return cutoff - timedelta(days=2 * window - 1)
    except OverflowError:
        raise catalog.InputError("截止日过早，无法构成两个完整窗口。") from None


def report_radar(snapshot, window):
    cutoff = snapshot["as_of"]
    start = period_start(date.fromisoformat(cutoff), window).isoformat()
    evidence = [
        r
        for r in snapshot["evidence"]
        if r["source_type"] == "feedback" and start <= r["date"] <= cutoff
    ]
    return feedback.build(evidence, snapshot["products"], cutoff, window_days=window)


def radar_snapshot(db, left, right, cutoff, window):
    products = pair_products(db, left, right)
    if cutoff is None:
        latest = db.execute(
            """SELECT max(date) FROM research_materials
                          WHERE source_type='feedback' AND product IN (?,?)""",
            (left, right),
        ).fetchone()[0]
        cutoff = date.fromisoformat(latest) if latest else date.today()
    start = period_start(cutoff, window)
    rows = db.execute(
        """SELECT * FROM research_materials WHERE source_type='feedback'
                          AND product IN (?,?) AND date BETWEEN ? AND ? ORDER BY date,id""",
        (left, right, start.isoformat(), cutoff.isoformat()),
    )
    evidence = [{**dict(r), "source_label": "用户反馈"} for r in rows]
    radar = feedback.build(evidence, products, cutoff.isoformat(), window_days=window)
    return {**radar, "products": products, "evidence": evidence}


def item(row):
    reviewed = json.loads(row["review_evidence"])
    return {
        "id": row["id"],
        **json.loads(row["snapshot"]),
        "status": row["status"],
        "note": row["note"],
        "updated_at": row["updated_at"],
        "review_evidence": reviewed,
        "review_refs": [r["id"] for r in reviewed],
    }


def review_walkthroughs(db, trigger, refs, previous):
    frozen = {r["id"]: r for r in previous}
    stored = []
    for ref in dict.fromkeys(refs):
        if ref in frozen:
            stored.append(frozen[ref])
            continue
        product, _, tail = ref.partition("@")
        day, _, step = tail.partition("/")
        if product != trigger["product"] or step != trigger["topic"]["capability"]:
            raise catalog.InputError("复核走查需属于本跟进项的产品和能力。")
        row = db.execute(
            """SELECT product,version,date,step,material,observation,handoff FROM walks
                         WHERE product=? AND date=? AND step=?""",
            (product, day, step),
        ).fetchone()
        if row is None:
            raise catalog.InputError("复核走查编号不存在，请重新选择。")
        stored.append(
            {
                "id": ref,
                "product": product,
                "capability": step,
                "date": day,
                "version": row["version"],
                "source_type": "walkthrough",
                "source": row["material"],
                "source_label": "任务走查",
                "excerpt": row["observation"],
                "handoff": row["handoff"],
            }
        )
    return stored


def stamp():
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def make_router(path):
    router = APIRouter(prefix="/api/research")

    @router.get("/materials/feedback-sample")
    def feedback_sample():
        return FileResponse(
            catalog.ROOT / "samples/feedback-observations.csv",
            media_type="text/csv",
            filename="feedback-observations.csv",
        )

    @router.get("/radar")
    def radar(
        left: str, right: str, as_of: date | None = None, window_days: Window = 7
    ):
        with closing(catalog.connect(path)) as db, db:
            db.execute("BEGIN")
            return radar_snapshot(db, left, right, as_of, window_days)

    @router.post("/followups")
    def follow(alert: AlertSelection):
        # Calculate and freeze the signal from the exact database state accepted on creation.
        with closing(catalog.connect(path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            key = (
                *sorted((alert.left, alert.right)),
                alert.product,
                alert.topic_id,
                alert.as_of.isoformat(),
                alert.window_days,
            )
            existing = db.execute(
                """SELECT * FROM feedback_followups
                      WHERE pair_a=? AND pair_b=? AND product=? AND topic_id=? AND as_of=? AND window_days=?""",
                key,
            ).fetchone()
            if existing:
                return item(existing)
            radar = radar_snapshot(
                db, alert.left, alert.right, alert.as_of, alert.window_days
            )
            match = next(
                (
                    a
                    for a in radar["alerts"]
                    if a["product"] == alert.product and a["topic_id"] == alert.topic_id
                ),
                None,
            )
            if match is None:
                raise HTTPException(
                    409, "这组产品、日期和窗口没有对应的反馈异动，请刷新雷达后确认。"
                )
            topic = next(
                t
                for t in radar["topics"]
                if t["product"] == alert.product and t["id"] == alert.topic_id
            )
            refs = set(topic["refs"])
            snapshot = {
                "product": alert.product,
                "topic_id": alert.topic_id,
                "as_of": radar["as_of"],
                "window_days": alert.window_days,
                "label": topic["label"],
                "reason": match["reason"],
                "refs": topic["refs"],
                "periods": radar["periods"],
                "topic": topic,
                "evidence": [r for r in radar["evidence"] if r["id"] in refs],
            }
            cursor = db.execute(
                """INSERT INTO feedback_followups
                              (pair_a,pair_b,product,topic_id,as_of,window_days,snapshot,updated_at)
                              VALUES(?,?,?,?,?,?,?,?)""",
                (*key, json.dumps(snapshot, ensure_ascii=False), stamp()),
            )
            return item(
                db.execute(
                    "SELECT * FROM feedback_followups WHERE id=?", (cursor.lastrowid,)
                ).fetchone()
            )

    @router.get("/followups")
    def list_followups(
        left: str,
        right: str,
        before: int | None = Query(None, gt=0),
        limit: int = Query(30, ge=1, le=100),
    ):
        with closing(catalog.connect(path)) as db:
            pair_products(db, left, right)
            keys = sorted((left, right))
            rows = db.execute(
                """SELECT * FROM feedback_followups WHERE pair_a=? AND pair_b=?
                              AND (? IS NULL OR id<?) ORDER BY id DESC LIMIT ?""",
                (*keys, before, before, limit + 1),
            ).fetchall()
        return {
            "items": [item(r) for r in rows[:limit]],
            "next_before": rows[limit - 1]["id"] if len(rows) > limit else None,
        }

    @router.patch("/followups/{followup_id}")
    def review(followup_id: int, review: Review):
        note = review.note.strip()
        if review.status != "open" and not note:
            raise catalog.InputError("请填写复核结论或归档原因。")
        with closing(catalog.connect(path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            found = db.execute(
                "SELECT * FROM feedback_followups WHERE id=?", (followup_id,)
            ).fetchone()
            if found is None:
                raise HTTPException(404, "跟进项不存在。")
            saved = found["review_evidence"]
            if review.review_refs is not None:
                saved = json.dumps(
                    review_walkthroughs(
                        db, json.loads(found["snapshot"]), review.review_refs, json.loads(saved)
                    ),
                    ensure_ascii=False,
                )
            db.execute(
                """UPDATE feedback_followups SET status=?,note=?,review_evidence=?,updated_at=?
                       WHERE id=?""",
                (review.status, note, saved, stamp(), followup_id),
            )
            return item(
                db.execute(
                    "SELECT * FROM feedback_followups WHERE id=?", (followup_id,)
                ).fetchone()
            )

    return router
