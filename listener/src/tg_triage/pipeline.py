"""Group messages into windows (cases), split each closed window into topics, one issue per topic.

Messages arrive one at a time and are attached to a window ("case") by the store (Assistant
definition 12.1). A window is analyzed when it is due: a command arrived, it is full, its
window expired, or aggregation is disabled. On close, the model splits the window into topic
groups (12.9); every group with a ticket or needs-human verdict becomes exactly one issue
holding only its own messages. The model only returns JSON; this module checks the partition
and decides every action. A classifier without `split_topics` (the single-message fakes) is
treated as one group per window.

Assistant definition 12.13 (replaces the "must be tagged" gate of 12.11): a group becomes an issue when
  - it has a /command with content, or a message that tags the listener (forced, whatever the model said:
    a model "chat" or "unsure" gives a needs-human issue), or
  - the model calls it bug, task or feedback (feature included) with enough confidence (automatic).
Everything else is kept in SQLite without calling GitHub or reacting: `chat` (talk with no work for the
team; a question, even tagged or /question, is `chat`), `pending_review` (the model was not sure and
nobody tagged or used a command) or `noise` (no letter at all, an empty command, or the safety block:
a group that is only contact data or an acknowledgement).

Assistant definition 12.12: the model (with reasoning) judges chat / bug / task / feedback, and no
keyword list decides it. Code keeps only the safety locks, in `_safety`: a structural block for groups
with no product content, the question rule, e-mail / phone redaction in the issue (issue_format), the
hourly issue limit and the full reconciliation. Every group logs a `reason` code (never any content).
"""

import logging
import re
import threading
import time
from dataclasses import dataclass, replace
from datetime import timezone

from . import reactions, rules
from .attachments import AttachmentProcessor, AttachmentResult
from .issue_format import format_issue
from .models import (CaseAnalysis, Classification, Decision, ISSUE_TYPES, PRIORITIES, TICKET_CATEGORIES,
                     TICKET_TYPES, TopicGroup, VERDICTS, canonical_category)

LOG = logging.getLogger(__name__)

CASE_STATUS = {"noise": "noise", "chat": "chat", "pending_review": "pending_review",
               "rate_limited": "error", "error": "error", "case_open": "open", "followup": "done"}
# Written by code (never by the model) into the "Cần làm rõ" section of a fallback ticket.
FALLBACK_NOTE = "Agent không tách được chủ đề cho các tin này, cần người đọc và tách thủ công"
# When one window yields several outcomes, the most urgent one is reported as the window's.
_RANK = {"error": 0, "rate_limited": 1, "issue": 2, "dry_run_issue": 2,
         "needs_human": 3, "dry_run_needs_human": 3, "pending_review": 4, "chat": 5, "noise": 6}
# 12.11: groups kept in SQLite only; no GitHub call, no reaction.
NO_TICKET_ACTIONS = ("noise", "chat", "pending_review")
# Why a group did not become a ticket (stored per message, exported in the CSV `reason` column).
REASON_PENDING_UNTAGGED = "pending_untagged"  # the model was not sure, nobody tagged or used a command
REASON_MODEL_CHAT = "model_chat"              # model: plain talk, nothing for the team
REASON_FORCED_COMMAND = "forced_command"      # a /command with content: always an issue (12.13)
REASON_FORCED_MENTION = "forced_mention"      # a message tags the listener: always an issue (12.13)
REASON_AUTO_PREFIX = "auto_"                  # auto_bug | auto_task | auto_feedback: confident model verdict
REASON_QUESTION = "question_no_ticket"        # a question (model or /question) is chat, never a ticket
REASON_SAFETY_BLOCK = "safety_block"          # only contact data / acknowledgement / empty command
REASON_MODEL_NOISE = "model_noise"
# A group the model called chat is an acknowledgement when its own words (command, e-mail, phone and
# @handle removed) number at most this many; a few more are allowed when contact data is shared.
MAX_ACK_WORDS = 3
MAX_CONTACT_WORDS = 5

_EMAIL = re.compile(r"[\w.+%\-]+@[\w\-]+(?:\.[\w\-]+)+")
_PHONE = re.compile(r"\+?\d[\d .\-]{6,}\d")
_HANDLE = re.compile(r"@\w+")
_WORD = re.compile(r"\w*[^\W\d_]\w*")


@dataclass
class Plan:
    """What to do with one group of messages, fully decided before anything is sent."""

    key: str                       # unique per group: <chat_id>:<smallest message id>
    messages: list                 # time order
    first: object                  # message that carries the marker and the OK-hand reaction
    tier: str
    action: str                    # issue | needs_human | noise | chat | pending_review
    classification: Classification | None = None
    typed: bool = False            # type label is trusted even though the issue needs a human
    clarify: tuple | None = None   # missing_info; None = single-case (legacy) rendering
    text: tuple | None = None      # model (title, summary) shown on a needs-human issue
    reason: str | None = None      # why a group was set aside (noise / chat / pending_review)
    key_ids: tuple = ()
    chat_hint: bool = False        # a command group the model called chat (12.11 point 7)
    fallback: bool = False         # no usable model judgment at all (model down, partition or JSON unusable)
    model_reason: str | None = None   # 12.12: the model's short why; stored for the reviewer only


def _order(item):
    when = item.received_at
    return (when if when.tzinfo is not None else when.replace(tzinfo=timezone.utc), item.message_id)


def validate_partition(groups, expected_ids):
    """Return the groups when they cover `expected_ids` exactly once each, else None (12.9)."""
    if not isinstance(groups, (tuple, list)) or not groups:
        return None
    seen = set()
    for group in groups:
        if not isinstance(group, TopicGroup) or not group.message_ids:
            return None
        for value in group.message_ids:
            if type(value) is not int or value not in expected_ids or value in seen:
                return None
            seen.add(value)
    return tuple(groups) if seen == set(expected_ids) else None


def decide(classification, tier, threshold) -> str:
    if not isinstance(classification, Classification) or not classification.is_valid():
        return "needs_human"
    if tier != "rule" and classification.confidence < threshold:
        return "needs_human"
    return "noise" if classification.type == "noise" else "issue"


def decide_case(analysis, tier, threshold):
    """Return (action, classification) for a model verdict; anything doubtful is needs_human."""
    if not isinstance(analysis, CaseAnalysis) or analysis.verdict not in VERDICTS:
        return "needs_human", None
    if analysis.verdict == "needs_human" or (analysis.verdict == "ticket" and analysis.actionable is False):
        return "needs_human", None
    value = analysis.classification
    if not isinstance(value, Classification) or not value.is_valid():
        return "needs_human", None
    if analysis.verdict == "ticket" and value.type not in TICKET_TYPES:
        return "needs_human", None
    if analysis.verdict == "noise" and value.type != "noise":
        value = replace(value, type="noise")
    return decide(value, tier, threshold), value


def decide_group(group, threshold):
    """Return (action, classification) for a model group; anything doubtful is needs_human."""
    if not isinstance(group, TopicGroup) or group.verdict not in VERDICTS:
        return "needs_human", None
    if group.verdict == "needs_human" or (group.verdict == "ticket" and group.actionable is False):
        return "needs_human", None
    value = group.classification()
    if group.verdict == "noise":
        # Nothing is posted for noise, so empty wording must not turn a courtesy into a ticket.
        value = replace(
            value, type="noise", title=(value.title or "").strip() or "Không cần xử lý",
            summary=(value.summary or "").strip() or "Tin không cần tạo issue",
            priority=value.priority if value.priority in PRIORITIES else "low")
    if not value.is_valid():
        return "needs_human", None
    if group.verdict == "ticket" and value.type not in TICKET_TYPES:
        return "needs_human", None
    return decide(value, "model", threshold), value


def _chat_value(wording):
    """What is stored for a chat group: its model wording, with defaults when it is unusable."""
    confidence = wording.confidence
    return Classification(
        "chat", confidence if type(confidence) in (int, float) and 0 <= confidence <= 1 else 1.0,
        (wording.title or "").strip() or "Trao đổi thường",
        (wording.summary or "").strip() or "Trao đổi giữa thành viên, không có việc cho team", "low")


def _has_content_command(messages) -> bool:
    """12.13: a /command that carries content. An empty command has nothing to ticket."""
    for item in messages:
        if rules.is_command(item.text):
            rule = rules.evaluate(item.text)
            if rule is not None and rule.type != "noise":
                return True
    return False


def _tagged(messages) -> bool:
    """12.13: Telegram's own `mentioned` flag only. An "@" inside an e-mail or in the text is never a tag."""
    return any(item.mentions_listener for item in messages)


def _own_words(text) -> int:
    """Words of a message that are not a command prefix, e-mail, phone number or @handle (structure only)."""
    text = text or ""
    match = rules.COMMAND.match(text)
    if match:
        text = text[match.end():]
    for pattern in (_EMAIL, _PHONE, _HANDLE):
        text = pattern.sub(" ", text)
    return len(_WORD.findall(text))


def _contentless(messages, model_chat) -> bool:
    """12.13 safety block: no product content. Only contact data (no word left once e-mail, phone and
    @handle are removed), or, when the model itself called the group chat, a few words of acknowledgement.
    A photo is content. No keyword list is involved."""
    if any(item.has_image for item in messages):
        return False
    total = sum(_own_words(item.text) for item in messages)
    shares_contact = any(_EMAIL.search(item.text or "") or _PHONE.search(item.text or "") for item in messages)
    return total == 0 or (model_chat and total <= (MAX_CONTACT_WORDS if shares_contact else MAX_ACK_WORDS))


def _legacy_analysis(value, messages):
    """Adapt a classifier that only offers `classify(text)` to a case verdict."""
    if not isinstance(value, Classification) or not value.is_valid():
        return None
    verdict = "noise" if value.type == "noise" else "ticket"
    return CaseAnalysis(verdict, value, tuple(item.message_id for item in messages))


class Pipeline:
    def __init__(self, config, store, classifier, github=None, *, timer=time.monotonic,
                 uploader=None, downloader=None, reactor=None):
        self.config = config
        self.reactor = reactor
        self.store = store
        self.classifier = classifier
        self.github = github
        self.timer = timer
        self._lock = threading.Lock()
        self.attachments = AttachmentProcessor(
            config.attachments, store, repo=config.gh_repo,
            downloader=downloader, uploader=uploader)

    def set_media_downloader(self, downloader):
        """Telegram transport hands over its image downloader once the client exists."""
        self.attachments.downloader = downloader

    def set_reactor(self, reactor):
        """Telegram transport hands over `reactor(chat_id, message_id, emoji) -> bool`, a blocking
        call that places the reaction on the event loop (12.10). None disables the heart."""
        self.reactor = reactor

    def _acknowledge(self, plan):
        """Heart on the first message of a ticket / needs-human group, before its issue (12.10).

        Placed once per message: a command already got its heart on receipt, and the stored
        flag (not Telegram) is what says so. Never raises and never retries.
        """
        if self.reactor is None:
            return
        first = plan.first
        try:
            if self.store.reaction_done(first.chat_id, first.message_id, "received"):
                return
            if self.reactor(first.chat_id, first.message_id, reactions.RECEIVED):
                self.store.mark_reaction(first.chat_id, first.message_id, "received")
        except Exception as error:
            LOG.warning("Acknowledge failed chat_id=%s message_id=%s exception=%s",
                        first.chat_id, first.message_id, type(error).__name__)

    def process(self, message) -> Decision | None:
        # Keep the quota check and the HTTP side effect sequential even for callers
        # other than the single Telegram worker.
        with self._lock:
            return self._process(message)

    def close_due_cases(self) -> list:
        """Analyze every case whose window expired; returns the decisions made."""
        results = []
        aggregation = self.config.aggregation
        with self._lock:
            due = self.store.due_cases(self.store.clock(), aggregation.window_minutes,
                                       aggregation.max_messages_per_case)
            for case_key in due:
                start = self.timer()
                try:
                    result = self._analyze_case(case_key, start)
                except Exception as error:
                    LOG.error("Case processing failed case_key=%s exception=%s",
                              case_key, type(error).__name__)
                    try:
                        self.store.fail_case(case_key, type(error).__name__)
                    except Exception as storage_error:
                        LOG.error("Case quarantine failed exception=%s", type(storage_error).__name__)
                    continue
                if result is not None:
                    results.append(result)
        return results

    def _process(self, message):
        start = self.timer()
        if not self.store.start_classifying(message):
            LOG.info("chat_id=%s message_id=%s tier=none type=None confidence=None "
                     "decision=duplicate issue_number=None latency_ms=0",
                     message.chat_id, message.message_id)
            return None
        # One rule evaluation per message; the result is reused by the case analysis.
        rule_cache = {message.message_id: rules.evaluate(message.text)}
        rule = rule_cache[message.message_id]
        is_command = rules.is_command(message.text)
        aggregation = self.config.aggregation
        assignment = self.store.assign_case(
            message, window_minutes=aggregation.window_minutes,
            max_messages=aggregation.max_messages_per_case, is_command=is_command)
        if assignment.followup:
            # Late message of a finished case: recorded on the case, never a second issue.
            return self._waiting(message, assignment, "followup", start)
        # A lone courtesy closes at once; a lone photo/caption waits, since the explanation
        # usually follows in the next message (12.8: the image must reach the issue).
        lone_noise = (assignment.created and rule is not None and rule.type == "noise"
                      and not message.has_image)
        due = (is_command or lone_noise or aggregation.window_minutes == 0
               or assignment.message_count >= aggregation.max_messages_per_case)
        if not due:
            return self._waiting(message, assignment, "case_open", start)
        return self._analyze_case(assignment.case_key, start, trigger=message, rule_cache=rule_cache)

    def _waiting(self, message, assignment, decision, start):
        result = Decision(decision, "none", case_key=assignment.case_key,
                          latency_ms=max(0, int((self.timer() - start) * 1000)))
        self._log(message.chat_id, message.message_id, result)
        return result

    def _rule(self, message, cache):
        if message.message_id not in cache:
            cache[message.message_id] = rules.evaluate(message.text)
        return cache[message.message_id]

    def _model(self, messages, forced_type):
        try:
            analyze = getattr(self.classifier, "analyze_case", None)
            if analyze is not None:
                value = analyze(messages, forced_type=forced_type)
            else:
                value = _legacy_analysis(
                    self.classifier.classify("\n".join(item.text for item in messages)), messages)
        except Exception as error:
            # An injected client must not be able to leak content via errors.
            LOG.warning("Classifier failed exception=%s", type(error).__name__)
            return None
        return value if isinstance(value, CaseAnalysis) else None

    def _judge(self, messages, cache):
        """Return (tier, action, classification, analysis) for a whole case."""
        threshold = self.config.ollama.confidence_threshold
        command = next((item for item in messages if rules.is_command(item.text)), None)
        if command is not None:
            rule = self._rule(command, cache)
            if len(messages) == 1:
                action = "noise" if rule.type == "noise" else "issue"
                return "rule", action, rule, None
            forced = rules.COMMAND.match(command.text)[1].lower()
            analysis = self._model(messages, forced)
            usable = (analysis is not None and analysis.verdict != "needs_human"
                      and isinstance(analysis.classification, Classification)
                      and analysis.classification.is_valid())
            if rule.type != "noise":
                # Explicit command: the type is the sender's, the model only words it.
                text = analysis.classification if usable else rule
                value = Classification(rule.type, 1.0, text.title, text.summary,
                                       text.priority if usable else rule.priority)
                return "rule", "issue", value, analysis
            if usable and analysis.verdict == "ticket":
                typed = replace(analysis.classification, type=forced)
                action, value = decide_case(replace(analysis, classification=typed), "model", threshold)
                return "rule", action, value, analysis
            action, value = decide_case(analysis, "model", threshold)
            return "rule", action, value, analysis
        rule_of_each = []
        for item in messages:
            rule = self._rule(item, cache)
            rule_of_each.append(rule)
            if rule is None or rule.type != "noise":
                break
        else:
            return "rule", "noise", rule_of_each[0], None
        analysis = self._model(messages, None)
        action, value = decide_case(analysis, "model", threshold)
        return "model", action, value, analysis

    @staticmethod
    def _key_ids(analysis, messages):
        """Key ids the model chose, restricted to this case. Only a display marker:
        every message of the case is rendered regardless (empty when there is no model verdict)."""
        if analysis is None:
            return ()
        present = {item.message_id for item in messages}
        return tuple(dict.fromkeys(
            value for value in analysis.key_message_ids if type(value) is int and value in present))

    def _images(self, case_key, messages, *, live):
        try:
            return self.attachments.process(case_key, messages, live=live)
        except Exception as error:
            LOG.warning("Attachments failed exception=%s", type(error).__name__)
            return AttachmentResult(notes=[f"Ảnh đính kèm không tải được: {type(error).__name__}"])

    # ---- planning: split a closed window into groups (12.9) ----------------------------

    def _topics_call(self):
        """The classifier's topic-splitting entry point: `classify_topics` (provider interface,
        12.12) or the older `split_topics`; None when it cannot split topics at all."""
        return (getattr(self.classifier, "classify_topics", None)
                or getattr(self.classifier, "split_topics", None))

    def _split(self, chunk):
        """Ask the model for topic groups; one retry when the answer is not an exact partition."""
        expected = {item.message_id for item in chunk}
        ask = self._topics_call()
        for attempt in (1, 2):
            try:
                answer = ask(chunk)
            except Exception as error:
                # An injected client must not be able to leak content via errors.
                LOG.warning("Topic split failed exception=%s", type(error).__name__)
                answer = None
            groups = validate_partition(answer, expected)
            if groups is not None:
                return groups
            LOG.warning("Topic split rejected chunk_messages=%s attempt=%s", len(chunk), attempt)
        return None

    def _noise_plan(self, item, value, reason, tier="rule"):
        return Plan(f"{item.chat_id}:{item.message_id}", [item], item, tier, "noise", value,
                    clarify=(), reason=reason)

    @staticmethod
    def _set_aside(plan, action, reason, value=None):
        """The same group, kept in SQLite only (no issue, no reaction) under `action` / `reason`."""
        return replace(plan, action=action, classification=value, typed=False, text=None,
                       reason=reason, key_ids=(), chat_hint=False, clarify=())

    def _auto_reason(self, plan):
        """`auto_<type>` when the model itself is sure enough that the group is a bug, task or feedback."""
        value = plan.classification
        if (plan.action == "issue" or (plan.action == "needs_human" and plan.typed)) and (
                isinstance(value, Classification) and value.is_valid() and value.type in TICKET_CATEGORIES
                and value.confidence >= self.config.ollama.confidence_threshold):
            return REASON_AUTO_PREFIX + value.type
        return None

    def _safety(self, plan):
        """12.13: the decision after the model, whatever it said. No keyword decides.

        1. Tier-1 noise (no letter, empty command) stays noise.
        2. A question (model type `question`, or /question) is `chat`: never a ticket, even tagged.
        3. A /command with content, or a tag from Telegram, FORCES an issue (`forced_command`,
           `forced_mention`): a model "chat" / "unsure" gives a `needs-human` issue, never a drop.
        4. Otherwise the model decides: bug / task / feedback with enough confidence is an issue
           (`auto_*`); chat is `chat`; anything unsure is `pending_review` (`pending_untagged`).
        5. Safety block: a group with no product content (only contact data or an acknowledgement) is
           never an issue, even forced, unless it holds an explicit command with content.
        Every outcome except an issue keeps all messages in SQLite and is reconciled.
        """
        value = plan.classification
        if isinstance(value, Classification) and value.type == "feature":
            # 12.11: feature is feedback; the label and the stored type say so.
            value = replace(value, type="feedback")
            plan = replace(plan, classification=value)
        explicit = _has_content_command(plan.messages)
        forced = REASON_FORCED_COMMAND if explicit else (
            REASON_FORCED_MENTION if _tagged(plan.messages) else None)
        if plan.action == "noise":
            return plan
        if (isinstance(value, Classification) and value.type == "question"
                and plan.action in ("issue", "needs_human")):
            return self._set_aside(plan, "chat", REASON_QUESTION, replace(value, type="chat"))
        model_chat = plan.action == "chat" or plan.chat_hint
        if forced is None:
            if model_chat:
                return self._set_aside(plan, "chat", REASON_MODEL_CHAT, plan.classification)
            reason = self._auto_reason(plan)
            if reason is None:
                return self._set_aside(plan, "pending_review", REASON_PENDING_UNTAGGED,
                                       plan.classification)
            candidate = replace(plan, reason=reason)
        elif plan.action in ("issue", "needs_human") and (not model_chat or explicit):
            # An explicit command keeps its own type even when the model said chat.
            candidate = replace(plan, reason=forced, chat_hint=False)
        else:
            # Forced, but the model said chat: the type is untrusted, a human decides.
            candidate = replace(plan, action="needs_human", classification=None, typed=False, text=None,
                                clarify=(), key_ids=(), chat_hint=False, fallback=False, reason=forced)
        if not explicit and _contentless(plan.messages, model_chat):
            LOG.info("Safety block key=%s messages=%s", plan.key, len(plan.messages))
            return self._set_aside(plan, "noise", REASON_SAFETY_BLOCK, Classification(
                "noise", 1.0, "Không cần xử lý", "Tin không cần tạo issue", "low"))
        return candidate

    def _plan(self, case_key, messages, first, cache):
        ordered = sorted(messages, key=_order)
        if self._topics_call() is None:
            return [self._safety(self._plan_single(case_key, ordered, first, cache))]
        plans, rest = [], []
        for item in ordered:
            rule = self._rule(item, cache)
            # Only a message with no letter at all (tier 1) is set aside by code; greetings and short
            # replies go to the model with the rest of the window (12.12).
            # A photo or a command stays in: its image or explicit type must reach an issue.
            if (rule is not None and rule.type == "noise" and not item.has_image
                    and not rules.is_command(item.text)):
                plans.append(self._noise_plan(item, rule, "rule_noise"))
            else:
                rest.append(item)
        size = max(1, self.config.aggregation.max_messages_per_window)
        for start in range(0, len(rest), size):
            plans.extend(self._plan_chunk(rest[start:start + size], cache))
        plans.sort(key=lambda plan: plan.first.message_id)
        return plans

    def _plan_chunk(self, chunk, cache):
        if len(chunk) == 1:
            item = chunk[0]
            rule = self._rule(item, cache)
            if rule is not None and not item.has_image:
                if rule.type == "noise":   # an empty command on its own: nothing to ticket
                    return [self._noise_plan(item, rule, REASON_SAFETY_BLOCK if rules.is_command(item.text)
                                             else "rule_noise")]
                if rules.is_command(item.text):   # lone explicit command: no model needed
                    return [self._safety(Plan(f"{item.chat_id}:{item.message_id}", [item], item, "rule",
                                              "issue", rule, typed=True, clarify=()))]
        by_id = {item.message_id: item for item in chunk}
        groups = self._split(chunk)
        fallback_group = groups is None
        if groups is None:
            # Never lose a message: the whole chunk becomes one ticket for a human.
            groups = (TopicGroup("", tuple(by_id), "needs_human", missing_info=(FALLBACK_NOTE,)),)
        fallback = fallback_group
        return [self._safety(self._plan_group(by_id, group, cache, fallback=fallback))
                for group in groups]

    def _plan_group(self, by_id, group, cache, fallback=False):
        messages = sorted((by_id[value] for value in group.message_ids), key=_order)
        first = min(messages, key=lambda item: item.message_id)
        key = f"{first.chat_id}:{first.message_id}"
        if group.actionable is False and group.verdict == "ticket":
            group = replace(group, verdict="needs_human")   # 12.12: an unclear request is never a sure ticket
        missing = group.missing_info
        wording = group.classification()
        command = next((item for item in messages if rules.is_command(item.text)), None)
        if command is not None:
            rule = self._rule(command, cache)
            if rule.type != "noise":
                # Explicit command: the type is the sender's, the model only words it.
                usable = group.verdict == "ticket" and wording.is_valid() and wording.type in TICKET_TYPES
                source = wording if usable else rule
                value = Classification(rule.type, 1.0, source.title, source.summary,
                                       source.priority if usable else rule.priority)
                needs = group.verdict == "needs_human" or bool(missing)
                # 12.11 point 7: the command still goes through the category gate; a model that
                # calls the group chat sends it to pending_review (never silently dropped).
                return Plan(key, messages, first, "rule", "needs_human" if needs else "issue", value,
                            typed=True, clarify=missing, text=(value.title, value.summary),
                            chat_hint=group.category == "chat", fallback=fallback,
                            model_reason=group.reason)
            if group.verdict == "ticket" and wording.is_valid():
                # An empty command still fixes the type when the rest of the group is a ticket.
                group = replace(group, type=rules.COMMAND.match(command.text)[1].lower())
        category = group.category
        # 12.13: a question (type `question` under feedback) is kept as such; `_safety` makes it chat.
        question = group.type == "question" and category in (None, "feedback")
        if category in TICKET_CATEGORIES and not question:
            group = replace(group, type=category)   # the category is authoritative over `type`
        elif category is None and group.type in TICKET_TYPES and not question:
            group = replace(group, type=canonical_category(group.type))   # legacy answer: fold feature
        action, value = decide_group(group, self.config.ollama.confidence_threshold)
        if category == "chat" and group.verdict != "needs_human":
            # Plain talk with nothing for the team (12.11); a tag is judged later, by `_safety`.
            return Plan(key, messages, first, "model", "chat", _chat_value(wording), clarify=(),
                        reason=REASON_MODEL_CHAT, model_reason=group.reason)
        typed = False
        if action == "issue" and missing:
            action, typed = "needs_human", True
        # `value` is None for a needs-human group in two cases: the model said needs_human (a
        # judgment: "not sure") or its wording was unusable (no judgment at all, like a failure).
        unusable = action == "needs_human" and value is None and group.verdict != "needs_human"
        return Plan(key, messages, first, "model", action, value, typed=typed, clarify=missing,
                    text=(group.title, group.summary) if action == "needs_human" else None,
                    reason=REASON_MODEL_NOISE if action == "noise" else None, fallback=fallback or unusable,
                    model_reason=group.reason)

    def _plan_single(self, case_key, ordered, first, cache):
        """One group for the whole window, for classifiers that cannot split topics."""
        tier, action, classification, analysis = self._judge(ordered, cache)
        reason = (("rule_noise" if tier == "rule" else REASON_MODEL_NOISE) if action == "noise" else None)
        if reason == "rule_noise" and any(rules.is_command(item.text) for item in ordered):
            reason = REASON_SAFETY_BLOCK   # an empty command
        category = analysis.category if analysis is not None else None
        chat = category == "chat" and tier == "model"
        if chat and action == "noise":
            action, reason = "chat", REASON_MODEL_CHAT
        elif (category in TICKET_CATEGORIES and tier == "model" and action == "issue"
              and not (category == "feedback" and classification.type == "question")):
            classification = replace(classification, type=category)   # category over `type`
        unsure = analysis is not None and (analysis.verdict == "needs_human" or (
            analysis.verdict == "ticket" and analysis.actionable is False))   # a judgment: "not sure"
        unusable = action == "needs_human" and classification is None and not unsure
        missing = analysis.missing_info if analysis is not None and tier == "model" else ()
        clarify, typed, text = None, False, None
        if missing and action == "issue":
            # 12.9: a confident ticket that still lacks information is a needs-human ticket with questions.
            action, typed, clarify = "needs_human", True, missing
            text = (classification.title, classification.summary)
        return Plan(case_key, ordered, first, tier, action, classification, typed=typed, clarify=clarify,
                    text=text, key_ids=self._key_ids(analysis, ordered), reason=reason, fallback=unusable,
                    chat_hint=(category == "chat" and action in ("issue", "needs_human")),
                    model_reason=analysis.reason if analysis is not None else None)

    # ---- execution: one issue per group ------------------------------------------------

    def _run(self, plan, case_key):
        result = Decision(plan.action, plan.tier, plan.classification, case_key=case_key,
                          reason=plan.reason, model_reason=plan.model_reason)
        first = plan.first
        LOG.info("Group decision key=%s action=%s reason=%s messages=%s",
                 plan.key, plan.action, plan.reason, len(plan.messages))
        if plan.action in NO_TICKET_ACTIONS:
            # noise / chat / pending_review: stored and reconciled, never sent, never reacted to.
            self.store.finish(first, result)
            return result
        self._acknowledge(plan)   # ticket / needs_human, live or dry-run, before any issue
        if self.config.mode == "dry-run":
            found = self._images(plan.key, plan.messages, live=False)
            result = replace(result, decision="dry_run_" + plan.action, images=found.dry_run_count)
            self.store.finish(first, result)
            return result
        self.store.finish(first, replace(result, decision="classifying"))
        try:
            found = AttachmentResult()
            if self.store.count_recent_issues() < self.config.max_issues_per_hour:
                # Images go first so the issue can embed their URLs (12.8).
                found = self._images(plan.key, plan.messages, live=True)
            trusted = (plan.clarify is None or plan.action != "needs_human" or plan.typed)
            issue = format_issue(
                first, plan.classification if trusted else None, plan.tier,
                plan.action == "needs_human", self.config.ollama.model,
                case_messages=plan.messages, key_message_ids=plan.key_ids, images=found.images,
                notes=found.notes, clarify=plan.clarify, text=plan.text,
                max_quoted=len(plan.messages))
            if not self.store.reserve_github_call(first, self.config.max_issues_per_hour):
                result = replace(result, decision="rate_limited")
                LOG.warning("Rate limit chat_id=%s message_id=%s", first.chat_id, first.message_id)
            else:
                number, url = self.github.create_issue(issue)
                result = replace(result, issue_number=number, issue_url=url,
                                 reaction_message_id=first.message_id,
                                 reaction_message_ids=(first.message_id,), images=len(found.images))
        except Exception as error:
            LOG.error("Message processing failed chat_id=%s message_id=%s exception=%s",
                      first.chat_id, first.message_id, type(error).__name__)
            diagnostic = self.store.fail_message(first, type(error).__name__)
            result = replace(result, decision="error", classification=None, error=diagnostic,
                             reaction_message_id=None, reaction_message_ids=())
        self.store.finish(first, result)
        return result

    @staticmethod
    def _combine(case_key, results):
        """The window's own Decision: the most urgent group outcome plus every issue and reaction."""
        base = min(results, key=lambda item: _RANK.get(item.decision, 9))
        created = next((item for item in results if item.issue_number is not None), None)
        targets = tuple(value for item in results for value in item.reaction_message_ids)
        return replace(
            base, case_key=case_key,
            issue_number=created.issue_number if created else None,
            issue_url=created.issue_url if created else None,
            error=next((item.error for item in results if item.error), None),
            images=sum(item.images or 0 for item in results),
            reaction_message_id=targets[0] if targets else None, reaction_message_ids=targets)

    def _analyze_case(self, case_key, start, trigger=None, rule_cache=None):
        if not self.store.begin_analysis(case_key):
            return None  # another caller owns this case; one case is analyzed once
        messages = self.store.case_messages(case_key)
        if not messages:
            error = self.store.fail_case(case_key, "EmptyCase")
            return Decision("error", "none", error=error, case_key=case_key)
        if trigger is not None:
            messages = [trigger if item.message_id == trigger.message_id else item for item in messages]
        case = self.store.get_case(case_key)
        first = next((item for item in messages if item.message_id == case["first_message_id"]),
                     messages[0])
        try:
            plans = self._plan(case_key, messages, first, rule_cache if rule_cache is not None else {})
            # The whole partition is stored before anything is sent: a message belongs to one group.
            self.store.plan_groups(case_key, [(plan.key, [item.message_id for item in plan.messages])
                                              for plan in plans])
            results = [self._run(plan, case_key) for plan in plans]
            result = self._combine(case_key, results)
            result = replace(result, latency_ms=max(0, int((self.timer() - start) * 1000)))
            tally = self.store.close_case(case_key, result, result.latency_ms)
        except Exception as error:
            # An unexpected failure must not leave the window `analyzing` with groups half done.
            try:
                self.store.fail_case(case_key, type(error).__name__)
            except Exception as storage_error:
                LOG.error("Case quarantine failed exception=%s", type(storage_error).__name__)
            raise
        self._log_split(case_key, plans, tally)
        self._log(first.chat_id, first.message_id, result, tally.status)
        return result

    def _log_split(self, case_key, plans, tally):
        """Counts only: groups, messages per group, issues, reconciliation. Never any content."""
        LOG.info("Case split case_key=%s groups=%s group_sizes=%s noise_groups=%s chat_groups=%s "
                 "pending_review_groups=%s issues=%s received=%s accounted=%s failed=%s "
                 "reconciliation=%s",
                 case_key, len(plans), ",".join(str(len(plan.messages)) for plan in plans),
                 sum(1 for plan in plans if plan.action == "noise"),
                 sum(1 for plan in plans if plan.action == "chat"),
                 sum(1 for plan in plans if plan.action == "pending_review"), tally.issues_created,
                 tally.received, tally.accounted, tally.failed,
                 "balanced" if tally.balanced else "mismatch")
        if not tally.balanced:
            LOG.warning("Reconciliation mismatch case_key=%s received=%s accounted=%s failed=%s "
                        "case_status=%s", case_key, tally.received, tally.accounted, tally.failed,
                        tally.status)

    def _log(self, chat_id, message_id, result, case_status=None):
        value = result.classification
        dry = result.decision.startswith("dry_run_")
        LOG.info("chat_id=%s message_id=%s tier=%s type=%s confidence=%s "
                 "decision=%s issue_number=%s latency_ms=%s case_status=%s images=%s "
                 "attachments_dry_run=%s",
                 chat_id, message_id, result.tier,
                 value.type if value else None, value.confidence if value else None,
                 result.decision, result.issue_number, result.latency_ms,
                 case_status or CASE_STATUS.get(result.decision, "done"),
                 0 if dry else (result.images or 0), (result.images or 0) if dry else 0)
