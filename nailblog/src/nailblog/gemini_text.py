"""Gemini text provider (generateContent, JSON mode).

A separate module on purpose: `ai_transport.py` is a byte-for-byte copy of nailhub's and must stay that way. This
file only reuses its pieces (`ProviderError`, `resolve`, `PinnedHTTPS`, `LIMIT`) and adds what Gemini needs: the key in
the `x-goog-api-key` header (never in the URL), `responseMimeType=application/json`, an optional low thinking level,
usage read from `usageMetadata`, and distinct error codes for HTTP 429 (`rate_limited`) and 503
(`provider_unavailable`) with ONE slow retry (5-10 seconds) before the error is raised; the caller's own backoff
(TD-5) takes over from there.

Prompts, source text, model output and the key are never logged.
"""

import http.client
import ipaddress
import json
import random
import re
import socket
import threading
import time
from urllib.parse import urlsplit

from .ai_transport import LIMIT, TEXT_INPUT_LIMIT, PinnedHTTPS, ProviderError, resolve

GEMINI_HOST = "generativelanguage.googleapis.com"
GEMINI_BASE = "https://" + GEMINI_HOST
KEY_SCOPE = "gemini|" + GEMINI_BASE
MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
RETRY_CODES = ("rate_limited", "provider_unavailable")
RETRY_SECONDS = (5.0, 10.0)
STATUS_CODES = {400: "bad_request", 401: "auth_failed", 403: "auth_failed", 404: "model_not_found",
                429: "rate_limited", 503: "provider_unavailable"}
# maxOutputTokens counts the thinking tokens too, so the visible-output cap gets headroom for them.
THINKING_HEADROOM = {"minimal": 1024, "low": 2048, "medium": 4096}
DEFAULT_HEADROOM = 16384
MAX_OUTPUT_CAP = 32768


def endpoint(model):
    if not isinstance(model, str) or not MODEL_NAME.fullmatch(model):
        raise ProviderError("invalid_model")
    return f"{GEMINI_BASE}/v1beta/models/{model}:generateContent"


def _check_url(url):
    try:
        parts = urlsplit(url)
        port = parts.port
        if (parts.scheme != "https" or parts.hostname != GEMINI_HOST or port not in (None, 443) or parts.username
                or parts.password or parts.query or parts.fragment or not parts.path.startswith("/v1beta/models/")
                or not parts.path.endswith(":generateContent") or any(ord(c) < 33 or ord(c) == 127 for c in url)):
            raise ValueError()
        return parts
    except ValueError:
        raise ProviderError("invalid_endpoint") from None


def request_gemini(url, payload, headers, timeout, provider="gemini"):
    """POST `payload` as JSON and return the decoded envelope. Same signature as ai_transport.request_json."""
    parts = _check_url(url)
    connection = response = watchdog = None
    expired = threading.Event()
    try:
        addresses = [item[4][0] for item in resolve(parts.hostname)]
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ProviderError("invalid_endpoint")
        connection = PinnedHTTPS(parts.hostname, addresses[0], timeout)
        connection.connect()
        upstream = connection.sock

        def expire():
            expired.set()
            try:
                upstream.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        watchdog = threading.Timer(timeout, expire)
        watchdog.daemon = True
        watchdog.start()
        connection.request("POST", parts.path, json.dumps(payload).encode(), headers)
        response = connection.getresponse()
        if response.status != 200:
            raise ProviderError(STATUS_CODES.get(response.status, "provider_failed"))
        deadline = time.monotonic() + timeout
        chunks, size = [], 0
        while size <= LIMIT and not response.isclosed():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderError("timeout")
            response.fp.raw._sock.settimeout(remaining)
            chunk = response.read1(min(65536, LIMIT + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        raw = b"".join(chunks)
        if expired.is_set():
            raise ProviderError("timeout")
        if len(raw) > LIMIT:
            raise ProviderError("response_too_large")
        return json.loads(raw)
    except ProviderError:
        raise
    except (TimeoutError, socket.timeout):
        raise ProviderError("timeout") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ProviderError("timeout" if expired.is_set() else "invalid_json") from None
    except (http.client.HTTPException, OSError, ValueError, AttributeError):
        raise ProviderError("timeout" if expired.is_set() else "provider_failed") from None
    finally:
        if watchdog:
            watchdog.cancel()
        if response:
            response.close()
        if connection:
            connection.close()


def _count(value):
    return value if type(value) is int and value >= 0 else 0


def usage_of(envelope):
    """Token counts from `usageMetadata`; counts only. Thinking tokens are billed as output."""
    zero = {"prompt_tokens": 0, "completion_tokens": 0, "cache_hit_tokens": 0, "thought_tokens": 0}
    try:
        meta = envelope.get("usageMetadata")
        if not isinstance(meta, dict):
            return zero
        thoughts = _count(meta.get("thoughtsTokenCount"))
        return {"prompt_tokens": _count(meta.get("promptTokenCount")),
                "completion_tokens": _count(meta.get("candidatesTokenCount")) + thoughts,
                "cache_hit_tokens": 0, "thought_tokens": thoughts}
    except Exception:
        return zero


def build_body(messages, schema, *, max_output, thinking=None):
    system = [m["content"] for m in messages if m["role"] == "system"]
    system.append("Return only one JSON object that follows this JSON Schema. The object is the answer itself: never "
                  "repeat or describe the schema. Schema: " + json.dumps(schema, ensure_ascii=False))
    contents = []
    for message in messages:
        if message["role"] == "system":
            continue
        role = "model" if message["role"] == "assistant" else "user"
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].append({"text": message["content"]})
        else:
            contents.append({"role": role, "parts": [{"text": message["content"]}]})
    headroom = THINKING_HEADROOM.get(thinking, DEFAULT_HEADROOM)
    config = {"responseMimeType": "application/json", "maxOutputTokens": min(MAX_OUTPUT_CAP, int(max_output) + headroom)}
    if thinking:
        config["thinkingConfig"] = {"thinkingLevel": thinking}
    return {"systemInstruction": {"parts": [{"text": "\n".join(system)}]}, "contents": contents,
            "generationConfig": config}


_STRAY_TAIL = re.compile(r"[\s}\]`]*")
_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _loads(text):
    """The JSON object in `text`. JSON mode without a response schema sometimes ends the object with a stray `}` or wraps
    it in a code fence; both are tolerated, a second value or any other text after the object is not."""
    fenced = _FENCE.match(text)
    text = (fenced.group(1) if fenced else text).strip()
    value, end = json.JSONDecoder().raw_decode(text)
    if not _STRAY_TAIL.fullmatch(text[end:]):
        raise ValueError("data after the JSON object")
    return value


def _text_of(envelope):
    parts = envelope["candidates"][0]["content"]["parts"]
    return "".join(part.get("text", "") for part in parts if isinstance(part, dict) and not part.get("thought"))


def retry_delay():
    return random.uniform(*RETRY_SECONDS)


def complete(model, messages, schema, *, key, timeout=60, max_output=2048, thinking=None, transport=request_gemini,
             usage_sink=None, sleep=None, delay=None):
    """One JSON object from Gemini. A 429/503 is retried once after 5-10 seconds, then raised with its code.

    `sleep` and `delay` are injection points for tests (default: time.sleep and a random 5-10 s)."""
    url = endpoint(model)
    if not key or any(ord(c) < 32 for c in key):
        raise ProviderError("key_required")
    if len(json.dumps(messages).encode()) > TEXT_INPUT_LIMIT:
        raise ProviderError("input_too_large")
    headers = {"Content-Type": "application/json", "x-goog-api-key": key}
    body = build_body(messages, schema, max_output=max_output, thinking=thinking)
    seconds = min(max(float(timeout), 1), 1800)
    for attempt in (1, 2):
        try:
            envelope = transport(url, body, headers, seconds, "gemini")
            break
        except ProviderError as error:
            if error.code not in RETRY_CODES or attempt == 2:
                raise
            (sleep or time.sleep)((delay or retry_delay)())
    if usage_sink is not None:
        try:
            usage_sink(usage_of(envelope))
        except Exception:
            pass
    try:
        result = _loads(_text_of(envelope))
    except Exception:
        raise ProviderError("invalid_json") from None
    if not isinstance(result, dict):
        raise ProviderError("invalid_json")
    return result
