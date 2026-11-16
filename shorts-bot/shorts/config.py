from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent


def load(path=None):
    p = Path(path) if path else ROOT / "config.yaml"
    with open(p, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["_root"] = ROOT
    return cfg


def data_dir(cfg) -> Path:
    d = ROOT / cfg["paths"]["data_dir"]
    d.mkdir(parents=True, exist_ok=True)
    return d


def out_dir(cfg) -> Path:
    d = ROOT / cfg["paths"]["out_dir"]
    d.mkdir(parents=True, exist_ok=True)
    return d


def category(cfg, cat_id):
    for c in cfg["categories"]:
        if c["id"] == cat_id:
            return c
    raise KeyError(f"অজানা ক্যাটাগরি: {cat_id}")
