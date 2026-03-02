from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo
import re

BERLIN = ZoneInfo("Europe/Berlin")

GRAPHQL_MARKERS = ("/api/graphql", "graphql")
POST_URL_RE = re.compile(r"^https?://(www\.)?facebook\.com/groups/([^/]+)/posts/(\d+)/?")

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


@dataclass(frozen=True)
class Paths:
    """
    Single source of truth for default paths.
    You can still override via CLI args.
    """
    default_state: Path = Path("facebook_state.json")
    default_out: Path = Path("sorted_n_days.json")
    default_debug_out: Path = Path("debug_output.json")


PATHS = Paths()
