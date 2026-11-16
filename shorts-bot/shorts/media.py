"""আসল ফুটেজ ও ছবি: Pexels, Pixabay ও Unsplash (বিনামূল্যের API) + শেষ ভরসা হিসেবে Lorem Picsum (কী লাগে না)।

দরকারি কী (GitHub Secrets, যেকোনো একটাই যথেষ্ট): PEXELS_API_KEY, PIXABAY_API_KEY, UNSPLASH_ACCESS_KEY
Lorem Picsum কিওয়ার্ড বোঝে না (এলোমেলো সুন্দর ছবি দেয়), তাই config.yaml-এ `media.picsum_fallback: true` দিলে তবেই চলে — বাকি সবকিছু ব্যর্থ হলে।
কী না থাকলে বা কিছু না মিললে ওই দৃশ্য আগের মতো ডিজাইন-ব্যাকগ্রাউন্ডে বানানো হয় (ভিডিও আটকায় না)।

একই ক্লিপ বারবার না আসতে `data/media_used.json`-এ ব্যবহার হওয়া আইডি মনে রাখা হয়।
"""
import json
import os
import random
import re
import sys
from pathlib import Path

import requests

PEXELS = "https://api.pexels.com"
PIXABAY = "https://pixabay.com/api"
UNSPLASH = "https://api.unsplash.com"
PICSUM = "https://picsum.photos"
MAX_BYTES = 80 * 1024 * 1024


def _key(name):
    return os.environ.get(name, "").strip().strip('"').strip("'").strip()


def clean_query(q):
    """শুধু ইংরেজি অক্ষর-সংখ্যা রাখে (স্টক সাইটে বাংলা/চিহ্ন দিলে ফল খারাপ হয়)।"""
    q = re.sub(r"[^A-Za-z0-9 ]+", " ", q or "")
    return " ".join(q.split())[:60]


def query_ladder(query, fallback):
    """আসল কিওয়ার্ড → ছোট করা কিওয়ার্ড → ক্যাটাগরির সাধারণ কিওয়ার্ড (পরপর চেষ্টা)।"""
    q = clean_query(query)
    out = []
    if q:
        out.append(q)
        words = q.split()
        if len(words) > 2:
            out.append(" ".join(words[:2]))
    fb = clean_query(fallback)
    if fb and fb not in out:
        out.append(fb)
    return out


def pick_video_file(files, target_w=1080):
    """Pexels-এর ফাইল-তালিকা থেকে লক্ষ্য প্রস্থের কাছাকাছি mp4 বাছে (৭২০-এর নিচে নয়)।"""
    ok = [f for f in files or [] if str(f.get("file_type", "video/mp4")).endswith("mp4") and f.get("link")
          and (f.get("width") or 0) >= 700]
    if not ok:
        return None
    return min(ok, key=lambda f: abs((f.get("width") or 0) - target_w))


class Used:
    """ব্যবহার হওয়া মিডিয়ার আইডি (সর্বশেষ ৩০০টি) — repo-তে সেভ হয়, তাই পরের রানেও মনে থাকে।"""

    def __init__(self, path):
        self.path = Path(path)
        try:
            self.ids = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.ids = []

    def has(self, mid):
        return mid in self.ids

    def add(self, mid):
        if mid in self.ids:
            self.ids.remove(mid)
        self.ids.append(mid)
        self.ids = self.ids[-300:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.ids, ensure_ascii=False), encoding="utf-8")


def _get(url, **kw):
    try:
        r = requests.get(url, timeout=30, **kw)
    except requests.RequestException as e:
        print(f"  ⚠ মিডিয়া সার্ভার: {e}", file=sys.stderr)
        return None
    if r.status_code != 200:
        print(f"  ⚠ মিডিয়া সার্ভার HTTP {r.status_code}", file=sys.stderr)
        return None
    try:
        return r.json()
    except ValueError:
        return None


def _download(url, dest):
    try:
        with requests.get(url, stream=True, timeout=60) as r:
            if r.status_code != 200:
                return False
            size = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        return False
                    f.write(chunk)
        return Path(dest).stat().st_size > 10_000
    except (requests.RequestException, OSError) as e:
        print(f"  ⚠ ডাউনলোড ব্যর্থ: {e}", file=sys.stderr)
        return False


# ---------- প্রোভাইডার ----------
def _pexels_videos(q, rnd, min_dur):
    k = _key("PEXELS_API_KEY")
    if not k:
        return []
    d = _get(f"{PEXELS}/videos/search", headers={"Authorization": k},
             params={"query": q, "orientation": "portrait", "size": "medium", "per_page": 15})
    out = []
    for v in (d or {}).get("videos", []):
        f = pick_video_file(v.get("video_files"))
        if f and (v.get("duration") or 0) >= min_dur:
            out.append({"id": f"pexels-v{v['id']}", "url": f["link"], "type": "video",
                        "credit": f"Pexels / {(v.get('user') or {}).get('name', '')}".strip(" /")})
    return out


def _pixabay_videos(q, rnd, min_dur):
    k = _key("PIXABAY_API_KEY")
    if not k:
        return []
    d = _get(f"{PIXABAY}/videos/", params={"key": k, "q": q, "per_page": 20, "safesearch": "true"})
    out = []
    for h in (d or {}).get("hits", []):
        vs = h.get("videos") or {}
        v = vs.get("medium") or vs.get("large") or {}
        # শুধু লম্বালম্বি বা চৌকো ক্লিপ — প্রশস্ত ক্লিপ কেটে ছোট হয়ে যায়
        if v.get("url") and (h.get("duration") or 0) >= min_dur and (v.get("height") or 0) >= (v.get("width") or 0) * 0.9:
            out.append({"id": f"pixabay-v{h['id']}", "url": v["url"], "type": "video",
                        "credit": f"Pixabay / {h.get('user', '')}".strip(" /")})
    return out


def _pexels_photos(q, rnd, min_dur):
    k = _key("PEXELS_API_KEY")
    if not k:
        return []
    d = _get(f"{PEXELS}/v1/search", headers={"Authorization": k},
             params={"query": q, "orientation": "portrait", "per_page": 15})
    return [{"id": f"pexels-p{p['id']}", "url": (p.get("src") or {}).get("large2x") or (p.get("src") or {}).get("large"),
             "type": "image", "credit": f"Pexels / {p.get('photographer', '')}".strip(" /")}
            for p in (d or {}).get("photos", []) if (p.get("src") or {}).get("large2x") or (p.get("src") or {}).get("large")]


def _pixabay_photos(q, rnd, min_dur):
    k = _key("PIXABAY_API_KEY")
    if not k:
        return []
    d = _get(f"{PIXABAY}/", params={"key": k, "q": q, "orientation": "vertical", "image_type": "photo",
                                    "safesearch": "true", "per_page": 20, "min_width": 1000})
    return [{"id": f"pixabay-p{h['id']}", "url": h["largeImageURL"], "type": "image",
             "credit": f"Pixabay / {h.get('user', '')}".strip(" /")}
            for h in (d or {}).get("hits", []) if h.get("largeImageURL")]


def _unsplash_photos(q, rnd, min_dur):
    k = _key("UNSPLASH_ACCESS_KEY")
    if not k:
        return []
    d = _get(f"{UNSPLASH}/search/photos", headers={"Authorization": f"Client-ID {k}", "Accept-Version": "v1"},
             params={"query": q, "orientation": "portrait", "content_filter": "high", "per_page": 20})
    out = []
    for p in (d or {}).get("results", []):
        raw = (p.get("urls") or {}).get("raw")
        if not raw:
            continue
        # raw লিংকে প্রস্থ/মান ঠিক করে নিই (imgix প্যারামিটার)
        url = f"{raw}{'&' if '?' in raw else '?'}w=1440&q=80&fm=jpg&fit=max"
        out.append({"id": f"unsplash-p{p['id']}", "url": url, "type": "image",
                    "credit": f"Unsplash / {(p.get('user') or {}).get('name', '')}".strip(" /"),
                    "track": (p.get("links") or {}).get("download_location")})
    return out


def _unsplash_track(c):
    """Unsplash-এর নিয়ম: ছবি ব্যবহার করলে download_location-এ একটা অনুরোধ পাঠাতে হয় (ব্যর্থ হলেও ক্ষতি নেই)।"""
    k = _key("UNSPLASH_ACCESS_KEY")
    if k and c.get("track"):
        try:
            requests.get(c["track"], headers={"Authorization": f"Client-ID {k}"}, timeout=10)
        except requests.RequestException:
            pass


def _picsum_photos(q, rnd, min_dur):
    """কিওয়ার্ড ছাড়া এলোমেলো ছবি — তাই শুধু শেষ ভরসা। সিড থেকেই একই ছবি ফেরে, তাই 'ব্যবহৃত' তালিকা কাজ করে।"""
    return [{"id": f"picsum-{seed}", "url": f"{PICSUM}/seed/{seed}/1080/1920.jpg", "type": "image",
             "credit": "Lorem Picsum"}
            for seed in (rnd.randrange(1, 1_000_000) for _ in range(6))]


def available(cfg=None) -> bool:
    if cfg and (cfg.get("media") or {}).get("picsum_fallback", False):
        return True
    return bool(_key("PEXELS_API_KEY") or _key("PIXABAY_API_KEY") or _key("UNSPLASH_ACCESS_KEY"))


def fetch(cfg, query, fallback_query, workdir, tag, used):
    """একটা দৃশ্যের জন্য ফুটেজ/ছবি আনে। ফেরত: {"type", "path", "id", "credit"} বা None।"""
    mc = cfg.get("media") or {}
    if not mc.get("enabled", False) or not available(cfg):
        return None
    min_dur = mc.get("min_clip_seconds", 3)
    order = [_pexels_videos, _pixabay_videos] if mc.get("prefer", "video") == "video" else []
    order += [_pexels_photos, _pixabay_photos, _unsplash_photos]
    if mc.get("prefer", "video") != "video":
        order += [_pexels_videos, _pixabay_videos]
    rnd = random.Random(f"{tag}-{query}")
    Path(workdir).mkdir(parents=True, exist_ok=True)

    def _try(fn, q):
        cands = [c for c in fn(q, rnd, min_dur) if not used.has(c["id"])]
        rnd.shuffle(cands)
        for c in cands[:3]:
            ext = ".mp4" if c["type"] == "video" else ".jpg"
            dest = Path(workdir) / f"m_{tag}{ext}"
            if _download(c["url"], dest):
                used.add(c["id"])
                if c["id"].startswith("unsplash-"):
                    _unsplash_track(c)
                return {"type": c["type"], "path": dest, "id": c["id"], "credit": c["credit"], "query": q}
        return None

    for q in query_ladder(query, fallback_query):
        for fn in order:
            got = _try(fn, q)
            if got:
                return got
    if mc.get("picsum_fallback", False):
        return _try(_picsum_photos, "random")
    return None
