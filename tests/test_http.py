"""Tests for http helpers."""
from __future__ import annotations
import os
import sys
import urllib.error
from unittest.mock import MagicMock, patch


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from solo_founder_os.http import urlopen_json, with_retry


def _ok(payload: dict):
    import json
    fake = MagicMock()
    fake.read.return_value = json.dumps(payload).encode()
    fake.__enter__ = lambda s: s
    fake.__exit__ = lambda *a: None
    return fake


# ─── urlopen_json ──────────────────────────────────────────

def test_urlopen_json_get_default():
    with patch("urllib.request.urlopen", return_value=_ok({"k": "v"})):
        r = urlopen_json("https://example.com/x")
    assert r == {"k": "v"}


def test_urlopen_json_post_with_data():
    captured: dict = {}
    def capture(req, **kw):
        captured["url"] = req.full_url
        captured["data"] = req.data
        captured["method"] = req.get_method()
        return _ok({"ok": True})
    with patch("urllib.request.urlopen", side_effect=capture):
        urlopen_json("https://x", data=b'{"a":1}', method="POST",
                     headers={"X": "Y", "Content-Type": "application/json"})
    assert captured["method"] == "POST"
    assert captured["data"] == b'{"a":1}'


def test_urlopen_json_propagates_http_error():
    err = urllib.error.HTTPError("x", 401, "auth", None, None)
    with patch("urllib.request.urlopen", side_effect=err):
        try:
            urlopen_json("https://x")
        except urllib.error.HTTPError as e:
            assert e.code == 401
            return
    raise AssertionError("expected HTTPError")


# ─── with_retry ────────────────────────────────────────────

def test_with_retry_succeeds_on_first_try():
    calls = [0]

    @with_retry(times=3, backoff_seconds=0)
    def f():
        calls[0] += 1
        return "ok"

    assert f() == "ok"
    assert calls[0] == 1


def test_with_retry_retries_then_succeeds():
    calls = [0]

    @with_retry(times=3, backoff_seconds=0)
    def f():
        calls[0] += 1
        if calls[0] < 3:
            raise ConnectionError("flaky")
        return "ok"

    assert f() == "ok"
    assert calls[0] == 3


def test_with_retry_exhausts_and_raises():
    calls = [0]

    @with_retry(times=3, backoff_seconds=0)
    def f():
        calls[0] += 1
        raise ConnectionError("dead")

    try:
        f()
    except ConnectionError as e:
        assert calls[0] == 3
        assert str(e) == "dead"
        return
    raise AssertionError("expected ConnectionError")


def test_with_retry_does_not_retry_unrelated_exceptions():
    """ValueError is not in default retry_on tuple — should raise immediately."""
    calls = [0]

    @with_retry(times=3, backoff_seconds=0)
    def f():
        calls[0] += 1
        raise ValueError("not retryable")

    try:
        f()
    except ValueError:
        assert calls[0] == 1
        return
    raise AssertionError("expected ValueError")


def test_with_retry_custom_retry_on():
    """Caller can opt into retrying ValueError if they really want."""
    calls = [0]

    @with_retry(times=3, backoff_seconds=0, retry_on=(ValueError,))
    def f():
        calls[0] += 1
        if calls[0] < 2:
            raise ValueError("flaky parse")
        return "ok"

    assert f() == "ok"
    assert calls[0] == 2


def test_with_retry_preserves_function_name():
    @with_retry()
    def my_fn():
        pass
    assert my_fn.__name__ == "my_fn"
    # __wrapped__ also exposed for introspection
    assert hasattr(my_fn, "__wrapped__")
