# ArgusFB_Scraper

A Unix-style CLI scraper that captures **posts from a Facebook group feed**, sorts them **newest → oldest**, and enriches each post with **reactions**, **comment totals**, and **extracted comments/replies**.

This project uses:

- **Playwright** (Chromium) to browse Facebook as an authenticated user (via `facebook_state.json`)
- **GraphQL network response parsing** to collect post URLs + creation timestamps from the feed
- **DOM-based extraction** (BeautifulSoup) to scroll and parse comments/replies per post
- A single primary output file: **sorted posts + extracted comments**, in JSON

> ⚠️ **Legal / ToS notice**  
> Scraping Facebook may violate Facebook’s Terms of Service and may be restricted by law depending on your jurisdiction and usage. Use this tool **only** on accounts and groups where you have permission and a lawful basis to access and process the data. You are responsible for compliance.

---

## Features

- Captures up to `--max-posts` unique posts from a group feed
- Optional filter to keep only posts from the last `--last-days` days
- Crawls each post page and extracts:
  - `post_reactions` (best-effort)
  - `post_comments_total` (best-effort)
  - `comments[]` (comments and replies)
- Outputs **one primary JSON** file by default
- Optional **debug JSON** that includes feed capture + filter + crawl internals

---

## Project structure

```
ArgusFB_Scraper/
  README.md
  requirements.txt
  facebook_state.json          # ignored by git (contains login session)
  src/
    main.py                    # entrypoint
    cli.py                     # argparse CLI
    config.py                  # constants / regex / default paths
    utils.py                   # helpers
    progress.py                # progress bar
    feed_capture.py            # feed GraphQL capture
    filters.py                 # last-days filtering
    comments_extract.py        # DOM parsing for comments + post metrics
    crawl.py                   # async crawling for posts
    output_build.py            # output JSON builder
```

---

## Requirements

- Python **3.10+** (recommended: 3.11/3.12)
- macOS/Linux/Windows
- Playwright Chromium installed (first-time setup)

---

## Installation

### 1) Clone

```bash
git clone https://github.com/D2F4ULT/ArgusFB_Scraper.git
cd ArgusFB_Scraper
```

### 2) Create a virtual environment (recommended)

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3) Install dependencies

```bash
pip install -r requirements.txt
playwright install chromium
```

---

## Authentication (`facebook_state.json`)

This project expects a Playwright storage state file in the project root:

- `facebook_state.json`

It contains cookies/localStorage for a logged-in Facebook session.

### Option A: Generate it with a quick Playwright snippet

Create `scripts/save_state.py` (or run this as a one-off) and log in interactively:

```python
from playwright.sync_api import sync_playwright

STATE_OUT = "facebook_state.json"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()

    page.goto("https://www.facebook.com/")
    print("Log in in the opened browser window, then press ENTER here...")
    input()

    context.storage_state(path=STATE_OUT)
    print(f"Saved {STATE_OUT}")
    browser.close()
```

Run:

```bash
python3 scripts/save_state.py
```

## Usage

Run from the project root:

```bash
python3 src/main.py --group https://www.facebook.com/groups/<GROUP_TOKEN> --last-days 10
```

### Minimal example (main output only)

```bash
python3 src/main.py --group https://www.facebook.com/groups/kvartira.berlin --last-days 10
```

This writes the primary output JSON:

- `sorted_n_days.json` (default)

### Debug output (adds one extra JSON)

```bash
python3 src/main.py --group https://www.facebook.com/groups/kvartira.berlin --last-days 10 --debug
```

This writes:

- `sorted_n_days.json` (main)
- `debug_output.json` (debug)

---

## CLI options

The CLI is intentionally “Unix-ish”:

- If you run with **no args** or forget `--group`, it prints full help and exits with code `0`.

Key flags:

- `--group` *(required)*: Facebook group URL
- `--state`: path to Playwright storage state JSON (default: `facebook_state.json`)
- `--last-days`: keep only posts from the last N days
- `--max-posts`: max unique posts to collect from feed
- `--max-comments`: max comment/reply items per post
- `--concurrency`: how many post crawls in parallel
- `--feed-headed`: show browser during feed capture
- `--headed`: show browser during post crawling
- `--out`: primary output JSON path
- `--debug`: write debug JSON
- `--debug-out`: debug output JSON path
- `--verbose`: prints logs and disables progress bars

To see all options:

```bash
python3 src/main.py --help
```

```
usage: main.py [-h] [--group GROUP] [--state STATE] [--last-days LAST_DAYS] [--max-posts MAX_POSTS] [--max-comments MAX_COMMENTS] [--concurrency CONCURRENCY]
               [--feed-headed] [--headed] [--out OUT] [--debug] [--debug-out DEBUG_OUT] [--page-timeout PAGE_TIMEOUT] [--per-post-max-seconds PER_POST_MAX_SECONDS]
               [--pause-ms PAUSE_MS] [--feed-pause-ms FEED_PAUSE_MS] [--graphql-batch GRAPHQL_BATCH] [--stall-limit STALL_LIMIT] [--feed-scroll-px FEED_SCROLL_PX]
               [--scroll-steps SCROLL_STEPS] [--cycles CYCLES] [--no-growth-cycles NO_GROWTH_CYCLES] [--verbose]

Fetch posts from a Facebook group and write ONE primary JSON file:
  posts sorted newest->oldest + extracted comments/replies per post.

Unix-like behavior:
  - No args OR missing required args => print help and exit(0).

options:
  -h, --help            show this help message and exit

Basic controls:
  --group GROUP         Facebook group URL (required).
  --state STATE         Playwright storage state JSON (default: facebook_state.json)
  --last-days LAST_DAYS
                        If >0: keep only posts from the last N days (default: 0)
  --max-posts MAX_POSTS
                        How many unique posts to collect from the feed (default: 50)
  --max-comments MAX_COMMENTS
                        Max comment/reply items per post (default: 50)
  --concurrency CONCURRENCY
                        How many posts to crawl in parallel (default: 3)
  --feed-headed         Show browser during feed capture (default: headless)
  --headed              Show browser during post crawling (default: headless)

Output control:
  --out OUT             Primary output JSON (default: sorted_n_days.json)

Debug output (optional):
  --debug               If set, also write debug JSON.
  --debug-out DEBUG_OUT
                        Debug JSON path (default: debug_output.json)

Timing & pauses (tuning):
  --page-timeout PAGE_TIMEOUT
                        Navigation timeout in ms (default: 60000)
  --per-post-max-seconds PER_POST_MAX_SECONDS
                        Max seconds per post before bailing (default: 90)
  --pause-ms PAUSE_MS   Pause between scroll steps inside comments (default: 450)
  --feed-pause-ms FEED_PAUSE_MS
                        Pause after each feed scroll (default: 900)

Advanced controls:
  --graphql-batch GRAPHQL_BATCH
                        GraphQL responses processed per cycle (default: 2)
  --stall-limit STALL_LIMIT
                        Stop feed after N stall cycles (default: 6)
  --feed-scroll-px FEED_SCROLL_PX
                        Pixels per feed scroll step (default: 1500)
  --scroll-steps SCROLL_STEPS
                        Scroll steps per cycle inside comments (default: 6)
  --cycles CYCLES       Max scroll cycles per post (default: 8)
  --no-growth-cycles NO_GROWTH_CYCLES
                        Stop after N no-growth cycles (default: 2)

Logging:
  --verbose             Verbose logs (disables progress bars)

Examples:
  Default:
    python3 src/main.py --group https://www.facebook.com/groups/kvartira.berlin --last-days 10

  Debug:
    python3 src/main.py --group https://www.facebook.com/groups/kvartira.berlin --last-days 10 --debug
```
---

## How it works (pipeline)

1. **Feed capture** (`feed_capture.py`)
   - Opens the group feed page
   - Listens to network responses
   - Filters GraphQL-like responses
   - Extracts `(post_id, url, creation_time)` from nested JSON

2. **Filter (optional)** (`filters.py`)
   - Keeps only posts newer than `now - last_days`

3. **Crawl posts** (`crawl.py`)
   - Opens each post page
   - Extracts “best effort” metrics (reactions + comment count)
   - Finds the comment root container
   - Scrolls and parses comment/reply “articles.”
   - Deduplicates items using a stable key

4. **Build output** (`output_build.py`)
   - Merges feed + crawl results
   - Sorts newest → oldest
   - Writes a single primary JSON file

---

## Output format

The primary output file (`--out`, default: `sorted_n_days.json`) is:

```json
{
  "meta": {
    "run_at": "...",
    "group_url": "...",
    "last_days": 10,
    "max_posts_requested": 50,
    "max_comments_per_post": 50,
    "concurrency": 3,
    "feed_headed": false,
    "crawl_headed": false
  },
  "posts": [
    {
      "post_id": "123...",
      "url": "https://www.facebook.com/groups/.../posts/.../",
      "creation_time": 1700000000,
      "created_at_iso_utc": "2025-03-01T...",
      "created_at_pretty_berlin": "Saturday, March 1, 2025 at 1:23 PM",

      "post_reactions": 42,
      "post_comments_total": 17,
      "post_comments_collected": 12,
      "post_comments_progress": "12/17",

      "comments": [
        {
          "type": "comment",
          "author": "Display Name",
          "aria_author": "Display Name",
          "relation": null,
          "age": "2 days ago",
          "aria_label": "Comment by ...",
          "text": "Comment text...",
          "profile_link": "https://www.facebook.com/...",
          "comment_link": "https://www.facebook.com/groups/.../?comment_id=...",
          "comment_link_canon": "https://www.facebook.com/groups/.../?comment_id=...",
          "reactions": 3
        }
      ]
    }
  ]
}
```

Notes:

- `post_reactions` / `post_comments_total` are **best-effort** and can be `null`
- `comments[]` may include both:
  - `"type": "comment"`
  - `"type": "reply"`
- `post_comments_progress` is `collected/total` or `collected/?` when unknown

---

## Common issues & fixes

### “Missing state file: facebook_state.json”
You need to generate the state file first (see **Authentication**).

### “comment_root_not_found”
Facebook markup changes often. The comment root selector is based on known class clusters. If it breaks, you may need to update:

- `COMMENT_ROOT_CLASSES` in `src/config.py`

### Timeouts / slow crawling
Increase these flags:

- `--page-timeout 90000`
- `--per-post-max-seconds 180`
- `--pause-ms 600`
- Reduce concurrency: `--concurrency 1`

### Getting blocked / login checkpoints
Use `--feed-headed` and `--headed` to see what’s happening:

```bash
python3 src/main.py --group ... --feed-headed --headed --verbose
```

Then re-generate `facebook_state.json` if needed.

---

## Development notes

### Update selectors safely
Keep all selectors / class clusters in:

- `src/config.py`

That keeps DOM tuning centralized and review-friendly.

### Keep sensitive files out of Git
Do **not** commit:

- `facebook_state.json`
- output JSON files
- `__pycache__/` and `.pyc`

Your `.gitignore` should cover these.

---

## License

GPL-3.0 (see `LICENSE`).

---

## Disclaimer

This tool i
