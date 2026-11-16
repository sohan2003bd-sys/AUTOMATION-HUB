import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from shorts import config, runner, youtube
from shorts.store import Store


class Step2(unittest.TestCase):
    def test_clean_meta_limits(self):
        m = youtube.clean_meta({"title": "ক" * 150 + "<b>", "description": "x<y>" * 3000,
                                "tags": ["#ফোন", "a<b", "", "ক" * 80] + [f"tag{i}" for i in range(40)]})
        self.assertLessEqual(len(m["title"]), 100)
        self.assertNotIn("<", m["title"] + m["description"] + "".join(m["tags"]))
        self.assertLessEqual(len(m["description"]), 4900)
        self.assertLessEqual(len(m["tags"]), 15)
        self.assertEqual(m["tags"][0], "ফোন")
        self.assertLessEqual(max(len(t) for t in m["tags"]), 30)

    def test_next_slots_spacing_and_rollover(self):
        cfg = config.load()
        cfg["upload"]["slots"] = ["07:30", "09:00", "10:30", "12:00", "13:30", "15:00", "17:00", "19:00", "20:30", "22:00"]
        now = datetime(2026, 11, 16, 7, 0, tzinfo=timezone.utc)  # বাংলাদেশে ১৩:০০
        s = youtube.next_slots(cfg, 12, now, now)
        self.assertEqual(len(s), 12)
        self.assertEqual(s, sorted(s))
        self.assertTrue(all(x > now + timedelta(minutes=20) for x in s))
        local = [x.astimezone(timezone(timedelta(hours=6))) for x in s]
        self.assertEqual((local[0].hour, local[0].minute), (13, 30))   # আজকের বাকি স্লট
        self.assertEqual(local[-1].day, 17)                            # পরের দিনে গড়িয়েছে
        # আগের শিডিউলের পর থেকে শুরু
        later = datetime(2026, 11, 18, 0, 0, tzinfo=timezone.utc)
        self.assertGreater(youtube.next_slots(cfg, 1, later, now)[0], later)

    def test_monthly_limit(self):
        from shorts import runner
        cfg = {"limits": {"monthly_max": 60}, "upload": {"timezone_offset_hours": 6}}
        now = datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)
        ts = lambda *a: int(datetime(*a, tzinfo=timezone.utc).timestamp())
        class S: pass
        st = S()
        st.rows = [{"uploaded": ts(2026, 11, 3)}] * 30 + [{"uploaded": ts(2026, 10, 20)}] * 40 + [{"status": "idea"}]
        self.assertEqual(runner.monthly_remaining(cfg, st, now), 30)   # অক্টোবরেরগুলো গোনা হয় না
        st.rows += [{"uploaded": ts(2026, 11, 9)}] * 30
        self.assertEqual(runner.monthly_remaining(cfg, st, now), 0)
        self.assertIsNone(runner.monthly_remaining({"upload": {}}, st, now))

    def test_token_cleanup_and_missing(self):
        with mock.patch.dict(os.environ, {"YT_CLIENT_ID": ' "abc" \n', "YT_CLIENT_SECRET": "", "YT_REFRESH_TOKEN": "1//x"}):
            self.assertEqual(youtube._env("YT_CLIENT_ID"), "abc")
            with self.assertRaises(youtube.YouTubeError) as c:
                youtube.get_access_token()
            self.assertTrue(c.exception.fatal)
            self.assertIn("YT_CLIENT_SECRET", str(c.exception))

    def test_api_error_fatal_mapping(self):
        r = mock.Mock(status_code=403)
        r.json.return_value = {"error": {"message": "m", "errors": [{"reason": "uploadLimitExceeded"}]}}
        self.assertTrue(youtube._api_error(r).fatal)
        r2 = mock.Mock(status_code=400)
        r2.json.return_value = {"error": {"message": "bad title", "errors": [{"reason": "invalidTitle"}]}}
        self.assertFalse(youtube._api_error(r2).fatal)

    def _setup(self, d, n=3):
        cfg = config.load()
        cfg["paths"] = {"data_dir": os.path.join(d, "data"), "out_dir": os.path.join(d, "out")}
        cfg["_root"] = d
        store = Store(os.path.join(d, "data", "queue.jsonl"))
        store.add_topics("tech", ["ফোনের ব্যাটারি বাঁচানোর সহজ উপায়", "হোয়াটসঅ্যাপে ভুল মেসেজ মুছে ফেলার নিয়ম",
                                      "পাসওয়ার্ড নিরাপদ রাখার তিনটি কৌশল", "এক্সেলে দ্রুত যোগফল বের করার শর্টকাট"][:n])
        return cfg, store

    def _fake_make(self, cfg, store, rec, engine=None):
        out = config.out_dir(cfg)
        (out / f"{rec['id']}.mp4").write_bytes(b"x")
        (out / f"{rec['id']}.json").write_text('{"title": "শিরোনাম", "description": "d", "tags": ["a"]}', encoding="utf-8")
        store.update(rec["id"], status="rendered", video=f"{rec['id']}.mp4")
        return out / f"{rec['id']}.mp4"

    def test_run_uploads_and_marks(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, store = self._setup(d)
            calls = []

            def fake_upload(cfg, tok, path, meta, privacy, when):
                calls.append((privacy, when))
                return {"id": f"vid{len(calls)}", "privacy": "private", "publish_at": None}

            with mock.patch.object(runner.pipeline, "make_one", self._fake_make), \
                 mock.patch.object(youtube, "get_access_token", return_value="t"), \
                 mock.patch.object(youtube, "upload_video", fake_upload):
                ok, bad, stopped = runner.run(cfg, store, 2, "schedule")
            self.assertEqual((ok, bad, stopped), (2, 0, False))
            self.assertTrue(all(c[0] == "private" and c[1] is not None for c in calls))
            self.assertLess(calls[0][1], calls[1][1])
            self.assertEqual(sum(1 for r in store.rows if r["status"] == "uploaded"), 2)
            self.assertEqual(len(list(config.out_dir(cfg).glob("*"))), 0)  # ফাইল মুছে গেছে

    def test_run_fatal_stops_and_requeues(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, store = self._setup(d)

            def boom(*a, **k):
                raise youtube.YouTubeError("সীমা শেষ", fatal=True)

            with mock.patch.object(runner.pipeline, "make_one", self._fake_make), \
                 mock.patch.object(youtube, "get_access_token", return_value="t"), \
                 mock.patch.object(youtube, "upload_video", boom):
                ok, bad, stopped = runner.run(cfg, store, 3, "private")
            self.assertEqual((ok, bad, stopped), (0, 1, True))
            self.assertEqual(sum(1 for r in store.rows if r["status"] == "scripted"), 1)  # আবার চেষ্টা হবে
            self.assertEqual(sum(1 for r in store.rows if r["status"] == "idea"), 2)      # বাকিগুলো ছোঁয়া হয়নি

    def test_rendered_without_file_is_requeued(self):
        with tempfile.TemporaryDirectory() as d:
            cfg, store = self._setup(d, 1)
            store.update(store.rows[0]["id"], status="rendered", video="gone.mp4")
            with mock.patch.object(runner.pipeline, "make_one", self._fake_make), \
                 mock.patch.object(youtube, "get_access_token", return_value="t"), \
                 mock.patch.object(youtube, "upload_video", return_value={"id": "v", "privacy": "private", "publish_at": None}):
                ok, bad, _ = runner.run(cfg, store, 1, "private")
            self.assertEqual(ok, 1)


if __name__ == "__main__":
    unittest.main()
