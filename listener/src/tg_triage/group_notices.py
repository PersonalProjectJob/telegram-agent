"""Bounded, plain-text ticket confirmations and owner clarification notices."""

import logging
import re
import unicodedata

from .issue_format import redact
from .models import utf8_valid

LOG = logging.getLogger(__name__)
MAX_QUESTIONS = 3
MAX_QUESTION_CHARS = 180
MAX_NOTICE_CHARS = 1100
TIMEOUT_SECONDS = 15
FALLBACK_QUESTION = "Bạn muốn team xử lý gì, ở màn hình/chức năng nào và kết quả mong muốn là gì?"
_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*")


def clean_question(value):
    if not isinstance(value, str) or not utf8_valid(value):
        return ""
    # Bound processing as well as output. Remove invisible format/control characters,
    # then redact contact data before truncation and disable mentions / automatic links.
    value = " ".join(value[:4000].split())
    value = "".join(ch for ch in value if not unicodedata.category(ch).startswith("C"))
    value = redact(value)
    value = re.sub(r"(?i)(?:https?://|www\.)\S+", "[liên kết đã ẩn]", value)
    value = value.replace("@", "＠")
    return value[:MAX_QUESTION_CHARS].strip()


def notice_text(result, missing_info, repo):
    issue_link = None
    if (isinstance(repo, str) and _REPO.fullmatch(repo)
            and type(result.issue_number) is int and result.issue_number > 0):
        issue_link = f"https://github.com/{repo}/issues/{result.issue_number}"
    if result.decision == "issue":
        if issue_link is None:
            return None
        text = f"Em đã ghi nhận nội dung này và tạo ticket: {issue_link} rồi nhé 😊"
    elif result.decision == "needs_human":
        text = ("Em đã ghi nhận nội dung này rồi nhé. "
                "Nhờ bạn hoặc Owner bổ sung/xác nhận thông tin ngay trong group để làm rõ yêu cầu.")
        if issue_link:
            text += f"\nEm đã tạo ticket để tiện theo dõi: {issue_link}"
        questions = []
        if isinstance(missing_info, (tuple, list)):
            for value in missing_info[:20]:
                question = clean_question(value)
                if question:
                    questions.append("• " + question)
                    if len(questions) == MAX_QUESTIONS:
                        break
        if not questions:
            questions.append("• " + FALLBACK_QUESTION)
        text += "\nThông tin cần làm rõ:\n" + "\n".join(questions)
    elif result.decision == "error":
        text = ("Chưa xác nhận được kết quả tạo GitHub issue cho nội dung này. "
                "Nhờ Owner kiểm tra repo và hỗ trợ ngay trong group. "
                "Issue có thể đã được tạo; kiểm tra repo trước khi thử tạo lại.")
    elif result.decision == "rate_limited":
        text = ("Chưa tạo GitHub issue vì đã đạt giới hạn tạo issue. "
                "Nhờ Owner kiểm tra và xác nhận cách xử lý ngay trong group.")
    else:
        return None
    return text[:MAX_NOTICE_CHARS]


def notify(store, sender, first, result, missing_info, repo):
    """At most one send attempt per group; even ambiguous transport failures stay terminal.

    Reserve is committed before transport. A crash may lose a notice, deliberately:
    replay must never blindly resend an already delivered message.
    """
    if sender is None:
        return
    text = notice_text(result, missing_info, repo)
    if text is None:
        return
    try:
        if not store.reserve_group_notice(first.chat_id, first.message_id, result.decision):
            return
    except Exception as error:
        LOG.warning("Group notice reservation failed exception=%s", type(error).__name__)
        return  # fail closed if durable state is unavailable
    status, notice_id = "failed", None
    try:
        sent = sender(first.chat_id, first.message_id, text)
        if type(sent) is int and sent > 0:
            status, notice_id = "sent", sent
    except Exception as error:
        LOG.warning("Group notice send failed exception=%s", type(error).__name__)
    try:
        store.finish_group_notice(first.chat_id, first.message_id, status, notice_id)
    except Exception as error:
        LOG.warning("Group notice state failed exception=%s", type(error).__name__)
