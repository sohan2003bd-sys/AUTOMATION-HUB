"""একটা আইডিয়ার পুরো পথ: গবেষণা → স্ক্রিপ্ট → ফ্যাক্ট-চেক → ভয়েস → আসল ফুটেজ → ভিডিও (অ্যানিমেশন, মিউজিক, মাস্টারিং)।"""
import json

from . import audio, config, media, render, research, scriptgen, tts

DEFAULT_STOCK = {
    "trending": "news city people", "health": "doctor hospital", "money": "money bank market",
    "scam": "phone call worried", "law": "court law office", "science": "science space nature",
    "tech": "technology smartphone", "career": "students office interview", "world": "world city skyline",
    "life": "family home lifestyle",
}


class Rejected(RuntimeError):
    """ফ্যাক্ট-চেকে বিষয়/স্ক্রিপ্ট বাতিল হয়েছে (ভুল বা পুরনো তথ্য)।"""


def fetch_media(cfg, cat, script, work, vid_id):
    """প্রতিটি দৃশ্যের জন্য আসল ফুটেজ/ছবি। কী না থাকলে বা না মিললে সেই দৃশ্যে None (ডিজাইন-ব্যাকগ্রাউন্ড চলবে)।"""
    if not (cfg.get("media") or {}).get("enabled") or not media.available(cfg):
        return None
    used = media.Used(config.data_dir(cfg) / "media_used.json")
    fallback = cat.get("stock") or DEFAULT_STOCK.get(cat["id"], "technology")
    items = []
    for i, sc in enumerate(script["scenes"]):
        try:
            items.append(media.fetch(cfg, sc.get("visual_query", ""), fallback, work, f"{vid_id}-{i}", used))
        except Exception as e:  # মিডিয়ায় সমস্যা হলে ভিডিও আটকাবে না
            print(f"  ⚠ দৃশ্য {i + 1}-এর ফুটেজ পাওয়া যায়নি: {e}")
            items.append(None)
    got = sum(1 for m in items if m)
    print(f"  ফুটেজ/ছবি: {got}/{len(items)} দৃশ্যে")
    return items


def finish(cfg, cat, script, vid_id, engine=None, voice_index=None):
    out = config.out_dir(cfg)
    work = config.data_dir(cfg) / "tmp" / vid_id
    work.mkdir(parents=True, exist_ok=True)
    voice = tts.pick_voice(cfg, vid_id, script.get("tone", "warm"), voice_index)
    voices, eng = tts.synth_all(cfg, script["scenes"], work, engine, voice)
    print(f"  ভয়েস: {eng} / {voice['name']} ({voice['gender']}, গতি {voice['rate']}, পিচ {voice['pitch']})")
    medias = fetch_media(cfg, cat, script, work, vid_id)
    music = audio.find_music(cfg, vid_id)
    video = out / f"{vid_id}.mp4"
    info = render.render_video(cfg, cat, script, voices, medias, video, work, seed=vid_id, music=music)
    desc = script["description"].rstrip()
    src = research.source_line(script.get("sources"))
    if src:
        desc += "\n\n" + src
    desc += "\n\n#Shorts"
    credits = sorted({m["credit"] for m in (medias or []) if m and m.get("credit")})
    meta = {"id": vid_id, "category": cat["id"], "title": script["title"], "description": desc,
            "tags": script["tags"], "video": video.name, "voice": eng, "voice_name": voice["name"],
            "voice_gender": voice["gender"], "look": info.get("look"), "credits": credits,
            "seconds": round(info["total"], 1)}
    (out / f"{vid_id}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    render.cleanup(work)
    return video


def make_script(cfg, store, cat, rec, spath):
    """গবেষণা → স্ক্রিপ্ট → ফ্যাক্ট-চেক। বাতিল হলে Rejected।"""
    info = research.gather(cfg, cat, rec["topic"])
    if info["rejected"]:
        store.update(rec["id"], status="rejected", error=("ফ্যাক্ট-চেক: " + info["rejected"])[:300])
        raise Rejected(f"বিষয় বাতিল (যাচাইয়ে টেকেনি): {info['rejected']}")
    if research.enabled(cfg) and not info["facts"] and not cat.get("evergreen", True):
        # আজকের-খবরভিত্তিক বিষয়ে কোনো সূত্র মেলেনি — মডেলের আন্দাজে ভিডিও বানানো ঝুঁকিপূর্ণ
        msg = "কোনো সূত্র/শিরোনাম মেলেনি, তাই আজকের খবরভিত্তিক এই বিষয় বাদ"
        store.update(rec["id"], status="rejected", error=msg)
        raise Rejected(msg)
    made = sum(1 for r in store.rows if r["status"] in ("rendered", "uploaded"))
    script = scriptgen.generate(cfg, cat, rec, info["facts"], variant=made)
    try:
        script, notes = research.verify(cfg, script, info["facts"])
    except RuntimeError as e:
        store.update(rec["id"], status="rejected", error=str(e)[:300])
        raise Rejected(str(e))
    script["sources"] = info["sources"]
    script["facts"] = info["facts"]
    script["fact_check"] = notes
    if notes:
        print("  ফ্যাক্ট-চেকে ঠিক করা হয়েছে:", "; ".join(notes))
    spath.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    store.update(rec["id"], status="scripted", title=script["title"])
    return script


def make_one(cfg, store, rec, engine=None):
    cat = config.category(cfg, rec["category"])
    sdir = config.data_dir(cfg) / "scripts"
    sdir.mkdir(parents=True, exist_ok=True)
    spath = sdir / f"{rec['id']}.json"
    if rec["status"] == "idea" or not spath.exists():
        script = make_script(cfg, store, cat, rec, spath)
    else:
        script = json.loads(spath.read_text(encoding="utf-8"))
    # এ পর্যন্ত যতগুলো ভিডিও বানানো হয়েছে — তার জোড়-বিজোড় দিয়ে নারী/পুরুষ কণ্ঠ পালা করে
    made = sum(1 for r in store.rows if r["status"] in ("rendered", "uploaded"))
    video = finish(cfg, cat, script, rec["id"], engine, made)
    store.update(rec["id"], status="rendered", video=video.name)
    return video
