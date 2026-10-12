"""Render the fixed Vietnamese issue template and neutralize user content."""

import re
import unicodedata
import html
from dataclasses import dataclass

from .models import Classification, IncomingMessage, TICKET_TYPES


MAX_QUOTED_MESSAGES = 10
TOTAL_QUOTE_BUDGET = 4000  # characters of quoted text shared by all messages in one body
MIN_QUOTE_PER_MESSAGE = 100  # floor, so a large group still shows something of every message
MAX_CLARIFY = 5
MAX_CLARIFY_CHARS = 200
NEEDS_CLARITY_LINE = "Chưa đủ thông tin để phân loại, cần người đọc xem lại."
NO_TITLE_LINE = "(không có tiêu đề)"
NO_SUMMARY_LINE = "(không có tóm tắt)"

# The classifier adds " [kèm ảnh]" to the text it sends the model, and the model sometimes copies
# it into title/summary. Variants: "[kèm ảnh]", "(có kèm ảnh)", "[có kèm ảnh]", bare "kèm ảnh" /
# "có kèm ảnh", any case, any run of spaces. A bracketed form is removed together with its brackets.
_IMAGE_WORDS = r"(?:có\s+)?kèm\s+ảnh"
_IMAGE_MARKER = re.compile(
    rf"\[\s*{_IMAGE_WORDS}\s*\]|\(\s*{_IMAGE_WORDS}\s*\)|(?<!\w){_IMAGE_WORDS}(?!\w)",
    re.IGNORECASE)
_EDGE_SEPARATORS = " ,;:.-–—·|/\\"
_BRACKETS = {"(": ")", "[": "]", "{": "}"}
_CLOSERS = {close: open_ for open_, close in _BRACKETS.items()}


# Contact data never goes into an issue of a (possibly public) repo. Redaction runs on text that
# `sanitize` already escaped, so the patterns accept its output: "@" is followed by U+200B, and
# "_" / "\\" are backslash-escaped. SQLite keeps the original text (internal data).
EMAIL_PLACEHOLDER = "[email đã ẩn]"
PHONE_PLACEHOLDER = "[số đã ẩn]"
_ZW = "\u200b"
# One character of an address as `sanitize` leaves it: word chars, . + % -, a backslash (escape of
# "_") and the zero-width space.
_EMAIL_CHAR = r"[\w.+%\-\\" + _ZW + "]"
_DOMAIN_CHAR = r"[\w\-\\" + _ZW + "]"
_EMAIL = re.compile(
    "(?<!" + _EMAIL_CHAR + ")" + _EMAIL_CHAR + r"*\w@" + _ZW + "?" + _DOMAIN_CHAR + "+"
    + r"(?:\." + _DOMAIN_CHAR + r"+)*\.[A-Za-z]{2,}")
# 9-11 digits, optionally led by "+" and grouped with single spaces or dashes: 0901234567,
# +84 901 234 567, 090-123-4567; also 090.123.4567. Longer digit runs (ids, card numbers) are left alone.
_PHONE = re.compile(
    r"(?<![\d+])(?:\+?\d(?:[ \-]?\d){8,10}|\d{3,4}\.\d{3}\.\d{3,4})(?!\d)(?!:\d)")
_DATE_LIKE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}-\d{1,2}-\d{2,4}")


def _phone(match) -> str:
    return match[0] if _DATE_LIKE.match(match[0].lstrip("+")) else PHONE_PLACEHOLDER


def redact(value: str) -> str:
    """Replace e-mail addresses and phone numbers in already-sanitized text."""
    value = _EMAIL.sub(EMAIL_PLACEHOLDER, value)
    return _PHONE.sub(_phone, value)


@dataclass(frozen=True)
class Issue:
    title: str
    body: str
    labels: tuple[str, ...]


def sanitize(value: str, *, inline=False) -> str:
    # Keep line boundaries only inside the original-message blockquote. Inline
    # fields collapse whitespace before Cc removal so tabs cannot join words.
    if inline:
        value = " ".join(value.split())
    value = "".join(
        ch for ch in value
        if ((ch == "\n" and not inline) or unicodedata.category(ch) != "Cc")
        and not ("‪" <= ch <= "‮" or "⁦" <= ch <= "⁩"))
    value = html.escape(value, quote=False)
    value = value.replace("@", "@​")
    value = re.sub(r"#(?=\d)", "#​", value)
    value = re.sub(r"(?i)(gh-)(?=\d)", r"\1" + "​", value)
    value = re.sub(r"(?i)github\.com/", "github​.com/", value)
    value = value.replace("://", ":​//")
    value = re.sub(r"(?i)(www)(?=\.)", r"\1" + "​", value)
    # Escape Markdown in the original quote too: quoting alone permits images
    # and clickable links. The code-generated Telegram link bypasses sanitizing.
    value = re.sub(r"([\\`*_\[\]()!|])", r"\\\1", value)
    return value


def telegram_link(message: IncomingMessage) -> str | None:
    if message.chat_username and re.fullmatch(r"[A-Za-z0-9_]+", message.chat_username):
        return f"https://t.me/{message.chat_username}/{message.message_id}"
    chat = str(message.chat_id)
    if chat.startswith("-100") and chat[4:].isdigit() and chat[4:]:
        return f"https://t.me/c/{chat[4:]}/{message.message_id}"
    return None


def _quote(text: str, limit: int) -> str:
    clean = redact(sanitize(text))   # before the cut, so a cut cannot leave half an address
    quoted = "\n".join("> " + line for line in clean[:limit].split("\n"))
    if len(clean) > limit:
        quoted += "\n> (đã cắt)"
    return quoted


def _trim_edges(value: str) -> str:
    """Drop separators and unbalanced brackets left hanging at either end."""
    value = value.strip()
    while value:
        before = value
        value = value.strip(_EDGE_SEPARATORS)
        if value and value[-1] in _BRACKETS:
            value = value[:-1]
        elif value and value[0] in _CLOSERS:
            value = value[1:]
        elif value and value[-1] in _CLOSERS and value.count(value[-1]) > value.count(_CLOSERS[value[-1]]):
            value = value[:-1]
        elif value and value[0] in _BRACKETS and value.count(value[0]) > value.count(_BRACKETS[value[0]]):
            value = value[1:]
        if value == before:
            break
    return value


def strip_image_marker(value) -> str:
    """Remove the system's image marker from a model-written title/summary (raw, pre-sanitize).

    Text without the marker is returned unchanged; only when something was removed are the
    whitespace and the edge punctuation tidied, so ordinary titles are never rewritten.
    """
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFC", value)
    cleaned, removed = _IMAGE_MARKER.subn(" ", value)
    if not removed:
        return value
    return _trim_edges(" ".join(cleaned.split()))


def _first_message_line(messages) -> str:
    """First non-empty original-message text (marker removed), for a title the model left empty."""
    for item in messages:
        text = _clean_line(strip_image_marker(item.text))
        if text:
            return text
    return ""


def _clean_line(value) -> str:
    return redact(sanitize(value, inline=True)) if isinstance(value, str) else ""


def format_issue(message: IncomingMessage, classification: Classification | None,
                 tier: str, needs_human: bool, model: str = "qwen3.5:9b", *,
                 case_messages=None, key_message_ids=None, images=(), notes=(),
                 clarify=None, text=None, max_quoted=None) -> Issue:
    """Render one issue. `message` is the case's (or topic group's) first message.

    All `case_messages` are quoted in time order; `key_message_ids` (Assistant
    definition 12.2) only marks the messages the model called key. `images` ((alt, url) pairs) and `notes` are written by code
    (12.8), never taken from the model, so they do not pass through `sanitize`.

    Topic groups (12.9): `clarify` (None = single-case rendering) is the group's missing_info and
    adds the "Cần làm rõ" section; `text` is the model's (title, summary) for a needs-human group;
    `max_quoted` raises the quoted-message cap so a whole group is rendered. A needs-human issue
    keeps its type label only when the type is trusted (explicit command, or a confident
    ticket that still lacks information): then `classification` is passed and is valid.
    """
    if not needs_human and (classification is None or not classification.is_valid()
                            or classification.type not in TICKET_TYPES):
        raise ValueError("An issue requires a valid non-noise classification")
    fallback_messages = sorted(case_messages or [message],
                               key=lambda item: (item.received_at, item.message_id))
    fallback_messages = [message] + [item for item in fallback_messages if item is not message]
    if needs_human:
        typed = (clarify is not None and classification is not None and classification.is_valid()
                 and classification.type in TICKET_TYPES)
        given_title = _clean_line(strip_image_marker(text[0])) if text else ""
        given_summary = _clean_line(strip_image_marker(text[1])) if text else ""
        prefix = f"[{classification.type}] " if typed else "[cần phân loại] "
        title = prefix + (given_title or _first_message_line(fallback_messages) or NO_TITLE_LINE)
        summary = given_summary or "(agent không chắc chắn, cần người đọc)"
        labels = ((classification.type, "triage", "needs-human", "source:telegram") if typed
                  else ("needs-human", "source:telegram"))
    else:
        title = f"[{classification.type}] " + (
            _clean_line(strip_image_marker(classification.title))
            or _first_message_line(fallback_messages) or NO_TITLE_LINE)
        summary = _clean_line(strip_image_marker(classification.summary)) or NO_SUMMARY_LINE
        labels = (classification.type, "triage", "source:telegram")
    title = title.replace("\n", " ")[:80]
    case_messages = sorted(case_messages or [message],
                           key=lambda item: (item.received_at, item.message_id))
    # Every message of the case is rendered; the model's key ids only add a marker.
    # Ids that are not in this case are ignored, so they can never mark or add anything.
    present = {item.message_id for item in case_messages}
    key_ids = {value for value in (key_message_ids or ())
               if type(value) is int and value in present}
    shown = case_messages[:MAX_QUOTED_MESSAGES if max_quoted is None else max(1, max_quoted)]
    hidden = len(case_messages) - len(shown)
    limit = TOTAL_QUOTE_BUDGET // len(shown)
    if max_quoted is not None:
        limit = max(limit, MIN_QUOTE_PER_MESSAGE)
    blocks = []
    for index, item in enumerate(shown, 1):
        link = telegram_link(item)
        link_text = f"[mở trên Telegram]({link})" if link else "không có link"
        label = "**Tin gốc**" if len(case_messages) == 1 else f"**Tin {index}**"
        mark = " · tin chính" if item.message_id in key_ids else ""
        blocks.append(
            f"{label} — {_clean_line(item.sender_name)}, "
            f"{item.received_at.astimezone().isoformat()} · {link_text}{mark}\n"
            f"{_quote(item.text, limit)}")
    if hidden:
        blocks.append(f"(còn {hidden} tin khác trong case, xem nhóm Telegram)")
    case_line = ""
    if len(case_messages) > 1:
        case_line = (f"Case: {len(case_messages)} tin trong khoảng "
                     f"{case_messages[0].received_at.astimezone().isoformat()} – "
                     f"{case_messages[-1].received_at.astimezone().isoformat()}\n")
    if tier == "rule":
        provenance = "tầng 1 lệnh"
    else:
        confidence = (f"{classification.confidence:.2f}"
                      if classification is not None and classification.is_valid() else "không có")
        provenance = f"tầng 2 {sanitize(model, inline=True)}, confidence {confidence}"
    if len(case_messages) > 1:
        provenance += f" · case {len(case_messages)} tin"
    clarify_block = ""
    if clarify is not None and (needs_human or clarify):
        questions = [_clean_line(entry)[:MAX_CLARIFY_CHARS] for entry in clarify
                     if isinstance(entry, str) and entry.strip()][:MAX_CLARIFY]
        lines = questions or [NEEDS_CLARITY_LINE]
        clarify_block = "**Cần làm rõ**\n" + "\n".join(f"- {entry}" for entry in lines) + "\n\n"
    extras = []
    if images:
        extras.append("**Ảnh đính kèm**\n" + "\n".join(f"![{alt}]({url})" for alt, url in images))
    if notes:
        extras.append("\n".join(" ".join(note.split()) for note in notes))
    body = (
        f"<!-- tg:{message.chat_id}:{message.message_id} -->\n"
        f"**Tóm tắt:** {summary}\n"
        + (case_line + "\n" if case_line else "\n")
        + clarify_block
        + "\n\n".join(blocks) + "\n\n"
        + "".join(extra + "\n\n" for extra in extras)
        + f"---\nPhân loại: {provenance} · tạo tự động bởi tg-triage"
    )
    return Issue(title, body, labels)
