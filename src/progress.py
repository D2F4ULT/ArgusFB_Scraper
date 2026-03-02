from __future__ import annotations

import sys
import time


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
