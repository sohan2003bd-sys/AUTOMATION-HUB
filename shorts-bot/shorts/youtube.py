"""YouTube আপলোড (Data API v3, resumable upload) — শুধু requests দিয়ে, বাড়তি লাইব্রেরি লাগে না।

দরকারি এনভায়রনমেন্ট ভ্যারিয়েবল (GitHub Secrets): YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN
"""
import os
import time
from datetime import datetime, timedelta, timezone

import requests

TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"


class YouTubeError(RuntimeError):
    """fatal=True মানে এই রানে আর চেষ্টা করে লাভ নেই (টোকেন/কোটা/সীমার সমস্যা)।"""

    def __init__(self, msg, fatal=False):
        super().__init__(msg)
        self.fatal = fatal


# ---------- টোকেন ----------
def _env(name):
    # পেস্ট করার সময় ঢুকে যাওয়া স্পেস/নতুন লাইন/উদ্ধৃতি চিহ্ন সরিয়ে দেয়
    return os.environ.get(name, "").strip().strip('"').strip("'").strip()


def get_access_token():
    cid, secret, refresh = _env("YT_CLIENT_ID"), _env("YT_CLIENT_SECRET"), _env("YT_REFRESH_TOKEN")
    missing = [n for n, v in (("YT_CLIENT_ID", cid), ("YT_CLIENT_SECRET", secret), ("YT_REFRESH_TOKEN", refresh)) if not v]
    if missing:
        raise YouTubeError("GitHub Secrets-এ নেই: " + ", ".join(missing), fatal=True)
    try:
        r = requests.post(TOKEN_URL, data={
            "client_id": cid, "client_secret": secret,
            "refresh_token": refresh, "grant_type": "refresh_token"}, timeout=60)
    except requests.RequestException as e:
        raise YouTubeError(f"নেটওয়ার্ক সমস্যা (টোকেন): {e}")
    if r.status_code == 200:
        return r.json()["access_token"]
    try:
        err = r.json().get("error", "")
    except ValueError:
        err = ""
    hints = {
        "invalid_grant": "রিফ্রেশ টোকেন বাতিল বা ভুল। OAuth Playground থেকে নতুন টোকেন নিয়ে YT_REFRESH_TOKEN বদলান।",
        "invalid_client": "Client ID বা Client secret ভুল। YT_CLIENT_ID ও YT_CLIENT_SECRET মিলিয়ে দেখুন।",
        "unauthorized_client": "টোকেন অন্য ক্লায়েন্টের। যে Client ID দিয়ে টোকেন নিয়েছেন, Secrets-এ সেটাই বসান।",
    }
    raise YouTubeError(f"টোকেন পাওয়া যায়নি ({err or r.status_code}). " + hints.get(err, ""), fatal=True)


def token_scope(access_token):
    r = requests.get("https://oauth2.googleapis.com/tokeninfo", params={"access_token": access_token}, timeout=60)
    return r.json().get("scope", "") if r.status_code == 200 else ""


# ---------- মেটাডেটা ----------
def clean_meta(meta):
    """YouTube-এর সীমা: শিরোনাম ≤100, বর্ণনা ≤5000, ট্যাগ মোট ≤500 অক্ষর; '<' '>' চলে না।"""
    def strip(s):
        return str(s).replace("<", "").replace(">", "").strip()

    title = strip(meta["title"])[:100]
    desc = strip(meta.get("description", ""))[:4900]
    tags, total = [], 0
    for t in meta.get("tags", []):
        t = strip(t).lstrip("#")[:30]
        if not t:
            continue
        cost = len(t) + (2 if " " in t else 0) + 1
        if total + cost > 400 or len(tags) >= 15:
            break
        tags.append(t)
        total += cost
    return {"title": title, "description": desc, "tags": tags}


# ---------- শিডিউল ----------
def next_slots(cfg, n, not_before_utc, now_utc=None):
    """আপলোড কনফিগের `slots` (স্থানীয় সময়) থেকে not_before-এর পরের n টি সময় (UTC) দেয়।"""
    u = cfg["upload"]
    tz = timezone(timedelta(hours=float(u.get("timezone_offset_hours", 6))))
    now = now_utc or datetime.now(timezone.utc)
    start = max(not_before_utc, now + timedelta(minutes=20))
    times = sorted(tuple(int(x) for x in s.split(":")) for s in u["slots"])
    out, day = [], start.astimezone(tz).date()
    while len(out) < n:
        for h, m in times:
            dt = datetime(day.year, day.month, day.day, h, m, tzinfo=tz)
            if dt > start:
                out.append(dt.astimezone(timezone.utc))
                if len(out) == n:
                    break
        day += timedelta(days=1)
    return out


def iso_z(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ---------- আপলোড ----------
FATAL_REASONS = {
    "quotaExceeded": "API কোটা শেষ। আগামীকাল (প্যাসিফিক সময় মধ্যরাতের পর) আবার চলবে।",
    "uploadLimitExceeded": "চ্যানেলের দৈনিক আপলোড সীমা শেষ। আগামীকাল আবার চলবে।",
    "dailyLimitExceeded": "দৈনিক সীমা শেষ।",
    "accessNotConfigured": "YouTube Data API v3 চালু নেই (Google Cloud → Library → Enable)।",
    "insufficientPermissions": "টোকেনে youtube.upload অনুমতি নেই। Playground-এ স্কোপ দিয়ে নতুন টোকেন নিন।",
    "forbidden": "অনুমতি নেই (403)। চ্যানেল/অনুমতি/স্কোপ দেখুন।",
    "youtubeSignupRequired": "এই Gmail-এ ইউটিউব চ্যানেল নেই।",
    "authError": "অথেনটিকেশন ত্রুটি। নতুন টোকেন নিন।",
}


def _api_error(r):
    reason, message = "", ""
    try:
        e = r.json().get("error", {})
        message = e.get("message", "")
        reason = (e.get("errors") or [{}])[0].get("reason", "")
    except ValueError:
        pass
    hint = FATAL_REASONS.get(reason, "")
    fatal = bool(hint) or r.status_code in (401, 403)
    return YouTubeError(f"HTTP {r.status_code} {reason}: {hint or message[:200]}", fatal=fatal)


def _query_offset(session_url, size, token):
    """ব্যর্থ হলে জানে কতটুকু আপলোড হয়েছে, যাতে বাকিটা থেকে চালানো যায়।"""
    try:
        r = requests.put(session_url, headers={"Authorization": f"Bearer {token}",
                                               "Content-Range": f"bytes */{size}", "Content-Length": "0"}, timeout=60)
        rng = r.headers.get("Range", "")
        if r.status_code == 308 and rng.startswith("bytes=0-"):
            return int(rng.split("-")[1]) + 1
    except (requests.RequestException, ValueError):
        pass
    return 0


def _put(session_url, path, size, token, attempts=4):
    last, offset = None, 0
    for i in range(attempts):
        try:
            with open(path, "rb") as f:
                f.seek(offset)
                headers = {"Authorization": f"Bearer {token}", "Content-Type": "video/mp4"}
                if offset:
                    headers["Content-Range"] = f"bytes {offset}-{size - 1}/{size}"
                r = requests.put(session_url, data=f, headers=headers, timeout=900)
            if r.status_code in (200, 201):
                return r.json()
            if r.status_code in (500, 502, 503, 504):
                last = f"HTTP {r.status_code}"
                offset = _query_offset(session_url, size, token)
            else:
                raise _api_error(r)
        except requests.RequestException as e:
            last = str(e)
            offset = _query_offset(session_url, size, token)
        time.sleep(2 ** (i + 1))
    raise YouTubeError(f"আপলোড ব্যর্থ: {last}")


def upload_video(cfg, token, video_path, meta, privacy="private", publish_at=None):
    """একটা ভিডিও আপলোড করে। privacy: private | public | unlisted। publish_at (UTC datetime) দিলে শিডিউল হবে।"""
    u = cfg["upload"]
    m = clean_meta(meta)
    status = {"privacyStatus": "private" if publish_at else privacy,
              "selfDeclaredMadeForKids": False, "embeddable": True}
    if publish_at:
        status["publishAt"] = iso_z(publish_at)
    body = {"snippet": {"title": m["title"], "description": m["description"], "tags": m["tags"],
                        "categoryId": str(u.get("category_id", "28")),
                        "defaultLanguage": u.get("language", "bn"),
                        "defaultAudioLanguage": u.get("language", "bn")},
            "status": status}
    size = os.path.getsize(video_path)
    params = {"uploadType": "resumable", "part": "snippet,status",
              "notifySubscribers": str(bool(u.get("notify_subscribers", False))).lower()}
    try:
        r = requests.post(UPLOAD_URL, params=params, json=body, timeout=120, headers={
            "Authorization": f"Bearer {token}", "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(size)})
    except requests.RequestException as e:
        raise YouTubeError(f"নেটওয়ার্ক সমস্যা (আপলোড শুরু): {e}")
    if r.status_code != 200:
        raise _api_error(r)
    res = _put(r.headers["Location"], video_path, size, token)
    st = res.get("status", {})
    return {"id": res["id"], "privacy": st.get("privacyStatus", "?"), "publish_at": st.get("publishAt")}
