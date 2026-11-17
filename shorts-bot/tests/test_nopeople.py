"""মানুষমুক্ত ফুটেজ: কিওয়ার্ড, শিরোনাম/ট্যাগ ও মুখ-শনাক্তকরণ।"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from shorts import config, media, scriptgen  # noqa: E402


class Words(unittest.TestCase):
    def test_strip_human_words(self):
        self.assertEqual(media.strip_human_words("hands counting banknotes"), "counting banknotes")
        self.assertEqual(media.strip_human_words("doctor talking to patient"), "talking to")
        self.assertEqual(media.strip_human_words("Women checking phone"), "checking phone")
        self.assertEqual(media.strip_human_words("empty dhaka street night"), "empty dhaka street night")
        self.assertEqual(media.strip_human_words("people crowd"), "")

    def test_human_text_detection(self):
        self.assertTrue(media.has_human_text("a-man-in-black-shirt-using-a-laptop 3195394"))
        self.assertTrue(media.has_human_text("Smiling woman at office"))
        self.assertFalse(media.has_human_text("banknotes on a wooden table"))
        self.assertFalse(media.has_human_text("city skyline at night"))

    def test_prompt_forbids_people(self):
        p = scriptgen.build_prompt(config.load(), {"name": "x"}, "বিষয়", "s")
        self.assertIn("কোনো মানুষ", p)
        self.assertNotIn("doctor talking to patient", p)

    def test_default_stock_has_no_people_words(self):
        from shorts import pipeline
        for k, v in pipeline.DEFAULT_STOCK.items():
            self.assertFalse(media.has_human_text(v), (k, v))


class Fetch(unittest.TestCase):
    ENV = {"PEXELS_API_KEY": "k", "PIXABAY_API_KEY": "", "UNSPLASH_ACCESS_KEY": ""}

    def _cands(self):
        return [
            {"id": "pexels-v1", "url": "u1", "type": "video", "text": "a woman using a laptop", "credit": "c"},
            {"id": "pexels-v2", "url": "u2", "type": "video", "text": "banknotes on table", "credit": "c"},
        ]

    def test_text_filter_skips_people_clips(self):
        cfg = config.load()
        cfg["media"]["face_check"] = False
        got = []

        def dl(url, dest):
            got.append(url)
            Path(dest).write_bytes(b"x" * 20000)
            return True
        with mock.patch.dict(os.environ, self.ENV), tempfile.TemporaryDirectory() as d, \
                mock.patch.object(media, "_pexels_videos", return_value=self._cands()), \
                mock.patch.object(media, "_download", dl):
            m = media.fetch(cfg, "money", "tech", d, "t", media.Used(Path(d) / "u.json"))
        self.assertEqual(m["id"], "pexels-v2")
        self.assertEqual(got, ["u2"])                     # মানুষের শিরোনামের ক্লিপ ডাউনলোডই হয়নি

    def test_face_check_rejects_and_moves_on(self):
        cfg = config.load()
        cands = [{"id": "pexels-v5", "url": "u5", "type": "video", "text": "street", "credit": "c"},
                 {"id": "pexels-v6", "url": "u6", "type": "video", "text": "road", "credit": "c"}]

        def dl(url, dest):
            Path(dest).write_bytes(b"x" * 20000)
            return True
        seen = []

        def person(path, kind):
            seen.append(1)
            return len(seen) == 1
        with mock.patch.dict(os.environ, self.ENV), tempfile.TemporaryDirectory() as d, \
                mock.patch.object(media, "_pexels_videos", return_value=cands), \
                mock.patch.object(media, "_download", dl), mock.patch.object(media, "has_person", person):
            m = media.fetch(cfg, "street", "tech", d, "t", media.Used(Path(d) / "u.json"))
        self.assertEqual(len(seen), 2)                     # প্রথমটিতে মানুষ ধরা পড়ল, দ্বিতীয়টি নেওয়া হলো
        self.assertIn(m["id"], ("pexels-v5", "pexels-v6"))

    def test_human_only_query_falls_back_to_safe_default(self):
        cfg = config.load()
        cfg["media"]["face_check"] = False
        qs = []

        def vids(q, rnd, md):
            qs.append(q)
            return []
        with mock.patch.dict(os.environ, self.ENV), tempfile.TemporaryDirectory() as d, \
                mock.patch.object(media, "_pexels_videos", vids), mock.patch.object(media, "_pexels_photos", return_value=[]), \
                mock.patch.object(media, "_pixabay_videos", return_value=[]), mock.patch.object(media, "_pixabay_photos", return_value=[]), \
                mock.patch.object(media, "_unsplash_photos", return_value=[]):
            media.fetch(cfg, "people crowd", "office desk laptop", d, "t", media.Used(Path(d) / "u.json"))
        self.assertEqual(qs, ["office desk laptop"])

    def test_no_people_off_keeps_old_behaviour(self):
        cfg = config.load()
        cfg["media"]["no_people"] = False
        qs = []

        def vids(q, rnd, md):
            qs.append(q)
            return []
        with mock.patch.dict(os.environ, self.ENV), tempfile.TemporaryDirectory() as d, \
                mock.patch.object(media, "_pexels_videos", vids), mock.patch.object(media, "_pexels_photos", return_value=[]), \
                mock.patch.object(media, "_pixabay_videos", return_value=[]), mock.patch.object(media, "_pixabay_photos", return_value=[]), \
                mock.patch.object(media, "_unsplash_photos", return_value=[]):
            media.fetch(cfg, "woman using phone", "tech", d, "t", media.Used(Path(d) / "u.json"))
        self.assertEqual(qs[0], "woman using phone")


class Detector(unittest.TestCase):
    def test_blank_image_has_no_person_and_bad_file_is_safe(self):
        try:
            import cv2  # noqa: F401
            import numpy as np
        except ImportError:
            self.skipTest("opencv নেই")
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "blank.jpg"
            import cv2 as c
            c.imwrite(str(p), np.full((640, 360, 3), 200, dtype=np.uint8))
            self.assertFalse(media.has_person(p, "image"))
            bad = Path(d) / "bad.mp4"
            bad.write_bytes(b"not a video")
            self.assertFalse(media.has_person(bad, "video"))   # ভাঙা ফাইলে ভিডিও আটকায় না


if __name__ == "__main__":
    unittest.main()
