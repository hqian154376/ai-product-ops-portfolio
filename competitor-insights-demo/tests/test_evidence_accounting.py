"""Attribution and arithmetic of the fixed block shown above the model narrative."""

import json
import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from insights.app import create_app

SELECTION = {"left": "INK", "right": "RILL", "as_of": "2026-10-10", "window_days": 7}


class EvidenceAccountingTests(unittest.TestCase):
    def client(self, handler=None):
        db = Path(self.enterContext(tempfile.TemporaryDirectory())) / "study.sqlite3"
        config = (
            {
                "LLM_MODEL": "test",
                "LLM_BASE_URL": "https://model.example.invalid",
                "LLM_API_KEY": "mock-only",
            }
            if handler
            else {}
        )
        app = create_app(db, config, httpx.MockTransport(handler) if handler else None)
        client = self.enterContext(TestClient(app, base_url="http://localhost"))
        sample = client.get("/api/research/materials/feedback-sample")
        reply = client.post(
            "/api/research/materials/import",
            content=sample.content,
            headers={"Content-Type": "text/csv"},
        )
        self.assertEqual(reply.status_code, 200, reply.text)
        return client

    def test_program_polarity_and_model_citations_are_attributed_independently(self):
        def handler(req):
            messages = json.loads(req.content)["messages"]

            def tool(name, args):
                return {
                    "tool_calls": [
                        {
                            "id": str(len(messages)),
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                    ]
                }

            if len(messages) == 2:
                msg = tool("read_feedback_trends", {"topic_id": "RILL:export:alpha"})
            elif len(messages) == 4:
                msg = tool("open_evidence", {"ids": ["SIG-RADAR-04"]})
            else:
                msg = {
                    "content": "请取回原文件核验透明通道，控制导出设置后复测。[SIG-RADAR-04]"
                }
            return httpx.Response(200, json={"choices": [{"message": msg}]})

        c = self.client(handler)
        reply = c.post(
            "/api/research/reports",
            json={
                **SELECTION,
                "question": "溪屿 Canvas 的透明背景导出反馈趋势和下一步？",
            },
        )
        self.assertEqual(reply.status_code, 201, reply.text)
        saved = reply.json()
        numbers, interpretation = saved["markdown"].split("## 模型解读")
        self.assertIn("## 程序核算", numbers)
        self.assertIn("提及 4/7", numbers)
        self.assertIn("提及 1/3", numbers)
        self.assertIn("负向的来源为 3/7", numbers)
        self.assertIn("主题渠道", numbers)
        by_id = {r["id"]: r for r in saved["evidence"]}
        self.assertTrue(saved["program_refs"])
        for evidence in saved["program_refs"]:
            polarity = {"support": "正向", "friction": "负向"}[
                by_id[evidence]["position"]
            ]
            self.assertIn(
                f"登记{polarity}原文（{by_id[evidence]['date']}）：[{evidence}]",
                numbers,
            )
        self.assertEqual(saved["cited_refs"], ["SIG-RADAR-04"])
        self.assertEqual(saved["counts"]["citations"], 1)
        self.assertEqual(saved["counts"]["observations"], 1)
        self.assertTrue(set(saved["program_refs"]) - set(saved["cited_refs"]))
        self.assertIn("SIG-RADAR-04", interpretation)
        export = c.get(f"/api/research/reports/{saved['id']}/export").text
        self.assertIn(saved["markdown"], export)
        self.assertIn("[SIG-RADAR-04]（模型解读）", export)
        for ref in set(saved["program_refs"]) - set(saved["cited_refs"]):
            self.assertIn(f"[{ref}]（程序核算）", export)

    def test_empty_denominator_and_normal_capability_questions(self):
        c = self.client()
        empty = c.post(
            "/api/research/reports",
            json={**SELECTION, "as_of": "2020-01-01", "question": "两款的反馈趋势？"},
        ).json()
        self.assertIn("无样本", empty["markdown"])
        self.assertNotIn("/0", empty["markdown"])
        self.assertNotIn("0%", empty["markdown"])
        self.assertFalse(empty["cited_refs"])
        self.assertFalse(empty["program_refs"])
        normal = c.post(
            "/api/research/reports",
            json={**SELECTION, "question": "哪个编辑入口要重传？"},
        ).json()
        self.assertNotIn("程序核算", normal["markdown"])
        self.assertFalse(normal["program_refs"])
