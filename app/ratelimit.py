"""Sliding-window rate limiter (report Section 11: rate-limited integrations).

In-process and per-key (client IP for login, username for chat). In a real
deployment an API gateway would sit in front; this is the in-app backstop so
the service protects itself even when reached directly.
"""
import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, max_events: int, window_s: float) -> None:
        self.max_events = max_events
        self.window_s = window_s
        self._events: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._events[key]
            while q and now - q[0] > self.window_s:
                q.popleft()
            if len(q) >= self.max_events:
                return False
            q.append(now)
            return True
