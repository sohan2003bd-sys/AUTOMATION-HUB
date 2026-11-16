"""ব্যবহার:
  python -m shorts demo [--tts edge|silent]      # নমুনা ভিডিও (AI কী লাগে না)
  python -m shorts ideas --per-category 20       # আইডিয়া বানাও
  python -m shorts make --count 3                # বাকি আইডিয়া থেকে ভিডিও বানাও
  python -m shorts status                        # অবস্থা দেখো
  python -m shorts auth-check                    # ইউটিউব টোকেন ঠিক আছে কিনা দেখো
  python -m shorts run --count 10 [--privacy private|public|schedule]
      # আইডিয়া ভরা → ভিডিও → ইউটিউবে আপলোড (প্রতিদিন GitHub Actions এটাই চালায়)
"""
import argparse
import sys

from . import config, ideas, pipeline, runner, samples, youtube
from .store import Store


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
        try:
            ok, bad, stopped = runner.run(cfg, store, a.count, a.privacy, a.tts, not a.no_upload)
        except youtube.YouTubeError as e:
            print("থেমে গেছে:", e, file=sys.stderr)
            sys.exit(1)
        print(f"সারসংক্ষেপ: সফল {ok}, ব্যর্থ {bad}")
        if stopped or (bad and not ok):
            sys.exit(1)
    elif a.cmd == "status":
        for c in cfg["categories"]:
            print(f"{c['name']:<24}", store.counts().get(c["id"], {}))


if __name__ == "__main__":
    main()
