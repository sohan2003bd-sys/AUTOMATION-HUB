"""আসল ফুটেজ ও ছবি: Pexels, Pixabay ও Unsplash (বিনামূল্যের API) + শেষ ভরসা হিসেবে Lorem Picsum (কী লাগে না)।

দরকারি কী (GitHub Secrets, যেকোনো একটাই যথেষ্ট): PEXELS_API_KEY, PIXABAY_API_KEY, UNSPLASH_ACCESS_KEY
Lorem Picsum কিওয়ার্ড বোঝে না (এলোমেলো সুন্দর ছবি দেয়), তাই config.yaml-এ `media.picsum_fallback: true` দিলে তবেই চলে — বাকি সবকিছু ব্যর্থ হলে।
কী না থাকলে বা কিছু না মিললে ওই দৃশ্য আগের মতো ডিজাইন-ব্যাকগ্রাউন্ডে বানানো হয় (ভিডিও আটকায় না)।

একই ক্লিপ বারবার না আসতে `data/media_used.json`-এ ব্যবহার হওয়া আইডি মনে রাখা হয়।
"""
import json
import math
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


# ---------- মানুষমুক্ত ফুটেজ ----------
HUMAN_WORDS = set("""people person persons man men woman women boy boys girl girls child children kid kids baby babies
family families couple couples crowd crowds team teams worker workers doctor doctors nurse nurses patient patients
student students teacher teachers customer customers client clients employee employees businessman businesswoman
entrepreneur entrepreneurs driver drivers farmer farmers chef chefs lady ladies guy guys male female adult adults
teen teens teenager teenagers toddler mother mothers father fathers parent parents friend friends face faces portrait
portraits selfie smiling smile hand hands finger fingers arm arms leg legs foot feet hair body bodies human humans
someone pedestrian pedestrians tourist tourists traveler travelers passenger passengers shopper shoppers protester
protesters audience group groups gathering gatherings politician politicians president minister athlete athletes player players model models bride
groom wedding yoga massage hug hugging kissing dancing dancer dancers singer singers""".split())


def _words(text):
    return re.findall(r"[a-z]+", (text or "").lower())


def is_human_word(w):
    return w in HUMAN_WORDS or (w.endswith("s") and w[:-1] in HUMAN_WORDS) or (w.endswith("es") and w[:-2] in HUMAN_WORDS)


def has_human_text(text):
    """শিরোনাম/ট্যাগ/স্লাগে মানুষ-সংক্রান্ত শব্দ আছে কিনা (Pexels স্লাগ, alt-টেক্সট, Pixabay ট্যাগ ইত্যাদি)।"""
    return any(is_human_word(w) for w in _words(text))


def strip_human_words(query):
    """কিওয়ার্ড থেকে মানুষ-সংক্রান্ত শব্দ বাদ দেয়। কিছু না থাকলে ফাঁকা।"""
    return " ".join(w for w in (query or "").split() if not is_human_word(w.lower()))


_DET = {}


def _detectors():
    """OpenCV থাকলে মুখ ও মানুষ শনাক্তকারী (বিনামূল্যে, CPU-তে); না থাকলে None — তখন শুধু লেখা-ভিত্তিক ছাঁকনি চলে।"""
    if "d" in _DET:
        return _DET["d"]
    try:
        import cv2
    except ImportError:
        print("  ℹ opencv নেই — ফুটেজে মুখ-শনাক্তকরণ বন্ধ, শুধু লেখা-ভিত্তিক ছাঁকনি চলছে", file=sys.stderr)
        _DET["d"] = None
        return None
    faces = []
    for n in ("haarcascade_frontalface_default.xml", "haarcascade_profileface.xml"):
        try:
            c = cv2.CascadeClassifier(os.path.join(cv2.data.haarcascades, n))
            if not c.empty():
                faces.append(c)
        except Exception:
            pass
    hog = None
    try:
        hog = cv2.HOGDescriptor()
        hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    except Exception:
        hog = None
    if not faces and hog is None:
        print("  ⚠ opencv-এ মুখ/মানুষ শনাক্তকারী পাওয়া যায়নি — শুধু লেখা-ভিত্তিক ছাঁকনি চলছে", file=sys.stderr)
        _DET["d"] = None
        return None
    _DET["d"] = (cv2, faces, hog)
    return _DET["d"]


def _frame_has_person(cv2, faces, hog, img):
    h, w = img.shape[:2]
    if w > 640:
        img = cv2.resize(img, (640, int(h * 640 / w)))
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    for c in faces:
        if len(c.detectMultiScale(gray, 1.1, 6, minSize=(36, 36))):
            return True
    if hog is not None:
        rects, weights = hog.detectMultiScale(img, winStride=(8, 8), padding=(8, 8), scale=1.05)
        if any(float(x) >= 0.7 for x in (weights.flatten() if hasattr(weights, "flatten") else weights)):
            return True
    return False


def has_person(path, kind):
    """ডাউনলোড-করা ছবি বা ভিডিওর ৩টি ফ্রেমে মুখ/মানুষ আছে কিনা। শনাক্ত করা না গেলে (ত্রুটি/ওপেনসিভি নেই) False।"""
    det = _detectors()
    if not det:
        return False
    cv2, faces, hog = det
    try:
        if kind == "image":
            img = cv2.imread(str(path))
            return img is not None and _frame_has_person(cv2, faces, hog, img)
        cap = cv2.VideoCapture(str(path))
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        found = False
        for pos in ((0.15, 0.5, 0.85) if n > 10 else (0,)):
            if n > 10:
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(n * pos))
            ok, img = cap.read()
            if ok and _frame_has_person(cv2, faces, hog, img):
                found = True
                break
        cap.release()
        return found
    except Exception as e:  # শনাক্তকরণে সমস্যা হলে ভিডিও আটকাবে না
        print(f"  ⚠ মুখ-শনাক্তকরণ ব্যর্থ ({e})", file=sys.stderr)
        return False


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
                        "text": str(v.get("url", "")).rsplit("/video/", 1)[-1].replace("-", " "),
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
            out.append({"id": f"pixabay-v{h['id']}", "url": v["url"], "type": "video", "text": h.get("tags", ""),
                        "credit": f"Pixabay / {h.get('user', '')}".strip(" /")})
    return out


def _pexels_photos(q, rnd, min_dur):
    k = _key("PEXELS_API_KEY")
    if not k:
        return []
    d = _get(f"{PEXELS}/v1/search", headers={"Authorization": k},
             params={"query": q, "orientation": "portrait", "per_page": 15})
    return [{"id": f"pexels-p{p['id']}", "url": (p.get("src") or {}).get("large2x") or (p.get("src") or {}).get("large"),
             "type": "image", "text": f"{p.get('alt', '')} {str(p.get('url', '')).rstrip('/').rsplit('/', 1)[-1].replace('-', ' ')}",
             "credit": f"Pexels / {p.get('photographer', '')}".strip(" /")}
            for p in (d or {}).get("photos", []) if (p.get("src") or {}).get("large2x") or (p.get("src") or {}).get("large")]


def _pixabay_photos(q, rnd, min_dur):
    k = _key("PIXABAY_API_KEY")
    if not k:
        return []
    d = _get(f"{PIXABAY}/", params={"key": k, "q": q, "orientation": "vertical", "image_type": "photo",
                                    "safesearch": "true", "per_page": 20, "min_width": 1000})
    return [{"id": f"pixabay-p{h['id']}", "url": h["largeImageURL"], "type": "image",
             "text": h.get("tags", ""), "credit": f"Pixabay / {h.get('user', '')}".strip(" /")}
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
                    "text": " ".join(str(x or "") for x in (p.get("alt_description"), p.get("description"))) + " "
                            + " ".join(str(t.get("title", "")) for t in (p.get("tags") or []) if isinstance(t, dict)),
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


def fetch(cfg, query, fallback_query, workdir, tag, used, state=None):
    """একটা দৃশ্যের জন্য ফুটেজ/ছবি আনে। state একই ভিডিওর দৃশ্যগুলোর মধ্যে people cap ভাগ করে।"""
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
    no_people = mc.get("no_people", True)
    face_check = mc.get("face_check", True)
    state = state if state is not None else {"people_scenes": 0, "total_scenes": 1}
    total_scenes = max(1, int(state.get("total_scenes", 1)))
    ratio = max(0.0, min(1.0, float(mc.get("people_max_ratio", 0.20))))
    configured_max = max(0, int(mc.get("people_max_scenes", 1)))
    people_limit = 0 if no_people or ratio <= 0 else min(configured_max, int(math.floor(total_scenes * ratio + 1e-9)))

    def _try(fn, q):
        cands = [c for c in fn(q, rnd, min_dur) if not used.has(c["id"])]
        rnd.shuffle(cands)
        for c in cands[:6]:
            text_person = has_human_text(c.get("text", ""))
            if no_people and text_person:
                continue  # মানুষমুক্ত মোডে শব্দ-ভিত্তিক ছাঁকনি
            if text_person and state.get("people_scenes", 0) >= people_limit:
                continue  # প্রতি ভিডিওতে নির্ধারিত মানুষ-দৃশ্যের সীমা
            ext = ".mp4" if c["type"] == "video" else ".jpg"
            dest = Path(workdir) / f"m_{tag}{ext}"
            if not _download(c["url"], dest):
                continue
            detected_person = has_person(dest, c["type"]) if face_check else False
            has_people = text_person or detected_person
            if no_people and detected_person:
                used.add(c["id"])
                print(f"  ℹ মানুষ ধরা পড়ায় বাদ: {c['id']}", file=sys.stderr)
                continue
            if has_people and state.get("people_scenes", 0) >= people_limit:
                used.add(c["id"])
                print(f"  ℹ প্রতি ভিডিওর মানুষ-দৃশ্যের সীমা পূর্ণ: {c['id']}", file=sys.stderr)
                continue
            if has_people:
                state["people_scenes"] = state.get("people_scenes", 0) + 1
            used.add(c["id"])
            if c["id"].startswith("unsplash-"):
                _unsplash_track(c)
            return {"type": c["type"], "path": dest, "id": c["id"], "credit": c["credit"],
                    "query": q, "has_people": has_people}
        return None

    base_query = strip_human_words(clean_query(query)) if no_people else query
    base_fallback = strip_human_words(fallback_query) if no_people else fallback_query
    queries = query_ladder(base_query, base_fallback)
    # মানুষের দৃশ্যের সীমা পূর্ণ হলে একই অর্থের মানুষবিহীন বিকল্প query-ও খোঁজা হয়।
    if not no_people and state.get("people_scenes", 0) >= people_limit:
        safe_queries = query_ladder(strip_human_words(clean_query(query)), strip_human_words(fallback_query))
        queries.extend(q for q in safe_queries if q and q not in queries)
    for q in queries:
        for fn in order:
            got = _try(fn, q)
            if got:
                return got
    if mc.get("picsum_fallback", False) and not no_people:
        return _try(_picsum_photos, "random")
    return None
