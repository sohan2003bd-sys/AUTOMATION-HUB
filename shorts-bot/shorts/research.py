"""তথ্য গবেষণা ও যাচাই।

১) gather(): স্ক্রিপ্ট লেখার আগে গুগল সার্চ (Gemini grounding) থেকে বিষয়টির সত্যি, হালনাগাদ তথ্য জোগাড়।
   বিষয়টি ভুল/পুরনো/অবাস্তব হলে "বাতিল" বলে ভিডিও বানানোই বন্ধ।
২) verify(): স্ক্রিপ্ট লেখার পর প্রতিটি দাবি জোগাড় করা তথ্যের সঙ্গে মেলানো; ভুল ধরা পড়লে ঠিক করে দেয়,
   মারাত্মক হলে স্ক্রিপ্ট বাতিল।

কোটা/নেটওয়ার্কে সমস্যা হলে (strict: false) গবেষণা বাদ দিয়ে আগের মতোই স্ক্রিপ্ট লেখা চলে, লগে সতর্কতা থাকে।
"""
import re
import sys
from urllib.parse import urlparse

from . import llm
from .llm import extract_json

RESEARCH_SYSTEM = (
    "তুমি একজন কঠোর তথ্য-গবেষক। গুগল সার্চ ব্যবহার করে বিষয়টি যাচাই করো। "
    "শুধু যা নির্ভরযোগ্য সূত্রে (অফিসিয়াল ডকুমেন্টেশন, কোম্পানির সাপোর্ট পেজ, নামী প্রযুক্তি সাইট) পেয়েছ তাই লিখবে। "
    "অ্যাপ/ফোনের মেনু ও বাটনের নাম হুবহু যেমন দেখা যায় (ইংরেজিতে) তেমন লিখবে। "
    "ভার্সন, তারিখ বা দেশভেদে ফিচার আলাদা হলে তা স্পষ্ট লিখবে। আন্দাজ বা স্মৃতি থেকে কিছু যোগ করবে না।"
)

VERIFY_SYSTEM = (
    "তুমি একজন ফ্যাক্ট-চেকার। তোমাকে একটি ভিডিও-স্ক্রিপ্ট ও যাচাই করা তথ্যের তালিকা দেওয়া হবে। "
    "স্ক্রিপ্টের প্রতিটি দাবি ও ধাপ তথ্যের সঙ্গে মেলাও। তথ্যে নেই বা তথ্যের সঙ্গে মেলে না এমন দাবি সমস্যা। "
    "উত্তর কেবল একটা JSON অবজেক্ট।"
)


def _bullets(text):
    out = []
    for ln in text.splitlines():
        ln = ln.strip()
        m = re.match(r"^(?:[-*•]|\d+[.)])\s+(.*)$", ln)
        if m and len(m.group(1)) > 8:
            out.append(m.group(1).strip())
    return out


def _domains(sources, limit=3):
    seen, out = set(), []
    for s in sources:
        host = urlparse(s.get("uri", "")).netloc
        title = (s.get("title") or "").strip()
        # grounding লিংক প্রায়ই vertexaisearch রিডাইরেক্ট হয়; তখন শিরোনামেই আসল সাইটের নাম থাকে
        name = title if ("vertexaisearch" in host or "googleapis" in host) and title else host or title
        name = name.removeprefix("www.")
        if name and name not in seen:
            seen.add(name)
            out.append(name)
        if len(out) >= limit:
            break
    return out


def enabled(cfg) -> bool:
    return bool((cfg.get("research") or {}).get("enabled", False)) and cfg["llm"]["provider"] == "gemini"


def gather(cfg, cat, topic):
    """ফেরত: {"facts": [...], "sources": [...], "rejected": None | কারণ, "ok": bool}
    ব্যর্থ হলে (কোটা ইত্যাদি) ok=False; strict হলে ত্রুটি ছোড়ে।"""
    rc = cfg.get("research") or {}
    empty = {"facts": [], "sources": [], "rejected": None, "ok": False}
    if not enabled(cfg):
        return empty
    user = (
        f"ক্যাটাগরি: {cat['name']}\nবিষয়: {topic}\n\n"
        "এই বিষয়ে ৩০ সেকেন্ডের একটা টিপস-ভিডিও বানানো হবে। গুগল সার্চ করে যাচাই করো:\n"
        "১) বিষয়টি/পদ্ধতিটি এখন সত্যিই কাজ করে কিনা (পুরনো, বন্ধ হয়ে যাওয়া বা ভুল হলে জানাও)।\n"
        "২) ধাপগুলো ঠিক কী কী, মেনু/অপশনের নাম হুবহু কী।\n"
        "৩) সাধারণ ভুল ধারণা বা সীমাবদ্ধতা (যেমন কোন ফোন/ভার্সনে চলে না)।\n\n"
        "উত্তরের ফরম্যাট (শুধু এটুকু):\n"
        "- বিষয়টি ভুল, বন্ধ বা অবাস্তব হলে প্রথম লাইনে লেখো: বাতিল: <এক লাইনে কারণ>\n"
        "- নইলে ৫–৮টি যাচাই করা তথ্য, প্রতিটি আলাদা লাইনে '- ' দিয়ে শুরু, বাংলায়।"
    )
    try:
        text, sources = llm.complete_grounded(cfg, RESEARCH_SYSTEM, user, max_tokens=2500)
    except llm.LLMError as e:
        if rc.get("strict"):
            raise
        print(f"  ⚠ গবেষণা বাদ ({e})", file=sys.stderr)
        return empty
    head = text.strip().splitlines()[0] if text.strip() else ""
    if head.startswith("বাতিল"):
        return {"facts": [], "sources": [], "rejected": head.split(":", 1)[-1].strip() or "বিষয়টি যাচাইয়ে টেকেনি",
                "ok": True}
    facts = _bullets(text)
    if len(facts) < 2:
        if rc.get("strict"):
            raise RuntimeError("গবেষণায় যথেষ্ট যাচাই করা তথ্য মেলেনি")
        return empty
    return {"facts": facts[:10], "sources": _domains(sources), "rejected": None, "ok": True}


def verify(cfg, script, facts):
    """স্ক্রিপ্ট তথ্যের সঙ্গে মেলায়। ফেরত: (ঠিক করা স্ক্রিপ্ট, সমস্যার তালিকা)।
    মারাত্মক ভুল হলে RuntimeError (ভিডিও বাতিল)। যাচাই চালানো না গেলে স্ক্রিপ্ট যেমন ছিল তেমনই ফেরত।"""
    rc = cfg.get("research") or {}
    if not rc.get("verify", True):
        return script, []
    scenes = [{"i": i, "narration": s["narration"], "screen_text": s["screen_text"]}
              for i, s in enumerate(script["scenes"])]
    fact_block = "\n".join(f"- {f}" for f in facts) if facts else "(কোনো সূত্র-তথ্য নেই; নিজের নিশ্চিত জ্ঞান দিয়ে যাচাই করো, সন্দেহ হলে সমস্যা ধরো)"
    user = (
        f"শিরোনাম: {script['title']}\n\nযাচাই করা তথ্য:\n{fact_block}\n\n"
        f"স্ক্রিপ্টের দৃশ্য:\n{scenes}\n\n"
        "কাজ: কোন দৃশ্যে তথ্যে-নেই/ভুল/অতিরঞ্জিত দাবি আছে খুঁজে বের করো। প্রতিটি সমস্যার জন্য একই ভাষায় ও প্রায় একই "
        "দৈর্ঘ্যে সংশোধিত narration ও screen_text দাও (তথ্যে যা আছে শুধু তাই বলে)।\n"
        'ফরম্যাট: {"verdict": "pass" | "fix" | "reject", "issues": [{"i": 0, "problem": "...", '
        '"narration": "...", "screen_text": "..."}]}\n'
        "reject দেবে কেবল যদি পুরো ভিডিওর মূল দাবিটাই ভুল বা বিপজ্জনক (যেমন ভুয়া/প্রতারণামূলক নির্দেশনা) হয়।"
    )
    try:
        data = extract_json(llm.complete(cfg, VERIFY_SYSTEM, user, max_tokens=2500, temperature=0.2))
    except (llm.LLMError, ValueError) as e:
        if rc.get("strict"):
            raise RuntimeError(f"তথ্য যাচাই করা যায়নি: {e}")
        print(f"  ⚠ যাচাই বাদ ({e})", file=sys.stderr)
        return script, []
    verdict = data.get("verdict", "pass") if isinstance(data, dict) else "pass"
    issues = [x for x in (data.get("issues") or []) if isinstance(x, dict)] if isinstance(data, dict) else []
    if verdict == "reject":
        why = "; ".join(str(x.get("problem", "")) for x in issues)[:200] or "মূল দাবি ভুল"
        raise RuntimeError(f"ফ্যাক্ট-চেকে স্ক্রিপ্ট বাতিল: {why}")
    fixed = [dict(s) for s in script["scenes"]]
    notes = []
    for x in issues:
        i = x.get("i")
        if isinstance(i, int) and 0 <= i < len(fixed):
            narr = str(x.get("narration", "")).strip()
            text = str(x.get("screen_text", "")).strip()[:80]
            if narr:
                fixed[i]["narration"] = narr
            if text:
                fixed[i]["screen_text"] = text
                hl = fixed[i].get("highlight")
                if hl and hl not in text:
                    fixed[i]["highlight"] = ""
            notes.append(f"দৃশ্য {i + 1}: {x.get('problem', '')}")
    return {**script, "scenes": fixed}, notes


def source_line(sources):
    """ভিডিওর বিবরণে বসানোর সূত্র-লাইন।"""
    return ("তথ্যসূত্র: " + ", ".join(sources)) if sources else ""
