"""দৃশ্যের ছবি (Pillow) + ভিডিও জোড়া (FFmpeg)। বাংলা যুক্তাক্ষর ঠিক রাখতে Raqm লেআউট লাগে।"""
import os
import shutil
import subprocess
import warnings
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, features

from . import tts

S = 2  # ছবি ২ গুণ রেজোলিউশনে আঁকা হয় (জুমের সময়ও ধারালো থাকে)
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
    glow = glow.filter(ImageFilter.GaussianBlur(120 * S / 2))
    img = Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB")
    _bg_cache[key] = img
    return img.copy()


def draw_frame(cfg, cat, scene, idx, total, step_no, handle):
    W, H = cfg["video"]["width"] * S, cfg["video"]["height"] * S
    px = lambda v: int(v * S)
    accent = hex_rgb(cat["accent"])
    img = _background(W, H, cat["accent"])
    d = ImageDraw.Draw(img)

    # উপরে অগ্রগতির বার
    gap, bw = px(10), (W - px(120) - px(10) * (total - 1)) / total
    for i in range(total):
        x0 = px(60) + i * (bw + gap)
        d.rounded_rectangle([x0, px(80), x0 + bw, px(92)], radius=px(6),
                            fill=accent if i <= idx else (70, 78, 100))

    # ক্যাটাগরি চিপ
    f_chip = load_font(cfg, px(44))
    label = cat["name"]
    tw = f_chip.getlength(label)
    d.rounded_rectangle([px(60), px(130), px(60) + tw + px(60), px(130) + px(88)], radius=px(44), fill=accent)
    draw_text(d, (px(60) + px(30), px(130) + px(18)), label, f_chip, fill=(255, 255, 255))

    # মূল লেখা (সাইজ নিজে নিজে ঠিক হয়)
    max_w = W - px(160)
    for size in range(112, 55, -4):
        font = load_font(cfg, px(size))
        lines = wrap(font, scene["screen_text"], max_w)
        line_h = int(px(size) * 1.5)
        if len(lines) <= 5:
            break
    block_h = len(lines) * line_h
    cy = H * 0.50
    top = cy - block_h / 2
    if scene["kind"] == "step" and step_no:
        r = px(80)
        cx, cyb = W / 2, top - px(150)
        d.ellipse([cx - r, cyb - r, cx + r, cyb + r], fill=accent)
        fb = load_font(cfg, px(96))
        t = bn_digits(step_no)
        draw_text(d, (cx - fb.getlength(t) / 2, cyb - px(72)), t, fb, fill=(255, 255, 255))
    for i, ln in enumerate(lines):
        x = (W - font.getlength(ln)) / 2
        draw_text(d, (x, top + i * line_h), ln, font, fill=(255, 255, 255),
                  stroke_width=px(2), stroke_fill=(0, 0, 0))
    ybar = top + block_h + px(30)
    d.rounded_rectangle([W / 2 - px(90), ybar, W / 2 + px(90), ybar + px(14)], radius=px(7), fill=accent)

    # নিচে হ্যান্ডেল
    if handle:
        fh = load_font(cfg, px(46))
        draw_text(d, ((W - fh.getlength(handle)) / 2, H - px(190)), handle, fh, fill=(205, 212, 230))
    return img


def _run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg ব্যর্থ: {r.stderr[-600:]}")


def render_video(cfg, cat, script, audio_paths, out_path, workdir):
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    W, H, fps = cfg["video"]["width"], cfg["video"]["height"], cfg["video"]["fps"]
    handle = (cfg.get("channel") or {}).get("handle", "")
    scenes, total, step_no, clips = script["scenes"], len(script["scenes"]), 0, []
    for i, (sc, audio) in enumerate(zip(scenes, audio_paths)):
        if sc["kind"] == "step":
            step_no += 1
        frame = workdir / f"f{i:02d}.png"
        draw_frame(cfg, cat, sc, i, total, step_no if sc["kind"] == "step" else 0, handle).save(frame)
        dur = tts.duration(audio) + 0.35
        n = max(2, int(dur * fps))
        if cfg["video"].get("motion", "zoom") == "zoom":
            vf = (f"zoompan=z='1+0.05*on/{n}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                  f":d={n}:s={W}x{H}:fps={fps},format=yuv420p")
            vin = ["-i", str(frame)]
        else:
            vf = f"scale={W}:{H}:flags=lanczos,format=yuv420p"
            vin = ["-loop", "1", "-framerate", str(fps), "-i", str(frame)]
        clip = workdir / f"c{i:02d}.mp4"
        _run(["ffmpeg", "-y", "-loglevel", "error", *vin, "-i", str(audio), "-vf", vf, "-af", "apad",
              "-t", f"{dur:.3f}", "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
              "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2", str(clip)])
        clips.append(clip)
    lst = workdir / "list.txt"
    lst.write_text("".join(f"file '{c.as_posix()}'\n" for c in clips), encoding="utf-8")
    _run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
          "-c", "copy", "-movflags", "+faststart", str(out_path)])
    return out_path


def cleanup(workdir):
    shutil.rmtree(workdir, ignore_errors=True)
