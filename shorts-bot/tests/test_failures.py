"""ব্যর্থতা ঠিকমতো ধরা পড়ছে কিনা: ভিডিও না বানালে রান লাল হতে হবে, কারণসহ।"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from shorts import config, llm, runner, youtube
from shorts.store import Store


def _setup(d, ideas=()):
    cfg = config.load()
    cfg["paths"] = {"data_dir": os.path.join(d, "data"), "out_dir": os.path.join(d, "out")}
    cfg["_root"] = d
    store = Store(os.path.join(d, "data", "queue.jsonl"))
    if ideas:
        store.add_topics("ai_tools", list(ideas))
    return cfg, store


class Failures(unittest.TestCase):
    def test_missing_key_is_fatal_with_fix_hint(self):
        cfg = config.load()
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "  ", "ANTHROPIC_API_KEY": ""}):
            for prov, name in (("gemini", "GEMINI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY")):
                cfg["llm"]["provider"] = prov
                with self.assertRaises(llm.LLMError) as c:
                    llm.complete(cfg, "s", "u")
                self.assertTrue(c.exception.fatal)
                self.assertIn(name, str(c.exception))

    def test_no_ideas_and_no_key_raises_not_green(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, store = _setup(d)
            cfg["llm"]["provider"] = "gemini"
            with mock.patch.dict(os.environ, {"GEMINI_API_KEY": ""}), \
                 mock.patch.object(youtube, "get_access_token", return_value="t"):
                with self.assertRaises(runner.RunError) as c:
                    runner.run(cfg, store, 1, "private")
            self.assertIn("GEMINI_API_KEY", str(c.exception))

    def test_fatal_llm_error_stops_after_first_category(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, store = _setup(d)
            calls = []

            def boom(*a, **k):
                calls.append(1)
                raise llm.LLMError("কী ভুল", fatal=True)

            with mock.patch.object(runner.ideas, "generate_for_category", boom):
                errs = runner.refill(cfg, store, 1)
            self.assertEqual(len(calls), 1)  # ১০টি ক্যাটাগরিতে ১০ বার নয়
            self.assertEqual(errs, ["কী ভুল"])

    def test_all_videos_failed_gives_zero_ok_and_reasons(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, store = _setup(d, ["ফোনের ব্যাটারি বাঁচানোর সহজ উপায়", "পাসওয়ার্ড নিরাপদ রাখার তিনটি কৌশল"])
            info = {}

            def bad_make(*a, **k):
                raise RuntimeError("স্ক্রিপ্ট বানানো যায়নি: test")

            with mock.patch.object(runner.pipeline, "make_one", bad_make), \
                 mock.patch.object(runner.ideas, "generate_for_category", return_value=0), \
                 mock.patch.object(youtube, "get_access_token", return_value="t"):
                ok, bad, stopped = runner.run(cfg, store, 2, "private", info=info)
            self.assertEqual((ok, bad), (0, 2))
            self.assertEqual(len(info["problems"]), 2)
            self.assertIn("স্ক্রিপ্ট বানানো যায়নি", info["problems"][0])

    def test_http_errors_explained_and_key_redacted(self):
        self.assertTrue(llm._explain(400, "API key not valid. Please pass a valid API key.")[1])
        self.assertIn("মডেলের নাম", llm._explain(404, "model not found")[0])
        self.assertIn("কোটা", llm._explain(429, "")[0])
        self.assertFalse(llm._explain(503, "")[1])
        self.assertNotIn("SECRETKEY", llm._redact("url ...generateContent?key=SECRETKEY&x=1 failed"))


if __name__ == "__main__":
    unittest.main()
