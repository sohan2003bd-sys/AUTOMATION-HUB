"""বাংলা ভয়েসওভার।

engine:
  edge   — Microsoft Edge-এর বিনামূল্যের বাংলা ভয়েস (একমাত্র আসল ভয়েস)
  silent — পরীক্ষার জন্য নীরব অডিও
একটা ভিডিওর সব দৃশ্য একই কণ্ঠে বানানো হয় (মাঝপথে গলা বদলায় না)।
"""
import asyncio
import random
import re
import subprocess
import sys
import time

from . import audio


def clean(text: str) -> str:
    text = re.sub(r"[*_`#>]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def duration(path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


# স্ক্রিপ্টের tone অনুযায়ী গতি (%) ও পিচ (Hz) — গম্ভীর খবরে ধীর ও ভারী, উদ্দীপনায় দ্রুত
TONES = {
    "serious":   (-3, -2),
    "warm":      (0, 0),
    "energetic": (6, 2),
    "urgent":    (8, 0),
}


def _fmt(v, unit):
    return f"{int(round(v)):+d}{unit}"


def _num(s, default=0):
    m = re.search(r"[-+]?\d+", str(s or ""))
    return int(m.group()) if m else default


def pick_voice(cfg, seed="", tone="warm", index=None):
    """এই ভিডিওর কণ্ঠ বাছে। ফেরত: {"name", "gender", "rate", "pitch"}।
    index দিলে (এ পর্যন্ত বানানো ভিডিওর সংখ্যা) নারী ↔ পুরুষ পালা করে; নইলে seed অনুযায়ী এলোমেলো।"""
    t = cfg["tts"]
    rnd = random.Random(f"voice-{seed}")
    pool = [v for v in (t.get("voices") or []) if v.get("name")]
    if not pool:
        pool = [{"name": t["voice"], "gender": "any", "weight": 1}]
    if t.get("alternate_gender", True) and index is not None:
        want = "female" if index % 2 == 0 else "male"
        same = [v for v in pool if v.get("gender") == want]
        pool = same or pool
    v = rnd.choices(pool, weights=[max(0.1, float(x.get("weight", 1))) for x in pool])[0]
    dr, dp = TONES.get(tone, TONES["warm"])
    if t.get("jitter", True):
        dr += rnd.uniform(-2.5, 2.5)
        dp += rnd.uniform(-2, 2)
    # পুরুষ কণ্ঠে পিচ একটু নিচে রাখলে ভারী ও বিশ্বাসযোগ্য শোনায়
    if v.get("gender") == "male":
        dp -= 1
    rate = _num(t.get("rate", "+0%")) + dr
    pitch = _num(t.get("pitch", "+0Hz")) + dp
    return {"name": v["name"], "gender": v.get("gender", "any"),
            "rate": _fmt(max(-20, min(20, rate)), "%"), "pitch": _fmt(max(-12, min(12, pitch)), "Hz")}


def _silent(text, path):
    secs = max(2.0, len(text.split()) / 2.4)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-t", f"{secs:.2f}", "-c:a", "libmp3lame", str(path)], check=True)


async def _edge(text, voice, rate, pitch, path):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(str(path))


def synth(cfg, text, path, engine=None, voice=None):
    """voice = pick_voice() এর ফল; না দিলে config-এর ডিফল্ট কণ্ঠ।"""
    tcfg = cfg["tts"]
    engine = engine or tcfg["engine"]
    vname = (voice or {}).get("name") or tcfg["voice"]
    vrate = (voice or {}).get("rate") or tcfg.get("rate", "+0%")
    vpitch = (voice or {}).get("pitch") or tcfg.get("pitch", "+0Hz")
    text = clean(text)
    if engine == "silent":
        return _silent(text, path)
    if engine != "edge":
        raise ValueError(f"অজানা tts engine: {engine}")
    last = None
    for i in range(4):
        try:
            asyncio.run(_edge(text, vname, vrate, vpitch, path))
            if duration(path) > 0.3:
                return
        except Exception as e:  # নেটওয়ার্ক/সার্ভিস সমস্যা
            last = e
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"ভয়েস বানানো যায়নি: {last}")


def engine_chain(cfg, engine=None):
    return [engine or cfg["tts"]["engine"]]


def synth_all(cfg, scenes, workdir, engine=None, voice=None):
    """সব দৃশ্যের ভয়েস একই engine ও একই কণ্ঠে বানায় ও পরিষ্কার (clean) করে। ফেরত: (wav পাথের তালিকা, ব্যবহৃত engine)।"""
    last = None
    chain = engine_chain(cfg, engine)
    for eng in chain:
        try:
            paths = []
            for i, sc in enumerate(scenes):
                raw = workdir / f"a{i:02d}.mp3"
                synth(cfg, sc["narration"], raw, eng, voice)
                clean_p = workdir / f"v{i:02d}.wav"
                audio.voice_clean(cfg, raw, clean_p)
                paths.append(clean_p)
            return paths, eng
        except Exception as e:
            last = e
            print(f"  ⚠ ভয়েস engine '{eng}' ব্যর্থ: {e}", file=sys.stderr)
    raise RuntimeError(f"ভয়েস বানানো যায়নি: {last}")
