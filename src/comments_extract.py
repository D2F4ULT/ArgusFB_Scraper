from __future__ import annotations

import re
from bs4 import BeautifulSoup

from config import (
    COMMENT_ROOT_CLASSES,
    POST_BAR_CLASSES,
    COMMENT_WORD_RE,
    AGE_TAIL_RE,
)
from utils import normalize_ws, make_absolute_fb_url, canonicalize_fb_url


def comment_root_selector() -> str:
    return "div." + ".".join(COMMENT_ROOT_CLASSES)


def parse_aria_label(aria: str):
    aria = aria or ""
    kind = "reply" if aria.startswith("Reply by ") else "comment" if aria.startswith("Comment by ") else "unknown"

    age = None
    m_age = AGE_TAIL_RE.search(aria)
    if m_age:
        age = normalize_ws(m_age.group(1))
        head = aria[:m_age.start()].strip()
    else:
        head = aria.strip()

    author = None
    rel = None

    if kind == "comment":
        m = re.match(r"Comment by\s+(.*)$", head)
        if m:
            author = normalize_ws(m.group(1))
    elif kind == "reply":
        m = re.match(r"Reply by\s+(.*?)(?:\s+to\s+(.*))?$", head)
        if m:
            author = normalize_ws(m.group(1))
            rel_raw = normalize_ws(m.group(2) or "")
            rel = rel_raw if rel_raw else None

    return kind if kind != "unknown" else None, (author or None), rel, (age or None)


def extract_text_from_article(article):
    parts = []
    for d in article.select('div[dir="auto"][style*="text-align:start"]'):
        txt = normalize_ws(d.get_text(" ", strip=True))
        if txt:
            parts.append(txt)

    if not parts:
        for d in article.select('div[dir="auto"]'):
            txt = normalize_ws(d.get_text(" ", strip=True))
            if txt and len(txt) > 20:
                parts.append(txt)
                break

    out = []
    seen = set()
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)

    joined = "\n".join(out).strip()
    return joined if joined else None


def extract_author_display(article):
    a = article.select_one('a[href] span[dir="auto"]')
    s = normalize_ws(a.get_text(strip=True)) if a else ""
    return s if s else None


def extract_profile_link(article):
    a = article.select_one(
        'a[href^="/groups/"][href*="/user/"], a[href^="/profile.php"], a[href^="/groups/"][href*="user/"]'
    )
    return a.get("href") if a else None


def extract_comment_permalink(article):
    a = article.select_one('a[href^="https://www.facebook.com/groups/"][href*="comment_id="]')
    return a.get("href") if a else None


def extract_reaction_count_comment(article):
    r = article.select_one('[aria-label$="reactions; see who reacted to this"] span')
    if r:
        t = normalize_ws(r.get_text(strip=True))
        if t.isdigit():
            return int(t)
    return None


def export_comments_from_root_html(root_html: str):
    soup = BeautifulSoup(root_html, "lxml")
    items = []

    for art in soup.select('div[role="article"][aria-label]'):
        aria = art.get("aria-label", "") or ""
        kind, aria_author, rel, age = parse_aria_label(aria)

        author_display = extract_author_display(art)
        profile_link = make_absolute_fb_url(extract_profile_link(art))
        comment_link = extract_comment_permalink(art)
        comment_link_canon = canonicalize_fb_url(comment_link) if comment_link else None
        text = extract_text_from_article(art)

        item = {
            "type": kind,
            "author": author_display or aria_author,
            "aria_author": aria_author,
            "relation": rel,
            "age": age,
            "aria_label": aria if aria else None,
            "text": text,
            "profile_link": profile_link,
            "comment_link": comment_link,
            "comment_link_canon": comment_link_canon,
            "reactions": extract_reaction_count_comment(art),
        }

        if item["author"] or item["text"]:
            items.append(item)

    return items


def _has_all_classes_bs4(tag, classes) -> bool:
    if not tag or not tag.has_attr("class"):
        return False
    tag_classes = set(tag.get("class", []))
    return all(c in tag_classes for c in classes)


def extract_post_metrics_from_page_html(page_html: str):
    soup = BeautifulSoup(page_html, "lxml")

    bar = None
    for div in soup.find_all("div"):
        if _has_all_classes_bs4(div, POST_BAR_CLASSES):
            if div.select_one('span[aria-label="See who reacted to this"][role="toolbar"]'):
                bar = div
                break

    if not bar:
        return None, None, "post_bar_not_found"

    reactions_count = None

    all_reactions_row = None
    for d in bar.find_all("div"):
        txt = normalize_ws(d.get_text(" ", strip=True))
        if txt.lower().startswith("all reactions"):
            all_reactions_row = d.parent if getattr(d, "parent", None) else d
            break

    if all_reactions_row:
        nums = [int(x) for x in re.findall(r"\b\d{1,7}\b", normalize_ws(all_reactions_row.get_text(" ", strip=True)))]
        if nums:
            reactions_count = max(nums)

    if reactions_count is None:
        per_total = 0
        found_any = False
        for d in bar.find_all(attrs={"aria-label": True}):
            al = d.get("aria-label", "") or ""
            m = re.match(r"^(Like|Love|Care|Haha|Wow|Sad|Angry)\s*:\s*(\d+)\s+person", al, re.IGNORECASE)
            if m:
                per_total += int(m.group(2))
                found_any = True
        if found_any:
            reactions_count = per_total

    if reactions_count is None:
        r = bar.select_one('[aria-label$="reactions; see who reacted to this"] span')
        if r:
            t = normalize_ws(r.get_text(strip=True))
            if t.isdigit():
                reactions_count = int(t)

    comments_total = None
    region = bar.parent if getattr(bar, "parent", None) else bar
    region_text = normalize_ws(region.get_text(" ", strip=True))
    m_c = COMMENT_WORD_RE.search(region_text)
    if m_c:
        comments_total = int(m_c.group(1))

    return reactions_count, comments_total, None
