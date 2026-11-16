"""ভাইরাল-টপিক সংস্করণের নতুন ফিচারের পরীক্ষা: ভয়েস বাছাই, মেয়াদ-উত্তীর্ণ আইডিয়া, রিফিল, লুক, স্ক্রিপ্ট যাচাই।"""
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shorts import config, render, runner, scriptgen, store as store_mod, tts  # noqa: E402


def _cfg(**over):
    cfg = config.load()
    cfg.update(over)
    return cfg


class Voices(unittest.TestCase):
    def test_gender_alternates(self):
        cfg = _cfg()
        genders = [tts.pick_voice(cfg, f"trending-{i:05d}", "warm", i)["gender"] for i in range(6)]
        self.assertEqual(genders, ["female", "male"] * 3)

    def test_voice_comes_from_pool_and_is_deterministic(self):
        cfg = _cfg()
        names = {v["name"] for v in cfg["tts"]["voices"]}
        a = tts.pick_voice(cfg, "health-00007", "serious", 3)
        b = tts.pick_voice(cfg, "health-00007", "serious", 3)
        self.assertEqual(a, b)
        self.assertIn(a["name"], names)

    def test_rate_pitch_format_valid_for_edge(self):
        cfg = _cfg()
        for i in range(40):
            v = tts.pick_voice(cfg, f"x-{i}", ["serious", "warm", "energetic", "urgent", "???"][i % 5], i)
            self.assertRegex(v["rate"], r"^[+-]\d+%$")
            self.assertRegex(v["pitch"], r"^[+-]\d+Hz$")
            self.assertLessEqual(abs(int(v["rate"][:-1])), 20)

    def test_serious_is_slower_than_urgent(self):
        cfg = _cfg()
        cfg["tts"]["jitter"] = False
        slow = int(tts.pick_voice(cfg, "a", "serious", 0)["rate"][:-1])
        fast = int(tts.pick_voice(cfg, "a", "urgent", 0)["rate"][:-1])
        self.assertLess(slow, fast)

    def test_fallback_to_single_voice_without_pool(self):
        cfg = _cfg()
        cfg["tts"].pop("voices")
        self.assertEqual(tts.pick_voice(cfg, "a", "warm", 1)["name"], cfg["tts"]["voice"])

    def test_synth_passes_voice_to_edge(self):
        cfg = _cfg()
        seen = {}

        async def fake_edge(text, voice, rate, pitch, path):
            seen.update(voice=voice, rate=rate, pitch=pitch)
            Path(path).write_bytes(b"x")

        v = {"name": "bn-BD-PradeepNeural", "rate": "+7%", "pitch": "-2Hz"}
        with mock.patch.object(tts, "_edge", fake_edge), mock.patch.object(tts, "duration", lambda p: 2.0):
            tts.synth(cfg, "পরীক্ষা", Path(tempfile.gettempdir()) / "viral_t.mp3", "edge", v)
        self.assertEqual(seen, {"voice": "bn-BD-PradeepNeural", "rate": "+7%", "pitch": "-2Hz"})


class Script(unittest.TestCase):
    def _scene(self, n=14):
        return {"kind": "step", "narration": " ".join(["শব্দ"] * n), "visual_query": "busy dhaka street"}

    def test_screen_text_optional_and_tone_kept(self):
        cfg = _cfg()
        d = {"title": "শিরোনাম", "tone": "Urgent", "tags": ["a"], "scenes": [self._scene() for _ in range(6)]}
        out = scriptgen.validate(cfg, d)
        self.assertEqual(out["tone"], "urgent")
        self.assertTrue(all(s["screen_text"] for s in out["scenes"]))

    def test_unknown_tone_becomes_warm(self):
        cfg = _cfg()
        d = {"title": "শিরোনাম", "tone": "wild", "scenes": [self._scene() for _ in range(6)]}
        self.assertEqual(scriptgen.validate(cfg, d)["tone"], "warm")

    def test_prompt_demands_subtitle_only_and_varied_real_footage(self):
        p = scriptgen.build_prompt(_cfg(), {"name": "x"}, "বিষয়", "s")
        self.assertIn("সাবটাইটেল", p)
        self.assertIn("animation", p)
        self.assertNotIn("screen_text", p)


class Freshness(unittest.TestCase):
    def _store(self, d):
        return store_mod.Store(Path(d) / "q.jsonl")

    def test_retire_and_expire(self):
        with tempfile.TemporaryDirectory() as d:
            st = self._store(d)
            old = time.time() - 5 * 86400
            st.rows = [
                {"id": "gone-1", "category": "gone", "topic": "a", "status": "idea", "created": time.time()},
                {"id": "money-1", "category": "money", "topic": "b", "status": "idea", "created": old},
                {"id": "health-1", "category": "health", "topic": "c", "status": "idea", "created": old},
                {"id": "money-2", "category": "money", "topic": "d", "status": "uploaded", "created": old},
                {"id": "money-3", "category": "money", "topic": "e", "status": "idea", "created": time.time()},
            ]
            n = st.retire({"money", "health"}, {"health"}, 2)
            by = {r["id"]: r["status"] for r in st.rows}
            self.assertEqual(n, 2)
            self.assertEqual(by, {"gone-1": "retired", "money-1": "expired", "health-1": "idea",
                                  "money-2": "uploaded", "money-3": "idea"})

    def test_refill_targets_fresh_topics_in_news_categories_first(self):
        cfg = _cfg()
        with tempfile.TemporaryDirectory() as d:
            st = self._store(d)
            calls = []

            words = ["ডলারের দাম বাড়ার কারণ", "মৌসুমি জ্বরে সতর্কতা", "নতুন ট্যাক্স নিয়ম কী বলছে", "ভুয়া কল থেকে বাঁচার উপায়",
                     "পরীক্ষার ফল প্রকাশের সময়সূচি", "বিশ্ব বাজারে তেলের দামের খবর"]

            def gen(cfg_, store, cat, n):
                calls.append(cat["id"])
                return store.add_topics(cat["id"], [f"{cat['id']}: {words[(len(calls) * 3 + i) % len(words)]}" for i in range(n)])

            with mock.patch.object(runner.ideas, "generate_for_category", gen):
                runner.refill(cfg, st, 2)
            evergreen = {c["id"] for c in cfg["categories"] if c.get("evergreen", True)}
            self.assertTrue(calls)
            self.assertFalse(set(calls) & evergreen, "প্রথমে শুধু টাটকা-থাকা-দরকার ক্যাটাগরি")
            self.assertLessEqual(len(calls), 3)

    def test_refill_skips_trending_search_when_fresh_enough(self):
        cfg = _cfg()
        with tempfile.TemporaryDirectory() as d:
            st = self._store(d)
            st.add_topics("trending", ["ডলারের দাম বাড়ার কারণ কী", "মৌসুমি জ্বরে সতর্কতা",
                                       "নতুন ট্যাক্স নিয়মে কী বদলাচ্ছে", "ভুয়া কল থেকে বাঁচার উপায়"])
            self.assertEqual(len(st.rows), 4)
            with mock.patch.object(runner.ideas, "generate_for_category") as g:
                runner.refill(cfg, st, 2)
            g.assert_not_called()


class Looks(unittest.TestCase):
    def test_look_deterministic_and_varied(self):
        cfg = _cfg()
        self.assertEqual(render.pick_look(cfg, "v1"), render.pick_look(cfg, "v1"))
        self.assertGreaterEqual(len({render.pick_look(cfg, f"v{i}")["name"] for i in range(40)}), 4)

    def test_looks_can_be_disabled(self):
        cfg = _cfg()
        cfg["video"]["looks"] = False
        self.assertIsNone(render.pick_look(cfg, "v1"))
        self.assertIn("eq=", render.look_filter(None))

    @unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg নেই")
    def test_every_look_filter_is_valid_ffmpeg(self):
        for name, l in render.LOOKS.items():
            f = render.look_filter({"name": name, **l, "off": 0})
            r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                                "testsrc2=size=320x568:rate=30:duration=0.3", "-vf", f"{f},format=yuv420p",
                                "-f", "null", "-"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, f"{name}: {r.stderr[-200:]}")


class ConfigSanity(unittest.TestCase):
    def test_new_defaults(self):
        cfg = config.load()
        self.assertEqual(cfg["video"]["style"], "subtitles")
        self.assertEqual(cfg["limits"]["monthly_max"], 60)
        self.assertEqual(len(cfg["upload"]["slots"]), 2)
        self.assertTrue(cfg["research"]["enabled"] and not cfg["research"]["verify"])  # verify টোকেন বাঁচাতে বন্ধ
        genders = {v["gender"] for v in cfg["tts"]["voices"]}
        self.assertEqual(genders, {"female", "male"})
        ids = [c["id"] for c in cfg["categories"]]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
