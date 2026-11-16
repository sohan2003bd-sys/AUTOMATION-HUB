"""প্রতিদিনের কাজ: আইডিয়া ভরা → ভিডিও বানানো → ইউটিউবে আপলোড/শিডিউল।"""
import json
import math
import sys
import time
from datetime import datetime, timezone

from . import config, ideas, pipeline, youtube


def refill(cfg, store, count):
    """বাকি আইডিয়া কমে গেলে নতুন আইডিয়া বানায় (প্রায় ২ দিনের জোগান রাখে)।"""
    left = sum(1 for r in store.rows if r["status"] in ("idea", "scripted"))
    if left >= count * 2:
        return
    cats = cfg["categories"]
    per = max(3, math.ceil(count * 5 / len(cats)))
    for c in cats:
        try:
            n = ideas.generate_for_category(cfg, store, c, per)
            print(f"আইডিয়া: {c['name']} +{n}")
        except Exception as e:
            print(f"আইডিয়া ব্যর্থ: {c['name']} — {e}", file=sys.stderr)


def _last_publish(store):
    best = datetime.now(timezone.utc)
    for r in store.rows:
        if r.get("publish_at"):
            try:
                d = datetime.strptime(r["publish_at"], "%Y-%m-%dT%H:%M:%S.000Z").replace(tzinfo=timezone.utc)
                best = max(best, d)
            except ValueError:
                pass
    return best


def run(cfg, store, count, privacy=None, engine=None, upload=True):
    """ফেরত: (সফল, ব্যর্থ, থামানো_হয়েছে)"""
    out = config.out_dir(cfg)
    mode = privacy or cfg["upload"].get("privacy", "private")
    if mode not in ("private", "public", "unlisted", "schedule"):
        raise ValueError(f"অজানা privacy: {mode}")

    if upload:
        youtube.get_access_token()  # টোকেন ভুল হলে শুরুতেই থেমে যাবে, কিছু নষ্ট হবে না

    # রেন্ডার হয়েছে কিন্তু ফাইল নেই (নতুন রানারে) → আবার বানাতে হবে
    for r in store.rows:
        if r["status"] == "rendered" and not (out / r.get("video", "x")).exists():
            r["status"] = "scripted"
    store.save()

    refill(cfg, store, count)
    todo = store.pending("scripted", count)
    if len(todo) < count:
        todo += store.pending("idea", count - len(todo))
    if not todo:
        print("কোনো আইডিয়া বাকি নেই।")
        return 0, 0, False

    slots = []
    if upload and mode == "schedule":
        slots = youtube.next_slots(cfg, len(todo), _last_publish(store))

    ok = bad = 0
    stopped = False
    for k, rec in enumerate(todo):
        print(f"→ {rec['id']} {rec['topic']}")
        try:
            video = pipeline.make_one(cfg, store, rec, engine)
        except Exception as e:
            store.update(rec["id"], error=str(e)[:300])
            print("  বানাতে ব্যর্থ:", e, file=sys.stderr)
            bad += 1
            continue
        if not upload:
            ok += 1
            continue
        meta = json.loads((out / f"{rec['id']}.json").read_text(encoding="utf-8"))
        when = slots[k] if mode == "schedule" else None
        try:
            token = youtube.get_access_token()
            res = youtube.upload_video(cfg, token, video, meta,
                                       "private" if mode == "schedule" else mode, when)
        except youtube.YouTubeError as e:
            store.update(rec["id"], status="scripted", error=str(e)[:300])
            print("  আপলোড ব্যর্থ:", e, file=sys.stderr)
            bad += 1
            if e.fatal:
                stopped = True
                break
            continue
        store.update(rec["id"], status="uploaded", youtube_id=res["id"], privacy=res["privacy"],
                     publish_at=res["publish_at"] or (youtube.iso_z(when) if when else None),
                     uploaded=int(time.time()), error=None)
        for p in (video, out / f"{rec['id']}.json"):
            p.unlink(missing_ok=True)
        ok += 1
        line = f"  আপলোড হয়েছে: https://youtube.com/shorts/{res['id']}  ({res['privacy']}"
        line += f", প্রকাশ: {res['publish_at']})" if res["publish_at"] else ")"
        print(line)
        if mode in ("public", "unlisted") and res["privacy"] == "private":
            print("  ⚠ Google ভিডিওটি Private রেখেছে — নতুন API প্রজেক্টের আপলোড অডিট ছাড়া Public হয় না।")
        if mode == "schedule" and not res["publish_at"]:
            print("  ⚠ শিডিউল সময় বসেনি — ভিডিও Private থেকে যেতে পারে (অডিট লাগতে পারে)।")
    return ok, bad, stopped
