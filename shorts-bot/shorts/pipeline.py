"""একটা আইডিয়ার পুরো পথ: স্ক্রিপ্ট → ভয়েস → ভিডিও।"""
import json

from . import config, render, scriptgen, tts


def finish(cfg, cat, script, vid_id, engine=None):
    out = config.out_dir(cfg)
    work = config.data_dir(cfg) / "tmp" / vid_id
    work.mkdir(parents=True, exist_ok=True)
    audio = []
    for i, sc in enumerate(script["scenes"]):
        p = work / f"a{i:02d}.mp3"
        tts.synth(cfg, sc["narration"], p, engine)
        audio.append(p)
    video = out / f"{vid_id}.mp4"
    render.render_video(cfg, cat, script, audio, video, work)
    desc = script["description"].rstrip() + "\n\n#Shorts"
    meta = {"id": vid_id, "category": cat["id"], "title": script["title"], "description": desc,
            "tags": script["tags"], "video": video.name}
    (out / f"{vid_id}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    render.cleanup(work)
    return video


def make_one(cfg, store, rec, engine=None):
    cat = config.category(cfg, rec["category"])
    sdir = config.data_dir(cfg) / "scripts"
    sdir.mkdir(parents=True, exist_ok=True)
    spath = sdir / f"{rec['id']}.json"
    if rec["status"] == "idea" or not spath.exists():
        script = scriptgen.generate(cfg, cat, rec)
        spath.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
        store.update(rec["id"], status="scripted", title=script["title"])
    else:
        script = json.loads(spath.read_text(encoding="utf-8"))
    video = finish(cfg, cat, script, rec["id"], engine)
    store.update(rec["id"], status="rendered", video=video.name)
    return video
