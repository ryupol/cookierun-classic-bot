"""Telegram Bot API transport (stdlib only, mirrors alerts.py's style).
Pure I/O — no bot state, no message wording (that's views/messages.py)."""

import json
import socket
import urllib.error
import urllib.parse
import urllib.request

from config import get_settings

API_BASE = f"https://api.telegram.org/bot{get_settings().telegram_bot_token}"
POLL_TIMEOUT = 30  # seconds, Telegram long-poll


def _api_url(method):
    return f"{API_BASE}/{method}"


def _api_post(method, body, timeout=10):
    payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        _api_url(method),
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (socket.gaierror, urllib.error.URLError) as e:
        print(f"⚠️ Telegram {method} failed — connection issue: {e}")
    except Exception as e:
        print(f"⚠️ Telegram {method} failed: {e}")
    return None


def send_message(chat_id, text, timeout=10, parse_mode=None, reply_markup=None):
    body = {"chat_id": chat_id, "text": text}
    if parse_mode:
        body["parse_mode"] = parse_mode
    if reply_markup is not None:
        body["reply_markup"] = reply_markup
    return _api_post("sendMessage", body, timeout=timeout)


def edit_message_reply_markup(chat_id, message_id, reply_markup, timeout=10):
    body = {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup}
    return _api_post("editMessageReplyMarkup", body, timeout=timeout)


def answer_callback_query(callback_query_id, timeout=10):
    return _api_post("answerCallbackQuery", {"callback_query_id": callback_query_id}, timeout=timeout)


def _message_id_from_result(result):
    if not isinstance(result, dict):
        return None
    message = result.get("result")
    if not isinstance(message, dict):
        return None
    return message.get("message_id")


def send_photo(chat_id, image_bytes, filename="screenshot.png", caption=None, timeout=20):
    boundary = "----CookieRunControllerBoundary"

    def field(name, value):
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode("utf-8")

    parts = [field("chat_id", chat_id)]
    if caption:
        parts.append(field("caption", caption))
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="photo"; filename="{filename}"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(image_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    body = b"".join(parts)

    request = urllib.request.Request(
        _api_url("sendPhoto"),
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=timeout)
    except (socket.gaierror, urllib.error.URLError) as e:
        print(f"⚠️ Couldn't send Telegram photo — connection issue: {e}")
    except Exception as e:
        print(f"⚠️ Failed to send Telegram photo: {e}")


def get_updates(offset, timeout=POLL_TIMEOUT):
    """Long-poll Telegram for updates. Returns a list of updates, or None on
    failure (network drop, sleep/wake, Telegram hiccup, etc.) so the caller
    can back off instead of hammering the API in a tight loop."""
    params = urllib.parse.urlencode({"offset": offset, "timeout": timeout})
    url = f"{_api_url('getUpdates')}?{params}"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout + 10) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data.get("result", [])
    except (socket.gaierror, urllib.error.URLError) as e:
        print(f"⚠️ Telegram poll failed (network): {e}")
        return None
    except Exception as e:
        print(f"⚠️ Telegram poll failed: {e}")
        return None
