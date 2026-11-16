"""AI মডেলে অনুরোধ পাঠানো (Anthropic বা Gemini) + JSON বের করা।"""
import json
import os
import re
import sys
import time

import requests


class LLMError(RuntimeError):
    """fatal=True মানে আবার চেষ্টা করে লাভ নেই (কী ভুল/নেই, মডেলের নাম ভুল, কোটা শেষ)।"""

    def __init__(self, msg, fatal=False, kind="other"):
        super().__init__(msg)
        self.fatal = fatal
        # kind: key (কী নেই/ভুল) | model (নাম ভুল) | quota | server | network | other
        # key ছাড়া বাকি ক্ষেত্রে বিকল্প মডেলে চেষ্টা করা যায়
        self.kind = kind


def _redact(text) -> str:
    """ত্রুটির লেখা থেকে API কী মুছে দেয় (Gemini কী URL-এ থাকে, তাই লগে ঢুকে যেতে পারে)।"""
    return re.sub(r"(key=)[^&\s'\"]+", r"\1***", str(text))


def _explain(status, text):
    """HTTP ত্রুটি → সহজ বাংলা কারণ ও সমাধান। ফেরত: (বার্তা, fatal, kind)"""
    t = (text or "").lower()
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


def _post(url, headers, body, retries=3, timeout=60):
    last, status, text = None, None, ""
    for i in range(retries):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            status, text = r.status_code, r.text[:300]
            last = f"HTTP {status}: {text}"
            if status not in (408, 409, 429, 500, 502, 503, 529):
                break
        except requests.RequestException as e:  # নেটওয়ার্ক সমস্যা / সময় শেষ
            status, text, last = None, "", f"নেটওয়ার্ক: {e}"
        if i < retries - 1:
            time.sleep(2 ** (i + 1))
    why, fatal, kind = _explain(status, text)
    raise LLMError(_redact(f"{why} [{last or 'অজানা ত্রুটি'}]"), fatal=fatal, kind=kind)


def _gemini(llm_cfg, model, key, system, user, max_tokens, temperature, retries):
    gen = {"maxOutputTokens": max_tokens * 3,  # Gemini 3.x-এ চিন্তার (thinking) টোকেনও এই সীমায় গোনা হয়
           "responseMimeType": "application/json"}
    if model.startswith("gemini-3"):
        # Gemini 3.x: নিচু চিন্তার স্তরে দ্রুত উত্তর (low | medium | high); temperature এখানে বাদ
        gen["thinkingConfig"] = {"thinkingLevel": llm_cfg.get("gemini_thinking", "low")}
    else:
        gen["temperature"] = temperature
    data = _post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
        {"content-type": "application/json"},
        {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": gen,
        },
        retries=retries,
    )
    try:
        parts = data["candidates"][0]["content"]["parts"]
    except (KeyError, IndexError):
        raise LLMError(f"Gemini থেকে খালি উত্তর: {_redact(str(data)[:300])}")
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def complete(cfg, system, user, max_tokens=4000, temperature=0.9) -> str:
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
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            raise LLMError("GEMINI_API_KEY নেই। GitHub রিপোর Settings → Secrets and variables → Actions-এ "
                           "GEMINI_API_KEY নামে কী যোগ করুন (aistudio.google.com/apikey থেকে ফ্রি কী পাবেন)", fatal=True, kind="key")
        llm_cfg = cfg["llm"]
        primary = llm_cfg["gemini_model"]
        fallback = llm_cfg.get("gemini_fallback_model")
        try:
            return _gemini(llm_cfg, primary, key, system, user, max_tokens, temperature, retries=3)
        except LLMError as e:
            # কী ঠিক না থাকলে বিকল্প মডেলে লাভ নেই; বাকি ক্ষেত্রে (চাপ, কোটা, নাম) বিকল্প চেষ্টা করি
            if not fallback or fallback == primary or e.kind == "key":
                raise
            print(f"  ⚠ {primary} কাজ করেনি, {fallback} দিয়ে চেষ্টা করছি", file=sys.stderr)
            try:
                return _gemini(llm_cfg, fallback, key, system, user, max_tokens, temperature, retries=2)
            except LLMError as e2:
                raise LLMError(f"{e} | বিকল্প মডেল {fallback}-ও ব্যর্থ: {e2}", fatal=e.fatal and e2.fatal,
                               kind=e.kind)
    raise LLMError(f"অজানা provider: {provider} (config.yaml-এ anthropic বা gemini লিখুন)", fatal=True, kind="key")


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
