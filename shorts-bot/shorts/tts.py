"""বাংলা ভয়েসওভার।

engine:
  gemini — Gemini TTS (আরও স্বাভাবিক, আবেগময় কণ্ঠ; একই GEMINI_API_KEY; ফ্রি সীমা কম, তাই fallback রাখুন)
  edge   — Microsoft Edge-এর বিনামূল্যের বাংলা ভয়েস
  silent — পরীক্ষার জন্য নীরব অডিও
একটা ভিডিওর সব দৃশ্য একই engine-এ বানানো হয় (মাঝপথে গলা বদলায় না)। প্রধান engine ব্যর্থ হলে পুরো ভিডিও fallback engine-এ আবার বানানো হয়।
"""
import asyncio
import base64
import re
import subprocess
import sys
import time

from . import audio, llm


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


def _gemini_tts(tcfg, text, path):
    key = llm._gemini_key()
    model = tcfg.get("gemini_model", "gemini-2.5-flash-preview-tts")
    style = (tcfg.get("gemini_style") or "").strip()
    prompt = f"{style}: {text}" if style else text
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": tcfg.get("gemini_voice", "Kore")}}},
        },
    }
    data = llm._post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
        {"content-type": "application/json"}, body, retries=2, timeout=120)
    try:
        part = data["candidates"][0]["content"]["parts"][0]
        blob = part.get("inlineData") or part["inline_data"]
        pcm = base64.b64decode(blob["data"])
    except (KeyError, IndexError, TypeError):
        raise llm.LLMError(f"Gemini TTS থেকে অডিও আসেনি: {llm._redact(str(data)[:200])}")
    if len(pcm) < 4000:
        raise llm.LLMError("Gemini TTS-এর অডিও খুব ছোট (খালি)")
    # Gemini TTS-এর আউটপুট: ২৪ কিলোহার্জ, ১৬-বিট, মনো PCM
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", "pipe:0",
                        "-c:a", "libmp3lame", "-b:a", "192k", str(path)], input=pcm, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"PCM → mp3 রূপান্তর ব্যর্থ: {r.stderr[-300:]!r}")


def synth(cfg, text, path, engine=None):
    tcfg = cfg["tts"]
    engine = engine or tcfg["engine"]
    text = clean(text)
    if engine == "silent":
        return _silent(text, path)
    if engine == "gemini":
        last = None
        for i in range(3):
            try:
                _gemini_tts(tcfg, text, path)
                if duration(path) > 0.3:
                    return
            except llm.LLMError as e:
                last = e
                if e.fatal:  # কী/কোটা — আবার চেষ্টা বৃথা
                    raise
            time.sleep(2 * (i + 1))
        raise RuntimeError(f"Gemini ভয়েস বানানো যায়নি: {last}")
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


_DEAD = set()   # এই রানে কী/কোটার কারণে অচল হয়ে যাওয়া engine — বারবার চেষ্টা করে সময় নষ্ট নয়


def engine_chain(cfg, engine=None):
    if engine:
        return [engine]
    t = cfg["tts"]
    chain = [t["engine"]]
    fb = t.get("fallback")
    if fb and fb != t["engine"]:
        chain.append(fb)
    return chain


def synth_all(cfg, scenes, workdir, engine=None):
    """সব দৃশ্যের ভয়েস একই engine-এ বানায় ও পরিষ্কার (clean) করে। ফেরত: (wav পাথের তালিকা, ব্যবহৃত engine)।"""
    last = None
    chain = engine_chain(cfg, engine)
    for pos, eng in enumerate(chain):
        if eng in _DEAD and pos < len(chain) - 1:
            continue
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
            if isinstance(e, llm.LLMError) and e.fatal:
                _DEAD.add(eng)
            print(f"  ⚠ ভয়েস engine '{eng}' ব্যর্থ: {e}", file=sys.stderr)
    raise RuntimeError(f"ভয়েস বানানো যায়নি: {last}")
