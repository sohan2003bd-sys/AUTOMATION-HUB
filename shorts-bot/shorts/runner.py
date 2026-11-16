"""প্রতিদিনের কাজ: আইডিয়া ভরা → ভিডিও বানানো → ইউটিউবে আপলোড/শিডিউল।"""
import json
import math
import sys
import time
from datetime import datetime, timezone

from . import config, ideas, llm, pipeline, report, youtube


class RunError(RuntimeError):
    """কোনো ভিডিও বানানোই সম্ভব হয়নি — মেসেজে কারণ ও সমাধান থাকে।"""


def refill(cfg, store, count, budget_sec=300):
    """বাকি আইডিয়া কমে গেলে নতুন আইডিয়া বানায়।
    যথেষ্ট জমলে থামে (পরীক্ষার রানে ১-৩টি কলই লাগে), AI ধীর হলে budget_sec-এর পর ছেড়ে দেয়।
    ফেরত: ত্রুটির তালিকা (কিছু ব্যর্থ হলে)।"""
    errors = []
    left = sum(1 for r in store.rows if r["status"] in ("idea", "scripted"))
    if left >= count * 2:
        return errors
    target = count * 5
    cats = sorted(cfg["categories"], key=lambda c: len(store.topics(c["id"])))  # কম আইডিয়াওয়ালা আগে
    per = max(2, math.ceil(target / len(cats)))
    t0 = time.time()
    for c in cats:
        if left >= target:
            break
        if time.time() - t0 > budget_sec:
            errors.append(f"আইডিয়া বানাতে {budget_sec} সেকেন্ডের বেশি লাগছে, বাকিগুলো ছেড়ে দিলাম")
            break
        try:
            n = ideas.generate_for_category(cfg, store, c, per)
            left += n
            print(f"আইডিয়া: {c['name']} +{n}")
        except Exception as e:
            print(f"আইডিয়া ব্যর্থ: {c['name']} — {e}", file=sys.stderr)
            errors.append(str(e))
            if isinstance(e, llm.LLMError) and e.fatal:
                break  # কী/কোটা/মডেলের সমস্যা — বাকি ক্যাটাগরিতেও একই হবে, সময় নষ্ট নয়
    return errors


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


def run(cfg, store, count, privacy=None, engine=None, upload=True, info=None):
    """ফেরত: (সফল, ব্যর্থ, থামানো_হয়েছে)
    কোনো ভিডিও বানানোর সুযোগই না থাকলে RunError ছোড়ে (যাতে রান সবুজ না হয়)।
    info দিলে (dict) তাতে "problems" ও "links" তালিকা ভরা হয়।"""
    info = info if info is not None else {}
    problems = info.setdefault("problems", [])
    links = info.setdefault("links", [])
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

    refill_errors = refill(cfg, store, count)
    todo = store.pending("scripted", count)
    if len(todo) < count:
        todo += store.pending("idea", count - len(todo))
    if not todo:
        if refill_errors:
            raise RunError("নতুন ভিডিওর আইডিয়া বানানো যায়নি, তাই কোনো ভিডিও হয়নি। কারণ: " + refill_errors[0])
        raise RunError("কোনো ভিডিওর আইডিয়া বাকি নেই এবং AI নতুন আইডিয়াও দেয়নি। আবার চালিয়ে দেখুন।")
    if refill_errors:
        report.warning("নতুন আইডিয়া বানানো যায়নি", refill_errors[0] + " (আগের জমানো আইডিয়া দিয়ে চলছে)")

    slots = []
    if upload and mode == "schedule":
        slots = youtube.next_slots(cfg, len(todo) + 3, _last_publish(store))

    ok = bad = 0
    stopped = False
    queue = list(todo)
    tried = {r["id"] for r in todo}
    spare = 3                       # ফ্যাক্ট-চেকে বাতিল হলে সর্বোচ্চ এতগুলো বিকল্প আইডিয়া নেওয়া হবে
    k = -1
    while queue:
        rec = queue.pop(0)
        print(f"→ {rec['id']} {rec['topic']}")
        try:
            video = pipeline.make_one(cfg, store, rec, engine)
        except pipeline.Rejected as e:
            print("  বাতিল:", e, file=sys.stderr)
            problems.append(f"বাতিল (ফ্যাক্ট-চেক) — {rec['topic']}: {e}")
            if spare > 0:
                spare -= 1
                extra = [r for r in store.pending("scripted", 30) + store.pending("idea", 30) if r["id"] not in tried]
                if extra:
                    tried.add(extra[0]["id"])
                    queue.append(extra[0])
            continue
        except Exception as e:
            store.update(rec["id"], error=str(e)[:300])
            print("  বানাতে ব্যর্থ:", e, file=sys.stderr)
            problems.append(f"বানাতে ব্যর্থ — {rec['topic']}: {e}")
            bad += 1
            continue
        k += 1
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
            problems.append(f"আপলোড ব্যর্থ — {rec['topic']}: {e}")
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
        links.append((rec["topic"], f"https://youtube.com/shorts/{res['id']}", res["privacy"]))
        line = f"  আপলোড হয়েছে: https://youtube.com/shorts/{res['id']}  ({res['privacy']}"
        line += f", প্রকাশ: {res['publish_at']})" if res["publish_at"] else ")"
        print(line)
        if mode in ("public", "unlisted") and res["privacy"] == "private":
            print("  ⚠ Google ভিডিওটি Private রেখেছে — নতুন API প্রজেক্টের আপলোড অডিট ছাড়া Public হয় না।")
        if mode == "schedule" and not res["publish_at"]:
            print("  ⚠ শিডিউল সময় বসেনি — ভিডিও Private থেকে যেতে পারে (অডিট লাগতে পারে)।")
    return ok, bad, stopped
