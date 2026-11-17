"""কাজের তালিকা (data/queue.jsonl): প্রতিটি লাইন একটা ভিডিও-আইডিয়া ও তার অবস্থা।
status: idea → scripted → rendered (ধাপ ২-এ: uploaded)।"""
import difflib
import json
import os
import re
import time
from pathlib import Path


def norm(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", s.lower())


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.rows = []
        if self.path.exists():
            with open(self.path, encoding="utf-8") as f:
                self.rows = [json.loads(l) for l in f if l.strip()]

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for r in self.rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, self.path)

    def topics(self, category, limit=None):
        t = [r["topic"] for r in self.rows if r["category"] == category]
        return t[-limit:] if limit else t

    def is_dup(self, category, topic, recent=400):
        n = norm(topic)
        if not n:
            return True
        for old in self.topics(category, recent):
            o = norm(old)
            if o == n or difflib.SequenceMatcher(None, o, n).ratio() >= 0.85:
                return True
        # অন্য ক্যাটাগরিতেও একই খবর/বিষয় যেন না আসে (যেমন 'আলোচিত' ও 'বিশ্ব')
        others = [r["topic"] for r in self.rows if r["category"] != category][-150:]
        for old in others:
            if difflib.SequenceMatcher(None, norm(old), n).ratio() >= 0.8:
                return True
        return False

    def add_topics(self, category, topics):
        added = 0
        for t in topics:
            extra = {}
            if isinstance(t, dict):  # {"topic":..., "facts":[...], "sources":[...]} — আইডিয়ার সঙ্গে আসা সংবাদ-তথ্য
                extra = {k: t[k] for k in ("facts", "sources") if t.get(k)}
                t = t.get("topic", "")
            t = str(t).strip()
            if not t or self.is_dup(category, t):
                continue
            nums = [int(r["id"].rsplit("-", 1)[1]) for r in self.rows if r["category"] == category]
            rid = f"{category}-{(max(nums) + 1 if nums else 1):05d}"
            self.rows.append({"id": rid, "category": category, "topic": t, "status": "idea",
                              "created": int(time.time()), **extra})
            added += 1
        self.save()
        return added

    def retire(self, valid_ids, evergreen_ids, max_age_days, now=None):
        """আর-নেই এমন ক্যাটাগরির আইডিয়া 'retired', আর টাটকা-থাকা-দরকার (non-evergreen) পুরনো আইডিয়া 'expired' করে দেয়।
        শুধু idea/scripted অবস্থার সারি ছোঁয়া হয়; আপলোড-হওয়া সারি ও ডুপ্লিকেট-মেলানোর ইতিহাস অক্ষত থাকে।"""
        now = now or time.time()
        n = 0
        for r in self.rows:
            if r["status"] not in ("idea", "scripted"):
                continue
            if r["category"] not in valid_ids:
                r["status"] = "retired"
                n += 1
            elif max_age_days and r["category"] not in evergreen_ids and now - r.get("created", now) > max_age_days * 86400:
                r["status"] = "expired"
                n += 1
        if n:
            self.save()
        return n

    def get(self, rid):
        for r in self.rows:
            if r["id"] == rid:
                return r
        raise KeyError(rid)

    def update(self, rid, **kw):
        self.get(rid).update(kw)
        self.save()

    def pending(self, status, n, category=None):
        """ক্যাটাগরিগুলোর মধ্যে ঘুরিয়ে-ফিরিয়ে (ব্যালান্স রেখে) n টি বাছে।"""
        done, last = {}, {}
        for i, r in enumerate(self.rows):
            if r["status"] in ("rendered", "uploaded"):
                done[r["category"]] = done.get(r["category"], 0) + 1
                last[r["category"]] = i
        queues = {}
        for r in self.rows:
            if r["status"] == status and (category is None or r["category"] == category):
                queues.setdefault(r["category"], []).append(r)
        picked = []
        for _ in range(n):
            avail = [c for c, q in queues.items() if q]
            if not avail:
                break
            c = min(avail, key=lambda x: (done.get(x, 0), last.get(x, -1)))  # কম হয়েছে, আর সবচেয়ে আগে হয়েছে—সেটা আগে
            picked.append(queues[c].pop(0))
            done[c] = done.get(c, 0) + 1
        return picked

    def counts(self):
        out = {}
        for r in self.rows:
            out.setdefault(r["category"], {}).setdefault(r["status"], 0)
            out[r["category"]][r["status"]] += 1
        return out
