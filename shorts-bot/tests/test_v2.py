import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from shorts import audio, config, llm, media, pipeline, render, research, runner, samples, scriptgen, smartedit, tts
from shorts.store import Store


def cfg_g():
    cfg = config.load()
    cfg["llm"]["provider"] = "gemini"
    cfg["research"] = {"enabled": True, "verify": True, "strict": False}
    cfg["news"] = {"enabled": False}  # পরীক্ষায় আসল নেটওয়ার্ক নয়
    cfg["llm"]["grounding"] = True    # এই পরীক্ষাগুলো গুগল-সার্চ পথ যাচাই করে
    return cfg


CAT = {"id": "security", "name": "অনলাইন নিরাপত্তা", "accent": "#EF4444"}


class Research(unittest.TestCase):
    def test_gather_parses_bullets_and_sources(self):
        text = "- গুগল অ্যাকাউন্টে সিকিউরিটি ট্যাব আছে\n- 2-Step Verification নামে অপশন আছে\nভূমিকা লাইন বাদ"
        src = [{"title": "Google Help", "uri": "https://support.google.com/x"}]
        with mock.patch.object(llm, "complete_grounded", return_value=(text, src)):
            r = research.gather(cfg_g(), CAT, "টু-স্টেপ")
        self.assertEqual(len(r["facts"]), 2)
        self.assertEqual(r["sources"], ["support.google.com"])
        self.assertIsNone(r["rejected"])

    def test_gather_rejects_wrong_topic(self):
        with mock.patch.object(llm, "complete_grounded", return_value=("বাতিল: ফিচারটি বন্ধ হয়ে গেছে", [])):
            r = research.gather(cfg_g(), CAT, "x")
        self.assertIn("বন্ধ", r["rejected"])

    def test_gather_quota_error_is_skipped_unless_strict(self):
        err = llm.LLMError("কোটা শেষ", fatal=True, kind="quota")
        with mock.patch.object(llm, "complete_grounded", side_effect=err):
            self.assertFalse(research.gather(cfg_g(), CAT, "x")["ok"])
            strict = cfg_g()
            strict["research"]["strict"] = True
            with self.assertRaises(llm.LLMError):
                research.gather(strict, CAT, "x")

    def test_disabled_for_anthropic(self):
        cfg = cfg_g()
        cfg["llm"]["provider"] = "anthropic"
        self.assertFalse(research.enabled(cfg))

    def test_verify_applies_fixes(self):
        script = scriptgen.validate(config.load(), samples.SCRIPT)
        resp = json.dumps({"verdict": "fix", "issues": [
            {"i": 1, "problem": "ভুল নাম", "narration": "সংশোধিত বাক্য", "screen_text": "Fixed Text"}]}, ensure_ascii=False)
        with mock.patch.object(llm, "complete", return_value=resp):
            fixed, notes = research.verify(cfg_g(), script, ["তথ্য ১", "তথ্য ২"])
        self.assertEqual(fixed["scenes"][1]["narration"], "সংশোধিত বাক্য")
        self.assertEqual(fixed["scenes"][1]["screen_text"], "Fixed Text")
        self.assertEqual(script["scenes"][1]["screen_text"], samples.SCRIPT["scenes"][1]["screen_text"])  # মূলটা অক্ষত
        self.assertEqual(len(notes), 1)

    def test_verify_reject_raises(self):
        script = scriptgen.validate(config.load(), samples.SCRIPT)
        resp = json.dumps({"verdict": "reject", "issues": [{"i": 0, "problem": "ভুয়া নির্দেশনা"}]}, ensure_ascii=False)
        with mock.patch.object(llm, "complete", return_value=resp):
            with self.assertRaises(RuntimeError):
                research.verify(cfg_g(), script, [])

    def test_verify_failure_keeps_script_when_not_strict(self):
        script = scriptgen.validate(config.load(), samples.SCRIPT)
        with mock.patch.object(llm, "complete", side_effect=llm.LLMError("নেটওয়ার্ক")):
            out, notes = research.verify(cfg_g(), script, [])
        self.assertIs(out, script)
        self.assertEqual(notes, [])

    def test_grounded_request_has_search_tool_and_no_json_mode(self):
        ok = mock.Mock(status_code=200)
        ok.json.return_value = {"candidates": [{"content": {"parts": [{"text": "- a"}]},
                                                "groundingMetadata": {"groundingChunks": [{"web": {"uri": "https://a.com/x", "title": "a.com"}}]}}]}
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"}), mock.patch.object(llm.requests, "post", return_value=ok) as post:
            text, src = llm.complete_grounded(cfg_g(), "s", "u")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["tools"], [{"google_search": {}}])
        self.assertNotIn("responseMimeType", body["generationConfig"])
        self.assertEqual(src[0]["uri"], "https://a.com/x")


class ScriptGen(unittest.TestCase):
    def test_validate_keeps_highlight_and_visual_query(self):
        d = json.loads(json.dumps(samples.SCRIPT))
        d["scenes"][0]["highlight"] = "চুরি"
        d["scenes"][0]["visual_query"] = "person typing   on laptop"
        d["scenes"][1]["highlight"] = "নেই-এমন-শব্দ"
        out = scriptgen.validate(config.load(), d)
        self.assertEqual(out["scenes"][0]["highlight"], "চুরি")
        self.assertEqual(out["scenes"][0]["visual_query"], "person typing on laptop")
        self.assertEqual(out["scenes"][1]["highlight"], "")

    def test_prompt_contains_facts(self):
        p = scriptgen.build_prompt(config.load(), CAT, "বিষয়", "s", ["তথ্য-ক", "তথ্য-খ"])
        self.assertIn("তথ্য-ক", p)
        self.assertIn("visual_query", p)


class Media(unittest.TestCase):
    def test_clean_query_and_ladder(self):
        self.assertEqual(media.clean_query("ফোন! smartphone, close-up"), "smartphone close up")
        self.assertEqual(media.query_ladder("person typing on laptop", "technology"),
                         ["person typing on laptop", "person typing", "technology"])
        self.assertEqual(media.query_ladder("বাংলা", "wifi router"), ["wifi router"])

    def test_pick_video_file_closest_to_1080(self):
        files = [{"file_type": "video/mp4", "link": "a", "width": 540}, {"file_type": "video/mp4", "link": "b", "width": 1080},
                 {"file_type": "video/mp4", "link": "c", "width": 2160}, {"file_type": "video/webm", "link": "d", "width": 1080}]
        self.assertEqual(media.pick_video_file(files)["link"], "b")
        self.assertIsNone(media.pick_video_file([{"file_type": "video/mp4", "link": "a", "width": 300}]))

    def test_used_tracks_and_trims(self):
        with tempfile.TemporaryDirectory() as d:
            u = media.Used(Path(d) / "u.json")
            for i in range(320):
                u.add(f"id{i}")
            u2 = media.Used(Path(d) / "u.json")
            self.assertEqual(len(u2.ids), 300)
            self.assertTrue(u2.has("id319"))
            self.assertFalse(u2.has("id0"))

    def test_no_key_returns_none(self):
        cfg = config.load()
        with mock.patch.dict(os.environ, {"PEXELS_API_KEY": "", "PIXABAY_API_KEY": ""}):
            self.assertIsNone(media.fetch(cfg, "laptop", "tech", tempfile.gettempdir(), "t", media.Used("/tmp/none.json")))

    def test_fetch_downloads_first_unused_video(self):
        cfg = config.load()
        resp = {"videos": [{"id": 1, "duration": 10, "user": {"name": "A"}, "video_files": [{"file_type": "video/mp4", "link": "http://x/1.mp4", "width": 1080}]},
                           {"id": 2, "duration": 10, "user": {"name": "B"}, "video_files": [{"file_type": "video/mp4", "link": "http://x/2.mp4", "width": 1080}]}]}
        with tempfile.TemporaryDirectory() as d:
            used = media.Used(Path(d) / "u.json")
            used.add("pexels-v1")
            got = []

            def fake_dl(url, dest):
                got.append(url)
                Path(dest).write_bytes(b"x" * 20000)
                return True

            with mock.patch.dict(os.environ, {"PEXELS_API_KEY": "k", "PIXABAY_API_KEY": ""}), \
                 mock.patch.object(media, "_get", return_value=resp), mock.patch.object(media, "_download", fake_dl):
                m = media.fetch(cfg, "phone", "tech", d, "t", used)
            self.assertEqual(m["id"], "pexels-v2")
            self.assertEqual(got, ["http://x/2.mp4"])
            self.assertTrue(used.has("pexels-v2"))


class MoreImageSources(unittest.TestCase):
    ENV = {"PEXELS_API_KEY": "", "PIXABAY_API_KEY": "", "UNSPLASH_ACCESS_KEY": ""}

    def _dl(self, got):
        def fake_dl(url, dest):
            got.append(url)
            Path(dest).write_bytes(b"x" * 20000)
            return True
        return fake_dl

    def test_unsplash_used_when_only_its_key_exists(self):
        cfg = config.load()
        cfg["media"]["prefer"] = "photo"
        resp = {"results": [{"id": "abc", "urls": {"raw": "https://images.unsplash.com/photo-1?ixid=x"},
                             "user": {"name": "Z"}, "links": {"download_location": "https://api.unsplash.com/photos/abc/download"}}]}
        got = []
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {**self.ENV, "UNSPLASH_ACCESS_KEY": "k"}), \
             mock.patch.object(media, "_get", return_value=resp), mock.patch.object(media, "_download", self._dl(got)), \
             mock.patch.object(media.requests, "get") as track:
            m = media.fetch(cfg, "phone", "tech", d, "t", media.Used(Path(d) / "u.json"))
        self.assertEqual(m["id"], "unsplash-pabc")
        self.assertEqual(m["credit"], "Unsplash / Z")
        self.assertIn("w=1440", got[0])
        track.assert_called_once()

    def test_picsum_off_by_default_and_last_resort_when_on(self):
        cfg = config.load()
        with mock.patch.dict(os.environ, self.ENV):
            self.assertFalse(media.available(cfg))
            self.assertIsNone(media.fetch(cfg, "x", "tech", tempfile.gettempdir(), "t", media.Used("/tmp/none2.json")))
            cfg["media"]["picsum_fallback"] = True
            cfg["media"]["no_people"] = False  # মানুষমুক্ত মোডে Picsum ইচ্ছাকৃতভাবে বন্ধ (আলাদা পরীক্ষা আছে)
            self.assertTrue(media.available(cfg))
            got = []
            with tempfile.TemporaryDirectory() as d, mock.patch.object(media, "_download", self._dl(got)):
                m = media.fetch(cfg, "x", "tech", d, "t", media.Used(Path(d) / "u.json"))
        self.assertTrue(m["id"].startswith("picsum-"))
        self.assertIn("picsum.photos/seed/", got[0])


class Voice(unittest.TestCase):
    def test_edge_is_only_engine(self):
        cfg = cfg_g()
        cfg["tts"]["engine"] = "edge"
        self.assertEqual(tts.engine_chain(cfg), ["edge"])
        self.assertEqual(tts.engine_chain(cfg, "silent"), ["silent"])
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                tts.synth(cfg, "পরীক্ষা", Path(d) / "a.mp3", "gemini")

    def test_edge_engine_used_for_all_scenes(self):
        cfg = cfg_g()
        cfg["tts"]["engine"] = "edge"
        real = tts.synth
        seen = []

        def fake(cfg_, text, path, engine=None, voice=None):
            seen.append(engine)
            return real(cfg_, text, path, "silent")

        with tempfile.TemporaryDirectory() as d, mock.patch.object(tts, "synth", fake):
            paths, eng = tts.synth_all(cfg, samples.SCRIPT["scenes"][:2], Path(d))
        self.assertEqual(eng, "edge")
        self.assertEqual(seen, ["edge", "edge"])
        self.assertTrue(all(p.suffix == ".wav" for p in paths))

    def test_edge_failing_raises(self):
        cfg = cfg_g()
        cfg["tts"]["engine"] = "edge"
        with tempfile.TemporaryDirectory() as d, mock.patch.object(tts, "synth", side_effect=RuntimeError("নেই")):
            with self.assertRaises(RuntimeError):
                tts.synth_all(cfg, samples.SCRIPT["scenes"][:1], Path(d))

    def test_loudnorm_handles_silence(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = Path(d) / "s.wav", Path(d) / "o.wav"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "1", str(src)], check=True)
            audio.loudnorm(src, dst)
            self.assertAlmostEqual(audio.probe_duration(dst), 1.0, delta=0.1)


class Render(unittest.TestCase):
    def test_caption_chunks_cover_time_and_limit_words(self):
        ch = render.caption_chunks("প্রথমে ফোনে গুগল অ্যাকাউন্টের সেটিংসে যান, তারপর সিকিউরিটি ট্যাব খুলুন।", 0.2, 6.2, 4)
        self.assertTrue(all(len(c.split()) <= 4 for c, _, _ in ch))
        self.assertAlmostEqual(ch[0][1], 0.2)
        self.assertAlmostEqual(ch[-1][2], 6.2, places=2)
        for (_, _, e), (_, s, _) in zip(ch, ch[1:]):
            self.assertAlmostEqual(e, s, places=3)

    def test_display_text_replaces_arrow(self):
        self.assertEqual(render.display_text("A → B"), "A › B")

    def test_quality_ultra_sets_2k(self):
        cfg = config.load()
        cfg["video"]["quality"] = "ultra"
        render._quality(cfg)
        self.assertEqual((cfg["video"]["width"], cfg["video"]["height"]), (1440, 2560))

    def test_plan_and_smoke_render(self):
        cfg = config.load()
        cfg["video"].update(quality="custom", width=270, height=480, fps=15)
        cat = config.category(cfg, samples.CATEGORY)
        script = json.loads(json.dumps(samples.SCRIPT))
        script["scenes"] = script["scenes"][:3]
        with tempfile.TemporaryDirectory() as d:
            w = Path(d)
            voices = []
            for i, sc in enumerate(script["scenes"]):
                raw, v = w / f"a{i}.mp3", w / f"v{i}.wav"
                tts.synth(cfg, sc["narration"], raw, "silent")
                audio.voice_clean(cfg, raw, v)
                voices.append(v)
            pl = render.plan(cfg, voices)
            self.assertAlmostEqual(pl["total"], sum(pl["durs"]), places=3)
            self.assertEqual(pl["starts"][0], 0.0)
            out = w / "o.mp4"
            info = render.render_video(cfg, cat, script, voices, None, out, w / "work", seed="x")
            self.assertEqual(len(info["transitions"]), 2)
            self.assertAlmostEqual(audio.probe_duration(out), pl["total"], delta=0.3)


class SmartEdit(unittest.TestCase):
    def test_speech_segments(self):
        segs = smartedit.speech_segments([(2.0, 4.0), (6.0, 10.0)], 10.0, pad=0.1, min_keep=0.25)
        self.assertEqual(len(segs), 2)
        self.assertAlmostEqual(segs[0][0], 0.0)
        self.assertAlmostEqual(segs[0][1], 2.1)
        self.assertAlmostEqual(segs[1][0], 3.9)

    def test_speech_segments_merge_and_drop_tiny(self):
        segs = smartedit.speech_segments([(1.0, 1.05), (3.0, 3.2)], 4.0, pad=0.12, min_keep=0.25)
        self.assertTrue(all(b - a >= 0.25 for a, b in segs))
        self.assertEqual(segs[0][0], 0.0)

    def test_valid_segments_cleans_llm_output(self):
        items = [{"start": 0, "end": 2, "text": "ক"}, {"start": 1.5, "end": 3, "text": "খ"},
                 {"start": 5, "end": 4, "text": "ভুল"}, {"start": "x"}, {"start": 3.0, "end": 99, "text": "গ"}, {"start": 0, "end": 1, "text": " "}]
        out = smartedit.valid_segments(items, 6.0)
        self.assertEqual([t for _, _, t in out], ["ক", "খ", "গ"])
        self.assertGreaterEqual(out[1][0], out[0][1])
        self.assertLessEqual(out[-1][1], 6.0)
        self.assertEqual(smartedit.valid_segments("না-তালিকা", 5), [])

    def test_detect_silences_on_real_audio(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.wav"
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                            "aevalsrc='0.5*sin(2*PI*300*t)*(lt(mod(t,4),1.5))':s=44100:d=8", str(p)], check=True)
            sil, dur = smartedit.detect_silences(p, -34, 0.45)
            self.assertAlmostEqual(dur, 8.0, delta=0.1)
            self.assertEqual(len(sil), 2)
            segs = smartedit.speech_segments(sil, dur, 0.1)
            kept = sum(b - a for a, b in segs)
            self.assertLess(kept, 4.5)

    def test_no_gemini_means_no_captions_but_no_crash(self):
        cfg = config.load()
        cfg["llm"]["grounding"] = True  # এই পরীক্ষা সূত্র-নীতি নিয়ে নয়
        cfg["llm"]["provider"] = "anthropic"
        self.assertEqual(smartedit.transcribe(cfg, "x.wav", 5.0, tempfile.gettempdir()), [])


class RejectedTopics(unittest.TestCase):
    def test_runner_replaces_rejected_topic(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = config.load()
            cfg["llm"]["grounding"] = True  # এই পরীক্ষা সূত্র-নীতি নিয়ে নয়
            cfg["paths"] = {"data_dir": os.path.join(d, "data"), "out_dir": os.path.join(d, "out")}
            cfg["_root"] = d
            store = Store(os.path.join(d, "data", "queue.jsonl"))
            store.add_topics("tech", ["ফোনের ব্যাটারি বাঁচানোর সহজ উপায়", "হোয়াটসঅ্যাপে ভুল মেসেজ মুছে ফেলার নিয়ম",
                                          "পাসওয়ার্ড নিরাপদ রাখার তিনটি কৌশল", "এক্সেলে দ্রুত যোগফল বের করার শর্টকাট"])
            calls = []

            def fake_make(cfg_, store_, rec, engine=None):
                calls.append(rec["id"])
                if len(calls) == 1:
                    store_.update(rec["id"], status="rejected")
                    raise pipeline.Rejected("ভুল তথ্য")
                out = config.out_dir(cfg_)
                (out / f"{rec['id']}.mp4").write_bytes(b"x")
                store_.update(rec["id"], status="rendered", video=f"{rec['id']}.mp4")
                return out / f"{rec['id']}.mp4"

            with mock.patch.object(runner.pipeline, "make_one", fake_make), \
                 mock.patch.object(runner, "refill", return_value=[]):
                ok, bad, stopped = runner.run(cfg, store, 2, "private", None, upload=False)
            self.assertEqual((ok, bad), (2, 0))      # একটা বাতিল হলেও বিকল্প নিয়ে ২টি হয়েছে
            self.assertEqual(len(set(calls)), 3)


if __name__ == "__main__":
    unittest.main()
