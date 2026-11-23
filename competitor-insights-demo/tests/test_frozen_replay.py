"""The publicly shipped replay must remain readable with an empty local report library."""
import csv
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from insights import catalog
from insights.app import create_app


class ReplayContent(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tools = []
        self.citations = {}
        self.quotes = {}
        self.section = ""
        self.source = ""
        self.in_quote = False

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == "section":
            self.section = fields.get("id", "")
        if tag == "li" and "data-tool" in fields:
            self.tools.append(fields["data-tool"])
        if tag == "a" and fields.get("href", "").startswith("#source-"):
            refs = self.citations.setdefault(self.section, set())
            refs.add(fields["href"].removeprefix("#source-"))
        if tag == "article":
            self.source = fields["id"].removeprefix("source-")
        if tag == "blockquote":
            self.in_quote = True

    def handle_endtag(self, tag):
        if tag == "blockquote":
            self.in_quote = False

    def handle_data(self, data):
        if self.in_quote:
            self.quotes[self.source] = self.quotes.get(self.source, "") + data


class FrozenReplayTests(unittest.TestCase):
    def test_model_and_program_citations_open_the_original_sample(self):
        sources = {}
        for name in ("catalog", "import-example", "research-signals", "feedback-observations"):
            with (catalog.ROOT / f"samples/{name}.csv").open(encoding="utf-8") as sample:
                for row in csv.DictReader(sample):
                    if "id" in row:
                        sources[row["id"]] = row["excerpt"]
                    else:
                        sources[f"{row['product']}@{row['date']}/{row['step']}"] = row["observation"]

        replay = ReplayContent()
        replay.feed((catalog.ROOT / "static/model-replay.html").read_text(encoding="utf-8"))
        self.assertEqual(replay.tools, ["read_feedback_trends", "read_feedback_trends",
                                        "list_changes", "compare_capabilities", "open_evidence"])
        self.assertEqual(len(replay.quotes), 12)
        self.assertEqual(len(replay.citations["interpretation"]), 9)
        self.assertEqual(len(replay.citations["accounting"]), 3)
        for refs in replay.citations.values():
            self.assertLessEqual(refs, replay.quotes.keys())
        self.assertEqual(replay.quotes, {ref: sources[ref] for ref in replay.quotes})

    def test_read_only_even_with_no_config_or_saved_report(self):
        route = "/static/model-replay.html"
        def reject_network(_):
            self.fail("The historical replay attempted an external call")
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(Path(tmp) / "empty.sqlite", {}, httpx.MockTransport(reject_network))
            with TestClient(app, base_url="http://localhost") as client:
                self.assertIn(f'href="{route}"', client.get("/").text)
                self.assertEqual(client.get(route).status_code, 200)
                self.assertEqual(client.post(route, json={}).status_code, 405)
                self.assertEqual(client.get("/api/research/reports/1").status_code, 404)
                self.assertEqual(client.get("/api/research/reports").json()["reports"], [])
