"""সংবাদ-শিরোনাম, ঘুরিয়ে ফরম্যাট, ক্যাটাগরি-পালা ও ডুপ্লিকেট আটকানো।"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from shorts import config, ideas, llm, news, research, scriptgen  # noqa: E402
from shorts.store import Store  # noqa: E402

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>ডলারের দাম বাড়ল ৩ টাকা - প্রথম আলো</title><source>প্রথম আলো</source><pubDate>Mon, 16 Nov 2026 09:00:00 GMT</pubDate></item>
<item><title>ডলারের দাম বাড়ল ৩ টাকা - ডেইলি স্টার</title><source>ডেইলি স্টার</source><pubDate>Mon, 16 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>ব্যাংকে নতুন সুদের হার ঘোষণা - বিডিনিউজ</title><source>বিডিনিউজ</source><pubDate>Mon, 16 Nov 2026 11:00:00 GMT</pubDate></item>
<item><title>ছোট</title><source>x</source></item>
</channel></rss>"""


def _cfg():
    cfg = config.load()
    cfg["llm"]["provider"] = "gemini"
    return cfg


class News(unittest.TestCase):
    def test_parse_cleans_title_and_dedupes(self):
        r = news.parse(RSS)
        self.assertEqual(len(r), 2)                       # একই শিরোনাম ও খুব ছোটটা বাদ
        self.assertEqual(r[0]["title"], "ডলারের দাম বাড়ল ৩ টাকা")
        self.assertEqual(r[0]["source"], "প্রথম আলো")

    def test_parse_bad_xml_is_safe(self):
        self.assertEqual(news.parse("<<not xml"), [])

    def test_facts_for_needs_two_headlines(self):
        cfg = _cfg()
        with mock.patch.object(news, "fetch", return_value=news.parse(RSS)):
            r = news.facts_for(cfg, "ডলারের দাম বাড়ল কেন")
        self.assertTrue(r["ok"] and len(r["facts"]) == 2)
        self.assertIn("প্রথম আলো", r["sources"])
        with mock.patch.object(news, "fetch", return_value=news.parse(RSS)[:1]):
            self.assertIsNone(news.facts_for(cfg, "ডলারের দাম বাড়ল কেন"))

    def test_gather_falls_back_to_headlines_when_search_down(self):
        cfg = _cfg()
        cfg["research"] = {"enabled": True, "verify": False, "strict": False}
        err = llm.LLMError("x", fatal=False, kind="grounding")
        with mock.patch.object(llm, "complete_grounded", side_effect=err), \
                mock.patch.object(news, "fetch", return_value=news.parse(RSS)):
            r = research.gather(cfg, {"name": "টাকা"}, "ডলারের দাম বাড়ল কেন")
        self.assertEqual(len(r["facts"]), 2)

    def test_grounding_switch_off_makes_no_request(self):
        cfg = _cfg()
        cfg["llm"]["grounding"] = False
        with mock.patch("requests.post") as post:
            with self.assertRaises(llm.LLMError) as cm:
                llm.complete_grounded(cfg, "s", "u")
        post.assert_not_called()
        self.assertEqual(cm.exception.kind, "grounding")

    def test_ideas_use_headlines_when_search_off(self):
        cfg = _cfg()
        cfg["ideas"]["trending"] = True
        cat = config.category(cfg, "money")
        with tempfile.TemporaryDirectory() as d:
            st = Store(Path(d) / "q.jsonl")
            with mock.patch.object(news, "fetch", return_value=news.parse(RSS) + [{"title": "আরেকটা বড় শিরোনাম এখানে", "source": "s", "date": ""}]), \
                    mock.patch.object(llm, "complete", return_value='["ডলারের নতুন দাম কী বলছে", "ব্যাংকের সুদের হার বদলের মানে"]') as c:
                n = ideas.generate_for_category(cfg, st, cat, 2)
        self.assertEqual(n, 2)
        self.assertEqual(c.call_count, 1)


class Variety(unittest.TestCase):
    def test_format_and_hook_rotate_with_variant(self):
        cfg = config.load()
        p = [scriptgen.build_prompt(cfg, {"name": "x"}, "বিষয়", "s", None, v) for v in range(6)]
        self.assertEqual(len(set(p)), 6)                  # ৬টি ভিডিও, ৬টি আলাদা ছাঁচ
        self.assertIn("ভিডিওর ফরম্যাট:", p[0])

    def test_pending_rotates_categories_without_repeat(self):
        with tempfile.TemporaryDirectory() as d:
            st = Store(Path(d) / "q.jsonl")
            for c in ("a", "b", "c"):
                for i in range(3):
                    st.rows.append({"id": f"{c}-{i}", "category": c, "topic": f"{c}{i}", "status": "idea"})
            order = []
            for _ in range(6):
                r = st.pending("idea", 1)[0]
                r["status"] = "rendered"
                order.append(r["category"])
            self.assertEqual(order[:3], ["a", "b", "c"])
            self.assertEqual(order[3:], ["a", "b", "c"])  # কখনো পরপর একই ক্যাটাগরি নয়

    def test_same_news_in_other_category_is_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            st = Store(Path(d) / "q.jsonl")
            self.assertEqual(st.add_topics("trending", ["ডলারের দাম এক দিনে তিন টাকা বেড়েছে, কী করবেন"]), 1)
            self.assertEqual(st.add_topics("world", ["ডলারের দাম এক দিনে তিন টাকা বেড়েছে কী করবেন"]), 0)


class Stored(unittest.TestCase):
    def test_ideas_keep_their_source_headlines(self):
        cfg = _cfg()
        cfg["ideas"]["trending"] = True
        cat = config.category(cfg, "money")
        heads = news.parse(RSS) + [{"title": "আরেকটা বড় শিরোনাম এখানে", "source": "ঢাকা ট্রিবিউন", "date": "16 Nov"}]
        reply = '[{"idea": "ডলারের নতুন দাম কী বলছে", "src": [1, 3]}, {"idea": "ব্যাংকের সুদের হার বদলের মানে", "src": [2]}]'
        with tempfile.TemporaryDirectory() as d:
            st = Store(Path(d) / "q.jsonl")
            with mock.patch.object(news, "fetch", return_value=heads), mock.patch.object(llm, "complete", return_value=reply):
                n = ideas.generate_for_category(cfg, st, cat, 2)
            self.assertEqual(n, 2)
            r = st.rows[0]
            self.assertEqual(len(r["facts"]), 2)
            self.assertIn("প্রথম আলো", r["facts"][0])
            self.assertIn("ঢাকা ট্রিবিউন", r["sources"])

    def test_pipeline_uses_stored_facts_without_new_requests(self):
        from shorts import pipeline
        cfg = _cfg()
        cfg["llm"]["grounding"] = False
        cfg["research"] = {"enabled": True, "verify": False, "strict": False}
        cat = config.category(cfg, "money")
        rec = {"id": "money-00001", "category": "money", "topic": "ডলারের নতুন দাম", "status": "idea",
               "facts": ["ডলারের দাম বাড়ল (সূত্র: প্রথম আলো, 16 Nov)"], "sources": ["প্রথম আলো"]}
        with tempfile.TemporaryDirectory() as d:
            st = Store(Path(d) / "q.jsonl")
            st.rows.append(rec)
            seen = {}

            def gen(cfg, cat, rec, facts=None, variant=None):
                seen["facts"] = facts
                return {"title": "শিরোনাম", "scenes": []}
            with mock.patch.object(research, "gather", side_effect=AssertionError("গবেষণা ডাকা উচিত নয়")), \
                    mock.patch.object(scriptgen, "generate", gen), \
                    mock.patch.object(research, "verify", side_effect=lambda c, sc, f: (sc, [])):
                sp = Path(d) / "s.json"
                pipeline.make_script(cfg, st, cat, rec, sp)
        self.assertEqual(seen["facts"], rec["facts"])


class Retry(unittest.TestCase):
    def setUp(self):
        news._CACHE.clear()

    def test_429_then_success_and_cache(self):
        cfg = _cfg()
        bad, ok = mock.Mock(status_code=429, text=""), mock.Mock(status_code=200, text=RSS)
        with mock.patch("requests.get", side_effect=[bad, ok]) as g, mock.patch("time.sleep") as sl:
            r = news.fetch(cfg, "ডলার", 5)
            self.assertEqual(len(r), 2)
            self.assertEqual(g.call_count, 2)
            sl.assert_called_once()
            news.fetch(cfg, "ডলার", 5)                    # ক্যাশ: আর কোনো অনুরোধ নয়
            self.assertEqual(g.call_count, 2)

    def test_falls_back_to_bing_when_google_keeps_429(self):
        cfg = _cfg()
        bad = mock.Mock(status_code=429, text="")
        bing = mock.Mock(status_code=200, text=RSS.replace("<source>", "<News:Source>").replace("</source>", "</News:Source>")
                         .replace("<rss>", '<rss xmlns:News="https://www.bing.com/news">'))
        with mock.patch("requests.get", side_effect=[bad, bad, bad, bing]), mock.patch("time.sleep"):
            r = news.fetch(cfg, "ডলার", 5)
        self.assertEqual(len(r), 2)
        self.assertEqual(r[0]["source"], "প্রথম আলো")


if __name__ == "__main__":
    unittest.main()
