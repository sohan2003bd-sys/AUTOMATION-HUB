"""রানের ফলাফল জানানো: GitHub Actions-এ লাল ত্রুটি (annotation) ও Summary পাতায় কারণসহ।

GitHub Actions-এর রান পাতায় (Summary) নামলেই কী সমস্যা হয়েছে দেখা যাবে — লগ খুলতে হবে না।
"""
import os
import sys


def _one_line(s) -> str:
    return " ".join(str(s).split())


def _annotate(level, title, detail):
    if os.environ.get("GITHUB_ACTIONS"):
        # GitHub-এ লাল (error) বা হলুদ (warning) চিহ্নসহ দেখায়
        print(f"::{level} title={_one_line(title)}::{_one_line(detail)}", flush=True)
    else:
        mark = "✖" if level == "error" else "⚠"
        print(f"{mark} {title}: {detail}", file=sys.stderr, flush=True)


def error(title, detail=""):
    _annotate("error", title, detail)


def warning(title, detail=""):
    _annotate("warning", title, detail)


def summary(markdown: str):
    """Actions রান পাতার Summary অংশে লেখা যোগ করে (GitHub-এর বাইরে চালালে কিছু করে না)।"""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(markdown.rstrip() + "\n\n")
    except OSError:
        pass
