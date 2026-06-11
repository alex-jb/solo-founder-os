"""Splunk HTTP Event Collector client — stdlib only.

Splunk HEC accepts JSON events at:
    POST <hec-url>/services/collector/event
    Authorization: Splunk <token>
    Body: {"event": {...}, "sourcetype": "...", "source": "...",
           "host": "...", "time": <epoch float>}

Multiple events can be batched by concatenating JSON objects (no array,
no commas) into the body. That's the protocol — don't "fix" it.

The client is deliberately small. It does ONE thing: POST a list of
events with retry. Sourcetype/source/host policy lives in
`sfos_translator`, not here.
"""
from __future__ import annotations
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from typing import Iterable


class HECError(RuntimeError):
    """Raised when HEC returns non-2xx or the body can't be parsed."""


class HECClient:
    """Minimal Splunk HEC POST client.

    Usage:
        hec = HECClient.from_env()              # uses SPLUNK_HEC_URL + TOKEN
        hec.send_event({"agent": "sfos", "outcome": "OK"},
                       sourcetype="sfos:reflection")

    No-op mode: if `url` and env are both empty, every send() returns 0
    and logs nothing. This is intentional so SFOS keeps running on
    machines that haven't opted into Splunk.
    """

    def __init__(
        self,
        url: str = "",
        token: str = "",
        *,
        default_source: str = "sfos",
        default_host: str = "",
        verify_tls: bool = True,
        timeout: float = 10.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.token = token
        self.default_source = default_source
        self.default_host = default_host or os.uname().nodename
        self.verify_tls = verify_tls
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "HECClient":
        return cls(
            url=os.environ.get("SPLUNK_HEC_URL", "").strip(),
            token=os.environ.get("SPLUNK_HEC_TOKEN", "").strip(),
            verify_tls=os.environ.get("SPLUNK_HEC_VERIFY_TLS", "1") != "0",
        )

    @property
    def enabled(self) -> bool:
        return bool(self.url and self.token)

    def send_event(
        self,
        event: dict,
        *,
        sourcetype: str,
        source: str | None = None,
        host: str | None = None,
        event_time: float | None = None,
    ) -> int:
        """Send a single event. Returns 1 if accepted, 0 if no-op."""
        return self.send_events(
            [event],
            sourcetype=sourcetype,
            source=source,
            host=host,
            event_time=event_time,
        )

    def send_events(
        self,
        events: Iterable[dict],
        *,
        sourcetype: str,
        source: str | None = None,
        host: str | None = None,
        event_time: float | None = None,
    ) -> int:
        """Send a batch. Returns count accepted, 0 if no-op."""
        if not self.enabled:
            return 0

        body_parts = []
        count = 0
        for ev in events:
            wrap = {
                "event": ev,
                "sourcetype": sourcetype,
                "source": source or self.default_source,
                "host": host or self.default_host,
                "time": event_time if event_time is not None else time.time(),
            }
            body_parts.append(json.dumps(wrap, separators=(",", ":")))
            count += 1
        if count == 0:
            return 0

        body = "\n".join(body_parts).encode("utf-8")
        req = urllib.request.Request(
            f"{self.url}/services/collector/event",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Splunk {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "sfos-splunk-obs/0.1",
            },
        )

        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    raw = r.read().decode("utf-8", errors="replace")
                    try:
                        resp = json.loads(raw)
                    except json.JSONDecodeError:
                        raise HECError(f"HEC non-JSON response: {raw[:200]}")
                    if resp.get("code") != 0:
                        raise HECError(f"HEC error: {resp}")
                    return count
            except urllib.error.HTTPError as e:
                # 4xx is caller's bug (bad token / bad payload) — don't retry.
                if 400 <= e.code < 500:
                    raise HECError(f"HEC HTTP {e.code}: {e.read()[:200]!r}") from e
                last_exc = e
            except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
                last_exc = e
            time.sleep(2 ** attempt)

        raise HECError(f"HEC POST failed after 3 attempts: {last_exc}")


def verify_webhook_signature(secret: str, signature_header: str, body: bytes) -> bool:
    """Constant-time HMAC-SHA256 compare for inbound Splunk webhooks.

    Splunk Alert Action webhooks include `X-Splunk-Signature: sha256=<hex>`
    when configured with a shared secret. Always use hmac.compare_digest
    so the comparison takes the same wall-time regardless of where the
    mismatch is — defends against timing side-channel attacks.
    """
    if not secret or not signature_header:
        return False
    import hashlib
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    sent = signature_header.removeprefix("sha256=").strip()
    return hmac.compare_digest(expected, sent)
