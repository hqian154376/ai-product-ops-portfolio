import json
import tempfile
import unittest
from pathlib import Path

from insights import catalog, research


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "data.sqlite3"
        catalog.initialize(self.db)

    def build(self, **kwargs):
        return research.build(*catalog.selected(self.db, "INK", "RILL"), **kwargs)

    def import_update(self):
        catalog.import_csv(
            self.db, (catalog.ROOT / "samples/import-example.csv").read_bytes()
        )

    @staticmethod
    def cell(report, capability, product):
        axis = next(r for r in report["capabilities"] if r["key"] == capability)
        return next(c for c in axis["cells"] if c["product"] == product)

    def test_sources_ranked_independent_feedback_and_verified_peer(self):
        report = self.build()
        self.assertEqual(len(report["capabilities"]), 7)
        cell = self.cell(report, "edit", "INK")
        self.assertEqual(cell["status"], "friction")
        self.assertTrue(cell["disagreement"])
        self.assertEqual(
            cell["source_count"], 3
        )  # walkthrough, release, a single interview
        self.assertEqual(len(cell["refs"]), 4)  # two excerpts from the same interview
        self.assertEqual(report["findings"][0]["priority"], "优先处置")
        self.assertIn("RILL@2026-10-10/edit", report["findings"][0]["refs"])
        self.assertEqual(self.cell(report, "mask", "INK")["status"], "conflict")
        self.assertEqual(self.cell(report, "export", "INK")["status"], "unknown")
        self.assertEqual(report["coverage"], {"INK": 3, "RILL": 3})

    def test_replay_and_experiment_after_new_observation(self):
        before = self.build()
        snapshot = catalog.save_report(self.db, {"title": "试验", **before})
        self.import_update()
        after = self.build()
        self.assertEqual(after["as_of"], "2026-10-17")
        self.assertEqual(self.build(as_of="2026-10-10"), before)
        self.assertEqual(
            catalog.get_report(self.db, snapshot["id"])["findings"], before["findings"]
        )
        self.assertFalse(self.cell(after, "edit", "INK")["disagreement"])
        self.assertNotIn("SIG-INK-01", self.cell(after, "edit", "INK")["refs"])
        self.assertEqual(after["findings"][0]["capability"], "mask")
        closed = next(f for f in after["findings"] if f["priority"] == "转入回归")
        self.assertEqual(closed["refs"], ["INK@2026-10-10/edit", "INK@2026-10-17/edit"])
        self.assertTrue(closed["metric"] and closed["experiment"])
        self.assertNotIn("优先处置", [f["priority"] for f in after["findings"]])
        self.assertNotIn("2026-10-17", json.dumps(before))

    def test_capability_semantics_as_of_and_intent(self):
        benchmark = self.build(intent="benchmark")
        self.assertIn("七项能力覆盖", benchmark["markdown"])
        self.assertNotIn("七项能力覆盖", self.build()["markdown"])
        self.assertEqual(self.build(intent="change")["changes"], [])
        self.import_update()
        version = self.build(intent="change")
        self.assertEqual(version["findings"][0]["priority"], "转入回归")
        future = self.build(as_of="2026-10-12")
        self.assertEqual(self.cell(future, "edit", "INK")["status"], "friction")
        empty = self.build(as_of="2026-01-01")
        self.assertEqual(empty["evidence"], [])
        self.assertTrue(
            all(
                c["status"] == "unknown"
                for r in empty["capabilities"]
                for c in r["cells"]
            )
        )
        unknown_names = {p["sku"]: p for p in empty["products"]}
        self.assertEqual(set(unknown_names), {"INK", "RILL"})
        with self.assertRaises(catalog.InputError):
            self.build(as_of="2026-02-31")

    def test_custom_evidence_filter_and_non_fixture_product(self):
        pair, rows = catalog.selected(self.db, "INK", "RILL")
        pair["products"][1] = {"sku": "X", "name": "其他工具"}
        signal = {
            **research.sample_signals()[0],
            "id": "SIG-X-1",
            "product": "X",
            "capability": "history",
            "origin": "X-REL",
            "excerpt": "可恢复历史版本。",
        }
        report = research.build(pair, rows, signals=[signal])
        self.assertEqual(self.cell(report, "history", "X")["status"], "reported")
        self.assertEqual(self.cell(report, "history", "X")["refs"], ["SIG-X-1"])
        self.assertEqual(self.cell(report, "edit", "X")["status"], "unknown")
        self.assertNotIn("RILL", json.dumps(report))

    def test_release_note_ahead_of_walkthrough_requests_validation(self):
        report = self.build(as_of="2026-10-16")
        self.assertEqual(self.cell(report, "edit", "INK")["status"], "friction")
        self.assertTrue(any("SIG-INK-07" in gap["refs"] for gap in report["gaps"]))
        self.assertNotIn("SIG-INK-07", self.cell(report, "edit", "INK")["refs"])

    def test_export_conflict_rechecked_with_evidence_from_a_real_task(self):
        pair, walks = catalog.selected(self.db, "INK", "RILL")
        baseline = {
            "product": "RILL",
            "version": "v2",
            "date": "2026-10-10",
            "capability": "export",
        }
        claims = [
            {
                **baseline,
                "source_type": "release",
                "origin": "NOTES-2",
                "source": "版本说明",
                "id": "SIG-RESULT-1",
                "position": "support",
                "excerpt": "导出支持透明背景。",
            },
            {
                **baseline,
                "source_type": "feedback",
                "origin": "INTERVIEW-1",
                "source": "使用者访谈",
                "id": "SIG-RESULT-2",
                "position": "friction",
                "excerpt": "在指定画布导出的图有底色。",
            },
        ]
        before = research.build(pair, walks, signals=claims)
        self.assertEqual(self.cell(before, "export", "RILL")["status"], "conflict")
        verified = {
            **walks[-1],
            "product": "RILL",
            "step": "export",
            "material": "EXPORT-RUN",
            "reference": "RILL@2026-10-10/export",
            "handoff": "fail",
            "observation": "导出的 PNG 有白色背景。",
        }
        after = research.build(pair, walks + [verified], signals=claims)
        cell = self.cell(after, "export", "RILL")
        self.assertTrue(cell["disagreement"])
        self.assertEqual(cell["status"], "friction")
        self.assertEqual(cell["source_count"], 3)
        self.assertCountEqual(
            cell["refs"], [verified["reference"], *[c["id"] for c in claims]]
        )
        finding = next(f for f in after["findings"] if f["capability"] == "export")
        self.assertIn("格式", finding["action"])
        self.assertIn("导出", finding["experiment"])
        fixed = research.build(
            pair,
            walks
            + [{**verified, "handoff": "pass", "observation": "本次导出符合规格。"}],
            signals=claims,
        )
        self.assertEqual(self.cell(fixed, "export", "RILL")["status"], "verified")
        self.assertTrue(self.cell(fixed, "export", "RILL")["disagreement"])

    def test_contradictory_retest_on_same_version_is_not_a_version_change(self):
        pair, rows = catalog.selected(self.db, "INK", "RILL")
        old = next(r for r in rows if r["product"] == "INK" and r["step"] == "edit")
        for first, second in (("upload", "direct"), ("direct", "upload")):
            with self.subTest(first=first):
                baseline = [{**r, "handoff": first} if r is old else r for r in rows]
                retest = {
                    **old,
                    "date": "2026-10-11",
                    "material": "INK-RETEST",
                    "reference": "INK@2026-10-11/edit",
                    "observation": "换入口复核编辑",
                    "handoff": second,
                }
                for intent in ("opportunity", "change"):
                    report = research.build(
                        pair, baseline + [retest], intent=intent, signals=[]
                    )
                    self.assertEqual(
                        report["changes"][0]["transition"], "same_version_conflict"
                    )
                    self.assertEqual(
                        self.cell(report, "edit", "INK")["status"], "conflict"
                    )
                    finding = next(
                        f for f in report["findings"] if f["capability"] == "edit"
                    )
                    self.assertEqual(finding["priority"], "优先验证")
                    self.assertCountEqual(
                        finding["refs"], [old["reference"], retest["reference"]]
                    )
                    self.assertIn("不一致", finding["reason"])
                    self.assertNotIn(
                        "转入回归", [f["priority"] for f in report["findings"]]
                    )

    def test_partial_new_batch_never_inherits_a_previous_step(self):
        pair, rows = catalog.selected(self.db, "INK", "RILL")
        previous = next(
            r for r in rows if r["product"] == "INK" and r["step"] == "generate"
        )
        new = {
            **previous,
            "version": "v7",
            "date": "2026-11-01",
            "reference": "INK@2026-11-01/generate",
        }
        report = research.build(pair, rows + [new])
        self.assertEqual(self.cell(report, "edit", "INK")["status"], "unknown")
        self.assertFalse(any(d["transition"] == "resolved" for d in report["changes"]))
        ids = {e["id"] for e in report["evidence"]}
        self.assertTrue(
            all(set(f["refs"]) <= ids for f in report["findings"] + report["trace"])
        )


if __name__ == "__main__":
    unittest.main()
