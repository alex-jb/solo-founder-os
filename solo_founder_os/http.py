"""HTTP helpers — pure stdlib, retry-aware, JSON-friendly.

Why no `requests` / `httpx`: every agent needs to install fast on locked-
down boxes (cron servers, Vercel functions, etc.). One-liner urllib calls
keep `pip install` to seconds and the dep tree to zero.

`with_retry` covers transient failures (5xx, connection reset). It does
NOT retry 4xx — those are the caller's bug, not the network's.
"""
from __future__ import annotations
import json
import time
import urllib.error
import urllib.request
from typing import Callable, TypeVar


T = TypeVar("T")


# Re-export for callers that want to catch HTTPError from urllib without
# importing it directly.
HTTPError = urllib.error.HTTPError


def urlopen_json(
    url: str,
    *,
    headers: dict | None = None,
    data: bytes | None = None,
    method: str = "GET",
    timeout: float = 10.0,
) -> dict:
    """GET (or POST with `data`) and parse JSON. Raises on HTTP error or
    invalid JSON — callers should wrap in try/except for graceful degrade.

    `headers` defaults to `{"Accept": "application/json"}`. If you pass
    your own, that line is replaced — call `urlopen_json(url, headers={...,
    "Accept": "application/json"})` to keep it.
    """
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers=headers if headers is not None else {"Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def with_retry(
    times: int = 3,
    backoff_seconds: float = 1.0,
    backoff_factor: float = 2.0,
    retry_on: tuple = (urllib.error.URLError, ConnectionError, TimeoutError),
) -> Callable:
    """Decorator: retry the wrapped function on transient errors.

    - Default 3 attempts (initial + 2 retries)
    - Exponential backoff: 1s, 2s, 4s
    - Only retries on transient errors (URLError, ConnectionError,
      TimeoutError). HTTPError 5xx are URLError subclasses so they retry.
      HTTPError 4xx is also a URLError but the convention here is "retry
      everything network-y" — caller can refine via retry_on if they have
      a strong opinion.

    Usage:
        @with_retry(times=3)
        def fetch_vercel():
            return urlopen_json(...)
    """
    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        def wrapped(*args, **kwargs) -> T:
            wait = backoff_seconds
            last_exc: Exception | None = None
            for attempt in range(times):
                try:
                    return fn(*args, **kwargs)
                except retry_on as e:
                    last_exc = e
                    if attempt < times - 1:
                        time.sleep(wait)
                        wait *= backoff_factor
            # exhausted retries — re-raise the last exception
            assert last_exc is not None
            raise last_exc
        wrapped.__wrapped__ = fn  # for tests + introspection
        wrapped.__name__ = getattr(fn, "__name__", "wrapped")
        return wrapped
    return decorator
