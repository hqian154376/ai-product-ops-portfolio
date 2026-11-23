import csv
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import ImportProject, ProjectFields, Receipt

ROOT = Path(__file__).resolve().parent.parent
FIELDS = tuple(ProjectFields.model_fields)
LABELS = dict(
    zip(
        FIELDS,
        ("项目名称", "负责人", "阶段", "进度", "里程碑", "目标日期", "风险", "下一步"),
    )
)
ORDER = "released_at DESC, pipeline_id DESC"


class Conflict(Exception):
    pass


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "tracker.sqlite3"
        with self.connection() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS project (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, owner TEXT NOT NULL, stage TEXT NOT NULL,
                    progress INTEGER NOT NULL, milestone TEXT NOT NULL, due TEXT NOT NULL,
                    risk TEXT NOT NULL, next_step TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS change_log (
                    seq INTEGER PRIMARY KEY, project_id TEXT NOT NULL REFERENCES project(id), at TEXT NOT NULL,
                    source TEXT NOT NULL, changes TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS receipt (
                    project_id TEXT NOT NULL REFERENCES project(id), environment TEXT NOT NULL, pipeline_id TEXT NOT NULL,
                    version TEXT NOT NULL, result TEXT NOT NULL, released_at TEXT NOT NULL,
                    PRIMARY KEY(project_id, environment, pipeline_id));
                CREATE TABLE IF NOT EXISTS outbox (
                    project_id TEXT PRIMARY KEY REFERENCES project(id), revision INTEGER NOT NULL, error TEXT NOT NULL);
            """)
        with self.connection(write=True) as db:
            if not db.execute("SELECT 1 FROM project LIMIT 1").fetchone():
                self.seed(db)

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=8)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def seed(self, db):
        versions = {"P-101": "1.2.0", "P-102": "0.9.0", "P-105": "2.1.0"}
        anchor = datetime.now(timezone.utc) - timedelta(days=7)
        with (ROOT / "examples/projects.csv").open(
            encoding="utf-8-sig", newline=""
        ) as file:
            for n, row in enumerate(csv.DictReader(file)):
                item = ImportProject(
                    id=row["项目编号"],
                    **{key: row[label] for key, label in LABELS.items()},
                )
                self.upsert(
                    db,
                    item.id,
                    item.model_dump(mode="json", exclude={"id"}),
                    "样例建档",
                )
                if item.id in versions:
                    stamp = (anchor + timedelta(hours=n)).isoformat(
                        timespec="microseconds"
                    )
                    db.execute(
                        "INSERT INTO receipt VALUES (?, 'production', ?, ?, 'success', ?)",
                        (item.id, "sample-" + str(n + 1), versions[item.id], stamp),
                    )

    @staticmethod
    def upsert(db, project_id, fields, source):
        old = db.execute("SELECT * FROM project WHERE id=?", (project_id,)).fetchone()
        changes = {
            key: {"before": old[key] if old else None, "after": fields[key]}
            for key in FIELDS
            if not old or old[key] != fields[key]
        }
        if not changes:
            return
        if old:
            assignments = ", ".join(f"{key}=?" for key in FIELDS)
            db.execute(
                f"UPDATE project SET {assignments} WHERE id=?",
                (*[fields[k] for k in FIELDS], project_id),
            )
        else:
            db.execute(
                "INSERT INTO project VALUES (?,?,?,?,?,?,?,?,?)",
                (project_id, *(fields[k] for k in FIELDS)),
            )
        db.execute(
            "INSERT INTO change_log (project_id,at,source,changes) VALUES (?,?,?,?)",
            (
                project_id,
                datetime.now(timezone.utc).isoformat(),
                source,
                json.dumps(changes, ensure_ascii=False),
            ),
        )

    def import_rows(self, rows):
        validated = [ImportProject.model_validate(row) for row in rows]
        if len({item.id for item in validated}) != len(validated):
            raise Conflict("同一张表中项目编号重复，请修正后再导入。")
        with self.connection(write=True) as db:
            for item in validated:
                self.upsert(
                    db,
                    item.id,
                    item.model_dump(mode="json", exclude={"id"}),
                    "飞书导入",
                )
        return len(validated)

    def edit(self, project_id, fields):
        with self.connection(write=True) as db:
            self.require_project(db, project_id)
            self.upsert(db, project_id, fields.model_dump(mode="json"), "本地编辑")

    @staticmethod
    def require_project(db, project_id):
        row = db.execute("SELECT * FROM project WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise KeyError(project_id)
        return dict(row)

    @staticmethod
    def projection(db, project_id, environment):
        query = "SELECT * FROM receipt WHERE project_id=? AND environment=?"
        args = (project_id, environment)
        current = db.execute(
            f"{query} AND result='success' ORDER BY {ORDER} LIMIT 1", args
        ).fetchone()
        last = db.execute(f"{query} ORDER BY {ORDER} LIMIT 1", args).fetchone()
        return {
            "version": current["version"] if current else "",
            "result": last["result"] if last else "",
            "released_at": last["released_at"] if last else "",
        }

    def record(self, event: Receipt):
        at = event.released_at.isoformat(timespec="microseconds")
        identity = (event.project_id, event.environment, event.pipeline_id)
        with self.connection(write=True) as db:
            self.require_project(db, event.project_id)
            old = db.execute(
                "SELECT version,result,released_at FROM receipt WHERE project_id=? AND environment=? AND pipeline_id=?",
                identity,
            ).fetchone()
            if old:
                if tuple(old) != (event.version, event.result, at):
                    raise Conflict(
                        "流水线标识已处理，但回执内容不同。新执行请使用新的流水线标识。"
                    )
                return False
            before = self.projection(db, event.project_id, event.environment)
            db.execute(
                "INSERT INTO receipt VALUES (?,?,?,?,?,?)",
                (*identity, event.version, event.result, at),
            )
            if (
                event.environment == "production"
                and self.projection(db, event.project_id, "production") != before
            ):
                # 同一项目只排队最新投影，即使失败重试也不会覆盖更新后的发布结果。
                db.execute(
                    """INSERT INTO outbox VALUES (?,1,'') ON CONFLICT(project_id)
                              DO UPDATE SET revision=revision+1, error=''""",
                    (event.project_id,),
                )
        return True

    def projects(self):
        with self.connection() as db:
            db.execute("BEGIN")
            return [
                {**dict(p), "production": self.projection(db, p["id"], "production")}
                for p in db.execute("SELECT * FROM project ORDER BY id")
            ]

    def detail(self, project_id):
        with self.connection() as db:
            db.execute("BEGIN")
            p = self.require_project(db, project_id)
            environments = {
                env: self.projection(db, project_id, env)
                for env in ("production", "staging")
            }
            receipts = [
                dict(r)
                for r in db.execute(
                    f"SELECT * FROM receipt WHERE project_id=? ORDER BY {ORDER}, environment LIMIT 100",
                    (project_id,),
                )
            ]
            changes = [
                {**dict(r), "changes": json.loads(r["changes"])}
                for r in db.execute(
                    "SELECT * FROM change_log WHERE project_id=? ORDER BY seq DESC LIMIT 100",
                    (project_id,),
                )
            ]
            return {
                **p,
                "environments": environments,
                "releases": receipts,
                "changes": changes,
            }
