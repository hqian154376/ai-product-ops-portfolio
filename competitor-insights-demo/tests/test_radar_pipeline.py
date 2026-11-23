"""Read a signal, preserve its trigger, and follow the same evidence into a report."""

import json
import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from insights.app import create_app

PAIR = {"left": "INK", "right": "RILL"}
CUT = {**PAIR, "as_of": "2026-10-10", "window_days": 7}
ALPHA = "RILL:export:alpha"
REPORTS = "/api/research/reports"
RADAR = "/api/research/radar"
FOLLOW = "/api/research/followups"
MATERIALS = "/api/research/materials"
MODEL = {
    "LLM_BASE_URL": "https://model.example.invalid/v1",
    "LLM_MODEL": "mock",
    "LLM_API_KEY": "radar-mock-only",
}


class RadarFlowTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "study.db"

    def client(self, handler=None):
        def offline(_):
            self.fail("local reports must not contact an external service")

        app = create_app(
            self.path, MODEL if handler else {}, httpx.MockTransport(handler or offline)
        )
        return self.enterContext(TestClient(app, base_url="http://localhost"))

    def seed(self, c):
        source = c.get(MATERIALS + "/feedback-sample")
        self.assertEqual(source.status_code, 200)
        result = c.post(
            MATERIALS + "/import",
            content=source.content,
            headers={"Content-Type": "text/csv"},
        )
        self.assertEqual(result.status_code, 200, result.text)
        return source.content

    def test_cutoff_counts_and_reproducible_followup(self):
        c = self.client()
        self.seed(c)
        radar = c.get(RADAR, params=CUT).json()
        topic = next(t for t in radar["topics"] if t["id"] == ALPHA)
        self.assertEqual(
            (
                topic["current_sources"],
                topic["current_total"],
                topic["previous_sources"],
                topic["previous_total"],
            ),
            (4, 7, 1, 3),
        )
        self.assertTrue(topic["needs_review"])
        self.assertTrue(
            all(
                "2026-09-27" <= r["date"] <= CUT["as_of"]
                and r["source_type"] == "feedback"
                for r in radar["evidence"]
            )
        )
        self.assertEqual(c.get(RADAR, params=PAIR).json()["as_of"], "2026-10-17")
        self.assertEqual(
            c.get(RADAR, params={**PAIR, "as_of": "2020-01-01"}).json()["evidence"], []
        )
        for w in (0, 6, 31):
            self.assertEqual(
                c.get(RADAR, params={**CUT, "window_days": w}).status_code, 422
            )
        req = {**CUT, "product": "RILL", "topic_id": ALPHA}
        self.assertEqual(
            c.post(FOLLOW, json={**req, "topic_id": "forged"}).status_code, 409
        )
        item = c.post(FOLLOW, json=req).json()
        self.assertEqual(item["topic"], topic)
        self.assertEqual({r["id"] for r in item["evidence"]}, set(item["refs"]))
        self.assertTrue(all(r["product"] == "RILL" for r in item["evidence"]))
        row = FOLLOW + "/" + str(item["id"])
        self.assertEqual(
            c.patch(row, json={"status": "reviewed", "note": " "}).status_code, 422
        )
        archived = c.patch(
            row, json={"status": "archived", "note": "记录导出设置后复测。"}
        ).json()
        self.assertEqual(archived["status"], "archived")
        # A new, late excerpt changes today's replay; the accepted signal stays as it was.
        csv = (
            "id,product,version,date,capability,source_type,origin,source,position,excerpt\n"
            "SIG-LATE-F1,RILL,v2,2026-10-10,export,feedback,EXTRA-INT,新增访谈,support,透明背景导出后正常。\n"
        )
        self.assertEqual(
            c.post(
                MATERIALS + "/import",
                content=csv.encode(),
                headers={"Content-Type": "text/csv"},
            ).status_code,
            200,
        )
        restarted = self.client()
        self.assertEqual(restarted.post(FOLLOW, json=req).json(), archived)
        stored = restarted.get(FOLLOW, params={"left": "RILL", "right": "INK"}).json()[
            "items"
        ]
        self.assertEqual(stored, [archived])
        self.assertEqual(
            restarted.patch(
                row, json={"status": "open", "note": "复核另一种配置"}
            ).json()["status"],
            "open",
        )
        self.assertEqual(
            next(
                t
                for t in restarted.get(RADAR, params=CUT).json()["topics"]
                if t["id"] == ALPHA
            )["current_sources"],
            5,
        )

    def test_review_evidence_freezes_newer_walkthroughs_and_rejects_unrelated_records(
        self,
    ):
        c = self.client()
        self.seed(c)
        trigger = c.post(
            FOLLOW, json={**CUT, "product": "RILL", "topic_id": ALPHA}
        ).json()
        row = FOLLOW + "/" + str(trigger["id"])
        source = c.get(MATERIALS + "/verification-sample").content
        source = source.replace(b"2026-10-10", b"2026-10-20")
        response = c.post(
            "/api/import", content=source, headers={"Content-Type": "text/csv"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        ref = "RILL@2026-10-20/export"
        review = {
            "status": "reviewed",
            "note": "本次 PNG 文件已复测。",
            "review_refs": [ref],
        }
        done = c.patch(row, json=review).json()
        self.assertEqual(done["review_refs"], [ref])
        self.assertEqual(done["review_evidence"][0]["handoff"], "fail")
        self.assertEqual(done["evidence"], trigger["evidence"])
        for bad in (
            "INK@2026-10-10/edit",
            "RILL@2026-10-10/iterate",
            "RILL@2099-01-01/export",
        ):
            self.assertEqual(
                c.patch(
                    row, json={**review, "status": "archived", "review_refs": [bad]}
                ).status_code,
                422,
            )
        self.assertEqual(c.get(FOLLOW, params=PAIR).json()["items"], [done])
        self.assertEqual(
            c.post(
                "/api/import",
                content=source.replace(b",fail", b",pass"),
                headers={"Content-Type": "text/csv"},
            ).status_code,
            200,
        )
        # Reopening notes without replacing the evidence preserves the original reviewed outcome.
        later = c.patch(row, json={"status": "archived", "note": "复核已归档。"}).json()
        self.assertEqual(later["review_evidence"], done["review_evidence"])
        self.assertEqual(
            self.client().get(FOLLOW, params=PAIR).json()["items"][0], later
        )

        ref_b = "RILL@2026-10-21/export"
        second = source.replace(b"2026-10-20", b"2026-10-21").replace(b",fail", b",pass")
        self.assertEqual(c.post("/api/import", content=second, headers={"Content-Type": "text/csv"}).status_code, 200)
        appended = c.patch(row, json={**review, "review_refs": [ref, ref_b]}).json()
        self.assertEqual(appended["review_evidence"][0], done["review_evidence"][0])
        self.assertEqual(appended["review_evidence"][1]["handoff"], "pass")
        removed = c.patch(row, json={**review, "review_refs": [ref_b]}).json()
        self.assertEqual(removed["review_refs"], [ref_b])

    def test_local_report_uses_selected_window_and_survives_restart(self):
        c = self.client()
        self.seed(c)
        case = {**CUT, "question": "溪屿 Canvas 的透明背景导出反馈趋势如何？"}
        response = c.post(REPORTS, json=case)
        self.assertEqual(response.status_code, 201, response.text)
        first = response.json()
        self.assertIn("4/7", first["markdown"])
        self.assertIn("1/3", first["markdown"])
        self.assertTrue(first["cited_refs"])
        self.assertTrue(
            set(first["cited_refs"])
            <= {r["id"] for r in first["evidence"] if r["source_type"] == "feedback"}
        )
        different = c.post(REPORTS, json={**case, "window_days": 14}).json()
        self.assertNotEqual(
            different["feedback_radar"]["periods"], first["feedback_radar"]["periods"]
        )
        empty = c.post(REPORTS, json={**case, "as_of": "2019-01-01"}).json()
        self.assertEqual(empty["cited_refs"], [])
        self.assertIn("证据不足", empty["markdown"])
        self.assertEqual(self.client().get(f"{REPORTS}/{first['id']}").json(), first)

    def test_model_statistics_and_opened_originals(self):
        seen = []

        def handler(req):
            payload = json.loads(req.content)
            seen.append(payload)

            def tool(n, args):
                return {
                    "tool_calls": [
                        {
                            "id": str(len(seen)),
                            "type": "function",
                            "function": {"name": n, "arguments": json.dumps(args)},
                        }
                    ]
                }

            if len(seen) == 1:
                msg = tool(
                    "read_feedback_trends", {"product": "RILL", "topic_id": ALPHA}
                )
            elif len(seen) == 2:
                stats = json.loads(payload["messages"][-1]["content"])
                self.assertEqual(
                    (
                        stats["topics"][0]["current_sources"],
                        stats["topics"][0]["current_total"],
                    ),
                    (4, 7),
                )
                self.assertNotIn("excerpt", payload["messages"][-1]["content"])
                self.assertTrue(stats["channel_mix"])
                self.assertEqual({r["product"] for r in stats["channel_mix"]}, {"RILL"})
                self.assertTrue(stats["topics"][0]["channel_mix"])
                msg = tool("open_evidence", {"ids": ["SIG-RADAR-01", "SIG-RADAR-04"]})
            else:
                raw = json.loads(payload["messages"][-1]["content"])
                self.assertTrue(all(r["excerpt"] for r in raw))
                msg = {
                    "content": "上期 1/3，本期 4/7。两条访谈分别称透明底可用和出现白底，应核查原文件。[SIG-RADAR-01] [SIG-RADAR-04]"
                }
            return httpx.Response(200, json={"choices": [{"message": msg}]})

        c = self.client(handler)
        self.seed(c)
        reply = c.post(
            REPORTS, json={**CUT, "question": "溪屿 Canvas 的透明背景反馈趋势如何？"}
        )
        self.assertEqual(reply.status_code, 201, reply.text)
        self.assertEqual(
            [t["step"] for t in reply.json()["model_trace"]],
            ["read_feedback_trends", "open_evidence"],
        )
        self.assertEqual(reply.json()["counts"]["citations"], 2)

    def test_statistics_tool_is_not_citation_authority(self):
        def handler(req):
            msgs = json.loads(req.content)["messages"]
            if len(msgs) == 2:
                answer = {
                    "tool_calls": [
                        {
                            "id": "q",
                            "type": "function",
                            "function": {
                                "name": "read_feedback_trends",
                                "arguments": "{}",
                            },
                        }
                    ]
                }
            else:
                answer = {"content": "有异动。[SIG-RADAR-04]"}
            return httpx.Response(200, json={"choices": [{"message": answer}]})

        c = self.client(handler)
        self.seed(c)
        self.assertEqual(
            c.post(
                REPORTS, json={**CUT, "question": "溪屿 Canvas 的反馈趋势？"}
            ).status_code,
            502,
        )
        self.assertFalse(c.get(REPORTS).json()["reports"])
