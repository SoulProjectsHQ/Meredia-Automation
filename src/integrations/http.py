"""HTTP access for research modules.

This file holds the contract that other modules build on: HttpResponse and the HttpClient
protocol. Modules take an HttpClient so tests can pass a fake and never touch the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# Statuses handed back to the caller. Every other status of 400 or above raises AutomationError.
PASSTHROUGH_STATUSES = frozenset({404, 410})


@dataclass(frozen=True)
class HttpResponse:
    url: str  # final URL after redirects
    status: int
    body: str
    elapsed_ms: int
    size_bytes: int
    content_type: str | None = None


class HttpClient(Protocol):
    def get(self, url: str, *, timeout: float = 10.0, max_bytes: int = 2_000_000) -> HttpResponse:
        """Fetch a URL. Returns 2xx, 404 and 410 responses, raises AutomationError otherwise."""
        ...
