"""AI মডেলে অনুরোধ পাঠানো (Anthropic বা Gemini) + JSON বের করা।"""
import json
import os
import re
import sys
import time

import requests


class LLMError(RuntimeError):
    """fatal=True মানে আবার চেষ্টা করে লাভ নেই (কী ভুল/নেই, মডেলের নাম ভুল, কোটা শেষ)।"""

    def __init__(self, msg, fatal=False, kind="other", daily=False):
        super().__init__(msg)
        self.fatal = fatal
        self.daily = daily  # true = দৈনিক কোটা শেষ; আজ আর চেষ্টা করে লাভ নেই
        # kind: key (কী নেই/ভুল) | model (নাম ভুল) | quota | server | network | other
        # key ছাড়া বাকি ক্ষেত্রে বিকল্প মডেলে চেষ্টা করা যায়
        self.kind = kind


def _redact(text) -> str:
    """ত্রুটির লেখা থেকে API কী মুছে দেয় (Gemini কী URL-এ থাকে, তাই লগে ঢুকে যেতে পারে)।"""
    return re.sub(r"(key=)[^&\s'\"]+", r"\1***", str(text))


def _explain(status, text):
    """HTTP ত্রুটি → সহজ বাংলা কারণ ও সমাধান। ফেরত: (বার্তা, fatal, kind)"""
    t = (text or "").lower()
    if status == 429:  # আগে এটা দেখি: Gemini-র 429 উত্তরেও "billing" শব্দ থাকে
        return "ফ্রি কোটা বা রেট লিমিট শেষ। কিছুক্ষণ পরে বা পরদিন আবার চালান", True, "quota"
    if "credit balance" in t or "billing" in t:
        return "AI অ্যাকাউন্টে ক্রেডিট/বিলিং নেই। অ্যাকাউন্টে ক্রেডিট যোগ করুন বা Gemini (ফ্রি) ব্যবহার করুন", True, "key"
    if status in (400, 401, 403) and any(k in t for k in ("api key", "api_key", "x-api-key", "authentication",
                                                             "unauthenticated", "permission")):
        return ("API কী ভুল বা কাজ করছে না। নতুন কী বানিয়ে GitHub Secrets-এ আবার বসান "
                "(শুরু/শেষে যেন স্পেস বা নতুন লাইন না থাকে)"), True, "key"
    if status == 404:
        return "মডেলের নাম ভুল বা এই কী দিয়ে মডেলটি পাওয়া যায় না। config.yaml-এর মডেলের নাম দেখুন", True, "model"
    if status == 429:
        return "ফ্রি কোটা বা রেট লিমিট শেষ। কিছুক্ষণ পরে বা পরদিন আবার চালান", True, "quota"
    if status is not None and status >= 500:
        return "AI সার্ভারে এখন চাপ বেশি (সাময়িক সমস্যা)। একটু পরে আবার চালান", False, "server"
    if status == 400:
        return "AI-এর কাছে পাঠানো অনুরোধে সমস্যা", False, "other"
    if status is None:
        return "ইন্টারনেট/নেটওয়ার্ক সমস্যা বা AI সময়মতো উত্তর দেয়নি", False, "network"
    return "AI থেকে অপ্রত্যাশিত উত্তর", False, "other"


def _post(url, headers, body, retries=3, timeout=60, fast429=False):
    last, status, text, daily = None, None, "", False
    for i in range(retries):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            status, text = r.status_code, r.text[:300]
            daily = status == 429 and "perday" in r.text.lower().replace(" ", "")
            if daily:  # দৈনিক কোটা: অপেক্ষা বা রিট্রাই করে লাভ নেই
                last = f"HTTP {status}: {text}"
                break
            last = f"HTTP {status}: {text}"
            if status not in (408, 409, 429, 500, 502, 503, 529):
                break
            if status == 429 and fast429:  # একাধিক কী থাকলে অপেক্ষা না করে সঙ্গে সঙ্গে পরের কী ধরি
                break
        except requests.RequestException as e:  # নেটওয়ার্ক সমস্যা / সময় শেষ
            status, text, last = None, "", f"নেটওয়ার্ক: {e}"
        if i < retries - 1:
            time.sleep(2 ** (i + 1))
    why, fatal, kind = _explain(status, text)
    raise LLMError(_redact(f"{why} [{last or 'অজানা ত্রুটি'}]"), fatal=fatal, kind=kind, daily=daily)


def _gemini_full(llm_cfg, model, key, system, user, max_tokens, temperature, retries,
                 tools=None, json_mode=True, extra_parts=None, timeout=60):
    """Gemini-কে অনুরোধ। ফেরত: (লেখা, উৎসের তালিকা)।
    tools দিলে (যেমন গুগল সার্চ) JSON মোড বন্ধ থাকে; extra_parts দিয়ে অডিও/ছবি পাঠানো যায়।"""
    gen = {"maxOutputTokens": max_tokens * 3}  # Gemini 3.x-এ চিন্তার (thinking) টোকেনও এই সীমায় গোনা হয়
    if json_mode and not tools:
        gen["responseMimeType"] = "application/json"
    if model.startswith("gemini-3"):
        # Gemini 3.x: নিচু চিন্তার স্তরে দ্রুত উত্তর (low | medium | high); temperature এখানে বাদ
        gen["thinkingConfig"] = {"thinkingLevel": llm_cfg.get("gemini_thinking", "low")}
    else:
        gen["temperature"] = temperature
    parts = list(extra_parts or []) + [{"text": user}]
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": gen,
    }
    if tools:
        body["tools"] = tools
    data = _post_rotating(model, body, retries, timeout)
    try:
        cand = data["candidates"][0]
        parts = cand["content"]["parts"]
    except (KeyError, IndexError):
        raise LLMError(f"Gemini থেকে খালি উত্তর: {_redact(str(data)[:300])}")
    sources = []
    for ch in (cand.get("groundingMetadata") or {}).get("groundingChunks", []) or []:
        w = ch.get("web") or {}
        if w.get("uri"):
            sources.append({"title": w.get("title", ""), "uri": w["uri"]})
    return "".join(p.get("text", "") for p in parts if not p.get("thought")), sources


def _gemini(llm_cfg, model, key, system, user, max_tokens, temperature, retries):
    return _gemini_full(llm_cfg, model, key, system, user, max_tokens, temperature, retries)[0]


# ---- একাধিক Gemini কী: একটার কোটা শেষ হলে পরেরটা, শেষেরটার পর আবার প্রথমটা (গোল ঘুরে) ----
KEY_NAMES = ["GEMINI_API_KEY"] + [f"GEMINI_API_KEY_{n}" for n in range(2, 11)]
DAILY_COOLDOWN = 6 * 3600  # দৈনিক কোটা শেষ হলে এই রানে ওই (মডেল, কী) আর ছোঁয়া হয় না
KEY_COOLDOWN = 90  # সেকেন্ড: কোটা-শেষ কী এতক্ষণ বাদ থাকে, তারপর আবার চেষ্টা (মিনিটের সীমা হলে ফিরে আসে)
_KEY_STATE = {"i": 0}
_COOLDOWN = {}  # (মডেল, কী-নম্বর) -> কখন পর্যন্ত বাদ


def gemini_keys():
    """সেট করা সব Gemini কী (GEMINI_API_KEY, GEMINI_API_KEY_2 … _10; কমা দিয়ে লেখা GEMINI_API_KEYS-ও চলে)।"""
    raw = [os.environ.get(n, "") for n in KEY_NAMES]
    raw += os.environ.get("GEMINI_API_KEYS", "").split(",")
    keys = []
    for k in raw:
        k = k.strip()
        if k and k not in keys:
            keys.append(k)
    return keys


def _gemini_key():
    keys = gemini_keys()
    if not keys:
        raise LLMError(
            "GEMINI_API_KEY নেই। GitHub রিপোর Settings → Secrets and variables → Actions-এ "
            "GEMINI_API_KEY নামে কী যোগ করুন (aistudio.google.com/apikey থেকে ফ্রি কী পাবেন; "
            "বাড়তি কী GEMINI_API_KEY_2 … GEMINI_API_KEY_5 নামে দিন)",
            fatal=True, kind="key")
    return keys[0]


def _post_rotating(model, body, retries, timeout):
    """কী ঘুরিয়ে ঘুরিয়ে অনুরোধ পাঠায়। কোটা শেষ (429) বা কী ভুল হলে পরের কী; অন্য ত্রুটি সোজা উপরে যায়।"""
    _gemini_key()  # কোনো কী না থাকলে এখানেই পরিষ্কার ত্রুটি
    keys = gemini_keys()
    n = len(keys)
    start = _KEY_STATE["i"] % n
    last = None
    for step in range(n):
        idx = (start + step) % n
        until = _COOLDOWN.get((model, idx), 0)
        if time.time() < until:
            continue
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={keys[idx]}"
        try:
            data = _post(url, {"content-type": "application/json"}, body, retries=retries,
                         timeout=timeout, fast429=(n > 1))
            _KEY_STATE["i"] = idx
            return data
        except LLMError as e:
            if e.kind not in ("quota", "key"):
                raise
            last = e
            _COOLDOWN[(model, idx)] = time.time() + (DAILY_COOLDOWN if e.daily else KEY_COOLDOWN)
            if n > 1:
                why = "কোটা শেষ" if e.kind == "quota" else "কী কাজ করছে না"
                nxt = (idx + 1) % n + 1
                print(f"  ⚠ Gemini কী নং {idx + 1}: {why} ({model}); কী নং {nxt} দিয়ে চেষ্টা করছি", file=sys.stderr)
    if last is None:  # সব কী এখন বিশ্রামে
        raise LLMError(f"ফ্রি কোটা বা রেট লিমিট শেষ (সব {n}টি কী-ই এই মডেলে ব্যস্ত)। কিছুক্ষণ পরে আবার চালান",
                       fatal=True, kind="quota")
    if n > 1:
        last = LLMError(f"সব {n}টি Gemini কী-তেই সমস্যা: {last}", fatal=last.fatal, kind=last.kind, daily=last.daily)
    raise last


def _run_gemini(cfg, task, call):
    """call(model, key, retries) চালায়; প্রধান মডেল ব্যর্থ হলে বিকল্প মডেলে চেষ্টা করে।"""
    key = _gemini_key()
    llm_cfg = cfg["llm"]
    primary = llm_cfg["gemini_model"]
    fallback = llm_cfg.get("gemini_fallback_model")
    if task == "ideas":  # ফাঁকা থাকলে স্ক্রিপ্টের মডেলই চলবে
        primary = llm_cfg.get("gemini_idea_model") or primary
        fallback = llm_cfg.get("gemini_idea_fallback_model") or fallback
    try:
        return call(primary, key, 3)
    except LLMError as e:
        # কী ঠিক না থাকলে বিকল্প মডেলে লাভ নেই; বাকি ক্ষেত্রে (চাপ, কোটা, নাম) বিকল্প চেষ্টা করি
        if not fallback or fallback == primary or e.kind == "key":
            raise
        print(f"  ⚠ {primary} কাজ করেনি, {fallback} দিয়ে চেষ্টা করছি", file=sys.stderr)
        try:
            return call(fallback, key, 2)
        except LLMError as e2:
            raise LLMError(f"{e} | বিকল্প মডেল {fallback}-ও ব্যর্থ: {e2}", fatal=e.fatal and e2.fatal,
                           kind=e.kind)


def complete(cfg, system, user, max_tokens=4000, temperature=0.9, task=None) -> str:
    """task=\"ideas\" দিলে আইডিয়ার জন্য আলাদা Gemini মডেল (gemini_idea_model) ব্যবহার হয়।"""
    provider = cfg["llm"]["provider"]
    if provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            raise LLMError("ANTHROPIC_API_KEY নেই। GitHub রিপোর Settings → Secrets and variables → Actions-এ "
                           "ANTHROPIC_API_KEY নামে কী যোগ করুন (অথবা config.yaml-এ provider: gemini দিন)", fatal=True, kind="key")
        data = _post(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            {
                "model": cfg["llm"]["anthropic_model"],
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
        return "".join(b.get("text", "") for b in data.get("content", []))
    if provider == "gemini":
        llm_cfg = cfg["llm"]
        return _run_gemini(cfg, task, lambda model, key, retries: _gemini(
            llm_cfg, model, key, system, user, max_tokens, temperature, retries))
    raise LLMError(f"অজানা provider: {provider} (config.yaml-এ anthropic বা gemini লিখুন)", fatal=True, kind="key")


def complete_grounded(cfg, system, user, max_tokens=3000, temperature=0.4):
    """গুগল সার্চে যাচাই করা (grounded) উত্তর। শুধু Gemini-তে চলে। ফেরত: (লেখা, উৎসের তালিকা)।"""
    if cfg["llm"]["provider"] != "gemini":
        raise LLMError("ওয়েব গবেষণা শুধু Gemini provider-এ চলে", fatal=False, kind="other")
    llm_cfg = cfg["llm"]
    return _run_gemini(cfg, None, lambda model, key, retries: _gemini_full(
        llm_cfg, model, key, system, user, max_tokens, temperature, retries,
        tools=[{"google_search": {}}], json_mode=False, timeout=120))


def transcribe_audio(cfg, audio_path, instruction, max_tokens=4000):
    """অডিও ফাইল (ছোট mp3/wav, ~১৫ এমবি পর্যন্ত) Gemini-কে শুনিয়ে JSON উত্তর নেয়। শুধু Gemini।"""
    import base64
    from pathlib import Path
    if cfg["llm"]["provider"] != "gemini":
        raise LLMError("অডিও থেকে ক্যাপশন বানাতে llm.provider: gemini লাগবে", fatal=True, kind="other")
    p = Path(audio_path)
    mime = {".mp3": "audio/mp3", ".wav": "audio/wav", ".m4a": "audio/mp4", ".ogg": "audio/ogg"}.get(p.suffix.lower(), "audio/mp3")
    part = {"inline_data": {"mime_type": mime, "data": base64.b64encode(p.read_bytes()).decode()}}
    llm_cfg = cfg["llm"]
    return _run_gemini(cfg, None, lambda model, key, retries: _gemini_full(
        llm_cfg, model, key, "তুমি নিখুঁত বাংলা ট্রান্সক্রাইবার। কেবল JSON দেবে।", instruction, max_tokens, 0.2,
        retries, extra_parts=[part], timeout=180)[0])


def extract_json(text: str):
    """উত্তর থেকে প্রথম বৈধ JSON (অবজেক্ট বা অ্যারে) বের করে।"""
    text = re.sub(r"```(?:json)?", "", text).strip()
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise ValueError("উত্তরে JSON পাওয়া যায়নি")
    start = min(starts)
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("JSON অসম্পূর্ণ")
