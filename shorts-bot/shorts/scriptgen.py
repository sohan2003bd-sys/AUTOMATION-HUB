"""একটা আইডিয়া থেকে সম্পূর্ণ স্ক্রিপ্ট (দৃশ্য-ভিত্তিক) বানায় ও যাচাই করে।"""
import random

from . import llm
from .llm import extract_json

KINDS = {"hook", "step", "tip", "outro"}
TONES = {"serious", "warm", "energetic", "urgent"}
HOOK_STYLES = [
    "একটা সরাসরি প্রশ্ন দিয়ে শুরু—যার উত্তর জানতে দর্শক থেকে যাবে",
    "সবচেয়ে চমকপ্রদ (কিন্তু যাচাই-করা) তথ্যটা প্রথম বাক্যেই বলে শুরু",
    "একটা প্রচলিত ভুল ধারণা ভেঙে শুরু",
    "দর্শকের পকেট/স্বাস্থ্য/নিরাপত্তায় সরাসরি কী প্রভাব পড়বে সেটা দিয়ে শুরু",
    "একটা ছোট, ধারালো বাক্যে ঘটনাটা বলে শুরু, তারপর 'কিন্তু আসল কথা হলো…'",
]

FORMATS = [
    "প্রশ্ন-উত্তর ধাঁচে: দর্শকের মনের প্রশ্ন তুলে একে একে সরল উত্তর",
    "মিথ বনাম সত্য: প্রচলিত ভুল ধারণা বনাম আসল তথ্য",
    "তিনটি জরুরি কথা: সংখ্যা ধরে ধরে সাজানো (এক, দুই, তিন)",
    "ছোট গল্পের মতো: একটা বাস্তব পরিস্থিতি দিয়ে শুরু করে শিক্ষায় শেষ",
    "সতর্কবার্তা: কী ঘটছে, কীভাবে চিনবেন, কী করবেন",
    "আগে-পরে তুলনা: আগে কী ছিল, এখন কী বদলেছে, তাতে আপনার কী",
]

SYSTEM = (
    "তুমি বাংলাদেশি দর্শকদের জন্য YouTube Shorts-এর স্ক্রিপ্ট লেখক—একজন আত্মবিশ্বাসী, বুদ্ধিদীপ্ত উপস্থাপকের মতো, "
    "যিনি জটিল বিষয় সহজ কথ্য বাংলায় পরিষ্কার করে বোঝান। বইয়ের ভাষা নয়, সাজানো কিন্তু স্বাভাবিক কথোপকথনের ভাষা; "
    "ছোট ছোট ধারালো বাক্য; একই কাঠামো বারবার নয়। "
    "নিয়ম: (১) শুধু যাচাই-করা তথ্য; নিশ্চিত না হলে বাদ দাও, আন্দাজে কিছু লিখো না। "
    "(২) ভুয়া পরিসংখ্যান, অতিরঞ্জন, ভয় দেখানো, ক্লিকবেইট, আয়ের প্রতিশ্রুতি নয়। "
    "(৩) কোনো নির্দিষ্ট ব্যক্তিকে দোষী সাব্যস্ত করবে না; দলীয় রাজনীতির পক্ষ-বিপক্ষ, ধর্মীয়/জাতিগত উসকানি, গ্রাফিক বর্ণনা নয়। "
    "(৪) স্বাস্থ্য/টাকা/আইনের বিষয়ে সাধারণ সচেতনতা দেবে, ব্যক্তিগত ডোজ বা বিনিয়োগ-নির্দেশ নয়; দরকারে বিশেষজ্ঞ/অফিসিয়াল সূত্রের কাছে যেতে বলবে। "
    "(৫) প্রতিটি ভিডিওতে অন্তত একটা নির্দিষ্ট, কাজে লাগার মতো জিনিস (সংখ্যা, তারিখ, নিয়ম বা করণীয়) থাকবে। "
    "(৬) যাচাই করা তথ্যের তালিকা দেওয়া থাকলে কেবল সেই তালিকার তথ্য ব্যবহার করবে, নিজে থেকে নতুন দাবি যোগ করবে না। "
    "(৭) নিজেকে ডাক্তার/বিশেষজ্ঞ/প্রত্যক্ষদর্শী বলে পরিচয় দেবে না। "
    "(৮) উত্তর কেবল একটা JSON অবজেক্ট।"
)


def build_prompt(cfg, cat, topic, seed, facts=None, variant=None):
    rnd = random.Random(seed)
    if variant is None:
        hook_style, fmt = rnd.choice(HOOK_STYLES), rnd.choice(FORMATS)
    else:  # পরপর ভিডিওতে হুক ও ফরম্যাট ঘুরে ঘুরে বদলায়, একই ছাঁচ পরপর আসে না
        hook_style, fmt = HOOK_STYLES[variant % len(HOOK_STYLES)], FORMATS[variant % len(FORMATS)]
    lo, hi = cfg["script"]["min_words"], cfg["script"]["max_words"]
    fact_block = ""
    if facts:
        fact_block = "যাচাই করা তথ্য (শুধু এগুলোই ব্যবহার করবে):\n" + "\n".join(f"- {f}" for f in facts) + "\n\n"
    return (
        f"ক্যাটাগরি: {cat['name']}\nবিষয়: {topic}\n\n{fact_block}"
        f"এই বিষয়ে প্রায় {cfg['script']['target_seconds']} সেকেন্ডের একটা Shorts-এর স্ক্রিপ্ট লেখো।\n"
        f"- ভিডিওর ফরম্যাট: {fmt}।\n"
        f"- হুক (প্রথম দৃশ্য): {hook_style}। প্রথম তিন সেকেন্ডেই দর্শককে ধরে রাখতে হবে।\n"
        f"- মোট ৫–৮টি দৃশ্য। সব দৃশ্যের narration মিলিয়ে {lo}–{hi}টি শব্দ।\n"
        "- গঠন: হুক → কী ঘটেছে/কী জানা দরকার → কেন গুরুত্বপূর্ণ (দর্শকের জীবনে প্রভাব) → কী করা উচিত → এক লাইনের সারকথা।\n"
        "- narration: ভয়েসওভারের বাংলা বাক্য, এটাই পর্দায় সাবটাইটেল হিসেবে দেখা যাবে (পর্দায় আর কোনো লেখা থাকবে না)। "
        "সংখ্যা কথায় বা বাংলা অঙ্কে লেখো। জনপ্রিয় ইংরেজি শব্দ (যেমন ফোন, অ্যাপ, ব্যাংক) বাংলা অক্ষরে লেখো, যাতে ভয়েস ঠিকভাবে পড়ে।\n"
        "- visual_query: ইংরেজিতে ২–৪ শব্দের স্টক-ফুটেজ খোঁজার কিওয়ার্ড। এটা এমন বাস্তব দৃশ্য হবে যা ক্যামেরায় তোলা যায় "
        "(মানুষ, জায়গা, বস্তু, কাজ) এবং সেই দৃশ্যের কথার সঙ্গে মেলে। নিয়ম: প্রতিটি দৃশ্যের কিওয়ার্ড আলাদা বিষয়ের হবে "
        "(একই জিনিস বারবার নয়); animation, cartoon, 3D, illustration, infographic, graphic, text, logo, brand ধরনের শব্দ নয়; "
        "ক্লোজআপ, ওয়াইড, হাঁটা, হাত দিয়ে কাজ ইত্যাদি বৈচিত্র্য আনো (যেমন \"hands counting banknotes\", \"busy dhaka street\", "
        "\"doctor talking to patient\", \"woman checking phone worried\").\n"
        "- kind: hook | step | tip | outro। শেষ দৃশ্য outro: একটা এক লাইনের সারকথা। "
        "'লাইক/সাবস্ক্রাইব করুন' ধরনের একই লাইন বারবার দেবে না।\n"
        "- tone: গোটা ভিডিওর ভয়েসের মেজাজ—serious (গম্ভীর বিষয়) | warm (সহজ, বন্ধুসুলভ) | energetic (উদ্দীপক) | urgent (জরুরি সতর্কতা)। "
        "বিষয়ের সঙ্গে মিলিয়ে বাছো।\n"
        "- title: ৬০ অক্ষরের মধ্যে আকর্ষণীয়, বাংলা শিরোনাম (ক্লিকবেইট নয়, সত্য প্রতিশ্রুতি)।\n"
        "- description: ২–৩ লাইন বাংলা বিবরণ। tags: ৮–১২টি, বাংলা ও ইংরেজি মিশিয়ে।\n\n"
        'ফরম্যাট: {"title": "...", "description": "...", "tags": ["..."], "tone": "warm", '
        '"scenes": [{"kind": "hook", "narration": "...", "visual_query": "..."}]}'
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
        kind = s.get("kind") if s.get("kind") in KINDS else "step"
        # screen_text এখন আর লাগে না (পর্দায় শুধু সাবটাইটেল); পুরনো classic স্টাইলের জন্য নারেশনের শুরুর অংশ বসানো থাকে
        text = str(s.get("screen_text", "")).strip()[:80] or " ".join(narr.split()[:7])
        hl = str(s.get("highlight", "")).strip()
        vq = " ".join(str(s.get("visual_query", "")).split())[:60]
        if narr and text:
            scenes.append({"kind": kind, "narration": narr, "screen_text": text,
                           "highlight": hl if hl and hl in text else "", "visual_query": vq})
    if not 4 <= len(scenes) <= 10:
        raise ValueError(f"দৃশ্যের সংখ্যা ঠিক নয়: {len(scenes)}")
    words = sum(len(s["narration"].split()) for s in scenes)
    lo, hi = cfg["script"]["min_words"] - 10, cfg["script"]["max_words"] + 20
    if not lo <= words <= hi:
        raise ValueError(f"শব্দ সংখ্যা ঠিক নয়: {words}")
    tags = [str(t).strip().lstrip("#") for t in d.get("tags", []) if str(t).strip()][:15]
    tone = str(d.get("tone", "warm")).strip().lower()
    return {"title": title, "description": str(d.get("description", "")).strip(),
            "tags": tags, "tone": tone if tone in TONES else "warm", "scenes": scenes}


def generate(cfg, cat, rec, facts=None, variant=None):
    last = None
    for attempt in range(cfg["script"]["max_retries"]):
        prompt = build_prompt(cfg, cat, rec["topic"], f"{rec['id']}-{attempt}", facts, variant)
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
