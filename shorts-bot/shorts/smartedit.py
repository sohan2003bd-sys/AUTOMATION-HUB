"""নিজের ভিডিও স্মার্ট এডিট: নীরবতা কাটা → ৯:১৬ রিফ্রেম → ক্যাপশন (Gemini) → ভয়েস পরিষ্কার ও লাউডনেস → টাইটেল/বিবরণ।

ব্যবহার:  python -m shorts edit আমার_ভিডিও.mp4 [--upload] [--privacy private]
          python -m shorts edit inbox/            # ফোল্ডারের সব ভিডিও

ক্যাপশনের সময় Gemini অডিও শুনে আন্দাজ করে (সেকেন্ড-পর্যায়ে নির্ভুল না-ও হতে পারে); আপলোডের আগে একবার দেখে নিন।
"""
import json
import re
import subprocess
import sys
from pathlib import Path

from . import audio, config, llm, render
from .llm import extract_json

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}


def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg ব্যর্থ: {r.stderr[-700:]}")
    return r


def has_audio(path) -> bool:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip()
    return bool(out)


def detect_silences(path, db=-34, min_silence=0.45):
    dur = audio.probe_duration(path)
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
                        f"silencedetect=noise={db}dB:d={min_silence}", "-f", "null", "-"],
                       capture_output=True, text=True)
    starts = [max(0.0, float(x)) for x in re.findall(r"silence_start: (-?[\d.]+)", r.stderr)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    ends += [dur] * (len(starts) - len(ends))        # শেষ পর্যন্ত নীরব থাকলে
    return list(zip(starts, ends)), dur


def speech_segments(silences, dur, pad=0.12, min_keep=0.25):
    """নীরবতার ফাঁক বাদ দিয়ে 'কথার অংশ'। আগে-পরে pad রেখে, কাছাকাছি অংশ জুড়ে, ছোট টুকরো বাদ।"""
    segs, cur = [], 0.0
    for s, e in silences:
        if s - cur > 0.02:
            segs.append((cur, s))
        cur = max(cur, e)
    if dur - cur > 0.05:
        segs.append((cur, dur))
    merged = []
    for a, b in ((max(0.0, a - pad), min(dur, b + pad)) for a, b in segs):
        if merged and a <= merged[-1][1] + 0.02:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return [(a, b) for a, b in merged if b - a >= min_keep]


def cut_and_reframe(cfg, src, segs, dst, W, H, has_aud, mode="blur"):
    fps = cfg["video"]["fps"]
    sel = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in segs) if segs else "1"
    g = [f"[0:v]select='{sel}',setpts=N/FRAME_RATE/TB,fps={fps},setsar=1[v0]"]
    if mode == "crop":
        g.append(f"[v0]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}[v]")
    else:
        g.append("[v0]split=2[p][q]")
        g.append(f"[p]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=40:8,eq=brightness=-0.07[bg]")
        g.append(f"[q]scale={W}:{H}:force_original_aspect_ratio=decrease[fg]")
        g.append("[bg][fg]overlay=(W-w)/2:(H-h)/2[v]")
    maps = ["-map", "[v]"]
    if has_aud:
        g.append(f"[0:a]aselect='{sel}',asetpts=N/SR/TB[a]")
        maps += ["-map", "[a]"]
    _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-filter_complex", ";".join(g), *maps,
          "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p",
          *(["-c:a", "pcm_s16le", "-ar", "44100"] if has_aud else ["-an"]), str(dst)])
    return dst


TRANSCRIBE_PROMPT = (
    "এই অডিওতে যা বলা হয়েছে হুবহু লেখো (বাংলা কথা বাংলা অক্ষরে, ইংরেজি শব্দ ইংরেজিতে)। অডিওর সময় ধরে সেগমেন্টে ভাগ করো, "
    "প্রতিটি সেগমেন্ট সর্বোচ্চ ৫ সেকেন্ড। start ও end সেকেন্ডে (দশমিকসহ), অডিওর শুরু থেকে গোনা। "
    "কোনো কথা না থাকলে খালি তালিকা দাও। ফরম্যাট: "
    '[{"start": 0.0, "end": 2.4, "text": "..."}]'
)


def valid_segments(items, dur):
    """Gemini-র ফেরত দেওয়া ক্যাপশন-সেগমেন্ট ঠিকঠাক করে: সময় সীমার ভেতরে, ক্রমানুসারে, ওভারল্যাপ ছাড়া।"""
    out = []
    for it in items if isinstance(items, list) else []:
        try:
            a, b, t = float(it["start"]), float(it["end"]), str(it["text"]).strip()
        except (KeyError, TypeError, ValueError):
            continue
        if not t or b <= a:
            continue
        a, b = max(0.0, a), min(dur, b)
        if out:
            a = max(a, out[-1][1])
        if b - a >= 0.2:
            out.append((a, b, t))
    return out


def transcribe(cfg, video_wav, dur, workdir):
    """কাটা ভিডিওর অডিও → [(শুরু, শেষ, লেখা)]। Gemini ছাড়া বা ব্যর্থ হলে খালি তালিকা (ভিডিও আটকায় না)।"""
    if cfg["llm"]["provider"] != "gemini":
        print("  ⚠ ক্যাপশনের জন্য llm.provider: gemini লাগবে — ক্যাপশন ছাড়াই এডিট হচ্ছে", file=sys.stderr)
        return []
    mp3 = Path(workdir) / "speech.mp3"
    _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video_wav), "-ac", "1", "-ar", "16000", "-b:a", "48k", str(mp3)])
    if mp3.stat().st_size > 14 * 1024 * 1024:
        print("  ⚠ অডিও অনেক বড়, ক্যাপশন বাদ", file=sys.stderr)
        return []
    try:
        raw = llm.transcribe_audio(cfg, mp3, TRANSCRIBE_PROMPT, max_tokens=6000)
        return valid_segments(extract_json(raw), dur)
    except (llm.LLMError, ValueError) as e:
        print(f"  ⚠ ক্যাপশন বানানো যায়নি ({e})", file=sys.stderr)
        return []


def make_meta(cfg, name, transcript):
    """ট্রান্সক্রিপ্ট থেকে টাইটেল/বিবরণ/ট্যাগ। ব্যর্থ হলে ফাইলের নাম থেকে।"""
    fallback = {"title": name[:90], "description": "", "tags": []}
    if not transcript.strip():
        return fallback
    system = "তুমি বাংলা YouTube Shorts-এর টাইটেল ও বিবরণ লেখক। কেবল JSON অবজেক্ট দেবে।"
    user = (f"ভিডিওতে বলা কথা:\n{transcript[:3500]}\n\n"
            "এর জন্য: title (৬০ অক্ষরের মধ্যে, আকর্ষণীয় কিন্তু ক্লিকবেইট নয়), description (২–৩ লাইন), tags (৮–১০টি)। "
            "কথায় যা নেই তা যোগ করবে না। "
            'ফরম্যাট: {"title": "...", "description": "...", "tags": ["..."]}')
    try:
        d = extract_json(llm.complete(cfg, system, user, max_tokens=1200, temperature=0.6))
        if isinstance(d, dict) and str(d.get("title", "")).strip():
            return {"title": str(d["title"]).strip()[:100], "description": str(d.get("description", "")).strip(),
                    "tags": [str(t).lstrip("#") for t in d.get("tags", []) if str(t).strip()][:15]}
    except (llm.LLMError, ValueError):
        pass
    return fallback


def edit_one(cfg, src, out_dir, captions=None):
    cfg_v = cfg["video"]
    render._quality(cfg)
    ec = cfg.get("smartedit") or {}
    W, H = cfg_v["width"], cfg_v["height"]
    src = Path(src)
    vid_id = "edit-" + re.sub(r"[^A-Za-z0-9]+", "-", src.stem).strip("-").lower()[:40]
    work = config.data_dir(cfg) / "tmp" / vid_id
    work.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    want_caps = ec.get("captions", True) if captions is None else captions

    has_aud = has_audio(src)
    dur = audio.probe_duration(src)
    segs = []
    if has_aud:
        sil, dur = detect_silences(src, ec.get("silence_db", -34), ec.get("min_silence", 0.45))
        segs = speech_segments(sil, dur, ec.get("keep_padding", 0.12))
    if not segs:
        segs = [(0.0, dur)]
    kept = sum(b - a for a, b in segs)
    print(f"  মূল {dur:.1f}s → কথার অংশ {kept:.1f}s ({len(segs)} টুকরো, {dur - kept:.1f}s নীরবতা বাদ)")

    cut = cut_and_reframe(cfg, src, segs, work / "cut.mp4", W, H, has_aud, ec.get("reframe", "blur"))
    cut_dur = audio.probe_duration(cut)

    capsegs, transcript = [], ""
    master = None
    if has_aud:
        wav = work / "cut_audio.wav"
        _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(cut), "-vn", "-c:a", "pcm_s16le", str(wav)])
        polished = audio.polish(wav, work / "polished.wav")
        master = audio.loudnorm(polished, work / "master.wav") if ec.get("loudnorm", True) else polished
        if want_caps:
            capsegs = transcribe(cfg, wav, cut_dur, work)
            transcript = " ".join(t for _, _, t in capsegs)

    # --- ক্যাপশন ওভারলে + চূড়ান্ত এনকোড ---
    preset, crf = render._quality(cfg)
    inputs = ["-i", str(cut)]
    filt, cur, k = [], "0:v", 1
    if master:
        inputs += ["-i", str(master)]
        k = 2
    for a, b, text in capsegs:
        for c, ca, cb in render.caption_chunks(text, a, b, int(cfg["video"].get("caption_words", 4))):
            img, cx, cy = render.caption_layer(cfg, render.display_text(c))
            p = work / f"cap{k:03d}.png"
            img.save(p)
            inputs += ["-loop", "1", "-framerate", str(cfg_v["fps"]), "-t", f"{cut_dur:.3f}", "-i", str(p)]
            nxt = f"o{k}"
            filt.append(f"[{cur}][{k}:v]overlay=x={cx}:y={cy}:enable='between(t,{ca:.3f},{cb:.3f})':format=auto[{nxt}]")
            cur, k = nxt, k + 1
    out = out_dir / f"{vid_id}.mp4"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs]
    if filt:
        cmd += ["-filter_complex", ";".join(filt), "-map", f"[{cur}]"]
    else:
        cmd += ["-map", "0:v"]
    if master:
        cmd += ["-map", "1:a"]
    cmd += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-profile:v", "high", "-pix_fmt", "yuv420p",
            *(["-c:a", "aac", "-b:a", "192k", "-ar", "44100"] if master else ["-an"]),
            "-movflags", "+faststart", "-t", f"{cut_dur:.3f}", str(out)]
    _run(cmd)

    meta = make_meta(cfg, src.stem, transcript)
    meta.update({"id": vid_id, "category": "edit", "video": out.name, "seconds": round(cut_dur, 1)})
    meta["description"] = (meta.get("description", "").rstrip() + "\n\n#Shorts").strip()
    (out_dir / f"{vid_id}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    render.cleanup(work)
    if cut_dur > 180:
        print(f"  ⚠ ভিডিও {cut_dur:.0f}s — Shorts-এর সীমা ৩ মিনিট; আরও ছোট করুন", file=sys.stderr)
    return out, meta


def collect(paths):
    files = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            files += sorted(f for f in p.iterdir() if f.suffix.lower() in VIDEO_EXT)
        elif p.exists():
            files.append(p)
        else:
            print(f"  ⚠ পাওয়া যায়নি: {p}", file=sys.stderr)
    return files
