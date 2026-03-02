from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

from config import BERLIN, GRAPHQL_MARKERS


def utc_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def safe_int(x):
    try:
        return int(x)
    except Exception:
        return None


def unix_to_iso_utc(ts):
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except Exception:
        return None


def unix_to_pretty_berlin(ts):
    try:
        dt = datetime.fromtimestamp(int(ts), tz=BERLIN)
    except Exception:
        return None

    weekday = dt.strftime("%A")
    month = dt.strftime("%B")
    day = dt.strftime("%d").lstrip("0")
    year = dt.strftime("%Y")

    try:
        hour = dt.strftime("%-I")
    except Exception:
        hour = str(dt.hour % 12 or 12)

    minute = dt.strftime("%M")
    ampm = dt.strftime("%p")
    return f"{weekday}, {month} {day}, {year} at {hour}:{minute} {ampm}"


def is_graphql(url: str) -> bool:
    return any(m in url for m in GRAPHQL_MARKERS)


def extract_group_token_from_url(group_url: str) -> str:
    p = urlparse(group_url.strip())
    parts = p.path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "groups":
        return parts[1]
    raise ValueError("Could not extract group token from URL")


def normalize_post_url(url: str) -> str:
    p = urlparse(url)
    clean = f"{p.scheme}://{p.netloc}{p.path}"
    if not clean.endswith("/"):
        clean += "/"
    return clean


def canonicalize_fb_url(url: str):
    if not url:
        return None
    try:
        p = urlparse(url)
        keep = {}
        for part in p.query.split("&"):
            if not part or "=" not in part:
                continue
            k, v = part.split("=", 1)
            if k in ("comment_id", "reply_comment_id"):
                keep[k] = v
        new_q = "&".join([f"{k}={keep[k]}" for k in ("comment_id", "reply_comment_id") if k in keep])
        return urlunparse((p.scheme, p.netloc, p.path, "", new_q, ""))
    except Exception:
        return url


def make_absolute_fb_url(path_or_url: str):
    if not path_or_url:
        return None
    s = str(path_or_url).strip()
    if not s:
        return None
    if s.startswith(("http://", "https://")):
        return s
    if s.startswith("/"):
        return "https://www.facebook.com" + s
    return s


def fmt_ratio(collected: int, total):
    if isinstance(total, int) and total >= 0:
        return f"{collected}/{total}"
    return f"{collected}/?"


def parse_fb_graphql_text(text: str):
    """
    FB can prefix responses with 'for (;;);' and sometimes stream JSON per line.
    Return a list of decoded JSON objects.
    """
    if not text:
        return []
    t = text.strip()
    if t.startswith("for (;;);"):
        t = t[len("for (;;);"):].lstrip()

    try:
        return [json.loads(t)]
    except Exception:
        pass

    out = []
    for line in t.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("for (;;);"):
            line = line[len("for (;;);"):].lstrip()
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out
