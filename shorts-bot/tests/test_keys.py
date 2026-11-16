"""একাধিক Gemini কী: কোটা শেষ হলে পরের কী, গোল ঘুরে।"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from shorts import config, llm

ENV_CLEAN = {n: "" for n in llm.KEY_NAMES}
ENV_CLEAN["GEMINI_API_KEYS"] = ""


def _resp(status, body):
    r = mock.Mock(status_code=status, text=str(body))
    r.json.return_value = body
    return r


GOOD = _resp(200, {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})
QUOTA = _resp(429, {"error": {"status": "RESOURCE_EXHAUSTED"}})


class KeyRotation(unittest.TestCase):
    def setUp(self):
        llm._KEY_STATE["i"] = 0
        llm._COOLDOWN.clear()
        self.cfg = config.load()
        self.cfg["llm"].update(provider="gemini", gemini_model="m-main", gemini_fallback_model="m-fb")

    def _env(self, **kw):
        env = dict(ENV_CLEAN)
        env.update(kw)
        return mock.patch.dict(os.environ, env)

    def _run(self, fake):
        with mock.patch.object(llm.requests, "post", fake), mock.patch.object(llm.time, "sleep"):
            return llm.complete(self.cfg, "s", "u")

    def test_next_key_when_first_is_exhausted(self):
        used = []

        def fake(url, **kw):
            used.append(url.split("key=")[1])
            return QUOTA if url.endswith("key=K1") else GOOD

        with self._env(GEMINI_API_KEY="K1", GEMINI_API_KEY_2="K2"):
            self.assertEqual(self._run(fake), "ok")
        self.assertEqual(used, ["K1", "K2"])  # ১ম কী-তে ১বারই চেষ্টা (অপেক্ষা নেই), তারপর ২য়

    def test_sticky_then_wraps_around(self):
        used = []
        dead = {"K1"}

        def fake(url, **kw):
            k = url.split("key=")[1]
            used.append(k)
            return QUOTA if k in dead else GOOD

        with self._env(GEMINI_API_KEY="K1", GEMINI_API_KEY_2="K2", GEMINI_API_KEY_3="K3"):
            self._run(fake)                       # K1 ✗ → K2 ✓
            self._run(fake)                       # এবার সরাসরি K2 (K1-এ সময় নষ্ট নয়)
            self.assertEqual(used, ["K1", "K2", "K2"])
            dead.update({"K2", "K3"}); dead.discard("K1")
            llm._COOLDOWN.clear()
            used.clear()
            llm._KEY_STATE["i"] = 1               # K2 থেকে শুরু: K2 ✗ → K3 ✗ → K1 (গোল ঘুরে) ✓
            self._run(fake)
            self.assertEqual(used, ["K2", "K3", "K1"])

    def test_all_keys_exhausted_then_fallback_model(self):
        urls = []

        def fake(url, **kw):
            urls.append(url)
            return QUOTA if "m-main" in url else GOOD

        with self._env(GEMINI_API_KEY="K1", GEMINI_API_KEY_2="K2"):
            self.assertEqual(self._run(fake), "ok")
        main = [u for u in urls if "m-main" in u]
        self.assertEqual(len(main), 2)            # দুটো কী-ই প্রধান মডেলে ব্যর্থ
        self.assertTrue(any("m-fb" in u for u in urls))

    def test_all_fail_raises_quota(self):
        with self._env(GEMINI_API_KEY="K1", GEMINI_API_KEY_2="K2"):
            with self.assertRaises(llm.LLMError) as c:
                self._run(lambda url, **kw: QUOTA)
        self.assertEqual(c.exception.kind, "quota")
        self.assertNotIn("K1", str(c.exception))

    def test_single_key_still_works_and_missing_key_errors(self):
        with self._env(GEMINI_API_KEY="K1"):
            self.assertEqual(self._run(lambda url, **kw: GOOD), "ok")
        with self._env():
            with self.assertRaises(llm.LLMError) as c:
                self._run(lambda url, **kw: GOOD)
        self.assertEqual(c.exception.kind, "key")

    def test_comma_list_and_dedupe(self):
        with self._env(GEMINI_API_KEY="A", GEMINI_API_KEYS="A, B ,C"):
            self.assertEqual(llm.gemini_keys(), ["A", "B", "C"])


class DailyQuota(unittest.TestCase):
    def test_daily_quota_not_retried_and_skipped_later(self):
        daily = _resp(429, "GenerateRequestsPerDayPerProjectPerModel-FreeTier")
        llm._COOLDOWN.clear()
        llm._KEY_STATE["i"] = 0
        env = dict(ENV_CLEAN, GEMINI_API_KEY="k1", GEMINI_API_KEY_2="k2")
        with mock.patch.dict(os.environ, env), mock.patch("time.sleep"), \
                mock.patch("requests.post", return_value=daily) as post:
            with self.assertRaises(llm.LLMError):
                llm._post_rotating("m", {}, 3, 10)
            self.assertEqual(post.call_count, 2)       # প্রতি কী-তে ১বার, রিট্রাই নয়
            with self.assertRaises(llm.LLMError) as cm:
                llm._post_rotating("m", {}, 3, 10)
            self.assertEqual(post.call_count, 2)       # দ্বিতীয়বার কোনো HTTP কল নয়
            self.assertTrue(cm.exception.fatal)
        llm._COOLDOWN.clear()


class QuotaDetail(unittest.TestCase):
    def test_extracts_real_reason(self):
        import json
        body = json.dumps({"error": {"code": 429, "details": [
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [
                {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
                 "quotaValue": "0", "quotaDimensions": {"model": "gemini-3.1-flash-lite"}}]},
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "30s"}]}})
        text, daily = llm._quota_detail(body)
        self.assertTrue(daily)
        self.assertIn("সীমা=0", text)
        self.assertIn("gemini-3.1-flash-lite", text)

    def test_bad_body_is_safe(self):
        self.assertEqual(llm._quota_detail("not json"), ("", False))


class GroundingDown(unittest.TestCase):
    def setUp(self):
        llm._COOLDOWN.clear(); llm._GROUND["down"] = False; llm._KEY_STATE["i"] = 0

    def tearDown(self):
        llm._COOLDOWN.clear(); llm._GROUND["down"] = False

    def test_grounded_429_flags_and_plain_still_works(self):
        env = dict(ENV_CLEAN, GEMINI_API_KEY="k1")
        cfg = {"llm": {"provider": "gemini", "gemini_model": "m", "gemini_fallback_model": ""}}

        def fake_post(url, headers=None, json=None, timeout=None):
            return _resp(429, "quota") if json.get("tools") else GOOD
        with mock.patch.dict(os.environ, env), mock.patch("time.sleep"), \
                mock.patch("requests.post", side_effect=fake_post) as post:
            with self.assertRaises(llm.LLMError) as cm:
                llm.complete_grounded(cfg, "s", "u")
            self.assertEqual(cm.exception.kind, "grounding")
            self.assertFalse(cm.exception.fatal)
            n = post.call_count
            with self.assertRaises(llm.LLMError):          # দ্বিতীয়বার কোনো HTTP কল নয়
                llm.complete_grounded(cfg, "s", "u")
            self.assertEqual(post.call_count, n)
            self.assertEqual(llm.complete(cfg, "s", "u"), "ok")  # সাধারণ অনুরোধ আটকায়নি


if __name__ == "__main__":
    unittest.main()
