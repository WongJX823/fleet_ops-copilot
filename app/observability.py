"""In-process observability (report Section 13 metrics).

Counts and timings kept in memory and exposed at /api/metrics (manager only):

- requests: per route -- count, error count, avg/max latency
- tools:    per governed tool -- count, avg/max latency
- llm:      calls, prompt/completion tokens, and estimated USD cost
- rate_limited: how many requests the limiter rejected

Per-turn numbers (latency, tokens, cost) also land in the audit log, so the
'cost per completed task' metric from the report can be derived offline.
This is deliberately in-process for the pilot; export to Prometheus/OTLP by
scraping snapshot() when a real monitoring stack exists.
"""
import threading
import time
from collections import defaultdict

# USD per 1M tokens (input, output). Unknown models cost 0 (e.g. the stub).
PRICES_PER_MTOK = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
}


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with getattr(self, "_lock", threading.Lock()):
            self.started_at = time.time()
            self.requests: dict[str, dict] = defaultdict(
                lambda: {"count": 0, "errors": 0, "total_ms": 0.0, "max_ms": 0.0}
            )
            self.tools: dict[str, dict] = defaultdict(
                lambda: {"count": 0, "total_ms": 0.0, "max_ms": 0.0}
            )
            self.llm = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
            self.rate_limited = 0

    def record_request(self, route: str, status: int, ms: float) -> None:
        with self._lock:
            r = self.requests[route]
            r["count"] += 1
            r["errors"] += 1 if status >= 400 else 0
            r["total_ms"] += ms
            r["max_ms"] = max(r["max_ms"], ms)

    def record_tool(self, name: str, ms: float) -> None:
        with self._lock:
            t = self.tools[name]
            t["count"] += 1
            t["total_ms"] += ms
            t["max_ms"] = max(t["max_ms"], ms)

    def record_llm(self, model: str, prompt_tokens: int, completion_tokens: int) -> float:
        """Returns the estimated cost of this call in USD."""
        in_price, out_price = PRICES_PER_MTOK.get(model, (0.0, 0.0))
        cost = (prompt_tokens * in_price + completion_tokens * out_price) / 1_000_000
        with self._lock:
            self.llm["calls"] += 1
            self.llm["prompt_tokens"] += prompt_tokens
            self.llm["completion_tokens"] += completion_tokens
            self.llm["cost_usd"] += cost
        return cost

    def record_rate_limited(self) -> None:
        with self._lock:
            self.rate_limited += 1

    def snapshot(self) -> dict:
        with self._lock:
            def fold(table: dict) -> dict:
                return {
                    name: {
                        **{k: v for k, v in row.items() if k != "total_ms"},
                        "avg_ms": round(row["total_ms"] / row["count"], 1) if row["count"] else 0.0,
                        "max_ms": round(row["max_ms"], 1),
                    }
                    for name, row in sorted(table.items())
                }

            return {
                "uptime_s": round(time.time() - self.started_at, 1),
                "requests": fold(self.requests),
                "tools": fold(self.tools),
                "llm": {**self.llm, "cost_usd": round(self.llm["cost_usd"], 6)},
                "rate_limited": self.rate_limited,
            }


metrics = Metrics()
