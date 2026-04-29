"""Push notifier adapters — fan out alerts to ntfy.sh / Telegram / Slack.

Lifted unchanged from funnel-analytics-agent v0.5.1 (where the design was
proven). Same graceful-degrade contract: missing creds → not configured,
network failure → False return, never raises.
"""
from __future__ import annotations
import json
import os
import urllib.request
import urllib.error


MAX_MESSAGE_CHARS = 3500


class Notifier:
    name: str = "base"

    @property
    def configured(self) -> bool:
        return False

    def send(self, message: str, *, title: str = "",
             priority: str = "default") -> bool:
        raise NotImplementedError


class NtfyNotifier(Notifier):
    """ntfy.sh — free, no signup. Pick a topic, subscribe via app.

    Env: NTFY_TOPIC (required), NTFY_SERVER (default https://ntfy.sh)
    """
    name = "ntfy"
    PRIORITY_MAP = {"default": "3", "high": "4", "urgent": "5"}

    @property
    def configured(self) -> bool:
        return bool(os.getenv("NTFY_TOPIC"))

    def send(self, message: str, *, title: str = "",
             priority: str = "default") -> bool:
        if not self.configured:
            return False
        topic = os.getenv("NTFY_TOPIC", "")
        server = os.getenv("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
        url = f"{server}/{topic}"
        body = message[:MAX_MESSAGE_CHARS].encode("utf-8")
        headers = {
            "Title": title or "solo-founder-os",
            "Priority": self.PRIORITY_MAP.get(priority, "3"),
            "Tags": "rotating_light" if priority == "urgent" else "bell",
            "Markdown": "yes",
        }
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status < 400
        except Exception:
            return False


class TelegramNotifier(Notifier):
    """Telegram via Bot API. Plain text on purpose — Telegram's V1
    Markdown rejects `**bold**` (CommonMark), and escaping every special
    char for V2 isn't worth it.

    Env: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    """
    name = "telegram"

    @property
    def configured(self) -> bool:
        return bool(os.getenv("TELEGRAM_BOT_TOKEN")) \
           and bool(os.getenv("TELEGRAM_CHAT_ID"))

    def send(self, message: str, *, title: str = "",
             priority: str = "default") -> bool:
        if not self.configured:
            return False
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        body = message[:MAX_MESSAGE_CHARS]
        if title:
            body = f"{title}\n\n{body}"
        payload = json.dumps({
            "chat_id": chat_id,
            "text": body,
            "disable_web_page_preview": True,
        }).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status < 400
        except Exception:
            return False


class SlackNotifier(Notifier):
    """Slack via incoming webhook. URL at api.slack.com/apps → your app
    → Incoming Webhooks → Add to workspace.

    Env: SLACK_WEBHOOK_URL
    """
    name = "slack"

    @property
    def configured(self) -> bool:
        return bool(os.getenv("SLACK_WEBHOOK_URL"))

    def send(self, message: str, *, title: str = "",
             priority: str = "default") -> bool:
        if not self.configured:
            return False
        webhook = os.getenv("SLACK_WEBHOOK_URL", "")
        body = message[:MAX_MESSAGE_CHARS]
        text = f"*{title}*\n\n{body}" if title else body
        payload = json.dumps({"text": text, "mrkdwn": True}).encode()
        req = urllib.request.Request(
            webhook, data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status < 400
        except Exception:
            return False


ALL_NOTIFIERS: dict[str, type[Notifier]] = {
    "ntfy": NtfyNotifier,
    "telegram": TelegramNotifier,
    "slack": SlackNotifier,
}


def fan_out(notifier_names: list[str], message: str, *,
            title: str = "", priority: str = "default") -> dict[str, bool]:
    """Send to all named notifiers. Returns {name: success_bool}."""
    results: dict[str, bool] = {}
    for name in notifier_names:
        cls = ALL_NOTIFIERS.get(name)
        if cls is None:
            results[name] = False
            continue
        n = cls()
        if not n.configured:
            results[name] = False
            continue
        try:
            results[name] = n.send(message, title=title, priority=priority)
        except Exception:
            results[name] = False
    return results
