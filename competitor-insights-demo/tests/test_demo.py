import json
import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from insights.app import create_app
from insights.catalog import ROOT

PAIR = {"left": "INK", "right": "RILL", "question": "哪个编辑入口值得改？"}
EXAMPLE = (ROOT / "samples/import-example.csv").read_bytes()
MODEL = {
    "LLM_BASE_URL": "https://model.example.invalid/v1",
    "LLM_MODEL": "test",
    "LLM_API_KEY": "mock-unit-test-only",
}


class WalkthroughTest(unittest.TestCase):
    def client(self, config=None, handler=None):
        directory = self.enterContext(tempfile.TemporaryDirectory())

        def offline(request):
            self.fail("规则模式发起了网络请求")

        app = create_app(
            Path(directory) / "test.db",
            config or {},
            httpx.MockTransport(handler or offline),
        )
        return self.enterContext(TestClient(app, base_url="http://localhost"))

    def load(self, client, data):
        return client.post(
            "/api/import", content=data, headers={"Content-Type": "text/csv"}
        )

    def test_version_and_snapshot(self):
        client = self.client()
        first = client.post("/api/reports", json=PAIR).json()
        self.assertIn("需重新上传", first["markdown"])
        self.assertEqual(
            first["counts"], {"materials": 2, "observations": 2, "citations": 2}
        )
        self.assertEqual(self.load(client, EXAMPLE).json(), {"rows": 3})
        self.assertEqual(self.load(client, EXAMPLE).json(), {"rows": 3})
        second = client.post("/api/reports", json=PAIR).json()
        self.assertIn("均可直接沿用", second["markdown"])
        self.assertEqual(first, client.get("/api/reports/1").json())
        self.assertIn("INK-W1", client.get("/api/reports/1/export").text)
        self.assertEqual(len(client.get("/api/reports").json()["reports"]), 2)

    def test_incomplete_latest_walk(self):
        client = self.client()
        self.load(client, b"\n".join(EXAMPLE.splitlines()[:2]))
        report = client.post("/api/reports", json=PAIR).json()
        self.assertIn("证据不足", report["markdown"])
        self.assertEqual(report["counts"]["observations"], 1)

    def test_import_is_atomic(self):
        client = self.client()
        header, first, second, _ = EXAMPLE.splitlines()
        for bad in (
            first,
            second.replace("墨汀".encode(), b"wrong"),
            second.replace(b"direct", b"invalid"),
        ):
            response = self.load(client, b"\n".join([header, first, bad]))
            self.assertEqual(response.status_code, 422)
            rows = client.get(
                "/api/compare", params=dict(left="INK", right="RILL")
            ).json()["observations"]
            self.assertEqual(len(rows), 6)
        self.assertEqual(self.load(client, b"." * 1048577).status_code, 413)

    def test_origin_and_selection(self):
        client = self.client()
        self.assertEqual(
            client.post(
                "/api/reports", json=PAIR, headers={"origin": "http://other.invalid"}
            ).status_code,
            403,
        )
        self.assertEqual(
            client.post("/api/reports", json={**PAIR, "right": "INK"}).status_code, 422
        )

    def simulated_model(self, final):
        def respond(request):
            payload = json.loads(request.content)
            self.assertEqual(len(payload["tools"]), 3)
            if len(payload["messages"]) == 2:
                calls = [
                    ("read_walkthrough", {"product": "INK"}),
                    ("compare_step", {"step": "edit"}),
                    ("read_history", {"product": "INK", "step": "edit"}),
                    ("compare_step", {"step": "edit"}),
                ]
                message = {
                    "tool_calls": [
                        {
                            "id": str(i),
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                        for i, (name, args) in enumerate(calls)
                    ]
                }
            else:
                self.assertEqual(
                    len([m for m in payload["messages"] if m["role"] == "tool"]), 4
                )
                message = {"content": final}
            return httpx.Response(200, json={"choices": [{"message": message}]})

        return self.client(MODEL, respond)

    def test_model_unique_reads(self):
        client = self.simulated_model(
            "## 走查观察\n需上传 [INK@2026-10-10/edit]，可沿用 [RILL@2026-10-10/edit]\n## 产品建议\n验证图片传递。"
        )
        response = client.post("/api/reports", json=PAIR)
        self.assertEqual(response.status_code, 201, response.text)
        report = response.json()
        self.assertEqual(report["mode"], "model")
        self.assertEqual(
            report["counts"], {"materials": 2, "observations": 4, "citations": 2}
        )

    def test_model_unread_reference(self):
        client = self.simulated_model("[INK@2026-10-10/edit] [RILL@2026-10-10/iterate]")
        self.assertEqual(client.post("/api/reports", json=PAIR).status_code, 502)
        self.assertEqual(client.get("/api/reports").json()["reports"], [])

    def test_model_failure(self):
        client = self.client(MODEL, lambda req: httpx.Response(403))
        self.assertEqual(client.post("/api/reports", json=PAIR).status_code, 502)
        self.assertEqual(client.get("/api/reports").json()["reports"], [])
