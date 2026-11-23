from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient
from tracker.feishu import FeishuError
from tracker.web import make_app


class FakeTable:
    def __init__(self):
        self.writes = []
        self.fail = False
        self.rows = []

    def write_release(self, *args):
        if self.fail:
            raise FeishuError("模拟表格不可用。")
        self.writes.append(args)

    def list_projects(self):
        return self.rows


class TrackerTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.table = FakeTable()
        self.client = TestClient(
            make_app(
                directory=directory.name,
                adapter_factory=lambda: self.table,
                release_token="unit-test-only",
            )
        )
        self.url = "/api/projects/P-101"

    def submit(
        self,
        ident="a",
        at="2090-02-01T08:00:00Z",
        version="3.0",
        result="success",
        env="production",
    ):
        return self.client.post(
            "/api/releases",
            headers={"Authorization": "Bearer unit-test-only"},
            json={
                "project_id": "P-101",
                "pipeline_id": ident,
                "released_at": at,
                "version": version,
                "result": result,
                "environment": env,
            },
        )

    def test_receipts(self):
        self.assertTrue(self.submit().json()["inserted"])
        self.assertFalse(self.submit().json()["inserted"])
        self.assertEqual(self.submit(version="4.0").status_code, 409)
        self.submit("b", "2090-02-02T08:00:00Z", "4.0", "failed")
        self.submit("older", "2089-01-01T00:00:00Z", "1.1")
        self.submit("preview", "2090-02-03T08:00:00Z", "5.0", env="staging")
        detail = self.client.get(self.url).json()
        self.assertEqual(detail["environments"]["production"]["version"], "3.0")
        self.assertEqual(detail["environments"]["production"]["result"], "failed")
        self.assertEqual(detail["environments"]["staging"]["version"], "5.0")
        self.assertEqual(len(detail["releases"]), 5)
        self.assertEqual(
            [w[1:3] for w in self.table.writes], [("3.0", "success"), ("3.0", "failed")]
        )
        self.assertEqual(detail["progress"], 65)

    def test_pending_then_retry(self):
        self.table.fail = True
        self.assertEqual(self.submit().json()["sync"], "pending")
        self.submit("new", "2090-02-03T00:00:00Z", "3.5")
        self.assertEqual(len(self.client.get("/api/sync").json()["pending"]), 1)
        self.table.fail = False
        self.assertEqual(
            self.client.post("/api/sync/retry").json()["results"], {"P-101": "complete"}
        )
        self.assertEqual(self.table.writes[0][1], "3.5")
        self.assertFalse(self.client.get("/api/sync").json()["pending"])

    def test_edit_and_import(self):
        original = self.client.get(self.url).json()
        fields = {
            k: original[k]
            for k in (
                "name",
                "owner",
                "stage",
                "progress",
                "milestone",
                "due",
                "risk",
                "next_step",
            )
        }
        fields["progress"] = 77
        self.assertEqual(self.client.patch(self.url, json=fields).status_code, 200)
        self.table.rows = [dict(fields, id="P-101", owner="试用小组")]
        self.assertEqual(self.client.post("/api/sync/import").json()["count"], 1)
        d = self.client.get(self.url).json()
        self.assertEqual(
            (d["owner"], d["progress"], d["environments"]["production"]["version"]),
            ("试用小组", 77, "1.2.0"),
        )
        self.assertEqual(d["changes"][0]["changes"]["owner"]["after"], "试用小组")

    def test_release_credentials_and_origin(self):
        event = {
            "project_id": "P-101",
            "environment": "production",
            "pipeline_id": "x",
            "released_at": "2090-01-01T00:00:00Z",
            "version": "v1",
            "result": "success",
        }
        self.assertEqual(self.client.post("/api/releases", json=event).status_code, 401)
        self.assertEqual(
            self.client.post(
                "/api/sync/import", headers={"Origin": "https://unrelated.example"}
            ).status_code,
            403,
        )
