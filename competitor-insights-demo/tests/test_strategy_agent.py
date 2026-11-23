"""Exercise the research workspace through its public HTTP APIs."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from insights import analysis, catalog
from insights.app import create_app

PAIR = {"left": "INK", "right": "RILL"}
CASE = {**PAIR, "intent": "opportunity", "question": "哪个断点值得优先验证？"}
ROUTE = "/api/research/reports"
MODEL = {
    "LLM_BASE_URL": "https://model.example.invalid/v1",
    "LLM_MODEL": "demo",
    "LLM_API_KEY": "research-test-only",
}
CITATIONS = ["INK@2026-10-10/edit", "RILL@2026-10-10/edit"]


def calls(*steps):
    return {"tool_calls": [
        {"id": f"step-{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(args)}}
        for i, (n, args) in enumerate(steps)
    ]}


def answer(message):
    return httpx.Response(200, json={"choices": [{"message": message}]})


class ResearchTests(unittest.TestCase):
    def client(self, config=None, handler=None, path=None):
        if path is None:
            path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "test.sqlite3"
        def offline(req):
            self.fail("Unexpected external request")
        app = create_app(path, config or {}, httpx.MockTransport(handler or offline))
        return self.enterContext(TestClient(app, base_url="http://localhost"))

    def test_snapshot_and_replay(self):
        client = self.client()
        snapshot = client.get("/api/research", params=PAIR).json()
        self.assertEqual(len(snapshot["capabilities"]), 7)
        first = client.post(ROUTE, json=CASE)
        self.assertEqual(first.status_code, 201, first.text)
        first = first.json()
        self.assertEqual(first["mode"], "local")
        self.assertTrue(first["trace"])
        self.assertTrue(first["cited_refs"])
        sample = (catalog.ROOT / "samples/import-example.csv").read_bytes()
        self.assertEqual(client.post("/api/import", content=sample, headers={"Content-Type":"text/csv"}).status_code, 200)
        latest = client.post(ROUTE, json={**CASE, "intent":"change"}).json()
        self.assertGreater(latest["as_of"], first["as_of"])
        self.assertNotEqual(latest["markdown"], first["markdown"])
        self.assertEqual(client.get(f"{ROUTE}/{first['id']}").json(), first)
        cutoff = client.get("/api/research", params={**PAIR, "as_of":first["as_of"]}).json()
        self.assertEqual(cutoff, snapshot)
        self.assertEqual(client.get("/api/reports").json(), {"reports":[]})
        page = client.get(ROUTE, params={"limit": 1}).json()
        self.assertEqual(page["reports"][0]["id"], latest["id"])
        following = client.get(ROUTE, params={"limit": 1, "before":page["next_before"]}).json()
        self.assertEqual(following["reports"][0]["id"], first["id"])
        self.assertIsNone(following["next_before"])
        export = client.get(f"{ROUTE}/{first['id']}/export")
        self.assertIn("attachment", export.headers["content-disposition"])
        self.assertIn(first["cited_refs"][0], export.text)

    def test_offline_question_selects_real_capabilities(self):
        client = self.client()
        selected = client.post(ROUTE, json={**CASE, "question":"选区和导出需要怎么验证？"}).json()
        self.assertIn("限定问题能力", [r["step"] for r in selected["trace"]])
        self.assertIn("选区精度", selected["markdown"])
        self.assertIn("交付导出", selected["markdown"])
        self.assertNotIn("### 编辑衔接", selected["markdown"])
        cited = [r for r in selected["evidence"] if r["id"] in selected["cited_refs"]]
        self.assertTrue(cited)
        self.assertLessEqual({r["capability"] for r in cited}, {"mask", "export"})
        before = client.post(ROUTE, json={**CASE, "question":"导出版本前后如何？", "intent":"change",
                                           "as_of":"2020-01-01"}).json()
        self.assertIn("缺少", before["markdown"])
        self.assertEqual(before["cited_refs"], [])
        default = client.post(ROUTE, json=CASE).json()
        self.assertTrue(any("全局" in r["detail"] for r in default["trace"]))

    def test_new_walkthrough_outcomes_are_scoped_and_replayed(self):
        client = self.client()
        old = client.post(ROUTE, json={**CASE, "question":"复核透明背景导出"}).json()
        example = (catalog.ROOT / "samples/verification-example.csv").read_bytes()
        def upload(content):
            return client.post("/api/import", content=content, headers={"Content-Type":"text/csv"})
        self.assertEqual(upload(example).status_code, 200)
        self.assertEqual(upload(example).status_code, 200)
        observations = client.get("/api/compare", params=PAIR).json()["observations"]
        self.assertEqual(len(observations), 7)
        ref = "RILL@2026-10-10/export"
        self.assertEqual(next(r for r in observations if r["reference"] == ref)["handoff"], "fail")
        snapshot = client.get("/api/research", params=PAIR).json()
        axis = next(a for a in snapshot["capabilities"] if a["key"] == "export")
        cell = next(c for c in axis["cells"] if c["product"] == "RILL")
        self.assertEqual(cell["status"], "friction")
        self.assertEqual(cell["walk_ref"], ref)
        self.assertIn("其他格式及规格未测试", cell["note"])
        after = client.post(ROUTE, json={**CASE, "question":"复核透明背景导出"}).json()
        self.assertIn(ref, after["cited_refs"])
        self.assertNotIn(ref, old["cited_refs"])
        self.assertEqual(client.get(f"{ROUTE}/{old['id']}").json(), old)
        self.assertTrue(analysis.REFERENCE.fullmatch(f"[{ref}]"))

    def test_targeted_version_report_separates_same_version_observation_changes(self):
        client = self.client()
        first = (catalog.ROOT / "samples/verification-example.csv").read_text()
        second = first.replace("2026-10-10", "2026-10-11").replace("RILL-PNG-01", "RILL-PNG-02")
        before = second.splitlines()[1].split(",")
        before[-2:] = ["本次使用另一个测试画布，输出保留透明背景。", "pass"]
        corrected = second.splitlines()[0] + "\n" + ",".join(before)
        for body in (first, corrected):
            self.assertEqual(client.post("/api/import",content=body.encode(),headers={"Content-Type":"text/csv"}).status_code, 200)
        report = client.post(ROUTE, json={**CASE, "intent":"change", "question":"导出能力与前次比较如何？"}).json()
        change = next(c for c in report["changes"] if c["capability"] == "export")
        self.assertEqual(change["transition"], "same_version_conflict")
        self.assertIn("同版本 v2 于", report["markdown"])
        self.assertIn("测试输入、配置或运行条件需复核", report["markdown"])
        self.assertNotIn("新版在本次任务上已通过", report["markdown"])
        # Independent, consistent retests have no earlier contrary observation in this version.
        consistent = self.client()
        for body in (first, second):
            self.assertEqual(consistent.post("/api/import", content=body.encode(),
                                             headers={"Content-Type":"text/csv"}).status_code, 200)
        repeat = consistent.post(ROUTE, json={**CASE, "intent":"change", "question":"导出能力与前次比较如何？"}).json()
        self.assertIn("两次实测结果相同", repeat["markdown"])
        self.assertNotIn("从 v2 到 v2", repeat["markdown"])

    def test_seven_step_compatibility(self):
        original = (catalog.ROOT / "samples/verification-example.csv").read_text()
        for step in ("mask", "reference", "history", "export"):
            for verdict in ("pass", "fail", "unknown"):
                content = original.replace(",export,", f",{step},").replace(",fail\n", f",{verdict}\n")
                self.assertEqual(catalog.parse_csv(content.encode())[0]["handoff"], verdict)
        for step, verdict in (("edit","fail"), ("generate","unknown"), ("iterate","pass"),
                              ("mask","direct"), ("export","upload"), ("reference","start")):
            content = original.replace(",export,", f",{step},").replace(",fail\n", f",{verdict}\n")
            with self.assertRaises(catalog.InputError):
                catalog.parse_csv(content.encode())

    def test_upgrade_preserves_existing_reports(self):
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "old.db"
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE reports (id INTEGER PRIMARY KEY, snapshot TEXT NOT NULL)")
            db.execute("INSERT INTO reports(snapshot) values (?)", (json.dumps({"title":"旧报告"}),))
        client = self.client(path=path)
        self.assertEqual(client.get("/api/reports/1").json()["title"], "旧报告")
        self.assertEqual(client.get(ROUTE).json()["reports"], [])
        self.assertEqual(client.post(ROUTE, json=CASE).status_code, 201)
        self.assertEqual(client.get("/api/reports/1").json()["title"], "旧报告")

    def test_model_follows_tools_and_only_unique_opens_count(self):
        rounds = []
        def respond(req):
            self.assertEqual(req.headers["authorization"], "Bearer " + MODEL["LLM_API_KEY"])
            self.assertEqual(req.url.path, "/v1/chat/completions")
            p = json.loads(req.content)
            self.assertEqual({t["function"]["name"] for t in p["tools"]},
                             {"compare_capabilities","list_changes","find_evidence","open_evidence","read_feedback_trends"})
            rounds.append(p)
            if len(rounds) == 1:
                return answer(calls(("compare_capabilities", {}), ("find_evidence", {"product":"INK"}), ("list_changes", {})))
            if len(rounds) == 2:
                directory = json.loads(p["messages"][-2]["content"])["items"]
                self.assertFalse(any("excerpt" in entry for entry in directory))
                return answer(calls(("open_evidence", {"ids":[*CITATIONS, CITATIONS[0]]})))
            opened = json.loads(p["messages"][-1]["content"])
            self.assertEqual([r["id"] for r in opened], CITATIONS)
            return answer({"content": "下一步验证两种入口。 " + " ".join(f"[{i}]" for i in CITATIONS)})
        client = self.client(MODEL, respond)
        resp = client.post(ROUTE, json=CASE)
        self.assertEqual(resp.status_code, 201, resp.text)
        report = resp.json()
        self.assertEqual(len(rounds), 3)
        self.assertEqual(report["counts"]["observations"], 2)
        self.assertEqual(report["counts"]["citations"], 2)
        self.assertEqual(report["model_trace"][-1]["refs"], CITATIONS)
        self.assertEqual(report["mode"], "model")
        self.assertNotIn(MODEL["LLM_API_KEY"], resp.text)

    def test_unopened_fabricated_and_missing_side_rejected(self):
        for invalid in ("[RILL@2026-10-10/iterate]", "[SIG-FAKE-99]", "[随意编号]", ""):
            with self.subTest(invalid=invalid):
                def respond(req):
                    msgs = json.loads(req.content)["messages"]
                    if len(msgs) == 2:
                        return answer(calls(("open_evidence", {"ids": CITATIONS})))
                    return answer({"content": "[INK@2026-10-10/edit] " + invalid})
                client = self.client(MODEL, respond)
                self.assertEqual(client.post(ROUTE, json=CASE).status_code, 502)
                self.assertEqual(client.get(ROUTE).json()["reports"], [])

    def test_model_errors_and_invalid_requests(self):
        def timed_out(req):
            raise httpx.ReadTimeout("upstream private diagnostic")
        for handler, reason in ((lambda req: httpx.Response(403, text="upstream private diagnostic"), "权限"),
                                (timed_out, "超时")):
            client = self.client(MODEL, handler)
            response = client.post(ROUTE, json=CASE)
            self.assertEqual(response.status_code, 502)
            self.assertIn(reason, response.json()["detail"])
            self.assertNotIn("upstream private diagnostic", response.text)
            self.assertFalse(client.get(ROUTE).json()["reports"])
        partial = self.client({"LLM_MODEL":"partial"})
        self.assertEqual(partial.post(ROUTE, json=CASE).status_code, 502)
        client = self.client()
        for updates in ({"intent":"invent"}, {"as_of":"bad"}, {"right":"INK"}, {"question":" "}):
            self.assertEqual(client.post(ROUTE, json={**CASE, **updates}).status_code, 422)
