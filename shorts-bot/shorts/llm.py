"""AI মডেলে অনুরোধ পাঠানো (Anthropic বা Gemini) + JSON বের করা।"""
import json
import os
import re
import time

import requests


class LLMError(RuntimeError):
    pass


def _post(url, headers, body, retries=4):
    last = None
    for i in range(retries):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=120)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code not in (408, 409, 429, 500, 502, 503, 529):
                break
        except requests.RequestException as e:  # নেটওয়ার্ক সমস্যা
            last = str(e)
        time.sleep(2 ** (i + 1))
    raise LLMError(last or "অজানা ত্রুটি")


def complete(cfg, system, user, max_tokens=4000, temperature=0.9) -> str:
    provider = cfg["llm"]["provider"]
    if provider == "anthropic":
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise LLMError("ANTHROPIC_API_KEY সেট করা নেই")
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
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise LLMError("GEMINI_API_KEY সেট করা নেই")
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
    raise LLMError(f"অজানা provider: {provider}")


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
