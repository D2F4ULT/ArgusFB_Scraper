from __future__ import annotations

import argparse
import sys
from pathlib import Path

from config import PATHS


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=Path(sys.argv[0]).name,
        formatter_class=argparse.RawTextHelpFormatter,
        add_help=True,
        description=(
            "Fetch posts from a Facebook group and write ONE primary JSON file:\n"
            "  posts sorted newest->oldest + extracted comments/replies per post.\n\n"
            "Unix-like behavior:\n"
            "  - No args OR missing required args => print help and exit(0).\n"
        ),
        epilog=(
            "Examples:\n"
            "  Default:\n"
            "    python3 src/main.py --group https://www.facebook.com/groups/kvartira.berlin --last-days 10\n\n"
            "  Debug:\n"
            "    python3 src/main.py --group https://www.facebook.com/groups/kvartira.berlin --last-days 10 --debug\n"
        ),
    )

    g_basic = parser.add_argument_group("Basic controls")
    g_basic.add_argument("--group", help="Facebook group URL (required).")
    g_basic.add_argument("--state", default=str(PATHS.default_state), help=f"Playwright storage state JSON (default: {PATHS.default_state})")
    g_basic.add_argument("--last-days", type=int, default=0, help="If >0: keep only posts from the last N days (default: 0)")
    g_basic.add_argument("--max-posts", type=int, default=50, help="How many unique posts to collect from the feed (default: 50)")
    g_basic.add_argument("--max-comments", type=int, default=50, help="Max comment/reply items per post (default: 50)")
    g_basic.add_argument("--concurrency", type=int, default=3, help="How many posts to crawl in parallel (default: 3)")
    g_basic.add_argument("--feed-headed", action="store_true", help="Show browser during feed capture (default: headless)")
    g_basic.add_argument("--headed", action="store_true", help="Show browser during post crawling (default: headless)")

    g_out = parser.add_argument_group("Output control")
    g_out.add_argument("--out", default=str(PATHS.default_out), help=f"Primary output JSON (default: {PATHS.default_out})")

    g_dbg = parser.add_argument_group("Debug output (optional)")
    g_dbg.add_argument("--debug", action="store_true", help="If set, also write debug JSON.")
    g_dbg.add_argument("--debug-out", default=str(PATHS.default_debug_out), help=f"Debug JSON path (default: {PATHS.default_debug_out})")

    g_timing = parser.add_argument_group("Timing & pauses (tuning)")
    g_timing.add_argument("--page-timeout", type=int, default=60_000, help="Navigation timeout in ms (default: 60000)")
    g_timing.add_argument("--per-post-max-seconds", type=int, default=90, help="Max seconds per post before bailing (default: 90)")
    g_timing.add_argument("--pause-ms", type=int, default=450, help="Pause between scroll steps inside comments (default: 450)")
    g_timing.add_argument("--feed-pause-ms", type=int, default=900, help="Pause after each feed scroll (default: 900)")

    g_adv = parser.add_argument_group("Advanced controls")
    g_adv.add_argument("--graphql-batch", type=int, default=2, help="GraphQL responses processed per cycle (default: 2)")
    g_adv.add_argument("--stall-limit", type=int, default=6, help="Stop feed after N stall cycles (default: 6)")
    g_adv.add_argument("--feed-scroll-px", type=int, default=1500, help="Pixels per feed scroll step (default: 1500)")
    g_adv.add_argument("--scroll-steps", type=int, default=6, help="Scroll steps per cycle inside comments (default: 6)")
    g_adv.add_argument("--cycles", type=int, default=8, help="Max scroll cycles per post (default: 8)")
    g_adv.add_argument("--no-growth-cycles", type=int, default=2, help="Stop after N no-growth cycles (default: 2)")

    g_log = parser.add_argument_group("Logging")
    g_log.add_argument("--verbose", action="store_true", help="Verbose logs (disables progress bars)")

    return parser


def parse_args_unix() -> argparse.Namespace:
    parser = make_parser()

    if len(sys.argv) == 1:
        parser.print_help(sys.stdout)
        raise SystemExit(0)

    args = parser.parse_args()

    if not args.group:
        parser.print_help(sys.stdout)
        raise SystemExit(0)

    return args
