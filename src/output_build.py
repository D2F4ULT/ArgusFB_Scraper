from __future__ import annotations

from utils import utc_ts, fmt_ratio


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
