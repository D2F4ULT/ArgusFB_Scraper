from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

from config import POST_URL_RE
from progress import ProgressBar
from utils import (
    utc_ts,
    safe_int,
    unix_to_iso_utc,
    unix_to_pretty_berlin,
    is_graphql,
    extract_group_token_from_url,
    normalize_post_url,
    parse_fb_graphql_text,
)


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
