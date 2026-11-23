import json
import os
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from tracker.feishu import Bitable, FeishuError


def row(identifier):
    return {"record_id": "record-" + identifier, "fields": {
        "项目编号": identifier, "项目名称": "图书索引", "负责人": [{"text": "开发组"}],
        "阶段": "开发中", "进度": 65, "里程碑": "联调", "目标日期": "2027-03-12",
        "风险": "低", "下一步": "联调", "当前版本": "旧数据",
    }}


class BitableTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.reply = self.normal_reply
        test = self

        class Handler(BaseHTTPRequestHandler):
            def handle_api(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                call = (self.command, urlsplit(self.path), json.loads(body) if body else None,
                        self.headers.get("Authorization"))
                test.calls.append(call)
                status, result = test.reply(*call)
                self.send_response(status)
                self.end_headers()
                try:
                    self.wfile.write(json.dumps(result).encode())
                except (BrokenPipeError, ConnectionResetError):
                    pass

            do_GET = do_POST = do_PUT = handle_api

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.adapter = Bitable("sample", "sample", "app", "table",
                               origin=f"http://127.0.0.1:{server.server_port}", timeout=0.5)

    def normal_reply(self, method, url, data, auth):
        if method == "POST":
            self.assertEqual(url.path, "/open-apis/auth/v3/tenant_access_token/internal")
            self.assertEqual(data, {"app_id": "sample", "app_secret": "sample"})
            return 200, {"code": 0, "tenant_access_token": "local-test"}
        self.assertEqual(auth, "Bearer local-test")
        base = "/open-apis/bitable/v1/apps/app/tables/table/records"
        if method == "PUT":
            self.assertEqual(url.path, base + "/record-B")
            return 200, {"code": 0}
        self.assertEqual(url.path, base)
        second = "page_token" in parse_qs(url.query)
        return 200, {"code": 0, "data": {"has_more": not second, "page_token": "next",
                                         "items": [row("B" if second else "A")]}}

    def test_import_and_write_back_exact_release_columns(self):
        result = self.adapter.list_projects()
        self.assertEqual([p["id"] for p in result], ["A", "B"])
        self.assertEqual(result[0]["owner"], "开发组")
        self.assertEqual(result[0]["progress"], 65)
        self.assertNotIn("version", result[0])
        self.adapter.write_release("B", "1.2.0", "failed", "2027-03-10T10:00:00Z")
        self.assertEqual(self.calls[-1][2], {"fields": {
            "当前版本": "1.2.0", "发布结果": "失败", "发布时间": "2027-03-10T10:00:00Z",
        }})

    def test_duplicate_missing_and_unknown_id_prevent_writes(self):
        for bad in [[row("A"), row("A")], [row("")], [row("X")]]:
            with self.subTest(bad=bad):
                self.reply = lambda m, u, d, a: self.normal_reply(m,u,d,a) if m == "POST" else (
                    200, {"code": 0, "data": {"has_more": False, "items": bad}})
                with self.assertRaises(FeishuError):
                    self.adapter.write_release("B", "1", "success", "2027-03-10")
                self.assertNotIn("PUT", [item[0] for item in self.calls])

    def test_denied_timeout_and_recovery(self):
        for http_status, body in [(403, {"msg": "private-response"}),
                                  (200, {"code": 1254302, "msg": "private-response"})]:
            self.reply = lambda *args: (http_status, body)
            with self.assertRaises(FeishuError) as raised:
                self.adapter.list_projects()
            self.assertNotIn("private-response", str(raised.exception))
        self.reply = lambda *args: (time.sleep(0.15) or 200, {})
        self.adapter.timeout = 0.03
        with self.assertRaisesRegex(FeishuError, "超时"):
            self.adapter.list_projects()
        self.reply = self.normal_reply
        self.adapter.timeout = 0.5
        self.assertEqual(len(self.adapter.list_projects()), 2)

    def test_optional_and_incomplete_settings(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(Bitable.from_env())
            os.environ["FEISHU_TABLE_ID"] = "table"
            with self.assertRaisesRegex(FeishuError, "填全"):
                Bitable.from_env().list_projects()
