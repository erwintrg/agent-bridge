"""Wall clock. Every time-based rule (row stamps, the duplicate window, wake-up gaps, crash detection) reads this,
so the demo and the tests can run on simulated time."""
from __future__ import annotations

import time
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

STAMP_FMT = "%Y-%m-%d %H:%M"


def _tz(name: str):
    if not name or name.upper() == "UTC" or ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception:  # unknown zone or no tz database: fall back to UTC instead of crashing the watcher
        return timezone.utc


class Clock:
    def __init__(self, tz: str = "UTC"):
        self.tz = _tz(tz)

    def now(self) -> float:
        return time.time()

    def dt(self, ts: float | None = None) -> datetime:
        return datetime.fromtimestamp(self.now() if ts is None else ts, self.tz)

    def stamp(self, ts: float | None = None) -> str:
        """Board timestamp, minute precision, like a person would type it into a sheet."""
        return self.dt(ts).strftime(STAMP_FMT)

    def parse_stamp(self, s: str) -> float | None:
        try:
            return datetime.strptime((s or "").strip()[:16], STAMP_FMT).replace(tzinfo=self.tz).timestamp()
        except ValueError:
            return None


class SimClock(Clock):
    """Simulated time for the demo and the tests."""

    def __init__(self, start: float, tz: str = "UTC"):
        super().__init__(tz)
        self.t = float(start)

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds
