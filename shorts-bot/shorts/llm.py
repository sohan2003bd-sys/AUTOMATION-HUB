"""AI মডেলে অনুরোধ পাঠানো (Anthropic বা Gemini) + JSON বের করা।"""
import json
import os
import re
import time

import requests


class LLMError(RuntimeError):
    """fatal=True মানে আবার চেষ্টা করে লাভ নেই (কী ভুল/নেই, মডেলের নাম ভুল, কোটা শেষ)।"""

    def __init__(self, msg, fatal=False):
        super().__init__(msg)
        self.fatal = fatal


def _redact(text) -> str:
    """ত্রুটির লেখা থেকে API কী মুছে দেয় (Gemini কী URL-এ থাকে, তাই লগে ঢুকে যেতে পারে)।"""
    return re.sub(r"(key=)[^&\s'\"]+", r"\1***", str(text))


def _explain(status, text):
    """HTTP ত্রুটি → সহজ বাংলা কারণ ও সমাধান। ফেরত: (বার্তা, fatal)"""
    t = (text or "").lower()
    if "credit balance" in t or "billing" in t:
        return "AI অ্যাকাউন্টে ক্রেডিট/বিলিং নেই। অ্যাকাউন্টে ক্রেডিট যোগ করুন বা Gemini (ফ্রি) ব্যবহার করুন", True
    if status in (400, 401, 403) and any(k in t for k in ("api key", "api_key", "x-api-key", "authentication",
                                                             "unauthenticated", "permission")):
        return ("API কী ভুল বা কাজ করছে না। নতুন কী বানিয়ে GitHub Secrets-এ আবার বসান "
                "(শুরু/শেষে যেন স্পেস বা নতুন লাইন না থাকে)"), True
    if status == 404:
        return "মডেলের নাম ভুল বা এই কী দিয়ে মডেলটি পাওয়া যায় না। config.yaml-এর মডেলের নাম দেখুন", True
    if status == 429:
        return "ফ্রি কোটা বা রেট লিমিট শেষ। কিছুক্ষণ পরে বা পরদিন আবার চালান", True
    if status is not None and status >= 500:
        return "AI সার্ভারে সাময়িক সমস্যা। একটু পরে আবার চালান", False
    if status == 400:
        return "AI-এর কাছে পাঠানো অনুরোধে সমস্যা", False
    if status is None:
        return "ইন্টারনেট/নেটওয়ার্ক সমস্যা", False
    return "AI থেকে অপ্রত্যাশিত উত্তর", False


def _post(url, headers, body, retries=4):
    last, status, text = None, None, ""
    for i in range(retries):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=120)
            if r.status_code == 200:
                return r.json()
            status, text = r.status_code, r.text[:300]
            last = f"HTTP {status}: {text}"
            if status not in (408, 409, 429, 500, 502, 503, 529):
                break
        except requests.RequestException as e:  # নেটওয়ার্ক সমস্যা
            status, text, last = None, "", f"নেটওয়ার্ক: {e}"
        if i < retries - 1:
            time.sleep(2 ** (i + 1))
    why, fatal = _explain(status, text)
    raise LLMError(_redact(f"{why} [{last or 'অজানা ত্রুটি'}]"), fatal=fatal)


def complete(cfg, system, user, max_tokens=4000, temperature=0.9) -> str:
    provider = cfg["llm"]["provider"]
    if provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            raise LLMError("ANTHROPIC_API_KEY নেই। GitHub রিপোর Settings → Secrets and variables → Actions-এ "
                           "ANTHROPIC_API_KEY নামে কী যোগ করুন (অথবা config.yaml-এ provider: gemini দিন)", fatal=True)
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
                           "GEMINI_API_KEY নামে কী যোগ করুন (aistudio.google.com/apikey থেকে ফ্রি কী পাবেন)", fatal=True)
        model = cfg["llm"]["gemini_model"]
        data = _post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
            {"content-type": "application/json"},
            {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens,
                                     "responseMimeType": "application/json"},
            },
        )
        try:
            return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError):
            raise LLMError(f"Gemini থেকে খালি উত্তর: {str(data)[:300]}")
    raise LLMError(f"অজানা provider: {provider} (config.yaml-এ anthropic বা gemini লিখুন)", fatal=True)


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
