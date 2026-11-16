"""বাংলা ভয়েসওভার।

engine:
  edge   — Microsoft Edge-এর বিনামূল্যের বাংলা ভয়েস (একমাত্র আসল ভয়েস)
  silent — পরীক্ষার জন্য নীরব অডিও
একটা ভিডিওর সব দৃশ্য একই কণ্ঠে বানানো হয় (মাঝপথে গলা বদলায় না)।
"""
import asyncio
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


def _silent(text, path):
    secs = max(2.0, len(text.split()) / 2.4)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-t", f"{secs:.2f}", "-c:a", "libmp3lame", str(path)], check=True)


async def _edge(text, voice, rate, pitch, path):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=rate, pitch=pitch).save(str(path))


def synth(cfg, text, path, engine=None):
    tcfg = cfg["tts"]
    engine = engine or tcfg["engine"]
    text = clean(text)
    if engine == "silent":
        return _silent(text, path)
    if engine != "edge":
        raise ValueError(f"অজানা tts engine: {engine}")
    last = None
    for i in range(4):
        try:
            asyncio.run(_edge(text, tcfg["voice"], tcfg.get("rate", "+0%"), tcfg.get("pitch", "+0Hz"), path))
            if duration(path) > 0.3:
                return
        except Exception as e:  # নেটওয়ার্ক/সার্ভিস সমস্যা
            last = e
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"ভয়েস বানানো যায়নি: {last}")


def engine_chain(cfg, engine=None):
    return [engine or cfg["tts"]["engine"]]


def synth_all(cfg, scenes, workdir, engine=None):
    """সব দৃশ্যের ভয়েস একই engine-এ বানায় ও পরিষ্কার (clean) করে। ফেরত: (wav পাথের তালিকা, ব্যবহৃত engine)।"""
    last = None
    chain = engine_chain(cfg, engine)
    for eng in chain:
        try:
            paths = []
            for i, sc in enumerate(scenes):
                raw = workdir / f"a{i:02d}.mp3"
                synth(cfg, sc["narration"], raw, eng)
                clean_p = workdir / f"v{i:02d}.wav"
                audio.voice_clean(cfg, raw, clean_p)
                paths.append(clean_p)
            return paths, eng
        except Exception as e:
            last = e
            print(f"  ⚠ ভয়েস engine '{eng}' ব্যর্থ: {e}", file=sys.stderr)
    raise RuntimeError(f"ভয়েস বানানো যায়নি: {last}")
