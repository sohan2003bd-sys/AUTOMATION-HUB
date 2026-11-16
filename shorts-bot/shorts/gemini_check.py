"""Gemini কী-গুলোর পরীক্ষা: প্রতিটি কী কাজ করছে কিনা, কোটার আসল কারণ কী, গ্রাউন্ডিং (গুগল সার্চ) আলাদাভাবে আটকাচ্ছে কিনা।
চালান: python -m shorts gemini-check   (GitHub Actions-এর 'gemini-check' ওয়ার্কফ্লো থেকেও চলে)
প্রতি কী-তে মাত্র ৩টি ছোট অনুরোধ যায়; কী কখনো লগে ছাপা হয় না।"""
import re
import sys

import requests

from . import llm

BASE = "https://generativelanguage.googleapis.com/v1beta"


def _compact(text, n=900):
    return re.sub(r"\s+", " ", str(text or "")).strip()[:n]


def _post(key, model, body):
    try:
        r = requests.post(f"{BASE}/models/{model}:generateContent", headers={"x-goog-api-key": key,
                          "content-type": "application/json"}, json=body, timeout=60)
    except requests.RequestException as e:
        return None, f"নেটওয়ার্ক: {e}"
    if r.status_code == 200:
        try:
            parts = r.json()["candidates"][0]["content"]["parts"]
            return 200, "ঠিক আছে — উত্তর: " + _compact("".join(p.get("text", "") for p in parts), 60)
        except (KeyError, IndexError, ValueError):
            return 200, "উত্তর এসেছে কিন্তু খালি/অন্য ধরনের"
    detail, _ = llm._quota_detail(r.text)
    return r.status_code, detail or _compact(r.text)


def run(cfg) -> int:
    keys = llm.gemini_keys()
    if not keys:
        print("কোনো GEMINI_API_KEY পাওয়া যায়নি।")
        return 1
    lc = cfg["llm"]
    models = [m for m in dict.fromkeys([lc.get("gemini_model"), lc.get("gemini_fallback_model")]) if m]
    print(f"মোট {len(keys)}টি কী; মডেল: {', '.join(models)}\n")
    plain = {"contents": [{"role": "user", "parts": [{"text": "বলো: ঠিক আছে"}]}],
             "generationConfig": {"maxOutputTokens": 60}}
    grounded = {**plain, "tools": [{"google_search": {}}]}
    summary = {}
    for i, k in enumerate(keys, 1):
        print(f"=== কী নং {i} (দৈর্ঘ্য {len(k)}, শুরু {k[:4]}…) ===")
        try:
            r = requests.get(f"{BASE}/models", headers={"x-goog-api-key": k}, params={"pageSize": 200}, timeout=30)
            if r.status_code == 200:
                names = {m.get("name", "").split("/")[-1] for m in r.json().get("models", [])}
                miss = [m for m in models if m not in names]
                print(f"  কী বৈধ। মডেল-তালিকায় নেই: {', '.join(miss) if miss else 'সব আছে'}")
            else:
                print(f"  মডেল-তালিকা: HTTP {r.status_code} {_compact(r.text, 300)}")
        except requests.RequestException as e:
            print("  মডেল-তালিকা: নেটওয়ার্ক সমস্যা", e)
        for m in models:
            for label, body in (("সাধারণ", plain), ("গুগল-সার্চসহ", grounded)):
                st, msg = _post(k, m, body)
                summary[(i, m, label)] = st
                print(f"  {m} [{label}] → {st}: {msg}")
        print()
    print("---- সারসংক্ষেপ ----")
    for m in models:
        for label in ("সাধারণ", "গুগল-সার্চসহ"):
            ok = sum(1 for (i, mm, ll), st in summary.items() if mm == m and ll == label and st == 200)
            print(f"{m} [{label}]: {ok}/{len(keys)} কী কাজ করছে")
    return 0
