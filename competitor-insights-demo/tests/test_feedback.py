import csv
import unittest
from pathlib import Path

from insights import feedback, materials

SAMPLES = Path(__file__).resolve().parents[1] / "samples"
PRODUCTS = [
    {"sku": "INK", "name": "墨汀 Studio"},
    {"sku": "RILL", "name": "溪屿 Canvas"},
]


def load(name):
    with (SAMPLES / name).open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.rows = load("research-signals.csv") + load("feedback-observations.csv")

    def report(self, as_of="2026-10-10", rows=None, products=None, days=7):
        return feedback.build(
            self.rows if rows is None else rows,
            PRODUCTS if products is None else products,
            as_of,
            days,
        )

    def test_sample_is_importable_and_two_windows_trigger_different_topics(self):
        materials.parse((SAMPLES / "feedback-observations.csv").read_bytes())
        first = self.report()
        self.assertEqual(
            first["periods"],
            {
                "current": {"start": "2026-10-04", "end": "2026-10-10"},
                "previous": {"start": "2026-09-27", "end": "2026-10-03"},
            },
        )
        self.assertEqual(
            [a["topic_id"] for a in first["alerts"]], ["RILL:export:alpha"]
        )
        later = self.report("2026-10-17")
        self.assertIn("INK:mask:boundary", [a["topic_id"] for a in later["alerts"]])
        self.assertEqual(first["topics"][0]["current_negative"], 3)
        self.assertEqual(first["topics"][0]["previous_negative"], 0)

    def test_unique_source_denominators_opposing_positions_and_separate_topics(self):
        report = self.report()
        by_id = {t["id"]: t for t in report["topics"]}
        topic = by_id["RILL:export:alpha"]
        self.assertEqual((topic["current_sources"], topic["previous_sources"]), (4, 1))
        self.assertEqual((topic["current_total"], topic["previous_total"]), (7, 3))
        self.assertEqual((topic["current_positive"], topic["current_negative"]), (1, 3))
        self.assertTrue(
            {"support", "friction"} <= {r["position"] for r in topic["representatives"]}
        )
        self.assertIn("RILL:export:size", by_id)
        self.assertIn("SIG-RADAR-12", report["unclassified"]["refs"])
        self.assertFalse({"SIG-RADAR-13", "SIG-INK-01"} & set(topic["refs"]))

    def test_duplicate_excerpt_and_source_never_inflate_an_alert(self):
        rows = [r for r in self.rows if r["product"] == "RILL"]
        duplicates = [{**r, "id": "SIG-DUP-" + str(n)} for n, r in enumerate(rows)]
        after = self.report(rows=rows + rows + duplicates)
        before = self.report(rows=rows)
        a, b = after["topics"][0], before["topics"][0]
        self.assertEqual(
            (
                a["current_total"],
                a["current_sources"],
                a["current_negative"],
                a["needs_review"],
            ),
            (
                b["current_total"],
                b["current_sources"],
                b["current_negative"],
                b["needs_review"],
            ),
        )
        same = [{**r, "origin": "SHARED"} for r in rows]
        self.assertFalse(self.report(rows=same)["alerts"])

    def test_future_other_product_and_non_feedback_are_excluded(self):
        scope = self.report(products=PRODUCTS[:1])
        self.assertEqual({t["product"] for t in scope["topics"]}, {"INK"})
        allowed = {
            r["id"]
            for r in self.rows
            if r["source_type"] == "feedback"
            and r["product"] == "INK"
            and r["date"] <= "2026-10-10"
        }
        self.assertTrue(all(set(t["refs"]) <= allowed for t in scope["topics"]))
        self.assertTrue(set(scope["unclassified"]["refs"]) <= allowed)
        self.assertTrue(all(t["last_seen"] <= "2026-10-10" for t in scope["topics"]))
        self.assertEqual(self.report(as_of="2026-01-01")["topics"], [])

    def test_sparse_baseline_and_unclassified_are_explicit(self):
        rows = [r for r in self.rows if r["date"] >= "2026-10-04"]
        result = self.report(rows=rows)
        self.assertFalse(result["alerts"])
        self.assertTrue(all(t["trend"] == "缺少上期基线" for t in result["topics"]))
        recent = self.report("2026-10-17", days=1)
        self.assertEqual(recent["periods"]["previous"]["end"], "2026-10-16")

    def test_classification_is_by_content_and_capability_not_source_identifier(self):
        item = dict(
            self.rows[1],
            product="OTHER",
            date="2026-10-10",
            id="MY-NEW",
            capability="export",
            source_type="feedback",
            position="friction",
            excerpt="透明通道有问题，想知道具体哪里出错。",
        )
        products = [{"sku": "OTHER", "name": "虚构产品"}]
        self.assertEqual(
            self.report(rows=[item], products=products)["topics"][0]["id"],
            "OTHER:export:alpha",
        )
        item["capability"] = "mask"
        self.assertEqual(
            self.report(rows=[item], products=products)["unclassified"]["refs"],
            ["MY-NEW"],
        )

    def test_nearby_phrase_and_arbitrary_text_remain_distinguishable(self):
        base = dict(
            self.rows[0],
            product="RILL",
            capability="export",
            source_type="feedback",
            date="2026-10-10",
        )
        names = self.report(
            rows=[dict(base, id="SIG-FUZZY", excerpt="反馈提及透明版背景导出")]
        )["topics"]
        self.assertEqual([t["id"] for t in names], ["RILL:export:alpha"])
        unknown = self.report(
            rows=[dict(base, id="SIG-OTHER", excerpt="约的时间需要再沟通一下。")]
        )
        self.assertFalse(unknown["topics"])
        self.assertEqual(unknown["unclassified"], {"count": 1, "refs": ["SIG-OTHER"]})

    def test_channel_mix_reconciles_and_negative_alert_has_auditable_channels(self):
        radar = self.report()
        mix = {r["channel"]: r for r in radar["channel_mix"] if r["product"] == "RILL"}
        self.assertEqual(sum(r["current_sources"] for r in mix.values()), 7)
        self.assertEqual(sum(r["previous_sources"] for r in mix.values()), 3)
        self.assertEqual(mix["unknown"]["current_sources"], 2)
        self.assertEqual(mix["support"]["current_sources"], 2)
        alert = radar["alerts"][0]
        negatives = {r["channel"]: r["current_negative"] for r in alert["channel_mix"]}
        self.assertEqual(negatives["community"], 1)
        self.assertEqual(negatives["support"], 2)
        self.assertEqual(sum(negatives.values()), 3)
        self.assertFalse(self.report("2000-01-01")["channel_mix"])

    def test_source_crossing_channels_counts_once_and_reclassifies_each_period(self):
        item = dict(
            self.rows[1],
            product="RILL",
            origin="ONE",
            capability="export",
            excerpt="透明底图丢失",
            date="2026-10-10",
            position="friction",
        )
        both = [
            dict(item, id="ONE-A", channel="support"),
            dict(
                item,
                id="ONE-B",
                channel="community",
                capability="mask",
                excerpt="笔刷位置偏了",
            ),
            dict(item, id="ONE-C", channel="unknown", date="2026-10-03"),
            dict(item, id="ONE-D", channel="interview", date="2026-10-03"),
        ]
        report = self.report(rows=both)
        summary = {
            r["channel"]: (r["current_sources"], r["previous_sources"])
            for r in report["channel_mix"]
        }
        self.assertEqual(summary, {"mixed": (1, 0), "interview": (0, 1)})
        topic = next(t for t in report["topics"] if t["capability"] == "export")
        self.assertEqual(topic["channel_mix"], report["channel_mix"])
