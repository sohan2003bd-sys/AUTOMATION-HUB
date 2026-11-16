"""দৃশ্যের ছবি (Pillow) + ভিডিও জোড়া (FFmpeg)। বাংলা যুক্তাক্ষর ঠিক রাখতে Raqm লেআউট লাগে।"""
import functools
import os
import random
import re
import shutil
import subprocess
import warnings
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, features

from . import audio

SS = 2  # লেখা ২ গুণ রেজোলিউশনে এঁকে ছোট করা হয় (ধারালো ও মসৃণ অক্ষর)
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoSansBengali-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansBengali[wdth,wght].ttf",
    "/usr/share/fonts/opentype/noto/NotoSansBengali-Bold.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansBengaliUI-Bold.ttf",
    "C:/Windows/Fonts/Nirmala.ttf", "C:/Windows/Fonts/nirmalab.ttf",
    "/Library/Fonts/NotoSansBengali-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Bangla MN.ttc",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]
_bg_cache = {}


def find_font(cfg) -> str:
    cands = [os.environ.get("SHORTS_FONT"), cfg["video"].get("font")]
    cands += [str(p) for p in (Path(cfg["_root"]) / "assets" / "fonts").glob("*.ttf")]
    cands += FONT_CANDIDATES
    for c in cands:
        if c and Path(c).exists():
            return c
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}", "Noto Sans Bengali:bold"],
                             capture_output=True, text=True).stdout
        if out and Path(out).exists() and "bengali" in out.lower():
            return out
    except FileNotFoundError:
        pass
    raise FileNotFoundError("বাংলা ফন্ট পাওয়া যায়নি। Noto Sans Bengali ইনস্টল করুন "
                            "(Ubuntu: sudo apt install fonts-noto-core) অথবা config.yaml-এ video.font দিন।")


LATIN_CANDIDATES = [
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/opentype/noto/NotoSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def find_latin_font(cfg):
    """ইংরেজি অক্ষরের ফন্ট (Noto Sans Bengali-তে ইংরেজি অক্ষর নেই, তাই আলাদা ফন্ট লাগে)। না পেলে None।"""
    cands = [os.environ.get("SHORTS_LATIN_FONT"), cfg["video"].get("latin_font")] + LATIN_CANDIDATES
    for c in cands:
        if c and Path(c).exists():
            return c
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}", "Noto Sans:bold"],
                             capture_output=True, text=True).stdout
        if out and Path(out).exists():
            return out
    except FileNotFoundError:
        pass
    return None


def _is_bn(ch):
    o = ord(ch)
    return 0x0980 <= o <= 0x09FF or o in (0x200C, 0x200D, 0x0964, 0x0965)


def split_runs(text):
    """লেখাকে (বাংলা?, অংশ) টুকরোয় ভাগ করে। বাংলা বাদে সব (ইংরেজি, সংখ্যা, চিহ্ন) ইংরেজি ফন্টে যায়;
    ফাঁকা জায়গা আগের টুকরোর সঙ্গে থাকে।"""
    runs = []
    for ch in text:
        if ch == " " and runs:
            runs[-1][1] += ch
            continue
        bn = _is_bn(ch)
        if runs and runs[-1][0] == bn:
            runs[-1][1] += ch
        else:
            runs.append([bn, ch])
    return [(bn, t) for bn, t in runs]


class MixedFont:
    """বাংলা অংশ বাংলা ফন্টে, বাকি সব ইংরেজি ফন্টে আঁকে (একই বেসলাইনে)।"""

    def __init__(self, bn, latin):
        self.bn, self.latin = bn, latin or bn

    def _f(self, is_bn):
        return self.bn if is_bn else self.latin

    def getlength(self, text):
        return sum(self._f(b).getlength(t) for b, t in split_runs(text))

    def draw(self, d, xy, text, **kw):
        x, y = xy
        base = y + self.bn.getmetrics()[0]
        for b, t in split_runs(text):
            f = self._f(b)
            d.text((x, base), t, font=f, anchor="ls", **kw)
            x += f.getlength(t)


def draw_text(d, xy, text, font, **kw):
    if isinstance(font, MixedFont):
        font.draw(d, xy, text, **kw)
    else:
        d.text(xy, text, font=font, **kw)


@functools.lru_cache(maxsize=256)
def _truetype(path, size):
    if features.check("raqm"):
        return ImageFont.truetype(path, size, layout_engine=ImageFont.Layout.RAQM)
    warnings.warn("Pillow-তে Raqm নেই: বাংলা যুক্তাক্ষর ভেঙে যেতে পারে (libraqm0, libfribidi0 ইনস্টল করুন)")
    return ImageFont.truetype(path, size)


def load_font(cfg, size):
    bn = _truetype(find_font(cfg), size)
    lp = find_latin_font(cfg)
    return MixedFont(bn, _truetype(lp, size) if lp else None)


def bn_digits(n: int) -> str:
    return str(n).translate(str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯"))


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def wrap(font, text, max_w):
    lines, cur = [], ""
    for w in text.split():
        t = f"{cur} {w}".strip()
        if font.getlength(t) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _background(W, H, accent):
    key = (W, H, accent)
    if key in _bg_cache:
        return _bg_cache[key].copy()
    a = hex_rgb(accent)
    top = Image.new("RGB", (W, H), (9, 13, 28))
    bottom = Image.new("RGB", (W, H), tuple(int(c * 0.28 + 12) for c in a))
    mask = Image.linear_gradient("L").resize((W, H))
    img = Image.composite(bottom, top, mask)
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(glow)
    d.ellipse([W * 0.45, -H * 0.06, W * 1.35, H * 0.30], fill=a + (110,))
    d.ellipse([-W * 0.4, H * 0.72, W * 0.6, H * 1.05], fill=a + (70,))
    glow = glow.filter(ImageFilter.GaussianBlur(120 * SS / 2))
    img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")
    _bg_cache[key] = img
    return img.copy()


# ======================================================================
#  ধাপ ৩: স্তরভিত্তিক (layered) রেন্ডার — আসল ফুটেজ/ছবি + অ্যানিমেটেড লেখা + ক্যাপশন + ট্রানজিশন
# ======================================================================
TRANSITIONS = ["fade", "slideleft", "slideup", "wipeleft", "smoothleft", "smoothup", "circleopen", "zoomin",
               "radial", "dissolve"]
VOICE_DELAY = 0.16          # দৃশ্য শুরুর কতক্ষণ পরে কথা শুরু
CLEAN = "-.,!?:;\"'()।‘’“”"


def _unit(cfg):
    return cfg["video"]["width"] / 1080.0


def _quality(cfg):
    """quality: high = ১০৮০×১৯২০ (Shorts-এর মান); ultra = ১৪৪০×২৫৬০ (ইউটিউব বেশি বিটরেট দেয়, রেন্ডার ধীর)।"""
    v = cfg["video"]
    q = v.get("quality", "high")
    if q == "ultra":
        v["width"], v["height"] = 1440, 2560
    elif q == "high":
        v["width"], v["height"] = v.get("width", 1080), v.get("height", 1920)
    return {"high": ("medium", 17), "ultra": ("medium", 18), "draft": ("veryfast", 23)}.get(q, ("medium", 17))


def display_text(t):
    """ফন্টে নেই এমন চিহ্ন বদলে দেয় (→ ইত্যাদি Noto Sans-এ না থাকলে ভাঙা বাক্স দেখায়)।"""
    return (t or "").replace("→", "›").replace("->", "›").replace("⇒", "›").replace("✓", "✔")


def _hl_color(accent):
    return tuple(min(255, int(c * 0.8 + 70)) for c in accent)


def _draw_tokens(d, font, x, y, line, hl_words, fill, hl_fill, stroke, stroke_fill=(0, 0, 0)):
    for tok in line.split(" "):
        if not tok:
            continue
        key = tok.strip(CLEAN)
        color = hl_fill if (key and key in hl_words) else fill
        draw_text(d, (x, y), tok, font, fill=color, stroke_width=stroke, stroke_fill=stroke_fill)
        x += font.getlength(tok + " ")


def static_layer(cfg, cat, idx, total, handle, media_on):
    """ছায়া (লেখা পড়ার সুবিধার জন্য) + অগ্রগতির বার + ক্যাটাগরি চিপ + হ্যান্ডেল — একটাই স্বচ্ছ ছবি।"""
    W, H = cfg["video"]["width"], cfg["video"]["height"]
    U = _unit(cfg)
    w, h = W * SS, H * SS
    px = lambda v: int(round(v * U * SS))
    accent = hex_rgb(cat["accent"])
    top_a, bot_a, base = (150, 200, 55) if media_on else (0, 0, 0)
    col = Image.new("L", (1, h))
    data = []
    for y in range(h):
        f = y / h
        a = base
        if f < 0.30:
            a += top_a * (1 - f / 0.30) ** 1.5
        if f > 0.45:
            a += bot_a * ((f - 0.45) / 0.55) ** 1.3
        data.append(int(min(255, a)))
    col.putdata(data)
    img = Image.new("RGBA", (w, h), (6, 8, 20, 255))
    img.putalpha(col.resize((w, h), Image.NEAREST))
    d = ImageDraw.Draw(img)

    gap, bw = px(10), (w - px(120) - px(10) * (total - 1)) / total
    for i in range(total):
        x0 = px(60) + i * (bw + gap)
        d.rounded_rectangle([x0, px(80), x0 + bw, px(92)], radius=px(6),
                            fill=accent + (255,) if i <= idx else (255, 255, 255, 70))
    f_chip = load_font(cfg, px(44))
    label = cat["name"]
    tw = f_chip.getlength(label)
    d.rounded_rectangle([px(60), px(130), px(60) + tw + px(60), px(130) + px(88)], radius=px(44), fill=accent + (255,))
    draw_text(d, (px(60) + px(30), px(130) + px(18)), label, f_chip, fill=(255, 255, 255, 255))
    if handle:
        fh = load_font(cfg, px(46))
        draw_text(d, ((w - fh.getlength(handle)) / 2, h - px(190)), handle, fh, fill=(230, 235, 248, 255),
                  stroke_width=px(2), stroke_fill=(0, 0, 0, 255))
    return img.resize((W, H), Image.LANCZOS)


def headline_layer(cfg, cat, scene, step_no):
    """মূল লেখা (+ ধাপ নম্বরের গোল ব্যাজ)। ফেরত: (ছবি, x, y) — শুধু লেখার মাপের ছোট ছবি।"""
    W, H = cfg["video"]["width"], cfg["video"]["height"]
    U = _unit(cfg)
    px = lambda v: int(round(v * U * SS))
    accent = hex_rgb(cat["accent"])
    hl_words = set((scene.get("highlight") or "").split())
    hl_words = {t.strip(CLEAN) for t in hl_words}
    max_w = W * SS - px(150)
    for size in range(108, 52, -4):
        font = load_font(cfg, px(size))
        lines = wrap(font, display_text(scene["screen_text"]), max_w)
        if len(lines) <= 4:
            break
    line_h = int(px(size) * 1.42)
    badge = scene["kind"] == "step" and step_no
    badge_h = px(190) if badge else 0
    ch = badge_h + len(lines) * line_h + px(70)
    img = Image.new("RGBA", (W * SS, ch), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if badge:
        r = px(72)
        cx, cy = W * SS / 2, px(86)
        d.ellipse([cx - r - px(8), cy - r - px(8), cx + r + px(8), cy + r + px(8)], fill=(255, 255, 255, 60))
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=accent + (255,))
        fb = load_font(cfg, px(88))
        t = bn_digits(step_no)
        draw_text(d, (cx - fb.getlength(t) / 2, cy - px(66)), t, fb, fill=(255, 255, 255, 255))
    for i, ln in enumerate(lines):
        x = (W * SS - font.getlength(ln)) / 2
        _draw_tokens(d, font, x, badge_h + i * line_h, ln, hl_words, (255, 255, 255, 255),
                     _hl_color(accent) + (255,), px(3), (0, 0, 0, 255))
    yb = badge_h + len(lines) * line_h + px(14)
    d.rounded_rectangle([W * SS / 2 - px(90), yb, W * SS / 2 + px(90), yb + px(14)], radius=px(7), fill=accent + (255,))
    out = img.resize((W, max(2, ch // SS)), Image.LANCZOS)
    y = int(H * 0.40 - out.height / 2)
    y = max(int(250 * U), min(y, int(H * 0.60 - out.height)))
    return out, 0, y


def caption_chunks(text, t0, t1, max_words=4):
    """বাক্যকে ছোট ছোট ফ্রেজে ভেঙে (যতিচিহ্নে আগে, তারপর ≤max_words) কথার সময়ের অনুপাতে সময় বসায়।
    ফেরত: [(ফ্রেজ, শুরু, শেষ)]"""
    parts = [p.strip() for p in re.split(r"(?<=[,।?!:;—])\s+", text.strip()) if p.strip()]
    chunks = []
    for p in parts:
        words = p.split()
        n = max(1, -(-len(words) // max_words))      # সিলিং ভাগ — সমান টুকরো করতে
        size = -(-len(words) // n)
        chunks += [" ".join(words[i:i + size]) for i in range(0, len(words), size)]
    weights = [max(1, len(c.replace(" ", ""))) for c in chunks]
    total_w, span = sum(weights), max(0.1, t1 - t0)
    out, t = [], t0
    for c, wgt in zip(chunks, weights):
        dt = span * wgt / total_w
        out.append((c, t, t + dt))
        t += dt
    return out


def caption_layer(cfg, text):
    """ক্যাপশনের কালো স্বচ্ছ পিল। ফেরত: (ছবি, x, y)।"""
    W, H = cfg["video"]["width"], cfg["video"]["height"]
    U = _unit(cfg)
    px = lambda v: int(round(v * U * SS))
    max_w = W * SS - px(240)
    for size in range(68, 40, -4):
        font = load_font(cfg, px(size))
        lines = wrap(font, text, max_w)
        if len(lines) <= 2:
            break
    line_h = int(px(size) * 1.38)
    tw = max(font.getlength(l) for l in lines)
    padx, pady = px(34), px(20)
    bw, bh = int(tw + 2 * padx), int(len(lines) * line_h + 2 * pady)
    img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, bw - 1, bh - 1], radius=px(34), fill=(8, 10, 22, 175))
    for i, ln in enumerate(lines):
        x = (bw - font.getlength(ln)) / 2
        draw_text(d, (x, pady + i * line_h), ln, font, fill=(255, 255, 255, 255))
    out = img.resize((max(2, bw // SS), max(2, bh // SS)), Image.LANCZOS)
    return out, int((W - out.width) / 2), int(H * 0.675 - out.height / 2)


def _zoom_expr(i, n):
    """দৃশ্যভেদে আলাদা Ken Burns চাল (ভিতরে/বাইরে জুম, বাঁ-ডান প্যান)।"""
    v = i % 4
    if v == 0:
        return f"1+0.08*on/{n}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if v == 1:
        return f"1.08-0.08*on/{n}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if v == 2:
        return "1.1", f"(iw-iw/zoom)*on/{n}", "ih/2-(ih/zoom/2)"
    return "1.1", f"(iw-iw/zoom)*(1-on/{n})", "ih/2-(ih/zoom/2)"


def plan(cfg, voices):
    """প্রতিটি দৃশ্যের সময়-পরিকল্পনা। ফেরত: dict(durs, starts, total, trans)"""
    v = cfg["video"]
    pad = float(v.get("scene_pad", 0.45))
    T = float(v.get("transition", 0.35))
    vd = [audio.probe_duration(p) for p in voices]
    durs = [d + pad for d in vd]
    durs[-1] += 0.4                       # শেষে একটু থামা
    starts, t = [], 0.0
    for d in durs:
        starts.append(t)
        t += d
    return {"voice_dur": vd, "durs": durs, "starts": starts, "total": t, "T": T}


def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg ব্যর্থ: {r.stderr[-700:]}")


def render_scene(cfg, cat, script, i, step_no, voice_dur, clip_len, media, workdir, handle):
    """একটা দৃশ্য → শব্দহীন ভিডিও (ক্লিপ/ছবি + ছায়া + অ্যানিমেটেড লেখা + ক্যাপশন)।"""
    workdir = Path(workdir)
    W, H, fps = cfg["video"]["width"], cfg["video"]["height"], cfg["video"]["fps"]
    sc = script["scenes"][i]
    total = len(script["scenes"])
    n = max(2, int(round(clip_len * fps)))
    media_on = bool(media)

    inputs, filt = [], []
    # --- পটভূমি ---
    if media and media["type"] == "video":
        inputs += ["-stream_loop", "-1", "-t", f"{clip_len:.3f}", "-i", str(media["path"])]
        filt.append(f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={fps},setsar=1,"
                    f"eq=contrast=1.06:saturation=1.1,format=yuv420p[bg]")
    else:
        if media:   # ছবি
            src = media["path"]
            im = Image.open(src).convert("RGB")
            im = ImageOps.fit(im, (W * 2, H * 2), Image.LANCZOS)
            bgp = workdir / f"bg{i:02d}.png"
            im.save(bgp)
        else:       # ডিজাইন-ব্যাকগ্রাউন্ড
            bgp = workdir / f"bg{i:02d}.png"
            _background(W * 2, H * 2, cat["accent"]).save(bgp)
        z, x, y = _zoom_expr(i, n)
        inputs += ["-i", str(bgp)]
        filt.append(f"[0:v]zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={W}x{H}:fps={fps},setsar=1,format=yuv420p[bg]")

    # --- স্থির স্তর ---
    st = workdir / f"st{i:02d}.png"
    static_layer(cfg, cat, i, total, handle, media_on).save(st)
    inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{clip_len:.3f}", "-i", str(st)]
    filt.append("[bg][1:v]overlay=0:0:format=auto[s0]")
    k, cur = 2, "s0"

    # --- অ্যানিমেটেড হেডলাইন (নিচ থেকে উঠে আসে + ফেড-ইন) ---
    himg, hx, hy = headline_layer(cfg, cat, sc, step_no)
    hp = workdir / f"hd{i:02d}.png"
    himg.save(hp)
    inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{clip_len:.3f}", "-i", str(hp)]
    slide = int(round(70 * _unit(cfg)))
    t0 = 0.18
    filt.append(f"[{k}:v]format=rgba,fade=t=in:st={t0}:d=0.4:alpha=1[hd]")
    filt.append(f"[{cur}][hd]overlay=x={hx}:y='{hy}+(1-pow(1-min(max(t-{t0},0)/0.55,1),3))*{slide}':format=auto[s1]")
    k, cur = k + 1, "s1"

    # --- ক্যাপশন (ফ্রেজ ধরে ধরে) ---
    if cfg["video"].get("captions", True):
        chunks = caption_chunks(sc["narration"], VOICE_DELAY, VOICE_DELAY + voice_dur,
                                int(cfg["video"].get("caption_words", 4)))
        for c, a, b in chunks:
            cimg, cx, cy = caption_layer(cfg, display_text(c))
            cp = workdir / f"cp{i:02d}_{k:02d}.png"
            cimg.save(cp)
            inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{clip_len:.3f}", "-i", str(cp)]
            nxt = f"s{k}"
            filt.append(f"[{cur}][{k}:v]overlay=x={cx}:y={cy}:enable='between(t,{a:.3f},{b:.3f})':format=auto[{nxt}]")
            cur = nxt
            k += 1
    filt.append(f"[{cur}]format=yuv420p[v]")

    out = workdir / f"scene{i:02d}.mp4"
    _run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filt), "-map", "[v]",
          "-an", "-r", str(fps), "-t", f"{clip_len:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "14",
          "-pix_fmt", "yuv420p", str(out)])
    return out


def join_scenes(cfg, scene_files, pl, out_path, seed):
    """দৃশ্যগুলো ট্রানজিশনসহ জোড়া + চূড়ান্ত মানের এনকোড (শব্দহীন)।"""
    fps = cfg["video"]["fps"]
    preset, crf = _quality(cfg)
    T, starts = pl["T"], pl["starts"]
    inputs = []
    for f in scene_files:
        inputs += ["-i", str(f)]
    filt = [f"[{i}:v]fps={fps},settb=AVTB,setsar=1[n{i}]" for i in range(len(scene_files))]
    rnd = random.Random(seed)
    cur, last = "n0", None
    trans = []
    for i in range(1, len(scene_files)):
        choices = [t for t in TRANSITIONS if t != last]
        last = rnd.choice(choices)
        trans.append(last)
        nxt = f"x{i}"
        if T > 0:
            filt.append(f"[{cur}][n{i}]xfade=transition={last}:duration={T}:offset={starts[i]:.3f}[{nxt}]")
        else:
            filt.append(f"[{cur}][n{i}]concat=n=2:v=1:a=0[{nxt}]")
        cur = nxt
    _run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(filt), "-map", f"[{cur}]",
          "-an", "-r", str(fps), "-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-profile:v", "high",
          "-pix_fmt", "yuv420p", "-t", f"{pl['total']:.3f}", str(out_path)])
    return trans


def mux(video, wav, out_path):
    _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-i", str(wav), "-map", "0:v", "-map", "1:a",
          "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-movflags", "+faststart",
          "-shortest", str(out_path)])


def render_video(cfg, cat, script, voices, medias, out_path, workdir, seed="", music=None):
    """সম্পূর্ণ ভিডিও: দৃশ্য আঁকা → ট্রানজিশনে জোড়া → অডিও মিক্স (ভয়েস+মিউজিক+ইফেক্ট) → মাস্টার → মিলিয়ে দেওয়া।"""
    _quality(cfg)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    handle = (cfg.get("channel") or {}).get("handle", "")
    pl = plan(cfg, voices)
    T = pl["T"]
    files, step_no = [], 0
    nsc = len(script["scenes"])
    for i, sc in enumerate(script["scenes"]):
        if sc["kind"] == "step":
            step_no += 1
        clip_len = pl["durs"][i] + (T if i < nsc - 1 else 0)
        files.append(render_scene(cfg, cat, script, i, step_no if sc["kind"] == "step" else 0,
                                  pl["voice_dur"][i], clip_len, medias[i] if medias else None, workdir, handle))
    video = workdir / "joined.mp4"
    trans = join_scenes(cfg, files, pl, video, seed)
    starts_v = [s + VOICE_DELAY for s in pl["starts"]]
    sfx = [max(0.0, s - 0.05) for s in pl["starts"][1:]]
    mixed = audio.build_mix(cfg, voices, starts_v, pl["total"], music, sfx, workdir, workdir / "mix.wav")
    master = audio.loudnorm(mixed, workdir / "master.wav")
    mux(video, master, out_path)
    return {"total": pl["total"], "transitions": trans}


def cleanup(workdir):
    shutil.rmtree(workdir, ignore_errors=True)
