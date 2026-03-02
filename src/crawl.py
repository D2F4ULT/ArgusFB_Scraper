from __future__ import annotations

import asyncio
import time
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PWTimeoutError

from comments_extract import (
    comment_root_selector,
    export_comments_from_root_html,
    extract_post_metrics_from_page_html,
)
from progress import ProgressBar
from utils import fmt_ratio


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
