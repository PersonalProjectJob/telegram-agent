"""Render the fixed Vietnamese issue template and neutralize user content."""

import re
import unicodedata
import html
from dataclasses import dataclass

from .models import Classification, IncomingMessage, TICKET_TYPES, canonical_category, immutable_details


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
# US/Canada numbers with parentheses, including sanitize's Markdown escapes.
_PAREN_PHONE = re.compile(
    r"(?<![\w+])(?:\+?1[ .\-]*)?\\?\(\d{3}\\?\)[ .\-]*\d{3}[ .\-]*\d{4}(?!\d)")
_DATE_LIKE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}-\d{1,2}-\d{2,4}")


def _phone(match) -> str:
    return match[0] if _DATE_LIKE.match(match[0].lstrip("+")) else PHONE_PLACEHOLDER


def redact(value: str) -> str:
    """Replace e-mail addresses and phone numbers in already-sanitized text."""
    value = _EMAIL.sub(EMAIL_PLACEHOLDER, value)
    value = _PAREN_PHONE.sub(PHONE_PLACEHOLDER, value)
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


_BLOCK_START = re.compile(
    r"( {0,3})(?:(?P<heading>#{1,6})(?=\s|$)|(?P<bullet>[-+])(?=\s|$)|(?P<ordered>\d{1,9})(?P<dot>[.)])(?=\s|$)"
    r"|(?P<quote>>)|(?P<fence>~{3,}))")
_RULE_LINE = re.compile(r"[-=+~ ]+")   # setext underline / thematic break made of these


def line_safe(value: str) -> str:
    """Neutralize Markdown block syntax at the start of each line of already sanitized text.

    `sanitize` escapes inline syntax but not what only matters at the start of a line: "# x",
    "- x", "+ x", "1. x", "> x", "---", "===", "~~~" and a 4-space indent. A backslash (or an
    entity for the indent) makes them literal; text that is not block syntax is returned as is.
    Call it AFTER sanitize/redact, on the text (never on a marker the code writes itself).
    """
    lines = []
    for line in value.split("\n"):
        if line.strip() and _RULE_LINE.fullmatch(line):
            line = "".join(ch if ch == " " else "\\" + ch for ch in line)
        elif line.startswith("    "):
            line = "&#32;" + line[1:]
        else:
            match = _BLOCK_START.match(line)
            if match:
                lead, end = match.end(1), match.end()
                if match["fence"]:
                    line = line[:lead] + "".join("\\" + ch for ch in match["fence"]) + line[end:]
                elif match["ordered"]:
                    line = line[:end - 1] + "\\" + line[end - 1:]
                else:
                    line = line[:lead] + "\\" + line[lead:]
        lines.append(line)
    return "\n".join(lines)


def _quote(text: str, limit: int) -> str:
    clean = redact(sanitize(text))   # before the cut, so a cut cannot leave half an address
    quoted = "\n".join("> " + line_safe(line) for line in clean[:limit].split("\n"))
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


MAX_DOCUMENT_LABEL = 80
_RAW_NAME_LIMIT = 1000
_PLACEHOLDER = re.compile("(" + re.escape(EMAIL_PLACEHOLDER) + "|" + re.escape(PHONE_PLACEHOLDER) + ")")
_MARKDOWN_MARKS = re.compile(r"[\\`*_\[\]()!|<>~#{}^]")


def document_label(name, fallback="tài liệu") -> str:
    """Display text of a document link (12.14): the sender's file name made safe for Markdown.

    The name goes through `sanitize` and `redact` (e-mail and phone are replaced) and then every
    Markdown-significant character is dropped (an underscore becomes a space), so the name cannot
    open a link, an image, emphasis or HTML. Placeholders keep their brackets, escaped. The result
    is capped at MAX_DOCUMENT_LABEL characters without cutting a placeholder; empty becomes `fallback`.
    """
    base = re.split(r"[\\/]", name)[-1] if isinstance(name, str) else ""
    cleaned = redact(sanitize(base[:_RAW_NAME_LIMIT], inline=True))
    pieces = []
    for part in _PLACEHOLDER.split(cleaned):
        if part in (EMAIL_PLACEHOLDER, PHONE_PLACEHOLDER):
            pieces.append(part.replace("[", "\\[").replace("]", "\\]"))
        else:
            pieces.append(_MARKDOWN_MARKS.sub(lambda mark: " " if mark[0] == "_" else "", part))
    label, used = "", 0
    for piece in pieces:
        if used + len(piece) > MAX_DOCUMENT_LABEL:
            if not piece.startswith("\\["):
                label += piece[:MAX_DOCUMENT_LABEL - used]
            break
        label += piece
        used += len(piece)
    label = " ".join(label.split())
    return label or fallback


def _clean_line(value) -> str:
    return redact(sanitize(value, inline=True)) if isinstance(value, str) else ""


# 12.15 / 12.16: all template wording, headings, emoji, HTML tags and automatic questions are
# code-owned constants; user and model text only ever enters through `sanitize` + `redact`.
NO_DETAIL_LINE = "(chưa có thông tin)"
DOD_EVIDENCE_LINE = "- [ ] Đính kèm evidence kết quả (ảnh/link/log) khi đóng issue"
SUMMARY_HEADING = "### 📝 Tóm tắt"
BUG_HEADING = "### 🐞 Lỗi"
EVIDENCE_HEADING = "### 🖼 Minh họa / Evidence"
FEEDBACK_HEADING = "### 💡 Vấn đề / nhu cầu"
ACCEPTANCE_HEADING = "### ✅ Tiêu chí chấp nhận (nháp)"
CONTEXT_HEADING = "### 🧭 Ngữ cảnh"
TASK_HEADING = "### 🛠 Việc cần làm"
TASK_EVIDENCE_HEADING = "### 🖼 Minh họa / Evidence (đầu vào)"
DONE_HEADING = "### 🏁 Xong khi"
CLARIFY_HEADING = "### ❓ Cần làm rõ"
LABEL_ENVIRONMENT = "Môi trường"
LABEL_STEPS = "Các bước tái hiện"
LABEL_EXPECTED = "Mong đợi"
LABEL_ACTUAL = "Thực tế"
LABEL_USER_STORY = "User story"
LABEL_PROPOSAL = "Đề xuất"
LABEL_ALTERNATIVES = "Phương án khác"
LABEL_REASON = "Lý do"
LABEL_SCOPE = "Phạm vi"
LABEL_DUE = "Hạn / người được nhắc"
SCOPE_IN_LINE = "Làm — "
SCOPE_OUT_LINE = "Không làm — "
SCOPE_SEPARATOR = " · "
DOCUMENT_BULLET = "- 📎 "
DETAILS_OPEN = "<details>"
DETAILS_CLOSE = "</details>"
SUMMARY_OPEN = "<summary>"
SUMMARY_CLOSE = "</summary>"
SUB_OPEN = "<sub>"
SUB_CLOSE = "</sub>"
FOOTER_RULE = "---"
ORIGINAL_ICON = "💬"
ORIGINAL_TITLE = "Tin gốc"
UNKNOWN_SENDER = "(không rõ)"
IMAGE_ONLY_LINE = "(chỉ có ảnh)"
DOCUMENT_ONLY_LINE = "(chỉ có tài liệu)"
TIME_FORMAT = "%d/%m %H:%M"
BUG_QUESTIONS = (
    "Lỗi xảy ra trên môi trường nào (app/bản build, thiết bị, trình duyệt)?",
    "Các bước để tái hiện lỗi là gì?",
    "Kết quả mong đợi và kết quả thực tế?",
    "Gửi thêm ảnh/video màn hình lỗi",
)
FEEDBACK_QUESTIONS = ("Vấn đề / nhu cầu là gì?",
                      "Là vai trò nào, bạn muốn hành động gì để có lợi ích gì?")
TASK_WORK_QUESTION = "Việc cần làm là gì?"
MAX_DETAIL_CHARS = 500
MAX_DETAIL_ITEMS = 10
MAX_DETAIL_ITEM_CHARS = 200


def _labelled(label, value):
    return f"**{label}:** {value}"


def _join_lines(parts):
    """Join (text, is_list) parts. A list sits right under its label; a blank line follows it, so
    the next label is never swallowed as a lazy continuation of its last item."""
    out = ""
    previous_list = False
    for index, (value, is_list) in enumerate(parts):
        if index:
            out += "\n\n" if previous_list else "\n"
        out += value
        previous_list = is_list
    return out


def _evidence_text(*parts):
    return "\n\n".join(part for part in parts if part)


def _type_sections(category, details, media, notes):
    """Render only this category's fields; return section strings and missing-field questions.

    `media` is the code-written image/document Markdown (shown inside the evidence/context area);
    `notes` are code-written attachment failure notes, placed right under it."""
    values = dict(immutable_details(details))

    def text(key):
        return line_safe(_clean_line(strip_image_marker(values.get(key)))[:MAX_DETAIL_CHARS])

    def items(key, numbered=False):
        entries = values.get(key, ())
        lines = [_clean_line(strip_image_marker(entry))[:MAX_DETAIL_ITEM_CHARS]
                 for entry in entries[:MAX_DETAIL_ITEMS]]
        lines = [line_safe(line) for line in lines if line]
        return "\n".join(f"{index}. {line}" if numbered else f"- [ ] {line}"
                         for index, line in enumerate(lines, 1))

    sections, questions = [], []
    notes_text = "\n".join(notes)
    if category == "bug":
        environment, steps, expected, actual = (text("environment"), items("steps", numbered=True),
                                                text("expected"), text("actual"))
        sections.append(BUG_HEADING + "\n" + _join_lines([
            (_labelled(LABEL_ENVIRONMENT, environment or NO_DETAIL_LINE), False),
            (f"**{LABEL_STEPS}:**" + ("" if steps else " " + NO_DETAIL_LINE), False),
            *(((steps, True),) if steps else ()),
            (_labelled(LABEL_EXPECTED, expected or NO_DETAIL_LINE), False),
            (_labelled(LABEL_ACTUAL, actual or NO_DETAIL_LINE), False)]))
        evidence = _evidence_text(text("error_text"), *media)
        sections.append(EVIDENCE_HEADING + "\n"
                        + _evidence_text(evidence or NO_DETAIL_LINE, notes_text))
        if not environment:
            questions.append(BUG_QUESTIONS[0])
        if not steps:
            questions.append(BUG_QUESTIONS[1])
        if not expected or not actual:
            questions.append(BUG_QUESTIONS[2])
        if not evidence:
            questions.append(BUG_QUESTIONS[3])
    elif category == "feedback":
        problem, user_story = text("problem"), text("user_story")
        proposal, alternatives = text("proposal"), text("alternatives")
        sections.append(FEEDBACK_HEADING + "\n" + _join_lines(
            [(problem or NO_DETAIL_LINE, False)]
            + [(_labelled(label, value), False) for label, value in
               ((LABEL_USER_STORY, user_story), (LABEL_PROPOSAL, proposal),
                (LABEL_ALTERNATIVES, alternatives)) if value]))
        acceptance = items("acceptance")
        if acceptance:
            sections.append(ACCEPTANCE_HEADING + "\n" + acceptance)
        context = _evidence_text(text("context"), *media, notes_text)
        if context:
            sections.append(CONTEXT_HEADING + "\n" + context)
        questions.extend(question for value, question in
                         zip((problem, user_story), FEEDBACK_QUESTIONS) if not value)
    elif category == "task":
        work = text("work")
        scope = SCOPE_SEPARATOR.join(label + value for label, value in
                                     ((SCOPE_IN_LINE, text("scope_in")), (SCOPE_OUT_LINE, text("scope_out")))
                                     if value)
        sections.append(TASK_HEADING + "\n" + _join_lines(
            [(work or NO_DETAIL_LINE, False)]
            + [(_labelled(label, value), False) for label, value in
               ((LABEL_REASON, text("reason_context")), (LABEL_SCOPE, scope), (LABEL_DUE, text("due")))
               if value]))
        clean_links = [_clean_line(strip_image_marker(entry))[:MAX_DETAIL_ITEM_CHARS]
                       for entry in values.get("links", ())[:MAX_DETAIL_ITEMS]]
        links = "\n".join("- " + line_safe(entry) for entry in clean_links if entry)
        evidence = _evidence_text(*media, notes_text, links)
        if evidence:
            sections.append(TASK_EVIDENCE_HEADING + "\n" + evidence)
        done = items("done")
        sections.append(DONE_HEADING + "\n" + (done + "\n" if done else "") + DOD_EVIDENCE_LINE)
        if not work:
            questions.append(TASK_WORK_QUESTION)
    return sections, questions


def _sender(item) -> str:
    return _clean_line(item.sender_name) or UNKNOWN_SENDER


def _time(item) -> str:
    return item.received_at.astimezone().strftime(TIME_FORMAT)


def _sender_list(case_messages) -> str:
    """Distinct senders in time order: up to two names, then "+K" for the rest."""
    names = list(dict.fromkeys(_sender(item) for item in case_messages))
    if len(names) >= 3:
        return f"{names[0]}, {names[1]} +{len(names) - 2}"
    return ", ".join(names)


def _original_text(item, limit) -> str:
    if not item.text.strip():
        if item.has_image:
            return "> " + IMAGE_ONLY_LINE
        if item.has_document:
            return "> " + DOCUMENT_ONLY_LINE
    return _quote(item.text, limit)


def format_issue(message: IncomingMessage, classification: Classification | None,
                 tier: str, needs_human: bool, model: str = "qwen3.5:9b", *,
                 case_messages=None, key_message_ids=None, images=(), documents=(), notes=(),
                 clarify=None, text=None, max_quoted=None, details=None) -> Issue:
    """Render one issue. `message` is the case's (or topic group's) first message.

    `details=None` preserves the legacy body; an empty mapping enables the type template
    for trusted tickets, including typed needs-human tickets (12.15).

    All `case_messages` are quoted in time order; `key_message_ids` (Assistant
    definition 12.2) only marks the messages the model called key. `images` ((alt, url) pairs) and `notes` are written by code
    (12.8), never taken from the model, so they do not pass through `sanitize`. `documents` ((file name,
    url) pairs, 12.14) are listed as plain links, never embedded; the sender's file name is cleaned by
    `document_label` here, so no caller can pass a raw name into the body.

    Topic groups (12.9): `clarify` (None = single-case rendering) is the group's missing_info and
    adds the "Cần làm rõ" section; `text` is the model's (title, summary) for a needs-human group;
    `max_quoted` raises the quoted-message cap so a whole group is rendered. A needs-human issue
    keeps its type label only when the type is trusted (explicit command, or a confident
    ticket that still lacks information): then `classification` is passed and is valid.
    """
    if not needs_human and (classification is None or not classification.is_valid()
                            or classification.type not in TICKET_TYPES):
        raise ValueError("An issue requires a valid non-noise classification")
    typed = not needs_human
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
    for item in shown:
        link = telegram_link(item)
        link_text = f" · [mở trên Telegram]({link})" if link else ""
        mark = " · tin chính" if item.message_id in key_ids else ""
        blocks.append(f"**{_sender(item)} · {_time(item)}**{link_text}{mark}\n"
                      f"{_original_text(item, limit)}")
    if hidden:
        blocks.append(f"(còn {hidden} tin khác trong case, xem nhóm Telegram)")
    if tier == "rule":
        provenance = "tầng 1 lệnh"
    else:
        confidence = (f"{classification.confidence:.2f}"
                      if classification is not None and classification.is_valid() else "không có")
        provenance = f"tầng 2 {sanitize(model, inline=True)}, confidence {confidence}"
    if len(case_messages) > 1:
        provenance += f" · case {len(case_messages)} tin"

    def clarify_section(entries):
        return CLARIFY_HEADING + "\n" + "\n".join(f"- {line_safe(entry)}" for entry in entries)

    clarify_blocks = []
    if clarify is not None and (needs_human or clarify):
        questions = [_clean_line(entry)[:MAX_CLARIFY_CHARS] for entry in clarify
                     if isinstance(entry, str) and entry.strip()][:MAX_CLARIFY]
        clarify_blocks.append(clarify_section(questions or [NEEDS_CLARITY_LINE]))
    media = []
    if images:
        media.append("\n".join(f"![{alt}]({url})" for alt, url in images))
    if documents:
        media.append("\n".join(
            f"{DOCUMENT_BULLET}[{document_label(name, f'tài liệu {index}')}]({url})"
            for index, (name, url) in enumerate(documents, 1)))
    clean_notes = [" ".join(note.split()) for note in notes]
    type_blocks = []
    category = canonical_category(classification.type) if typed else None
    if details is not None and category in ("bug", "feedback", "task"):
        # Move the existing, code-owned attachment Markdown into the category's evidence area.
        type_blocks, auto_questions = _type_sections(category, details, media, clean_notes)
        media, clean_notes = [], []
        questions = []
        seen = set()
        for entry in clarify or ():
            clean = _clean_line(strip_image_marker(entry))[:MAX_CLARIFY_CHARS]
            if clean and clean not in seen:
                seen.add(clean)
                questions.append(clean)
        for question in auto_questions:
            key = _clean_line(question)
            if key not in seen:
                seen.add(key)
                questions.append(question)
        questions = questions[:MAX_CLARIFY]
        clarify_blocks = []
        if questions or (needs_human and clarify is not None):
            clarify_blocks.append(clarify_section(questions or [NEEDS_CLARITY_LINE]))
    extra_evidence = _evidence_text(*media, "\n".join(clean_notes))
    if extra_evidence:   # untyped / legacy issue: images, documents and notes under one heading
        type_blocks = [*type_blocks, EVIDENCE_HEADING + "\n" + extra_evidence]
    original_title = (f"{ORIGINAL_ICON} {ORIGINAL_TITLE} ({len(case_messages)} tin)"
                      f" — {_sender_list(case_messages)}, {_time(case_messages[0])}")
    original = (f"{DETAILS_OPEN}{SUMMARY_OPEN}{original_title}{SUMMARY_CLOSE}\n\n"
                + "\n\n".join(blocks) + f"\n\n{DETAILS_CLOSE}")
    sections = [
        f"<!-- tg:{message.chat_id}:{message.message_id} -->\n{SUMMARY_HEADING}\n{line_safe(summary)}",
        *type_blocks, *clarify_blocks, original,
        f"{FOOTER_RULE}\n{SUB_OPEN}Phân loại: {provenance} · tạo tự động bởi tg-triage{SUB_CLOSE}"]
    body = "\n\n".join(sections)
    return Issue(title, body, labels)
