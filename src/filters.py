from __future__ import annotations

import time
from datetime import datetime, timezone

from utils import safe_int


def filter_last_days(posts_obj: dict, last_days: int) -> dict:
    posts = posts_obj.get("posts", [])
    posts_sorted = sorted(posts, key=lambda x: int(x.get("creation_time") or 0), reverse=True)

    if not last_days or last_days <= 0:
        return {"meta": {**posts_obj.get("meta", {}), "last_days": None}, "posts": posts_sorted}

    now = int(time.time())
    cutoff = now - (last_days * 24 * 60 * 60)

    kept = []
    for p in posts_sorted:
        ct_i = safe_int(p.get("creation_time"))
        if ct_i is None:
            continue
        if ct_i >= cutoff:
            kept.append(p)

    return {
        "meta": {
            **posts_obj.get("meta", {}),
            "last_days": last_days,
            "cutoff_unix": cutoff,
            "cutoff_iso_utc": datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat(),
            "kept_posts": len(kept),
        },
        "posts": kept,
    }
