"""Smoke tests for splunk_obs.hec_client — no live Splunk needed.

We mock urllib.request.urlopen so these run in <1s and don't touch the
network. The goal is: verify the protocol shape (one JSON-per-line body,
Splunk auth header) and the no-op-when-unconfigured behavior.
"""
from __future__ import annotations
import io
import json
from unittest.mock import patch, MagicMock

from solo_founder_os.splunk_obs.hec_client import (
    HECClient,
    HECError,
    verify_webhook_signature,
)


def _ok_response(payload: dict | None = None) -> MagicMock:
    body = json.dumps(payload or {"text": "Success", "code": 0}).encode()
    resp = MagicMock()
    resp.__enter__ = MagicMock(return_value=resp)
    resp.__exit__ = MagicMock(return_value=False)
    resp.read = MagicMock(return_value=body)
    return resp


def test_disabled_when_no_url_or_token():
    c = HECClient(url="", token="")
    assert c.enabled is False
    assert c.send_event({"k": "v"}, sourcetype="sfos:reflection") == 0


def test_send_event_posts_one_json_line():
    c = HECClient(url="https://splunk.example/", token="tok-abc")
    with patch("urllib.request.urlopen", return_value=_ok_response()) as up:
        n = c.send_event(
            {"agent": "sfos", "outcome": "OK"},
            sourcetype="sfos:reflection",
            event_time=1717000000.0,
        )
    assert n == 1
    req = up.call_args[0][0]
    assert req.full_url == "https://splunk.example/services/collector/event"
    assert req.headers["Authorization"] == "Splunk tok-abc"
    sent = json.loads(req.data.decode())
    assert sent["sourcetype"] == "sfos:reflection"
    assert sent["event"] == {"agent": "sfos", "outcome": "OK"}
    assert sent["time"] == 1717000000.0


def test_send_events_batch_uses_newline_separator():
    c = HECClient(url="https://splunk.example", token="tok-abc")
    events = [{"i": 1}, {"i": 2}, {"i": 3}]
    with patch("urllib.request.urlopen", return_value=_ok_response()) as up:
        n = c.send_events(events, sourcetype="sfos:eval")
    assert n == 3
    body = up.call_args[0][0].data.decode()
    # HEC wants concatenated JSON objects (one per line), NOT a JSON array.
    assert body.count("\n") == 2
    for line in body.split("\n"):
        assert json.loads(line)["sourcetype"] == "sfos:eval"


def test_hec_non_zero_code_raises():
    c = HECClient(url="https://splunk.example", token="tok-abc")
    bad = _ok_response({"text": "Invalid token", "code": 4})
    with patch("urllib.request.urlopen", return_value=bad):
        try:
            c.send_event({"x": 1}, sourcetype="sfos:reflection")
        except HECError as e:
            assert "code" in str(e) or "Invalid" in str(e)
        else:
            raise AssertionError("expected HECError on code != 0")


def test_webhook_signature_constant_time():
    secret = "shhh"
    body = b'{"alert":"eval_drift"}'
    import hashlib, hmac
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_webhook_signature(secret, sig, body) is True
    assert verify_webhook_signature(secret, "sha256=deadbeef", body) is False
    assert verify_webhook_signature("", sig, body) is False
