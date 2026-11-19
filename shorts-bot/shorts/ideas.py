"""প্রতিটি ক্যাটাগরির জন্য ভিডিও-আইডিয়া বানায়: আজকের ভাইরাল ও মানুষের জানা-দরকার বিষয় (গুগল সার্চ দিয়ে খুঁজে)।"""
import re
import sys
from datetime import datetime, timedelta, timezone

from . import llm, news
from .llm import extract_json
from .research import _bullets

SAFETY = (
    "নিয়ম: (১) শুধু নির্ভরযোগ্য সূত্রে নিশ্চিত হওয়া বিষয়; গুজব, অপ্রমাণিত দাবি বা ভুয়া খবরকে সত্য বলে দেবে না "
    "(ভুয়া খবরের সংশোধন—'আসলে সত্যটা কী'—দেওয়া যাবে)। "
    "(২) দলীয় রাজনীতির পক্ষ-বিপক্ষ, ধর্মীয়/জাতিগত উসকানি, কারও চরিত্রহনন, গ্রাফিক সহিংসতা বা মৃত্যুর বিবরণ নয়। "
    "(৩) চিকিৎসা/বিনিয়োগ/আইনের ব্যক্তিগত পরামর্শ বা আয়ের প্রতিশ্রুতি নয়—শুধু সচেতনতা ও যাচাই-করা তথ্য। "
    "(৪) ক্র্যাক/পাইরেসি, হ্যাকিং শেখানো নয়।"
)

SYSTEM = (
    "তুমি বাংলাদেশি দর্শকদের জন্য YouTube Shorts-এর আইডিয়া-সম্পাদক। প্রতিটি আইডিয়া হবে তথ্যসমৃদ্ধ, কাজে লাগার মতো, "
    "নির্দিষ্ট এবং আগের বিষয়গুলো থেকে স্পষ্ট আলাদা। একই খবরকে নতুন শব্দে আবার পরিবেশন করবে না। " + SAFETY +
    " উত্তর কেবল JSON অ্যারে (বাংলা স্ট্রিংয়ের তালিকা), অন্য কিছু নয়।"
)

GROUND_SYSTEM = (
    "তুমি বাংলাদেশি দর্শকদের জন্য YouTube Shorts-এর আইডিয়া-গবেষক। গুগল সার্চ ব্যবহার করে আজকের সত্যিকার আলোচিত "
    "বিষয় খুঁজে বের করো। " + SAFETY
)


def today(cfg):
    off = timedelta(hours=(cfg.get("upload") or {}).get("timezone_offset_hours", 6))
    return (datetime.now(timezone.utc) + off).strftime("%Y-%m-%d")


def _avoid(store, cat):
    """সব ক্যাটাগরির সাম্প্রতিক বিষয় দেখায়, যাতে একই ঘটনা অন্য নামে ফিরে না আসে।"""
    rows = getattr(store, "rows", [])[-100:]
    items = [f"- [{r.get('category', 'other')}] {r.get('topic', '')}" for r in rows if r.get("topic")]
    return "\n".join(items) or "(এখনো কিছু নেই)"


def _trending(cfg, store, cat, count):
    country = (cfg.get("ideas") or {}).get("country", "Bangladesh")
    user = (
        f"আজকের তারিখ: {today(cfg)} (বাংলাদেশ সময়)। দর্শক: {country}-এর সাধারণ মানুষ।\n"
        f"ক্যাটাগরি: {cat['name']}\nপরিধি: {cat['hint']}\n\n"
        f"গুগল সার্চ করে গত ২–৩ দিনে এই ক্যাটাগরিতে সবচেয়ে বেশি আলোচিত/ভাইরাল এবং মানুষের সত্যিই জানা দরকার—এমন "
        f"{count}টি আলাদা বিষয় বের করো। প্রতিটি এমন হবে যা ৪০ সেকেন্ডে সহজে বোঝানো যায়: কী ঘটল বা কী জানা দরকার + "
        f"কেন গুরুত্বপূর্ণ + দর্শকের কী করা উচিত।\n\n"
        f"এগুলোর মতো বা কাছাকাছি দেবে না:\n{_avoid(store, cat)}\n\n"
        "ফরম্যাট: প্রতিটি আইডিয়া আলাদা লাইনে '- ' দিয়ে শুরু, একটা বাক্যে বাংলায়, বিষয়ের মূল নাম ও সম্ভব হলে সময় সহ। শুধু তালিকা।"
    )
    text, _ = llm.complete_grounded(cfg, GROUND_SYSTEM, user, max_tokens=2500)
    return [b for b in _bullets(text) if 12 <= len(b) <= 260][: count * 2]


def _plain(cfg, store, cat, count):
    evergreen = cat.get("evergreen", True)
    scope = (
        "সময়-নিরপেক্ষ, দীর্ঘদিন প্রাসঙ্গিক শিক্ষামূলক/ব্যবহারিক বিষয় বেছে নাও। আজকের সংবাদ, চলমান ঘটনা, "
        "বর্তমান দাম বা সাময়িক ট্রেন্ডকে নতুনভাবে সাজিয়ে দেবে না। প্রতিটি বিষয়ের আলাদা সাব-টপিক থাকতে হবে।"
        if evergreen else
        "শুধু নির্ভরযোগ্যভাবে যাচাইযোগ্য সাম্প্রতিক আলোচিত বিষয় বেছে নাও; শিরোনাম/সূত্র ছাড়া খবর বানাবে না।"
    )
    user = (
        f"আজকের তারিখ: {today(cfg)}।\nক্যাটাগরি: {cat['name']}\nবিষয়ের পরিধি: {cat['hint']}\n\n"
        f"ঠিক {count}টি আলাদা আইডিয়া দাও। প্রতিটি একটি বাক্যে, বাংলায়। {scope}\n\n"
        f"এগুলোর মতো বা কাছাকাছি দেবে না (এই তালিকা সব ক্যাটাগরির আগের ভিডিও):\n{_avoid(store, cat)}\n\n"
        'ফরম্যাট: ["আইডিয়া ১", "আইডিয়া ২", ...]'
    )
    raw = llm.complete(cfg, SYSTEM, user, max_tokens=4000, temperature=1.0, task="ideas")
    items = extract_json(raw)
    if not isinstance(items, list):
        raise ValueError("অ্যারে আশা করা হয়েছিল")
    return [i for i in items if isinstance(i, str)]


def _match_headlines(idea, heads, k=2):
    """মডেল নম্বর না দিলে শব্দ-মিলে সবচেয়ে কাছের শিরোনাম বাছে (কমপক্ষে ২টি শব্দ মিলতে হবে)।"""
    iw = {w for w in re.findall(r"\w+", idea) if len(w) >= 3}
    scored = sorted(((len(iw & {w for w in re.findall(r"\w+", h["title"]) if len(w) >= 3}), i)
                     for i, h in enumerate(heads)), reverse=True)
    return [heads[i] for s, i in scored[:k] if s >= 2]


def _from_news(cfg, store, cat, count):
    """আজকের আসল শিরোনাম (Google News RSS) দেখে আইডিয়া — গুগল-সার্চ ছাড়াই টাটকা ও সত্যিকার বিষয়।"""
    if not news.enabled(cfg):
        return []
    q = ((cfg.get("news") or {}).get("queries") or {}).get(cat["id"], "")
    heads = news.fetch(cfg, q, 15)
    if len(heads) < 3:
        return []
    block = "\n".join(f"[{i}] {h['title']} ({h['source']}, {h['date']})" for i, h in enumerate(heads, 1))
    user = (
        f"আজকের তারিখ: {today(cfg)}।\nক্যাটাগরি: {cat['name']}\nপরিধি: {cat['hint']}\n\n"
        f"নিচে আজকের আসল সংবাদ-শিরোনাম:\n{block}\n\n"
        f"এই শিরোনামগুলো থেকে দর্শকের জন্য সবচেয়ে কাজের, একে অন্যের থেকে আলাদা ঠিক {count}টি ভিডিও-আইডিয়া বানাও। "
        "প্রতিটি আইডিয়া একটা বাক্যে, বাংলায়, শিরোনামের আসল ঘটনা/নাম সহ—শিরোনামে নেই এমন কোনো তথ্য নিজে থেকে যোগ করবে না। "
        "প্রতিটির সঙ্গে যে শিরোনাম-নম্বরগুলো থেকে নিয়েছ সেগুলো (১–৩টি) দাও। "
        "দলীয় রাজনীতির পক্ষ-বিপক্ষ, সহিংসতা বা গুজব বাদ দেবে।\n\n"
        f"এগুলোর মতো বা কাছাকাছি দেবে না:\n{_avoid(store, cat)}\n\n"
        'ফরম্যাট: [{"idea": "আইডিয়া ১", "src": [1, 4]}, {"idea": "আইডিয়া ২", "src": [2]}]'
    )
    try:
        items = extract_json(llm.complete(cfg, SYSTEM, user, max_tokens=4000, temperature=1.0, task="ideas"))
    except ValueError:
        return []
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if isinstance(it, str):  # পুরনো ধাঁচের উত্তর: শুধু লেখা
            out.append(it)
        elif isinstance(it, dict) and isinstance(it.get("idea"), str):
            idx = []
            for x in it.get("src") or []:
                m = re.search(r"\d+", str(x))
                if m and 1 <= int(m.group()) <= len(heads):
                    idx.append(int(m.group()))
            hs = [heads[i - 1] for i in dict.fromkeys(idx)][:3] or _match_headlines(it["idea"], heads)
            if not hs and not cat.get("evergreen", True):
                continue  # সূত্র ছাড়া খবরভিত্তিক আইডিয়া রাখলে পরে বাতিল হবে
            out.append({"topic": it["idea"],
                        "facts": [f"{h['title']} (সূত্র: {h['source'] or 'সংবাদমাধ্যম'}, {h['date']})" for h in hs],
                        "sources": list(dict.fromkeys(h["source"] for h in hs if h["source"]))})
    return out


def generate_for_category(cfg, store, cat, count):
    items = []
    no_search = False
    # কেবল evergreen: false (trending) ক্যাটাগরিই সংবাদ-সার্চ করবে। অন্য ক্যাটাগরি সংবাদ-শিরোনাম ধার করবে না।
    if not cat.get("evergreen", True) and (cfg.get("ideas") or {}).get("trending") \
            and cfg["llm"]["provider"] == "gemini":
        try:
            items = _trending(cfg, store, cat, count)
        except llm.LLMError as e:
            if e.fatal:
                raise
            no_search = True
            if e.kind == "grounding":
                print("  ℹ গুগল-সার্চ বন্ধ — আজকের সংবাদ-শিরোনাম থেকে আইডিয়া নিচ্ছি", file=sys.stderr)
            else:
                print(f"  ⚠ আজকের আলোচিত বিষয় খোঁজা যায়নি ({e}); সাধারণ আইডিয়ায় যাচ্ছি", file=sys.stderr)
    if not cat.get("evergreen", True) and not items and (no_search or not (cfg.get("ideas") or {}).get("trending")):
        items = _from_news(cfg, store, cat, count)
    if not items:
        # সার্চ ছাড়া মডেল আজকের খবর জানে না, গুজব/ভুল বানাতে পারে — তাই টাটকা-থাকা-দরকার ক্যাটাগরি বাদ
        # (ইচ্ছা করলে config-এ ideas.allow_news_without_search: true)
        if (no_search or llm.grounding_down()) and not cat.get("evergreen", True) \
                and not (cfg.get("ideas") or {}).get("allow_news_without_search", False):
            print(f"  ⚠ {cat['name']}: সার্চ ছাড়া আজকের খবরভিত্তিক আইডিয়া নিরাপদ নয়, বাদ দিলাম", file=sys.stderr)
            return 0
        items = _plain(cfg, store, cat, count)
    return store.add_topics(cat["id"], items)
