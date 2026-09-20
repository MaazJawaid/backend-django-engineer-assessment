"""Per-request stage timing for route optimize / map views."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator

from django.conf import settings

logger = logging.getLogger(__name__)


class StageTimer:
    """Collect wall-clock durations for named pipeline stages."""

    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self._stages: dict[str, float] = {}
        self.meta: dict[str, Any] = {}
        self._t0 = time.perf_counter() if enabled else 0.0

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        if not self.enabled:
            yield
            return
        start = time.perf_counter()
        try:
            yield
        finally:
            self._stages[name] = round((time.perf_counter() - start) * 1000, 1)

    def finish(self) -> None:
        if not self.enabled:
            return
        self._stages["total"] = round((time.perf_counter() - self._t0) * 1000, 1)

    @property
    def stages(self) -> dict[str, float]:
        return dict(self._stages)

    def as_dict(self) -> dict[str, Any]:
        return {
            "unit": "ms",
            "stages": self.stages,
            "meta": dict(self.meta),
        }

    def server_timing_header(self) -> str:
        """W3C Server-Timing value, e.g. ``corridor_match;dur=6200, total;dur=6283``."""
        parts = []
        for name, ms in self._stages.items():
            # Metric names must be token-safe (no spaces)
            safe = name.replace(" ", "_")
            parts.append(f"{safe};dur={ms}")
        return ", ".join(parts)

    def apply_to_response(self, response: Any) -> Any:
        """Attach Server-Timing header when enabled; return response unchanged."""
        if self.enabled and self._stages:
            response["Server-Timing"] = self.server_timing_header()
        return response

    def log(self, log: logging.Logger | None = None, label: str = "request") -> None:
        if not self.enabled:
            return
        log = log or logger
        stage_bits = " ".join(f"{k}={v}ms" for k, v in self._stages.items())
        meta_bits = " ".join(f"{k}={v}" for k, v in self.meta.items())
        log.info("%s timings %s %s", label, stage_bits, meta_bits)


def get_timer() -> StageTimer:
    """Return a real timer when INCLUDE_TIMINGS is on, else a no-op timer."""
    enabled = bool(getattr(settings, "INCLUDE_TIMINGS", False))
    return StageTimer(enabled=enabled)
