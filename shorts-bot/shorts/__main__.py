"""ব্যবহার:
  python -m shorts demo [--tts edge|silent]      # নমুনা ভিডিও (AI কী লাগে না)
  python -m shorts ideas --per-category 20       # আইডিয়া বানাও
  python -m shorts make --count 3                # বাকি আইডিয়া থেকে ভিডিও বানাও
  python -m shorts status                        # অবস্থা দেখো
  python -m shorts auth-check                    # ইউটিউব টোকেন ঠিক আছে কিনা দেখো
  python -m shorts doctor                        # সবকিছু ঠিকমতো বসানো আছে কিনা দেখো (ffmpeg, ফন্ট, কী, ট্রানজিশন)
  python -m shorts edit ভিডিও.mp4|ফোল্ডার/ [--upload] [--privacy private] [--no-captions]
      # নিজের ভিডিও স্মার্ট এডিট: নীরবতা কাটা, ৯:১৬, ক্যাপশন, ভয়েস মাস্টারিং, টাইটেল
  python -m shorts run --count 10 [--privacy private|public|schedule]
      # আইডিয়া ভরা → ভিডিও → ইউটিউবে আপলোড (প্রতিদিন GitHub Actions এটাই চালায়)
"""
import argparse
import sys

import json
import os
import shutil
import subprocess

from . import config, ideas, media, pipeline, render, report, runner, samples, smartedit, youtube
from .store import Store


def _write_summary(ok, bad, stopped, info):
    """GitHub Actions-এর Summary পাতায় ফলাফল (সফল হলে লিংক, ব্যর্থ হলে কারণ)।"""
    icon = "✅" if ok and not stopped else "❌"
    lines = [f"## {icon} সফল {ok}, ব্যর্থ {bad}"]
    for topic, url, privacy in info["links"]:
        lines.append(f"- ✅ [{topic}]({url}) — {privacy}")
    for p in info["problems"]:
        lines.append(f"- ❌ {p}")
    report.summary("\n".join(lines))


def _doctor(cfg) -> int:
    """সবকিছু ঠিকমতো বসানো কিনা পরীক্ষা (নেটওয়ার্ক কল ছাড়া)। সমস্যা থাকলে 1 ফেরত দেয়।"""
    bad = 0

    def line(ok, msg, fatal=True):
        nonlocal bad
        print(("✅ " if ok else ("❌ " if fatal else "⚠️  ")) + msg)
        if not ok and fatal:
            bad += 1

    line(bool(shutil.which("ffmpeg") and shutil.which("ffprobe")), "FFmpeg")
    if shutil.which("ffmpeg"):
        out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        line(" xfade " in out, "FFmpeg-এ xfade ট্রানজিশন আছে")
        line(" sidechaincompress " in out and " loudnorm " in out, "FFmpeg-এ মিউজিক-ডাকিং ও লাউডনেস ফিল্টার আছে")
    try:
        line(True, "বাংলা ফন্ট: " + render.find_font(cfg))
    except FileNotFoundError as ex:
        line(False, str(ex))
    from PIL import features
    line(features.check("raqm"), "Pillow-তে Raqm (বাংলা যুক্তাক্ষর ঠিক রাখে)")
    prov = cfg["llm"]["provider"]
    keyname = "GEMINI_API_KEY" if prov == "gemini" else "ANTHROPIC_API_KEY"
    if prov == "gemini":
        from shorts import llm as _llm
        nkeys = len(_llm.gemini_keys())
        line(nkeys > 0, f"{keyname} (llm.provider: {prov}) — মোট {nkeys}টি Gemini কী পাওয়া গেছে")
    else:
        line(bool(os.environ.get(keyname, "").strip()), f"{keyname} (llm.provider: {prov})")
    if (cfg.get("research") or {}).get("enabled") and prov != "gemini":
        line(False, "research চালু কিন্তু provider gemini নয় — গবেষণা বাদ যাবে", fatal=False)
    if (cfg.get("media") or {}).get("enabled"):
        line(media.available(cfg), "PEXELS_API_KEY / PIXABAY_API_KEY / UNSPLASH_ACCESS_KEY (না থাকলেও Lorem Picsum চলবে)", fatal=False)
    for n in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"):
        line(bool(os.environ.get(n, "").strip()), n, fatal=False)
    music = audio_music_count(cfg)
    line(music > 0, f"assets/music-এ গান: {music}টি (০ হলে মিউজিক ছাড়া চলবে)", fatal=False)
    line(cfg["channel"]["handle"] not in ("", "@your_channel"), "config.yaml-এ channel.handle বসানো", fatal=False)
    print("\nসব ঠিক আছে ✅" if not bad else f"\n{bad}টি জরুরি সমস্যা ❌")
    return 1 if bad else 0


def audio_music_count(cfg) -> int:
    from pathlib import Path
    d = Path(cfg["_root"]) / (cfg.get("audio") or {}).get("music_dir", "assets/music")
    return sum(1 for p in d.glob("*") if p.suffix.lower() in {".mp3", ".wav", ".m4a", ".ogg", ".aac"}) if d.exists() else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="shorts")
    ap.add_argument("--config")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("demo")
    d.add_argument("--tts", choices=["edge", "silent"], default="silent")
    i = sub.add_parser("ideas")
    i.add_argument("--per-category", type=int, default=20)
    i.add_argument("--category")
    m = sub.add_parser("make")
    m.add_argument("--count", type=int, default=1)
    m.add_argument("--category")
    m.add_argument("--tts", choices=["edge", "silent"])
    sub.add_parser("status")
    sub.add_parser("auth-check")
    r = sub.add_parser("run")
    r.add_argument("--count", type=int, default=1)
    r.add_argument("--privacy", choices=["private", "public", "unlisted", "schedule"])
    r.add_argument("--tts", choices=["edge", "silent"])
    r.add_argument("--no-upload", action="store_true")
    e = sub.add_parser("edit")
    e.add_argument("paths", nargs="+")
    e.add_argument("--upload", action="store_true")
    e.add_argument("--privacy", choices=["private", "public", "unlisted"])
    e.add_argument("--no-captions", action="store_true")
    sub.add_parser("doctor")
    a = ap.parse_args(argv)

    cfg = config.load(a.config)
    store = Store(config.data_dir(cfg) / "queue.jsonl")

    if a.cmd == "demo":
        cat = config.category(cfg, samples.CATEGORY)
        v = pipeline.finish(cfg, cat, samples.SCRIPT, "demo", a.tts)
        print("তৈরি:", v)
    elif a.cmd == "ideas":
        cats = [config.category(cfg, a.category)] if a.category else cfg["categories"]
        for c in cats:
            try:
                n = ideas.generate_for_category(cfg, store, c, a.per_category)
                print(f"{c['name']}: নতুন {n}টি")
            except Exception as e:
                print(f"{c['name']}: ব্যর্থ — {e}", file=sys.stderr)
    elif a.cmd == "make":
        todo = store.pending("scripted", a.count, a.category)
        if len(todo) < a.count:
            todo += store.pending("idea", a.count - len(todo), a.category)
        if not todo:
            print("কোনো আইডিয়া বাকি নেই। আগে `ideas` চালান।")
        for rec in todo:
            try:
                print("→", rec["id"], rec["topic"])
                print("  তৈরি:", pipeline.make_one(cfg, store, rec, a.tts))
            except Exception as e:
                store.update(rec["id"], error=str(e)[:300])
                print("  ব্যর্থ:", e, file=sys.stderr)
    elif a.cmd == "auth-check":
        try:
            tok = youtube.get_access_token()
        except youtube.YouTubeError as e:
            print("ব্যর্থ:", e, file=sys.stderr)
            sys.exit(1)
        scope = youtube.token_scope(tok)
        print("টোকেন ঠিক আছে। স্কোপ:", scope or "(জানা গেল না)")
        if "youtube.upload" not in scope:
            print("সতর্কতা: youtube.upload অনুমতি নেই", file=sys.stderr)
            sys.exit(1)
    elif a.cmd == "run":
        info = {"problems": [], "links": []}
        try:
            ok, bad, stopped = runner.run(cfg, store, a.count, a.privacy, a.tts, not a.no_upload, info)
        except (runner.RunError, youtube.YouTubeError) as e:
            # কোনো ভিডিও হয়নি → রান লাল হবে, কারণ Summary পাতায় লেখা থাকবে
            title = "ইউটিউব সমস্যা" if isinstance(e, youtube.YouTubeError) else "ভিডিও বানানো যায়নি"
            print("থেমে গেছে:", e, file=sys.stderr)
            report.error(title, str(e))
            report.summary(f"## ❌ {title}\n\n{e}")
            sys.exit(1)
        if info.get("limit_reached"):
            report.summary("## ✅ এই মাসের ভিডিওর সীমা পূর্ণ\n\nআগামী মাসে আবার শুরু হবে।")
            return
        print(f"সারসংক্ষেপ: সফল {ok}, ব্যর্থ {bad}")
        _write_summary(ok, bad, stopped, info)
        if stopped or ok == 0:
            first = info["problems"][0] if info["problems"] else "কোনো ভিডিও তৈরি বা আপলোড হয়নি"
            report.error("ভিডিও হয়নি" if ok == 0 else "মাঝপথে থেমে গেছে", first)
            sys.exit(1)
        if bad:
            report.warning(f"{bad}টি ভিডিও হয়নি", info["problems"][0])
    elif a.cmd == "doctor":
        sys.exit(_doctor(cfg))
    elif a.cmd == "edit":
        files = smartedit.collect(a.paths)
        if not files:
            print("কোনো ভিডিও ফাইল পাওয়া যায়নি", file=sys.stderr)
            sys.exit(1)
        done = 0
        for f in files:
            print("→", f)
            try:
                out, meta = smartedit.edit_one(cfg, f, config.out_dir(cfg), False if a.no_captions else None)
            except Exception as ex:
                print("  এডিট ব্যর্থ:", ex, file=sys.stderr)
                continue
            print("  তৈরি:", out, "|", meta["title"])
            done += 1
            if a.upload:
                try:
                    mode = a.privacy or cfg["upload"].get("privacy", "private")
                    res = youtube.upload_video(cfg, youtube.get_access_token(), out, meta,
                                               "private" if mode == "schedule" else mode, None)
                    print(f"  আপলোড হয়েছে: https://youtube.com/shorts/{res['id']} ({res['privacy']})")
                except youtube.YouTubeError as ex:
                    print("  আপলোড ব্যর্থ:", ex, file=sys.stderr)
        if done == 0:
            sys.exit(1)
    elif a.cmd == "status":
        for c in cfg["categories"]:
            print(f"{c['name']:<24}", store.counts().get(c["id"], {}))


if __name__ == "__main__":
    main()
