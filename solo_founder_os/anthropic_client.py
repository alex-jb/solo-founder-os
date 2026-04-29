"""Wrapped Anthropic client with auto-log + graceful degrade.

Pattern: every agent that calls Claude does the same 5 things —
1. Check ANTHROPIC_API_KEY exists, return safely if not
2. Construct anthropic.Anthropic() (lazy import so no-key path doesn't pay)
3. Call messages.create()
4. Catch any exception and return graceful fallback
5. Log token usage to ~/.<agent>/usage.jsonl

Centralizing here means one fix benefits all current AND future agents.

The wrapped client intentionally returns the raw response object, NOT a
parsed string. Each agent's prompt format differs (JSON / VERDICT lines /
free text), so they each parse from `response.content`. We only handle
the boilerplate around the call.
"""
from __future__ import annotations
import os
import pathlib
from typing import Any, Optional

from .usage_log import log_usage


DEFAULT_HAIKU_MODEL = "claude-haiku-4-5"
DEFAULT_SONNET_MODEL = "claude-sonnet-4-6"


class AnthropicClient:
    """Wrapped client. Construct once per agent run, call .messages_create()
    repeatedly.

    Args:
        usage_log_path: pathlib.Path where token usage gets recorded after
            each call. None = no logging.
        env_key: env var to read for the API key (default ANTHROPIC_API_KEY).
            Allows agents to use a per-agent key if the user wants budget
            isolation.

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
    ):
        self.usage_log_path = usage_log_path
        self.env_key = env_key
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

        Auto-logs token usage to self.usage_log_path on success.
        """
        client, err = self._ensure_client()
        if err is not None:
            return None, err

        try:
            resp = client.messages.create(**kwargs)
        except Exception as e:
            return None, f"anthropic call failed: {e}"

        # Best-effort token logging
        if self.usage_log_path is not None:
            try:
                in_tok = getattr(resp.usage, "input_tokens", 0)
                out_tok = getattr(resp.usage, "output_tokens", 0)
                log_usage(
                    log_path=self.usage_log_path,
                    model=kwargs.get("model", "unknown"),
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                )
            except Exception:
                pass

        return resp, None

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
