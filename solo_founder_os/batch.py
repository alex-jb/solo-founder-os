"""Anthropic Message Batches API — 50% off async processing.

Use cases in the Solo Founder OS stack:
- bilingual-content-sync-agent: full-catalog refresh (925 keys at once)
- customer-discovery-agent: weekly digest (cluster N posts at once)
- cost-audit-agent: monthly run with per-provider analysis (when LLM-summarized)
- any agent doing periodic batch work that doesn't need realtime

Key facts (as of 2026-04):
- 50% pricing discount vs realtime
- Most batches finish < 1 hour (no SLA, but typical)
- Up to 100,000 requests per batch
- Up to 256 MB total batch size
- Async polling — no streaming responses
- 29-day retention on results
- NOT eligible for Zero Data Retention (ZDR)

Usage:
    from solo_founder_os import AnthropicClient, batch_submit, batch_results

    client = AnthropicClient()
    requests = [
        batch_request(custom_id=f"key-{i}",
                       model="claude-haiku-4-5",
                       max_tokens=200,
                       messages=[{"role": "user", "content": f"Translate {x}"}])
        for i, x in enumerate(items)
    ]
    batch_id, err = batch_submit(client, requests)
    # ... poll ... when status is "ended":
    results, err = batch_results(client, batch_id)
    # results is a dict {custom_id: response_or_error}
"""
from __future__ import annotations
import json
import time
from typing import Any, Optional


def batch_request(
    *,
    custom_id: str,
    model: str,
    max_tokens: int,
    messages: list[dict],
    system: Any = None,
    **extra,
) -> dict:
    """Build one request entry for a batch submission.

    `custom_id` is your unique key for the request — you'll match
    results back to inputs via this. Must be unique within a batch and
    contain only ASCII alphanumeric + dash + underscore.

    `extra` forwards to the underlying messages.create params (tools,
    output_config, etc.).
    """
    params = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if system is not None:
        params["system"] = system
    params.update(extra)
    return {"custom_id": custom_id, "params": params}


def batch_submit(
    client,
    requests: list[dict],
) -> tuple[Optional[str], Optional[str]]:
    """Submit a batch and return its id.

    Returns (batch_id, error_str). On success, error_str is None and
    batch_id is the Anthropic-assigned id you'll poll with batch_status
    and batch_results.

    `client` is an AnthropicClient instance (for credentials + lazy SDK
    construction). The actual SDK call goes through client._ensure_client.
    """
    if not requests:
        return None, "empty requests list"

    sdk, err = client._ensure_client()
    if err is not None:
        return None, err

    try:
        # The SDK exposes Message Batches as client.messages.batches.create.
        # `requests` is the array shape Anthropic expects.
        batch = sdk.messages.batches.create(requests=requests)
        return getattr(batch, "id", None), None
    except Exception as e:
        return None, f"batch create failed: {e}"


def batch_status(
    client,
    batch_id: str,
) -> tuple[Optional[dict], Optional[str]]:
    """Return (status_dict, error). The status_dict mirrors what the API
    returns — interesting fields are `processing_status` (in_progress / ended)
    and `request_counts` (succeeded / errored / canceled / expired)."""
    sdk, err = client._ensure_client()
    if err is not None:
        return None, err
    try:
        batch = sdk.messages.batches.retrieve(batch_id)
        return {
            "id": getattr(batch, "id", batch_id),
            "processing_status": getattr(batch, "processing_status", None),
            "request_counts": getattr(batch, "request_counts", None),
            "created_at": getattr(batch, "created_at", None),
            "ended_at": getattr(batch, "ended_at", None),
            "expires_at": getattr(batch, "expires_at", None),
        }, None
    except Exception as e:
        return None, f"batch retrieve failed: {e}"


def batch_results(
    client,
    batch_id: str,
) -> tuple[Optional[dict], Optional[str]]:
    """Stream the batch's results. Returns ({custom_id: response_or_err}, error).

    Each value in the returned dict is either:
      - a parsed Message-shaped dict (success) with `content`, `usage`, etc.
      - an error dict {"error_type": ..., "error_message": ...} for failures

    Caller is responsible for matching custom_ids back to original inputs.
    """
    sdk, err = client._ensure_client()
    if err is not None:
        return None, err

    try:
        # The SDK's results() method returns a streaming JSONL iterator
        out: dict = {}
        for entry in sdk.messages.batches.results(batch_id):
            cid = getattr(entry, "custom_id", None)
            if cid is None:
                continue
            result = getattr(entry, "result", None)
            if result is None:
                out[cid] = {"error_type": "missing_result",
                            "error_message": "no result in entry"}
                continue
            r_type = getattr(result, "type", None)
            if r_type == "succeeded":
                msg = getattr(result, "message", None)
                if msg is not None:
                    # Normalize to dict shape for downstream parsing
                    out[cid] = {
                        "content": [
                            {"type": getattr(b, "type", "text"),
                             "text": getattr(b, "text", "")}
                            for b in (getattr(msg, "content", []) or [])
                        ],
                        "usage": _usage_dict(getattr(msg, "usage", None)),
                        "stop_reason": getattr(msg, "stop_reason", None),
                    }
                else:
                    out[cid] = {"error_type": "no_message",
                                "error_message": "succeeded but no message"}
            else:
                # errored / canceled / expired
                err_info = getattr(result, "error", None)
                out[cid] = {
                    "error_type": r_type or "unknown",
                    "error_message": (
                        getattr(err_info, "message", None)
                        or getattr(err_info, "type", None)
                        or "unknown"
                    ),
                }
        return out, None
    except Exception as e:
        return None, f"batch results fetch failed: {e}"


def _usage_dict(usage: Any) -> dict:
    if usage is None:
        return {}
    return {
        "input_tokens": getattr(usage, "input_tokens", 0),
        "output_tokens": getattr(usage, "output_tokens", 0),
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0),
        "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0),
    }


def batch_wait(
    client,
    batch_id: str,
    *,
    poll_interval_s: float = 30.0,
    timeout_s: float = 3600.0,
    sleep_fn=time.sleep,
) -> tuple[Optional[dict], Optional[str]]:
    """Poll until the batch ends or the timeout fires. Returns the same
    shape as batch_results once the batch is done.

    `sleep_fn` is injectable for tests. `poll_interval_s` defaults to 30s
    (Anthropic recommends polling sparingly).
    """
    elapsed = 0.0
    while elapsed < timeout_s:
        status, err = batch_status(client, batch_id)
        if err is not None:
            return None, err
        proc = status.get("processing_status") if status else None
        if proc == "ended":
            return batch_results(client, batch_id)
        if proc is None:
            return None, "batch status response missing processing_status"
        sleep_fn(poll_interval_s)
        elapsed += poll_interval_s
    return None, f"batch did not end within {timeout_s}s"
