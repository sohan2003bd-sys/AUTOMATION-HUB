"""প্রতিদিনের কাজ: আইডিয়া ভরা → ভিডিও বানানো → ইউটিউবে আপলোড/শিডিউল।"""
import json
import math
import os
import sys
import time
from datetime import datetime, timezone

from . import config, ideas, llm, pipeline, report, youtube


class RunError(RuntimeError):
    """কোনো ভিডিও বানানোই সম্ভব হয়নি — মেসেজে কারণ ও সমাধান থাকে।"""


def refill(cfg, store, count, budget_sec=300):
    """আইডিয়া ভরে।
    (১) টাটকা-থাকা-দরকার ক্যাটাগরিতে (evergreen: false) গত ২০ ঘণ্টার অন্তত count×২টি আইডিয়া না থাকলে আজকের আলোচিত
        বিষয় থেকে নতুন আইডিয়া (যে ক্যাটাগরিতে টাটকা আইডিয়া কম ও কম ভিডিও হয়েছে সেটা আগে);
    (২) তারপরও মোট বাকি আইডিয়া কমে গেলে বাকি ক্যাটাগরি থেকে ভরা।
    যথেষ্ট জমলে থামে; AI ধীর হলে budget_sec-এর পর ছেড়ে দেয়। ফেরত: ত্রুটির তালিকা (কিছু ব্যর্থ হলে)।"""
    errors = []
    ic = cfg.get("ideas") or {}
    now = time.time()
    live = ("idea", "scripted")
    left = sum(1 for r in store.rows if r["status"] in live)
    t0 = time.time()
    done_ids = set()
    state = {"left": left, "stop": False}

    def run_job(c, n):
        if time.time() - t0 > budget_sec:
            errors.append(f"আইডিয়া বানাতে {budget_sec} সেকেন্ডের বেশি লাগছে, বাকিগুলো ছেড়ে দিলাম")
            state["stop"] = True
            return 0
        try:
            added = ideas.generate_for_category(cfg, store, c, n)
        except Exception as e:
            print(f"আইডিয়া ব্যর্থ: {c['name']} — {e}", file=sys.stderr)
            errors.append(str(e))
            if isinstance(e, llm.LLMError) and e.fatal:
                state["stop"] = True  # কী/কোটা/মডেলের সমস্যা — বাকি ক্যাটাগরিতেও একই হবে, সময় নষ্ট নয়
            return 0
        state["left"] += added
        done_ids.add(c["id"])
        print(f"আইডিয়া: {c['name']} +{added}")
        return added

    if ic.get("trending"):
        news_cats = [c for c in cfg["categories"] if not c.get("evergreen", True)]

        def fresh_n(c):
            return sum(1 for r in store.rows if r["category"] == c["id"] and r["status"] in live
                       and now - r.get("created", 0) < 40 * 3600)

        # বাংলাদেশি ক্যালেন্ডার-দিনে সর্বোচ্চ নির্দিষ্ট সংখ্যক সংবাদ-ভিডিও; বাকি ভিডিও evergreen ক্যাটাগরি থেকে।
        from datetime import timedelta
        offset = timedelta(hours=(cfg.get("upload") or {}).get("timezone_offset_hours", 6))
        local_today = (datetime.now(timezone.utc) + offset).date()
        news_ids = {c["id"] for c in news_cats}
        made_news_today = 0
        for r in store.rows:
            if r.get("category") in news_ids and r.get("created"):
                created_day = (datetime.fromtimestamp(r["created"], timezone.utc) + offset).date()
                if created_day == local_today:
                    made_news_today += 1
        daily_cap = max(0, int(ic.get("daily_news_max", 2)))
        need = min(count * 2, max(0, daily_cap - made_news_today))
        have = sum(fresh_n(c) for c in news_cats)
        done_n = {}
        for r in store.rows:
            if r["status"] in ("rendered", "uploaded"):
                done_n[r["category"]] = done_n.get(r["category"], 0) + 1
        for c in sorted(news_cats, key=lambda c: (fresh_n(c), done_n.get(c["id"], 0))):
            if have >= need or state["stop"]:
                break
            want = min(need - have, max(1, count * 2 - state["left"]))
            have += run_job(c, want)
    if state["stop"] or state["left"] >= count * 2:
        return errors
    target = count * 5
    cats = sorted((c for c in cfg["categories"] if c["id"] not in done_ids),
                  key=lambda c: len(store.topics(c["id"])))  # কম আইডিয়াওয়ালা আগে
    per = max(2, math.ceil(target / max(1, len(cfg["categories"]))))
    for c in cats:
        if state["left"] >= target or state["stop"]:
            break
        run_job(c, per)
    return errors


def monthly_remaining(cfg, store, now=None):
    """এই মাসে (বাংলাদেশ সময়ে) আর কয়টি ভিডিও আপলোড করা যাবে। সীমা না থাকলে None।"""
    cap = int((cfg.get("limits") or {}).get("monthly_max", 0) or 0)
    if cap <= 0:
        return None
    from datetime import timedelta
    off = timedelta(hours=cfg.get("upload", {}).get("timezone_offset_hours", 6))
    now = now or datetime.now(timezone.utc)
    cur = (now + off).strftime("%Y-%m")
    used = sum(1 for r in store.rows if r.get("uploaded")
               and (datetime.fromtimestamp(r["uploaded"], timezone.utc) + off).strftime("%Y-%m") == cur)
    return max(0, cap - used)


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
    if upload:
        rem = monthly_remaining(cfg, store)
        if rem is not None:
            if rem <= 0:
                print("এই মাসের সীমা (limits.monthly_max) পূর্ণ — আজ কোনো ভিডিও বানানো হবে না।")
                info["limit_reached"] = True
                return 0, 0, False
            count = min(count, rem)
    problems = info.setdefault("problems", [])
    links = info.setdefault("links", [])
    out = config.out_dir(cfg)
    mode = privacy or cfg["upload"].get("privacy", "private")
    if mode not in ("private", "public", "unlisted", "schedule"):
        raise ValueError(f"অজানা privacy: {mode}")

    # GitHub Actions-এর key slot (১–১০) প্রতিটি নির্ধারিত দৈনিক ভিডিওকে আলাদা Gemini key দেয়।
    # হাতে চালালে বর্তমান UTC ২-ঘণ্টার ব্লক থেকে slot নির্ধারিত হয়।
    base_key_index = None
    if cfg["llm"].get("provider") == "gemini":
        keys = llm.gemini_keys()
        slot_env = os.environ.get("GEMINI_KEY_SLOT", "").strip()
        # GitHub Actions-এর নির্ধারিত ১০-slot দৈনিক রান হলে ১০টি স্বতন্ত্র secret বাধ্যতামূলক।
        # লোকাল ডেভেলপমেন্টে (slot env না থাকলে) এক/একাধিক key দিয়ে পরীক্ষা করা যায়।
        if slot_env and len(keys) != 10:
            raise RunError(
                f"দৈনিক ১০টি আলাদা ভিডিওর জন্য ১০টি স্বতন্ত্র Gemini key দরকার; এখন পাওয়া গেছে {len(keys)}টি। "
                "GitHub Secrets-এ GEMINI_API_KEY এবং GEMINI_API_KEY_2 থেকে GEMINI_API_KEY_10 পর্যন্ত ১০টি আলাদা key যোগ করুন।"
            )
        if keys:
            try:
                slot = int(slot_env)
            except ValueError:
                slot = 0
            if not 1 <= slot <= 10:
                slot = (datetime.now(timezone.utc).hour // 2) % 10 + 1
            base_key_index = llm.pin_gemini_key(slot - 1)
            print(f"🔑 আজকের ভিডিও slot {slot}: Gemini key #{base_key_index + 1} (এই ভিডিওতে একই key থাকবে)")

    if upload:
        youtube.get_access_token()  # টোকেন ভুল হলে শুরুতেই থেমে যাবে, কিছু নষ্ট হবে না

    # রেন্ডার হয়েছে কিন্তু ফাইল নেই (নতুন রানারে) → আবার বানাতে হবে
    for r in store.rows:
        if r["status"] == "rendered" and not (out / r.get("video", "x")).exists():
            r["status"] = "scripted"
    store.save()

    ic = cfg.get("ideas") or {}
    gone = store.retire({c["id"] for c in cfg["categories"]},
                        {c["id"] for c in cfg["categories"] if c.get("evergreen", True)},
                        int(ic.get("max_age_days", 0) or 0))
    if gone:
        print(f"পুরনো/বাদ-পড়া ক্যাটাগরির {gone}টি আইডিয়া সরানো হয়েছে")
    if cfg["llm"]["provider"] == "gemini" and not cfg["llm"].get("grounding", True) \
            and (cfg.get("research") or {}).get("enabled"):
        ever = {c["id"] for c in cfg["categories"] if c.get("evergreen", True)}
        old = store.expire_unsourced(ever)
        if old:
            print(f"সূত্র-ছাড়া {old}টি খবরভিত্তিক পুরনো আইডিয়া বাদ দেওয়া হলো (নতুনগুলো সূত্রসহ আসবে)")
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
    spare = 1                       # ফ্যাক্ট-চেকে বাতিল হলে সর্বোচ্চ এতগুলো বিকল্প আইডিয়া নেওয়া হবে
    k = -1
    video_key_offset = 0
    while queue:
        rec = queue.pop(0)
        if base_key_index is not None:
            current_key = llm.pin_gemini_key(base_key_index + video_key_offset)
            print(f"  🔑 এই ভিডিওর Gemini key #{current_key + 1}")
        video_key_offset += 1
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
    llm.unpin_gemini_key()
    return ok, bad, stopped
