"""প্রতিটি ক্যাটাগরির জন্য আলাদা আলাদা ভিডিও-আইডিয়া বানায়।"""
from . import llm
from .llm import extract_json

SYSTEM = (
    "তুমি বাংলাদেশি দর্শকদের জন্য প্রযুক্তি ও AI টিপসের YouTube Shorts-এর আইডিয়া লেখক। "
    "প্রতিটি আইডিয়া হবে নির্দিষ্ট, কাজে লাগার মতো ও একে অন্যের থেকে স্পষ্ট আলাদা। "
    "কেবল এমন বিষয় দেবে যা তুমি নিশ্চিত জানো এবং যা এখনকার জনপ্রিয় অ্যাপ/ফোনে সত্যিই কাজ করে। "
    "ভুয়া দাবি, অবাস্তব আয়ের প্রতিশ্রুতি, ক্র্যাক/পাইরেসি, হ্যাকিং শেখানো—এসব কখনো নয়। "
    "উত্তর কেবল JSON অ্যারে (বাংলা স্ট্রিংয়ের তালিকা), অন্য কিছু নয়।"
)


def generate_for_category(cfg, store, cat, count):
    recent = store.topics(cat["id"], 60)
    avoid = "\n".join(f"- {t}" for t in recent) or "(এখনো কিছু নেই)"
    user = (
        f"ক্যাটাগরি: {cat['name']}\nবিষয়ের পরিধি: {cat['hint']}\n\n"
        f"ঠিক {count}টি আলাদা আইডিয়া দাও। প্রতিটি একটা বাক্যে, বাংলায়, "
        "যেমন: \"ফোনের স্টোরেজ ভরে গেলে কোন ফাইলগুলো নিরাপদে মুছবেন\"। "
        "একই ধরনের আইডিয়া বারবার দেবে না; কঠিন-সহজ, নতুন-পুরনো সব ধরনের দর্শকের কথা ভাবো।\n\n"
        f"এগুলোর মতো বা কাছাকাছি দেবে না:\n{avoid}\n\n"
        'ফরম্যাট: ["আইডিয়া ১", "আইডিয়া ২", ...]'
    )
    raw = llm.complete(cfg, SYSTEM, user, max_tokens=4000, temperature=1.0, task="ideas")
    items = extract_json(raw)
    if not isinstance(items, list):
        raise ValueError("অ্যারে আশা করা হয়েছিল")
    return store.add_topics(cat["id"], [i for i in items if isinstance(i, str)])
