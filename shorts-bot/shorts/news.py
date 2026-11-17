"""আজকের আসল সংবাদ-শিরোনাম (Google News RSS, বিনামূল্যে, কোনো API কী লাগে না)।
Gemini-র গুগল-সার্চ (গ্রাউন্ডিং) না চললে এটাই 'টাটকা বিষয়' ও 'তথ্যের সূত্র'-র কাজ করে:
  fetch()      — কোনো ক্যাটাগরির আজকের শিরোনাম
  facts_for()  — একটা বিষয়ের সঙ্গে মেলে এমন শিরোনাম, গবেষণার 'যাচাই-করা তথ্য'-র বদলে
নেটওয়ার্ক/পার্সে সমস্যা হলে চুপচাপ খালি তালিকা ফেরত দেয় (ভিডিও বানানো আটকায় না)।"""
import re
import sys
import time
from urllib.parse import quote
from xml.etree import ElementTree as ET

import requests

BASE = "https://news.google.com/rss"
UA = {"User-Agent": "Mozilla/5.0 (compatible; shorts-bot)"}


def enabled(cfg) -> bool:
    return bool((cfg.get("news") or {}).get("enabled", False))


def _url(cfg, query):
    n = cfg.get("news") or {}
    hl, gl, ceid = n.get("hl", "bn"), n.get("gl", "BD"), n.get("ceid", "BD:bn")
    if query:
        q = f"{query} when:{n.get('max_age', '2d')}"
        return f"{BASE}/search?q={quote(q)}&hl={hl}&gl={gl}&ceid={ceid}"
    return f"{BASE}?hl={hl}&gl={gl}&ceid={ceid}"


def parse(xml_text, limit=12):
    """RSS লেখা থেকে [{title, source, date}]; একই শিরোনাম দুবার নয়।"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out, seen = [], set()
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        se = next((c for c in it if c.tag.split("}")[-1].lower() == "source"), None)  # Google: <source>, Bing: <News:Source>
        source = (se.text or "").strip() if se is not None else ""
        if source and title.endswith(" - " + source):
            title = title[: -(len(source) + 3)].strip()
        elif not source and " - " in title:
            title, source = [x.strip() for x in title.rsplit(" - ", 1)]
        key = re.sub(r"\W+", "", title.lower())[:40]
        if len(title) < 12 or key in seen:
            continue
        seen.add(key)
        out.append({"title": title, "source": source, "date": (it.findtext("pubDate") or "").strip()[:16]})
        if len(out) >= limit:
            break
    return out


_CACHE = {}


def _bing_url(cfg, query):
    return f"https://www.bing.com/news/search?q={quote(query or 'বাংলাদেশ আজকের খবর')}&format=RSS&setlang=bn&cc=BD"


def _get(url):
    """HTTP 429 (অতিরিক্ত অনুরোধ) পেলে একটু থেমে আবার; অন্য ত্রুটিতে None।"""
    for i, wait in enumerate((0, 6, 15)):
        if wait:
            time.sleep(wait)
        try:
            r = requests.get(url, headers=UA, timeout=15)
        except requests.RequestException as e:
            print(f"  ⚠ সংবাদ-শিরোনাম পাওয়া যায়নি ({e})", file=sys.stderr)
            return None
        if r.status_code == 200:
            return r.text
        if r.status_code == 429 and i < 2:
            continue
        print(f"  ⚠ সংবাদ-শিরোনাম পাওয়া যায়নি (HTTP {r.status_code})", file=sys.stderr)
        return None
    return None


def fetch(cfg, query="", limit=12):
    key = (query, limit)
    if key in _CACHE:
        return _CACHE[key]
    out = []
    txt = _get(_url(cfg, query))
    if txt:
        out = parse(txt, limit)
    if not out and (cfg.get("news") or {}).get("bing_fallback", True):  # গুগলে না পেলে Bing News RSS
        txt = _get(_bing_url(cfg, query))
        if txt:
            out = parse(txt, limit)
    if out:
        _CACHE[key] = out
    return out


def facts_for(cfg, topic):
    """বিষয়ের সঙ্গে মেলে এমন কমপক্ষে ২টি শিরোনাম থাকলে গবেষণার ফল (research.gather-এর ফরম্যাটে), নইলে None।"""
    if not enabled(cfg):
        return None
    words = re.findall(r"\w+", topic or "")
    if len(words) < 2:
        return None
    heads = fetch(cfg, " ".join(words[:8]), 8)
    if len(heads) < 2:
        return None
    facts = [f"{h['title']} (সূত্র: {h['source'] or 'সংবাদমাধ্যম'}, {h['date']})" for h in heads[:8]]
    sources = list(dict.fromkeys(h["source"] for h in heads if h["source"]))[:3]
    return {"facts": facts, "sources": sources, "rejected": None, "ok": True}
