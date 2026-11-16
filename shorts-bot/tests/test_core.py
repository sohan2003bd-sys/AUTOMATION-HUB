import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from shorts import config, render, samples, scriptgen
from shorts.llm import extract_json
from shorts.store import Store


class Core(unittest.TestCase):
    def test_extract_json_fenced_and_nested(self):
        self.assertEqual(extract_json('হ্যাঁ ```json\n{"a": {"b": "}"}, "c": [1]}\n``` শেষ'), {"a": {"b": "}"}, "c": [1]})
        self.assertEqual(extract_json('["এক", "দুই"]'), ["এক", "দুই"])
        with self.assertRaises(ValueError):
            extract_json("কিছু নেই")

    def test_validate_sample_ok(self):
        cfg = config.load()
        out = scriptgen.validate(cfg, samples.SCRIPT)
        self.assertEqual(len(out["scenes"]), 7)

    def test_validate_rejects_short(self):
        cfg = config.load()
        bad = {"title": "x", "scenes": samples.SCRIPT["scenes"][:2]}
        with self.assertRaises(ValueError):
            scriptgen.validate(cfg, bad)

    def test_store_dedupe_and_round_robin(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(os.path.join(d, "q.jsonl"))
            self.assertEqual(s.add_topics("a", ["ফোনের ব্যাটারি বাঁচানোর উপায়", "ফোনের ব্যাটারি বাঁচানোর উপায়!", "অন্য বিষয়"]), 2)
            s.add_topics("b", ["বিষয় ক", "বিষয় খ"])
            picked = s.pending("idea", 3)
            self.assertEqual({p["category"] for p in picked[:2]}, {"a", "b"})
            s2 = Store(os.path.join(d, "q.jsonl"))
            self.assertEqual(len(s2.rows), 4)
            self.assertEqual(s2.rows[0]["id"], "a-00001")

    def test_wrap_fits(self):
        cfg = config.load()
        f = render.load_font(cfg, 60)
        for ln in render.wrap(f, "পাসওয়ার্ড চুরি হলেও নিরাপদ থাকুন এবং সতর্ক থাকুন", 500):
            self.assertLessEqual(f.getlength(ln), 500)


if __name__ == "__main__":
    unittest.main()
