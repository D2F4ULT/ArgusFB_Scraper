from __future__ import annotations

import asyncio
import json
from pathlib import Path

from cli import parse_args_unix
from feed_capture import capture_posts_from_feed
from filters import filter_last_days
from crawl import crawl_all_posts
from output_build import build_main_sorted_output
from utils import utc_ts


def main():
    args = parse_args_unix()

    state_file = Path(args.state)
    if not state_file.exists():
        raise SystemExit(f"Missing state file: {state_file}")

    if args.concurrency < 1:
        raise SystemExit("--concurrency must be >= 1")

    feed_obj = capture_posts_from_feed(args)
    filtered = filter_last_days(feed_obj, args.last_days)

    posts_to_crawl = filtered.get("posts", [])
    if args.verbose:
        print(f"[crawl] Starting crawl posts={len(posts_to_crawl)} concurrency={args.concurrency} headed={args.headed}")

    crawl_obj = asyncio.run(crawl_all_posts(posts_to_crawl, args, state_file))

    main_obj = build_main_sorted_output(filtered, crawl_obj, args)
    Path(args.out).write_text(json.dumps(main_obj, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[+] Wrote main sorted output: {args.out}")

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
