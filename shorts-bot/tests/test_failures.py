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


class GeminiFallback(unittest.TestCase):
    def _resp(self, status, body):
        r = mock.Mock(status_code=status, text=str(body))
        r.json.return_value = body
        return r

    def _cfg(self):
        cfg = config.load()
        cfg["llm"].update(provider="gemini", gemini_model="gemini-3.8-flash",
                          gemini_fallback_model="gemini-3.6-flash", gemini_thinking="low")
        return cfg

    def test_body_has_low_thinking_and_bigger_token_limit(self):
        ok = self._resp(200, {"candidates": [{"content": {"parts": [{"text": "[]"}]}}]})
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"}), \
             mock.patch.object(llm.requests, "post", return_value=ok) as post:
            self.assertEqual(llm.complete(self._cfg(), "s", "u", max_tokens=1000), "[]")
        body = post.call_args.kwargs["json"]["generationConfig"]
        self.assertEqual(body["thinkingConfig"], {"thinkingLevel": "low"})
        self.assertEqual(body["maxOutputTokens"], 3000)
        self.assertNotIn("temperature", body)

    def test_server_busy_falls_back_to_second_model(self):
        busy = self._resp(503, {"error": {"status": "UNAVAILABLE"}})
        good = self._resp(200, {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
        urls = []

        def fake_post(url, **kw):
            urls.append(url)
            return busy if "3.8-flash" in url else good

        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"}), \
             mock.patch.object(llm.requests, "post", fake_post), mock.patch.object(llm.time, "sleep"):
            self.assertEqual(llm.complete(self._cfg(), "s", "u"), "ok")
        self.assertTrue(any("3.6-flash" in u for u in urls))

    def test_bad_key_does_not_try_fallback(self):
        bad = self._resp(400, {"error": {"message": "API key not valid"}})
        urls = []
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"}), \
             mock.patch.object(llm.requests, "post", lambda url, **kw: urls.append(url) or bad):
            with self.assertRaises(llm.LLMError) as c:
                llm.complete(self._cfg(), "s", "u")
        self.assertEqual(c.exception.kind, "key")
        self.assertEqual(len(urls), 1)

    def test_refill_stops_when_enough_ideas(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, store = _setup(d)
            calls = []

            def gen(cfg, store, cat, n):
                calls.append(cat["id"])
                pool = ["ফোনের ব্যাটারি বাঁচানোর উপায়", "পাসওয়ার্ড নিরাপদ রাখার কৌশল", "এক্সেলে দ্রুত যোগফলের শর্টকাট",
                        "ওয়াইফাই স্পিড বাড়ানোর সেটিং", "হোয়াটসঅ্যাপে ভুল মেসেজ মুছুন"]
                return store.add_topics(cat["id"], pool[:n])

            with mock.patch.object(runner.ideas, "generate_for_category", gen):
                runner.refill(cfg, store, 1)
            self.assertLessEqual(len(calls), 3)  # ১০টি ক্যাটাগরিতে নয়

    def test_refill_respects_time_budget(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, store = _setup(d)
            with mock.patch.object(runner.ideas, "generate_for_category", return_value=0) as g:
                errs = runner.refill(cfg, store, 10, budget_sec=-1)
            self.assertEqual(g.call_count, 0)
            self.assertTrue(errs)
