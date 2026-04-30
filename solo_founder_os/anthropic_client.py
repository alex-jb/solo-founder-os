"""Wrapped Anthropic client with auto-log + graceful degrade + prompt caching.

Pattern: every agent that calls Claude does the same 6 things —
1. Check ANTHROPIC_API_KEY exists, return safely if not
2. Construct anthropic.Anthropic() (lazy import so no-key path doesn't pay)
3. Format system prompt with cache_control if cache_system=True
4. Call messages.create()
5. Catch any exception and return graceful fallback
6. Log token usage to ~/.<agent>/usage.jsonl (including cache hit/miss)

Centralizing here means one fix benefits all current AND future agents.

The wrapped client intentionally returns the raw response object, NOT a
parsed string. Each agent's prompt format differs (JSON / VERDICT lines /
free text), so they each parse from `response.content`. We only handle
the boilerplate around the call.

v0.4: Prompt caching support. Pass `cache_system=True` (default) to
auto-wrap any string `system` argument in the cache_control format
Anthropic's API expects. Cache reads cost 10% of base input price,
cache writes 125% (5min TTL) or 200% (1h TTL). Net effect: after the
first request hits the cache, system-prompt input tokens cost 90% less.

Caching minimums (you must clear these for the cache to actually engage):
  - Haiku 4.5 / Opus 4.x:  4096 tokens
  - Sonnet 4.6:            2048 tokens
  - Earlier models:        1024 tokens
If your system prompt is shorter than the model's minimum, the cache
silently no-ops and you pay base price. Check resp.usage.cache_read_input_tokens
to confirm a hit.
"""
from __future__ import annotations
import os
import pathlib
from typing import Any, Optional

from .usage_log import log_usage


DEFAULT_HAIKU_MODEL = "claude-haiku-4-5"
DEFAULT_SONNET_MODEL = "claude-sonnet-4-6"


def _wrap_system_with_cache(system: Any, ttl: str = "5m") -> Any:
    """Convert a string `system` arg to a list-of-blocks format with
    `cache_control` on the (last/only) block. If system is already a list
    of blocks, append cache_control to the LAST block (the canonical
    breakpoint position per Anthropic's docs).

    Returns the system arg unchanged if it's None or empty.

    `ttl` is "5m" (default, 1.25× cache write price) or "1h" (2× write
    price; use only when calls are spaced out > 5min apart).
    """
    if not system:
        return system

    cache_control = {"type": "ephemeral"}
    if ttl != "5m":
        cache_control["ttl"] = ttl

    if isinstance(system, str):
        return [{
            "type": "text",
            "text": system,
            "cache_control": cache_control,
        }]

    if isinstance(system, list):
        # Add cache_control to the LAST block (canonical breakpoint)
        if not system:
            return system
        # Don't mutate caller's list; return a shallow copy with the last
        # block annotated
        out = list(system)
        last = dict(out[-1]) if isinstance(out[-1], dict) else out[-1]
        if isinstance(last, dict):
            last["cache_control"] = cache_control
            out[-1] = last
        return out

    # Unknown shape — pass through; caller is on their own
    return system


class AnthropicClient:
    """Wrapped client. Construct once per agent run, call .messages_create()
    repeatedly.

    Args:
        usage_log_path: pathlib.Path where token usage gets recorded after
            each call. None = no logging. Cache hit/miss is logged in the
            extras dict.
        env_key: env var to read for the API key (default ANTHROPIC_API_KEY).
            Allows agents to use a per-agent key if the user wants budget
            isolation.
        cache_system: when True (default), auto-wrap the `system` arg of
            messages_create() with cache_control. Pass False to opt out
            for one-off calls.
        cache_ttl: "5m" (default) or "1h". 1h costs 2× base on the write
            but reads are still 0.1× — use when calls are spread > 5min
            apart (e.g. cron at every 7min).

    Properties:
        configured: True iff the API key is present in env.

    Methods:
        messages_create(...): same signature as anthropic.Anthropic().messages.create.
            Returns (response, error_str). On success, error_str is None and
            response is the SDK response object. On any failure (no key,
            network, rate limit), response is None and error_str is the
            human-readable explanation.
    """

    def __init__(
        self,
        *,
        usage_log_path: Optional[pathlib.Path] = None,
        env_key: str = "ANTHROPIC_API_KEY",
        cache_system: bool = True,
        cache_ttl: str = "5m",
    ):
        self.usage_log_path = usage_log_path
        self.env_key = env_key
        self.cache_system = cache_system
        self.cache_ttl = cache_ttl
        self._client: Any = None  # lazy

    @property
    def configured(self) -> bool:
        return bool(os.getenv(self.env_key))

    def _ensure_client(self) -> tuple[Any, Optional[str]]:
        if self._client is not None:
            return self._client, None
        if not self.configured:
            return None, f"missing env var {self.env_key}"
        try:
            from anthropic import Anthropic
        except ImportError:
            return None, "anthropic SDK not installed (pip install anthropic)"
        # Pass the key explicitly so non-default env vars work
        self._client = Anthropic(api_key=os.getenv(self.env_key))
        return self._client, None

    def messages_create(self, **kwargs) -> tuple[Any, Optional[str]]:
        """Wrapped messages.create call.

        Returns (response, error_str):
            success → (anthropic.types.Message, None)
            failure → (None, "<reason>")

        Auto-logs token usage (including cache hit/miss) to
        self.usage_log_path on success.

        If `cache_system=True` (default) and `system` is a string, it
        gets auto-wrapped with cache_control. Pass `cache_system=False`
        as a kwarg here OR construct the client with cache_system=False
        to opt out.
        """
        client, err = self._ensure_client()
        if err is not None:
            return None, err

        # Per-call override; falls back to client-level default
        cache_system = kwargs.pop("cache_system", self.cache_system)
        cache_ttl = kwargs.pop("cache_ttl", self.cache_ttl)

        if cache_system and "system" in kwargs:
            kwargs["system"] = _wrap_system_with_cache(
                kwargs["system"], ttl=cache_ttl)

        try:
            resp = client.messages.create(**kwargs)
        except Exception as e:
            return None, f"anthropic call failed: {e}"

        # Best-effort token logging — include cache fields when present.
        # `isinstance(..., int)` gate is deliberate: tests pass MagicMock
        # response objects whose missing attrs return MagicMock (truthy
        # but not int), and we don't want those to leak into the log.
        if self.usage_log_path is not None:
            try:
                in_tok = getattr(resp.usage, "input_tokens", 0)
                out_tok = getattr(resp.usage, "output_tokens", 0)
                cache_read = getattr(resp.usage, "cache_read_input_tokens", 0)
                cache_write = getattr(resp.usage, "cache_creation_input_tokens", 0)
                extra: dict = {}
                # Emit cache fields only if they're real ints. Always emit
                # both together when one is set so consumers (cost-audit) can
                # distinguish "had cache" vs "no cache info reported".
                if isinstance(cache_read, int) and isinstance(cache_write, int):
                    if cache_read > 0 or cache_write > 0:
                        extra["cache_read_input_tokens"] = cache_read
                        extra["cache_creation_input_tokens"] = cache_write
                log_usage(
                    log_path=self.usage_log_path,
                    model=kwargs.get("model", "unknown"),
                    input_tokens=int(in_tok) if isinstance(in_tok, int) else 0,
                    output_tokens=int(out_tok) if isinstance(out_tok, int) else 0,
                    extra=extra or None,
                )
            except Exception:
                pass

        return resp, None

    def messages_create_json(
        self,
        *,
        schema: dict,
        model: str,
        max_tokens: int = 1024,
        system: Any = None,
        messages: list | None = None,
        **extra,
    ) -> tuple[Optional[dict], Optional[str]]:
        """Structured-output convenience wrapper.

        Calls the Anthropic API with the public-beta structured-outputs
        feature (header: anthropic-beta=structured-outputs-2025-11-13).
        The model is forced to emit JSON conforming to `schema`.

        Returns (parsed_dict, error_str):
            success → (parsed_dict, None)
            failure → (None, "<reason>")

        Failure paths covered by the same graceful-degrade contract as
        messages_create:
            - missing API key
            - network / rate-limit / SDK error
            - JSON parse failure (shouldn't happen with structured outputs
              but we belt-and-brace because the feature is beta)
            - unexpected response shape

        Caller code that previously did:
            resp, err = client.messages_create(...)
            text = AnthropicClient.extract_text(resp)
            try:
                data = json.loads(text)
            except Exception:
                # template fallback
                ...

        becomes:
            data, err = client.messages_create_json(schema=..., ...)
            if err:
                # template fallback
                ...

        `schema` is a JSON Schema dict. Examples:
            {"type": "object",
             "properties": {"name": {"type": "string"}},
             "required": ["name"]}

        `extra` is forwarded to messages_create — use for `extra_headers`,
        `cache_system`, `cache_ttl`, etc.
        """
        # Beta header for structured outputs (Anthropic public beta as of
        # 2025-11-13). Agents that already pass extra_headers get merged.
        extra_headers = dict(extra.pop("extra_headers", {}) or {})
        extra_headers.setdefault(
            "anthropic-beta", "structured-outputs-2025-11-13")

        # The structured output param shape per Anthropic docs:
        # output_config = {"format": {"type": "json_schema", "schema": {...}}}
        output_config = {
            "format": {"type": "json_schema", "schema": schema},
        }

        resp, err = self.messages_create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages or [],
            extra_headers=extra_headers,
            output_config=output_config,
            **extra,
        )
        if err is not None:
            return None, err

        # Even with structured outputs, the response is in the standard
        # text-content shape — just guaranteed parseable.
        import json
        text = self.extract_text(resp)
        if not text:
            return None, "empty response from API"
        try:
            return json.loads(text), None
        except json.JSONDecodeError as e:
            # Beta feature occasionally hiccups; fall back gracefully.
            return None, f"structured output parse failed: {e}"

    @staticmethod
    def extract_text(resp: Any) -> str:
        """Concatenate text blocks from a response. Returns "" if resp is
        None or has no text content."""
        if resp is None:
            return ""
        try:
            return "".join(b.text for b in resp.content if b.type == "text").strip()
        except Exception:
            return ""
