#!/usr/bin/env python3
"""
fb_sorted_posts_comments.py

A Unix-style CLI that outputs ONE primary JSON file:
  - posts sorted newest->oldest
  - each post includes extracted comments/replies

By default it writes ONLY that main sorted file (no extra debug blobs).

Optional: if you enable --debug, it also writes an additional debug JSON file
containing the raw feed capture + filtered posts + crawl internals.

Behavior (Unix-ish):
- If you run with no args OR forget a required arg (like --group),
  it prints the full help/usage and exits with code 0.

Pipeline:
1) Capture posts from the group feed by parsing GraphQL network responses.
2) Optionally filter posts to the last N days (creation_time from feed).
3) Crawl each post page and extract:
   - reactions (best effort)
   - total comments (best effort)
   - comments/replies (DOM-based, scrolling comment root)
4) Produce primary output: sorted posts + comments (compact + useful).

Examples:
  # Default: only main sorted output file
  python3 fb_sorted_posts_comments.py --group https://www.facebook.com/groups/kvartira.berlin --last-days 10

  # Also write debug JSON
  python3 fb_sorted_posts_comments.py --group https://www.facebook.com/groups/kvartira.berlin --last-days 10 --debug
"""

import argparse
import asyncio
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from playwright.async_api import async_playwright, TimeoutError as PWTimeoutError


# ----------------------------
# Constants / Regex
# ----------------------------

GRAPHQL_MARKERS = ("/api/graphql", "graphql")
POST_URL_RE = re.compile(r"^https?://(www\.)?facebook\.com/groups/([^/]+)/posts/(\d+)/?")

BERLIN = ZoneInfo("Europe/Berlin")

COMMENT_ROOT_CLASSES = [
    "html-div", "x14z9mp", "xat24cr", "x1lziwak", "xexx8yu", "xyri2b",
    "x18d9i69", "x1c1uobl", "x1gslohp"
]
POST_BAR_CLASSES = ["x6s0dn4", "x78zum5", "x1iyjqo2", "x6ikm8r", "x10wlt62"]

COMMENT_WORD_RE = re.compile(
    r"(\d{1,7})\s*(?:comments?|comment|kommentare|kommentar|коммент(?:арии|ариев|ария|арий)?)\b",
    re.IGNORECASE,
)

AGE_TAIL_RE = re.compile(
    r"\s("
    r"(?:\d+\s*(?:seconds?|minutes?|hours?|days?|weeks?|months?|years?)\s+ago)"
    r"|(?:\d+\s*[smhdwy])"
    r"|(?:a\s+\w+\s+ago)"
    r"|(?:an\s+\w+\s+ago)"
    r"|(?:yesterday)"
    r"|(?:today)"
    r"|(?:just\s+now)"
    r")\s*$",
    re.IGNORECASE,
)


# ----------------------------
# Utilities
# ----------------------------

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
    parsed = urlparse(group_url.strip())
    parts = parsed.path.strip("/").split("/")
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
    if s.startswith("http://") or s.startswith("https://"):
        return s
    if s.startswith("/"):
        return "https://www.facebook.com" + s
    return s

def fmt_ratio(collected: int, total):
    if isinstance(total, int) and total >= 0:
        return f"{collected}/{total}"
    return f"{collected}/?"


# ----------------------------
# Progress bar (only when verbose OFF)
# ----------------------------

class ProgressBar:
    def __init__(self, enabled: bool, total: int, prefix: str = ""):
        self.enabled = enabled
        self.total = max(int(total), 1)
        self.prefix = prefix
        self.start = time.time()
        self.last_render = 0.0
        self.width = 28

    def update(self, current: int, suffix: str = ""):
        if not self.enabled:
            return
        now = time.time()
        if now - self.last_render < 0.08 and current < self.total:
            return
        self.last_render = now

        current = max(0, min(int(current), self.total))
        frac = current / self.total
        filled = int(self.width * frac)
        bar = "█" * filled + "░" * (self.width - filled)

        elapsed = now - self.start
        eta = 0.0
        if current > 0:
            eta = (elapsed / current) * (self.total - current)

        line = f"{self.prefix}[{bar}] {current}/{self.total} ({frac*100:5.1f}%) ETA {eta:5.0f}s"
        if suffix:
            line += f" | {suffix}"

        pad = " " * max(0, 160 - len(line))
        sys.stdout.write("\r" + line + pad)
        sys.stdout.flush()

    def done(self, suffix: str = "done"):
        if not self.enabled:
            return
        self.update(self.total, suffix=suffix)
        sys.stdout.write("\n")
        sys.stdout.flush()


# ----------------------------
# Feed capture
# ----------------------------

def parse_fb_graphql_text(text: str):
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

def walk_find_story_time_url_pairs(obj, target_group: str, found: list):
    if isinstance(obj, dict):
        url = obj.get("url")
        ctime = obj.get("creation_time")

        if isinstance(url, str) and ctime is not None:
            m = POST_URL_RE.match(url)
            if m and m.group(2) == target_group:
                found.append({
                    "post_id": m.group(3),
                    "url": normalize_post_url(url),
                    "creation_time": safe_int(ctime),
                    "created_at_iso_utc": unix_to_iso_utc(ctime),
                    "created_at_pretty_berlin": unix_to_pretty_berlin(ctime),
                })

        for v in obj.values():
            walk_find_story_time_url_pairs(v, target_group, found)
    elif isinstance(obj, list):
        for it in obj:
            walk_find_story_time_url_pairs(it, target_group, found)

def capture_posts_from_feed(args) -> dict:
    target_group = extract_group_token_from_url(args.group)
    state_file = Path(args.state)
    if not state_file.exists():
        raise SystemExit(f"Missing state file: {state_file}")

    collected_posts = {}
    graphql_queue = []
    total_graphql_seen = 0
    stall_counter = 0

    bar = ProgressBar(enabled=(not args.verbose), total=args.max_posts, prefix="[feed] ")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=(not args.feed_headed))
        context = browser.new_context(
            storage_state=str(state_file),
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        def on_response(resp):
            nonlocal total_graphql_seen
            if not is_graphql(resp.url):
                return
            total_graphql_seen += 1
            try:
                text = resp.text()
            except Exception:
                return
            graphql_queue.append(text)

        page.on("response", on_response)

        if args.verbose:
            print(f"[feed] Opening group: {args.group}")

        page.goto(args.group, wait_until="domcontentloaded", timeout=args.page_timeout)

        while True:
            if len(collected_posts) >= args.max_posts:
                if args.verbose:
                    print("[feed] Reached max-posts target.")
                break

            page.mouse.wheel(0, args.feed_scroll_px)
            page.wait_for_timeout(args.feed_pause_ms)

            if len(graphql_queue) < args.graphql_batch:
                stall_counter += 1
                if args.verbose:
                    print(f"[feed] Waiting for GraphQL... stall {stall_counter}/{args.stall_limit}")
                else:
                    bar.update(len(collected_posts), suffix=f"graphql_seen={total_graphql_seen} stall={stall_counter}/{args.stall_limit}")
                if stall_counter >= args.stall_limit:
                    if args.verbose:
                        print("[feed] No new GraphQL detected. Stopping.")
                    break
                continue

            stall_counter = 0

            for _ in range(args.graphql_batch):
                if not graphql_queue:
                    break
                text = graphql_queue.pop(0)
                objs = parse_fb_graphql_text(text)

                for obj in objs:
                    found = []
                    walk_find_story_time_url_pairs(obj, target_group, found)
                    for item in found:
                        pid = item.get("post_id")
                        if pid and pid not in collected_posts:
                            collected_posts[pid] = item
                    if len(collected_posts) >= args.max_posts:
                        break
                if len(collected_posts) >= args.max_posts:
                    break

            if args.verbose:
                print(f"[feed] Posts collected: {len(collected_posts)} | GraphQL seen: {total_graphql_seen}")
            else:
                bar.update(len(collected_posts), suffix=f"graphql_seen={total_graphql_seen}")

        try:
            browser.close()
        except Exception:
            pass

    if not args.verbose:
        bar.done(suffix="feed capture complete")

    posts = list(collected_posts.values())
    posts.sort(key=lambda x: int(x.get("creation_time") or 0), reverse=True)

    return {
        "meta": {
            "captured_at": utc_ts(),
            "group_url": args.group,
            "group_token": target_group,
            "graphql_seen": total_graphql_seen,
            "unique_posts": len(posts),
        },
        "posts": posts,
    }


# ----------------------------
# Filter posts by last-days
# ----------------------------

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


# ----------------------------
# Comment extraction
# ----------------------------

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


# ----------------------------
# Playwright async crawl
# ----------------------------

async def scroll_inside_comment_root(page, root_loc, steps: int, pause_ms: int) -> bool:
    try:
        box = await root_loc.bounding_box(timeout=5000)
    except Exception:
        box = None
    if not box:
        return False

    cx = box["x"] + box["width"] / 2
    cy = box["y"] + box["height"] / 2

    await page.mouse.move(cx, cy)
    await page.mouse.click(cx, cy)

    for _ in range(steps):
        await page.mouse.wheel(0, 1200)
        await page.wait_for_timeout(pause_ms)

    return True

def stable_comment_key(it: dict):
    return (
        it.get("type"),
        it.get("author"),
        it.get("age"),
        it.get("text"),
        it.get("comment_link_canon"),
    )

async def crawl_single_post(browser, state_file: Path, post: dict, args) -> dict:
    post_id = post.get("post_id")
    url = post.get("url")

    post_result = {
        "post_id": post_id,
        "url": url,
        "creation_time": post.get("creation_time"),
        "created_at_iso_utc": post.get("created_at_iso_utc"),
        "created_at_pretty_berlin": post.get("created_at_pretty_berlin"),

        "post_reactions": None,
        "post_comments_total": None,
        "post_comments_collected": 0,
        "post_comments_progress": "0/?",

        "comments": [],
        "errors": [],
        "stats": {"cycles_run": 0, "total_added": 0},
    }

    context = await browser.new_context(
        storage_state=str(state_file),
        viewport={"width": 1280, "height": 900},
    )
    page = await context.new_page()

    t0 = time.time()

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=args.page_timeout)
    except PWTimeoutError:
        post_result["errors"].append("goto_timeout")
        await context.close()
        return post_result
    except Exception as e:
        post_result["errors"].append(f"goto_error:{type(e).__name__}")
        await context.close()
        return post_result

    try:
        await page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass

    try:
        page_html = await page.content()
        reacts, ctotal, dbg = extract_post_metrics_from_page_html(page_html)
        post_result["post_reactions"] = reacts
        post_result["post_comments_total"] = ctotal
        if dbg and args.verbose:
            post_result["errors"].append(dbg)
    except Exception as e:
        post_result["errors"].append(f"post_metrics_error:{type(e).__name__}")

    root_loc = page.locator(comment_root_selector()).first
    try:
        await root_loc.wait_for(state="attached", timeout=8_000)
    except Exception:
        post_result["errors"].append("comment_root_not_found")
        post_result["post_comments_progress"] = fmt_ratio(0, post_result["post_comments_total"])
        await context.close()
        return post_result

    try:
        await root_loc.scroll_into_view_if_needed(timeout=5000)
    except Exception:
        pass

    seen = set()
    no_growth_streak = 0

    for cycle in range(args.cycles):
        if time.time() - t0 > args.per_post_max_seconds:
            post_result["errors"].append("per_post_timeout")
            break

        ok = await scroll_inside_comment_root(page, root_loc, steps=args.scroll_steps, pause_ms=args.pause_ms)
        if not ok:
            post_result["errors"].append("scroll_no_bbox")
            break

        try:
            root_html = await root_loc.inner_html(timeout=8_000)
        except Exception as e:
            post_result["errors"].append(f"root_inner_html_error:{type(e).__name__}")
            break

        items = export_comments_from_root_html(root_html)

        added = 0
        for it in items:
            k = stable_comment_key(it)
            if k in seen:
                continue
            seen.add(k)
            post_result["comments"].append(it)
            added += 1
            if len(post_result["comments"]) >= args.max_comments:
                break

        post_result["stats"]["cycles_run"] = cycle + 1
        post_result["stats"]["total_added"] += added

        post_result["post_comments_collected"] = len(post_result["comments"])
        post_result["post_comments_progress"] = fmt_ratio(
            post_result["post_comments_collected"],
            post_result["post_comments_total"],
        )

        if args.verbose:
            print(f"  - cycle {cycle+1}/{args.cycles}: collected={post_result['post_comments_progress']}, added={added}")

        if len(post_result["comments"]) >= args.max_comments:
            post_result["errors"].append("max_comments_reached")
            break

        if added == 0:
            no_growth_streak += 1
        else:
            no_growth_streak = 0

        if no_growth_streak >= args.no_growth_cycles:
            post_result["errors"].append("no_new_items_stopped")
            break

    await context.close()
    return post_result

async def crawl_all_posts(posts, args, state_file: Path):
    out = {
        "meta": {
            "group_url": args.group,
            "posts_crawled_requested": len(posts),
            "max_comments_per_post": args.max_comments,
            "concurrency": args.concurrency,
            "timezone_note": "Times come from feed capture creation_time fields.",
        },
        "posts": [],
    }

    sem = asyncio.Semaphore(args.concurrency)
    progress = ProgressBar(enabled=(not args.verbose), total=len(posts), prefix="[crawl] ")
    done_count = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=(not args.headed))

        async def run_one(i, post):
            async with sem:
                if args.verbose:
                    print(f"\n[{i}/{len(posts)}] Crawling post {post.get('post_id')}: {post.get('url')}")
                try:
                    return await asyncio.wait_for(
                        crawl_single_post(browser, state_file, post, args),
                        timeout=args.per_post_max_seconds + 30,
                    )
                except asyncio.TimeoutError:
                    return {
                        "post_id": post.get("post_id"),
                        "url": post.get("url"),
                        "creation_time": post.get("creation_time"),
                        "created_at_iso_utc": post.get("created_at_iso_utc"),
                        "created_at_pretty_berlin": post.get("created_at_pretty_berlin"),
                        "post_reactions": None,
                        "post_comments_total": None,
                        "post_comments_collected": 0,
                        "post_comments_progress": "0/?",
                        "comments": [],
                        "errors": ["asyncio_wait_for_timeout"],
                        "stats": {"cycles_run": 0, "total_added": 0},
                    }

        tasks = [asyncio.create_task(run_one(i + 1, post)) for i, post in enumerate(posts)]

        for fut in asyncio.as_completed(tasks):
            res = await fut
            out["posts"].append(res)

            done_count += 1
            if not args.verbose:
                progress.update(done_count, suffix=f"last_post={res.get('post_id')} comments={res.get('post_comments_progress')}")

        await browser.close()

    if not args.verbose:
        progress.done(suffix="comment crawl complete")

    return out


# ----------------------------
# Output: MAIN sorted file
# ----------------------------

def build_main_sorted_output(filtered_posts: dict, crawl_results: dict, args) -> dict:
    by_id = {p.get("post_id"): p for p in crawl_results.get("posts", []) if p.get("post_id")}

    posts = sorted(
        filtered_posts.get("posts", []),
        key=lambda x: int(x.get("creation_time") or 0),
        reverse=True,
    )

    out_posts = []
    for fp in posts:
        pid = fp.get("post_id")
        cr = by_id.get(pid, {})

        out_posts.append({
            "post_id": pid,
            "url": fp.get("url"),
            "creation_time": fp.get("creation_time"),
            "created_at_iso_utc": fp.get("created_at_iso_utc"),
            "created_at_pretty_berlin": fp.get("created_at_pretty_berlin"),

            "post_reactions": cr.get("post_reactions"),
            "post_comments_total": cr.get("post_comments_total"),
            "post_comments_collected": cr.get("post_comments_collected", 0),
            "post_comments_progress": cr.get("post_comments_progress", fmt_ratio(0, cr.get("post_comments_total"))),

            "comments": cr.get("comments", []),
        })

    return {
        "meta": {
            "run_at": utc_ts(),
            "group_url": args.group,
            "last_days": args.last_days if args.last_days and args.last_days > 0 else None,
            "max_posts_requested": args.max_posts,
            "max_comments_per_post": args.max_comments,
            "concurrency": args.concurrency,
            "feed_headed": bool(args.feed_headed),
            "crawl_headed": bool(args.headed),
        },
        "posts": out_posts,
    }


# ----------------------------
# CLI (always show help on missing args)
# ----------------------------

def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=Path(sys.argv[0]).name,
        formatter_class=argparse.RawTextHelpFormatter,
        add_help=True,
        description=(
            "Fetch posts from a Facebook group and write ONE primary JSON file:\n"
            "  posts sorted newest->oldest + extracted comments/replies per post.\n\n"
            "By default, ONLY the primary output is written.\n"
            "If you pass --debug, an extra debug JSON file is written.\n\n"
            "Unix-like behavior:\n"
            "  - No args OR missing required args => print this help and exit(0).\n"
        ),
        epilog=(
            "Examples:\n"
            "  Default (only main file):\n"
            "    python3 fb_sorted_posts_comments.py --group https://www.facebook.com/groups/kvartira.berlin --last-days 10\n\n"
            "  Also write debug file:\n"
            "    python3 fb_sorted_posts_comments.py --group https://www.facebook.com/groups/kvartira.berlin --last-days 10 --debug\n"
        ),
    )

    # Basic controls
    g_basic = parser.add_argument_group("Basic controls")
    g_basic.add_argument("--group", help="Facebook group URL (required).")
    g_basic.add_argument("--state", default="facebook_state.json", help="Playwright storage state JSON (default: facebook_state.json)")
    g_basic.add_argument("--last-days", type=int, default=0, help="If >0: keep only posts from the last N days (default: 0)")
    g_basic.add_argument("--max-posts", type=int, default=50, help="How many unique posts to collect from the feed (default: 50)")
    g_basic.add_argument("--max-comments", type=int, default=50, help="Max comment/reply items per post (default: 50)")
    g_basic.add_argument("--concurrency", type=int, default=3, help="How many posts to crawl in parallel (default: 3)")
    g_basic.add_argument("--feed-headed", action="store_true", help="Show browser during feed capture (default: headless)")
    g_basic.add_argument("--headed", action="store_true", help="Show browser during post crawling (default: headless)")

    # Output control (main output is ALWAYS produced)
    g_out = parser.add_argument_group("Output control")
    g_out.add_argument("--out", default="sorted_n_days.json",
                       help="Primary output file (default: sorted_n_days.json)\n"
                            "Contains: posts sorted newest->oldest + comments.")

    # Debug (optional extra file)
    g_dbg = parser.add_argument_group("Debug output (optional)")
    g_dbg.add_argument("--debug", action="store_true",
                       help="If set, also write a debug JSON file (raw feed + crawl internals). OFF by default.")
    g_dbg.add_argument("--debug-out", default="debug_output.json",
                       help="Debug JSON output path (used only with --debug). Default: debug_output.json")

    # Timing & pauses
    g_timing = parser.add_argument_group("Timing & pauses (tuning)")
    g_timing.add_argument("--page-timeout", type=int, default=60_000, help="Navigation timeout in ms (default: 60000)")
    g_timing.add_argument("--per-post-max-seconds", type=int, default=90, help="Max seconds per post before bailing (default: 90)")
    g_timing.add_argument("--pause-ms", type=int, default=450, help="Pause between scroll steps inside comments (default: 450)")
    g_timing.add_argument("--feed-pause-ms", type=int, default=900, help="Pause after each feed scroll (default: 900)")

    # Advanced knobs
    g_adv = parser.add_argument_group("Advanced controls")
    g_adv.add_argument("--graphql-batch", type=int, default=2, help="GraphQL responses processed per cycle (default: 2)")
    g_adv.add_argument("--stall-limit", type=int, default=6, help="Stop feed after N stall cycles (default: 6)")
    g_adv.add_argument("--feed-scroll-px", type=int, default=1500, help="Pixels per feed scroll step (default: 1500)")
    g_adv.add_argument("--scroll-steps", type=int, default=6, help="Scroll steps per cycle inside comments (default: 6)")
    g_adv.add_argument("--cycles", type=int, default=8, help="Max scroll cycles per post (default: 8)")
    g_adv.add_argument("--no-growth-cycles", type=int, default=2, help="Stop after N no-growth cycles (default: 2)")

    # Logging
    g_log = parser.add_argument_group("Logging")
    g_log.add_argument("--verbose", action="store_true", help="Verbose logs (disables progress bars)")

    return parser

def parse_args_unix() -> argparse.Namespace:
    parser = make_parser()

    # no args => show help, exit 0
    if len(sys.argv) == 1:
        parser.print_help(sys.stdout)
        raise SystemExit(0)

    args = parser.parse_args()

    # missing required logical arg => show help, exit 0
    if not args.group:
        parser.print_help(sys.stdout)
        raise SystemExit(0)

    return args


# ----------------------------
# main
# ----------------------------

def main():
    args = parse_args_unix()

    state_file = Path(args.state)
    if not state_file.exists():
        raise SystemExit(f"Missing state file: {state_file}")

    if args.concurrency < 1:
        raise SystemExit("--concurrency must be >= 1")

    # 1) feed capture
    feed_obj = capture_posts_from_feed(args)

    # 2) filter for last-days
    filtered = filter_last_days(feed_obj, args.last_days)

    # 3) crawl
    posts_to_crawl = filtered.get("posts", [])
    if args.verbose:
        print(f"[crawl] Starting crawl posts={len(posts_to_crawl)} concurrency={args.concurrency} headed={args.headed}")
    crawl_obj = asyncio.run(crawl_all_posts(posts_to_crawl, args, state_file))

    # 4) MAIN output: always written
    main_obj = build_main_sorted_output(filtered, crawl_obj, args)
    Path(args.out).write_text(json.dumps(main_obj, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[+] Wrote main sorted output: {args.out}")

    # 5) OPTIONAL debug: only if --debug
    if args.debug:
        debug_obj = {
            "meta": {
                "run_at": utc_ts(),
                "note": "Debug output contains raw feed capture + filtered posts + crawl internals.",
            },
            "feed_posts": feed_obj,
            "filtered_posts": filtered,
            "crawl_results": crawl_obj,
        }
        Path(args.debug_out).write_text(json.dumps(debug_obj, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[+] Wrote debug output: {args.debug_out}")

if __name__ == "__main__":
    main()
