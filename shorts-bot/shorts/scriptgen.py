"""একটা আইডিয়া থেকে সম্পূর্ণ স্ক্রিপ্ট (দৃশ্য-ভিত্তিক) বানায় ও যাচাই করে।"""
import random

from . import llm
from .llm import extract_json

KINDS = {"hook", "step", "tip", "outro"}
HOOK_STYLES = [
    "একটা সরাসরি প্রশ্ন দিয়ে শুরু",
    "পরিচিত একটা সমস্যার কথা বলে শুরু",
    "একটা চমকপ্রদ কিন্তু সত্য তথ্য দিয়ে শুরু",
    "'আপনি কি জানেন' ধাঁচের নয়—সরাসরি ফলাফল বলে শুরু",
    "একটা ভুল ধারণা ভেঙে শুরু",
]

SYSTEM = (
    "তুমি বাংলা প্রযুক্তি-টিপসের YouTube Shorts-এর স্ক্রিপ্ট লেখক। ভাষা সহজ, কথ্য ও পরিষ্কার। "
    "নিয়ম: (১) শুধু সেই তথ্য ও ধাপ দেবে যা তুমি নিশ্চিত জানো; মেনু/অপশনের নাম যেমন অ্যাপে ইংরেজিতে "
    "দেখা যায় তেমন লেখো। (২) নিশ্চিত না হলে সেই ধাপ বাদ দাও, আন্দাজে কিছু লিখো না। "
    "(৩) ভুয়া পরিসংখ্যান, অতিরঞ্জন, ভয় দেখানো, আয়ের প্রতিশ্রুতি নয়। (৪) প্রতিটি ভিডিওতে "
    "অন্তত একটা নির্দিষ্ট, কাজে লাগার মতো জিনিস থাকবে—সাধারণ উপদেশ নয়। "
    "(৫) উত্তর কেবল একটা JSON অবজেক্ট।"
)


def build_prompt(cfg, cat, topic, seed):
    rnd = random.Random(seed)
    hook_style = rnd.choice(HOOK_STYLES)
    lo, hi = cfg["script"]["min_words"], cfg["script"]["max_words"]
    return (
        f"ক্যাটাগরি: {cat['name']}\nবিষয়: {topic}\n\n"
        f"এই বিষয়ে প্রায় {cfg['script']['target_seconds']} সেকেন্ডের একটা Shorts-এর স্ক্রিপ্ট লেখো।\n"
        f"- হুক (প্রথম দৃশ্য): {hook_style}।\n"
        f"- মোট ৫–৭টি দৃশ্য। সব দৃশ্যের narration মিলিয়ে {lo}–{hi}টি শব্দ।\n"
        "- narration: ভয়েসওভারের বাংলা বাক্য। সংখ্যা কথায় বা বাংলা অঙ্কে লেখো। "
        "জনপ্রিয় ইংরেজি শব্দ (যেমন ফোন, অ্যাপ, ওয়াইফাই) বাংলা অক্ষরে লেখো, যাতে ভয়েস ঠিকভাবে পড়ে।\n"
        "- screen_text: পর্দায় যা দেখাবে—সর্বোচ্চ ৮টি শব্দ। মেনুর নাম ইংরেজিতে থাকতে পারে।\n"
        "- kind: hook | step | tip | outro। শেষ দৃশ্য outro: একটা এক লাইনের সারকথা। "
        "'লাইক/সাবস্ক্রাইব করুন' ধরনের একই লাইন বারবার দেবে না।\n"
        "- title: ৬০ অক্ষরের মধ্যে আকর্ষণীয়, বাংলা শিরোনাম (ক্লিকবেইট নয়)।\n"
        "- description: ২–৩ লাইন বাংলা বিবরণ। tags: ৮–১২টি, বাংলা ও ইংরেজি মিশিয়ে।\n\n"
        'ফরম্যাট: {"title": "...", "description": "...", "tags": ["..."], '
        '"scenes": [{"kind": "hook", "narration": "...", "screen_text": "..."}]}'
    )


def validate(cfg, d):
    if not isinstance(d, dict):
        raise ValueError("অবজেক্ট আশা করা হয়েছিল")
    title = str(d.get("title", "")).strip()[:100]
    if not title:
        raise ValueError("title নেই")
    scenes = []
    for s in d.get("scenes", []):
        narr = str(s.get("narration", "")).strip()
        text = str(s.get("screen_text", "")).strip()[:80]
        kind = s.get("kind") if s.get("kind") in KINDS else "step"
        if narr and text:
            scenes.append({"kind": kind, "narration": narr, "screen_text": text})
    if not 4 <= len(scenes) <= 10:
        raise ValueError(f"দৃশ্যের সংখ্যা ঠিক নয়: {len(scenes)}")
    words = sum(len(s["narration"].split()) for s in scenes)
    lo, hi = cfg["script"]["min_words"] - 10, cfg["script"]["max_words"] + 20
    if not lo <= words <= hi:
        raise ValueError(f"শব্দ সংখ্যা ঠিক নয়: {words}")
    tags = [str(t).strip().lstrip("#") for t in d.get("tags", []) if str(t).strip()][:15]
    return {"title": title, "description": str(d.get("description", "")).strip(),
            "tags": tags, "scenes": scenes}


def generate(cfg, cat, rec):
    last = None
    for attempt in range(cfg["script"]["max_retries"]):
        prompt = build_prompt(cfg, cat, rec["topic"], f"{rec['id']}-{attempt}")
        try:
            raw = llm.complete(cfg, SYSTEM, prompt, max_tokens=3500, temperature=0.9)
            return validate(cfg, extract_json(raw))
        except llm.LLMError as e:
            if e.fatal:  # কী/কোটা/মডেলের সমস্যা — আবার চেষ্টা করে লাভ নেই
                raise RuntimeError(f"স্ক্রিপ্ট বানানো যায়নি: {e}")
            last = e
        except ValueError as e:
            last = e
    raise RuntimeError(f"স্ক্রিপ্ট বানানো যায়নি: {last}")
