"""অডিও প্রসেসিং: ভয়েস পরিষ্কার, ব্যাকগ্রাউন্ড মিউজিক (ভয়েসের সময় নিজে নিচু হয়), হালকা ট্রানজিশন-সাউন্ড, চূড়ান্ত লাউডনেস।

মিউজিক: `assets/music/` ফোল্ডারে কপিরাইট-মুক্ত গান (mp3/wav/m4a/ogg) রাখুন (যেমন YouTube Audio Library থেকে নামানো)।
ফোল্ডার ফাঁকা থাকলে শুধু ভয়েস ও সাউন্ড-ইফেক্ট থাকবে। গান বাছার আগে তার লাইসেন্স নিজে দেখে নিন।
"""
import json
import math
import random
import re
import subprocess
from pathlib import Path

MUSIC_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".aac"}


def _run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg ব্যর্থ: {r.stderr[-600:]}")
    return r


def probe_duration(path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def voice_clean(cfg, src, dst):
    """শুরুর নীরবতা ছাঁটা, কম-ফ্রিকোয়েন্সি গোলমাল কাটা, ভলিউম সমান করা (কম্প্রেসার)।"""
    base = ("highpass=f=80,lowpass=f=15000,"
            "acompressor=threshold=-20dB:ratio=3:attack=5:release=80:makeup=3,alimiter=limit=0.95")
    tail = ["-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(dst)]
    try:
        _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-af",
              "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.03," + base, *tail])
        if probe_duration(dst) > 0.3:
            return dst
    except (RuntimeError, ValueError):
        pass
    _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-af", base, *tail])  # পুরো নীরব অডিওতেও চলে
    return dst


def polish(src, dst):
    """নিজের রেকর্ড করা ভয়েসের জন্য: গোলমাল কমানো + ভলিউম সমান (সময় বদলায় না, তাই ভিডিওর সঙ্গে মিলে থাকে)।"""
    _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-af",
          "highpass=f=80,afftdn=nr=12:nf=-40,acompressor=threshold=-20dB:ratio=3:attack=5:release=80:makeup=3,"
          "alimiter=limit=0.95", "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(dst)])
    return dst


def find_music(cfg, seed=""):
    mc = cfg.get("audio") or {}
    d = Path(cfg["_root"]) / mc.get("music_dir", "assets/music")
    files = sorted(p for p in d.glob("*") if p.suffix.lower() in MUSIC_EXT) if d.exists() else []
    return random.Random(seed).choice(files) if files else None


def make_whoosh(dst):
    """ছোট 'হুশ' সাউন্ড (গোলাপি নয়েজ + ফেড) — দৃশ্য বদলের সময় বাজে।"""
    _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anoisesrc=d=0.6:c=pink:r=44100:a=0.7",
          "-af", "highpass=f=450,lowpass=f=5200,afade=t=in:st=0:d=0.22,afade=t=out:st=0.22:d=0.38,"
                 "volume=0.55,aformat=channel_layouts=stereo", "-c:a", "pcm_s16le", str(dst)])
    return dst


def build_mix(cfg, voices, starts, total, music, sfx_times, workdir, dst):
    """ভয়েস (প্রতিটি নিজের শুরুর সময়ে) + ডাকিং-সহ মিউজিক + সাউন্ড-ইফেক্ট → একটা wav।"""
    ac = cfg.get("audio") or {}
    workdir = Path(workdir)
    inputs, filt, labels = [], [], []
    for i, (v, s) in enumerate(zip(voices, starts)):
        inputs += ["-i", str(v)]
        ms = int(max(0, s) * 1000)
        filt.append(f"[{i}:a]aresample=44100,aformat=channel_layouts=stereo,adelay={ms}|{ms}[v{i}]")
        labels.append(f"[v{i}]")
    n = len(voices)
    filt.append("".join(labels) + f"amix=inputs={n}:normalize=0:duration=longest,apad=whole_dur={total:.3f}[voice]")
    idx = n
    bed = "[voice]"
    if music:
        inputs += ["-stream_loop", "-1", "-i", str(music)]
        vol = float(ac.get("music_volume", 0.16))
        fo = max(0.0, total - 1.5)
        filt.append(f"[{idx}:a]aresample=44100,aformat=channel_layouts=stereo,atrim=0:{total:.3f},asetpts=PTS-STARTPTS,"
                    f"volume={vol},afade=t=in:st=0:d=1.0,afade=t=out:st={fo:.3f}:d=1.5[music]")
        filt.append("[voice]asplit=2[vkey][vmix]")
        filt.append("[music][vkey]sidechaincompress=threshold=0.02:ratio=10:attack=15:release=350[duck]")
        filt.append("[vmix][duck]amix=inputs=2:normalize=0:duration=first[bed]")
        bed = "[bed]"
        idx += 1
    sfx_labels = []
    if ac.get("sfx", True) and sfx_times:
        whoosh = make_whoosh(workdir / "whoosh.wav")
        for j, t in enumerate(sfx_times):
            inputs += ["-i", str(whoosh)]
            ms = int(max(0, t) * 1000)
            filt.append(f"[{idx}:a]adelay={ms}|{ms},volume={float(ac.get('sfx_volume', 0.4))}[s{j}]")
            sfx_labels.append(f"[s{j}]")
            idx += 1
    if sfx_labels:
        filt.append(f"{bed}{''.join(sfx_labels)}amix=inputs={1 + len(sfx_labels)}:normalize=0:duration=first[mix]")
        bed = "[mix]"
    filt.append(f"{bed}atrim=0:{total:.3f},asetpts=PTS-STARTPTS[out]")
    _run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filt), "-map", "[out]",
          "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(dst)])
    return dst


def loudnorm(src, dst, target=-14.0, tp=-1.5, lra=11.0):
    """দুই-ধাপের লাউডনেস মাস্টারিং (YouTube-এর লক্ষ্য প্রায় -14 LUFS)। নীরব অডিওতে শুধু কপি।"""
    base = f"loudnorm=I={target}:TP={tp}:LRA={lra}"
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(src), "-af", base + ":print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True)
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr, re.S)
    af = base
    if m:
        try:
            j = json.loads(m.group(0))
            vals = {k: float(j[k]) for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
            if all(math.isfinite(v) for v in vals.values()):
                af = (f"{base}:measured_I={vals['input_i']}:measured_TP={vals['input_tp']}:"
                      f"measured_LRA={vals['input_lra']}:measured_thresh={vals['input_thresh']}:"
                      f"offset={vals['target_offset']}:linear=true")
            else:
                _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-c:a", "pcm_s16le", str(dst)])
                return dst
        except (ValueError, KeyError):
            pass
    _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-af", af, "-ar", "44100", "-c:a", "pcm_s16le", str(dst)])
    return dst
