"""বাংলা ভয়েসওভার। edge: Microsoft Edge-এর বিনামূল্যের বাংলা ভয়েস; silent: পরীক্ষার জন্য নীরব অডিও।"""
import asyncio
import re
import subprocess
import time


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


async def _edge(text, voice, rate, path):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=rate).save(str(path))


def synth(cfg, text, path, engine=None):
    engine = engine or cfg["tts"]["engine"]
    text = clean(text)
    if engine == "silent":
        return _silent(text, path)
    if engine != "edge":
        raise ValueError(f"অজানা tts engine: {engine}")
    last = None
    for i in range(4):
        try:
            asyncio.run(_edge(text, cfg["tts"]["voice"], cfg["tts"].get("rate", "+0%"), path))
            if duration(path) > 0.3:
                return
        except Exception as e:  # নেটওয়ার্ক/সার্ভিস সমস্যা
            last = e
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"ভয়েস বানানো যায়নি: {last}")
