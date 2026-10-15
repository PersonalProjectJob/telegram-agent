# QA: Telegram triage THINKING

**Version** v1.1.0 · **Updated** 2026-10-15 23:29 Asia/Saigon · **Owner** AlexChu · **Executor** Codex

## Related Documents

- [Master scope](../00-master/worker-agent-fleet-1_master_261007_v1.0.8.md)
- [TG-01..TG-32](../01-product/telegram-triage-noissue_business_261015_v1.0.0.md)
- [Engineering and research](../02-engineering/telegram-triage-noissue_thinking-research_261015_v1.0.0.md)

## Test Strategy

Hosted repair regression uses the real CloudProvider/Pipeline with fake network/tools: malformed JSON or parser-invalid output then valid partition must use two cloud calls with identical bundle/schema; network failure must use cloud then configured local; two malformed outputs must stop after two calls with honest scoped clarification. Legacy THINKING-off fallback remains covered. Installed malformed-image failure is retained; successful reruns do not erase it or establish model accuracy.

Test the production Pipeline, Store and provider entry paths with fake GitHub/Telegram tools and temporary SQLite. The plan was recorded before implementation in the task's durable `.context/thinking-runtime-plan.md`; this artifact captures acceptance cases. Existing safety suites run with THINKING enabled by default. A separate opt-in smoke uses the configured real model, synthetic text and fake external tools; it never opens live SQLite or Telegram session. Independent reviewer/verifier and deployment controller report their own machine output; this document does not grant itself a release PASS.

## Test Cases

### TC-01: Three messages, one request

| Property | Value |
|---|---|
| Priority | P0 |
| Type | Happy path |
| Precondition | Real default classifier prompt; fake provider response; isolated Store |

**Steps**: send three untagged messages describing one broken save button; close the window.

**Expected Result**: one issue; one reply to source 1 with its actual canonical link; decision planned before create, verified afterward.

### TC-02: Three independent requests

| Property | Value |
|---|---|
| Priority | P0 |
| Type | Happy path |
| Precondition | One window, three distinct work requests |

**Steps**: send save-button bug, date-filter request and stop-opt-out request; classify three partitions.

**Expected Result**: three issues and replies to sources 1/2/3 with matching links; no tag required.

### TC-03: Missing information

| Property | Value |
|---|---|
| Priority | P0 |
| Type | Happy path |
| Precondition | Work request recognized but screen/result unknown |

**Steps**: return needs_human with a focused missing-screen question.

**Expected Result**: tracking issue and group reply asking the missing question, bot xưng Em; no silent approval queue.

### TC-04: Screenshot context and restart

| Property | Value |
|---|---|
| Priority | P0 |
| Type | Happy path |
| Precondition | Synthetic sources 429/431 work, 430/432 captionless media in one original ticket |

**Steps**: reply “cần lên 2 ticket”; reply source 429 “đây là ticket 1”; add seven history turns; restart SQLite; reply source 431 “đây là ticket 2”.

**Expected Result**: confirmed bindings survive trim/restart; original issue plus one sibling; 430/432 explicitly unassigned; source 431 only in sibling; no repeated request for known ticket-1 anchor. Duplicate ingress does not create again.

### TC-05: Rollback and durable trace

| Property | Value |
|---|---|
| Priority | P0 |
| Type | Happy path |
| Precondition | `[thinking] enabled=false` |

**Steps**: process a normal case; inspect SQLite and provider output cap.

**Expected Result**: legacy semantic path/output limits; existing reservations and historical decision records remain; no new THINKING journal entry.

### TC-06: Budget, queue and stale state

| Property | Value |
|---|---|
| Priority | P0 |
| Type | Edge case / Performance |
| Precondition | Invalid provider outputs; occupied model lock; two writers on same decision key |

**Steps**: exhaust cloud retries then attempt fallback; expire a lock wait; submit a stale decision version; exhaust text/read bounds.

**Expected Result**: at most two actual model attempts shared across retries/fallback; no HTTP attempt consumed during queue-only expiration; stale CAS rejected; retained confirmations and safe clarification fallback.

### TC-07: Authority and ambiguous effects

| Property | Value |
|---|---|
| Priority | P0 |
| Type | Edge case / Error handling |
| Precondition | Existing followup safety fixtures |

**Steps**: run conflicting sender, cross-root/cross-chat, invented source ID, third-request-as-ancillary, reserved POST crash and notice-failure cases.

**Expected Result**: no invented authority, hidden third request or duplicate POST; confirmed anchors survive; ambiguous outcome requires reconciliation. Unknown profile uses Anh/Chị.

### TC-08: Existing DeepSeek think=false reaches the wire

| Property | Value |
|---|---|
| Priority | P0 |
| Type | Error handling / Provider contract |
| Precondition | Configured hosted thinking false; fake HTTP connections |

**Steps**: call bounded transport for DeepSeek, OpenAI, compatible, Anthropic and Ollama; capture serialized request bodies.

**Expected Result**: DeepSeek has exactly `thinking: {type: disabled}` and `temperature: 0`; the other four have no new top-level `thinking`/`temperature` field. Provider/model/consent and production20/40 budgets stay unchanged. No reasoning_content is read or persisted. Run default20/40 configured-model smoke with the original fixed labels; diagnostic30/60 experiments must never be reported as production behavior.

### TC-09: Intake essentials versus debugging extras

| Property | Value |
|---|---|
| Priority | P0 |
| Type | Happy path / Missing information |
| Precondition | Known module/setting, observed contrary behavior and desired correction supplied |

**Steps**: run the unchanged fully specified three-request model fixture; compare with a genuinely vague “nút này lỗi” request.

**Expected Result**: the clear existing-setting bug is actionable without demanding platform/logs/full reproduction; vague object/behavior gets a focused question. Do not change confidence thresholds, fixture labels or source partition assertions. Do not invent any unknown debugging facts.

### TC-10: Image-caption regression acceptance

Production-path fixtures in `listener/tests/test_thinking_image_context.py` cover: captioned image + long backend Q&A + ack; lone caption image; fair marked excerpts with all IDs/reply refs; uncertain image group beside independent task/chat; image-reply partition contradiction; unavailable pixels/pure questions; cloud-image-consent false; explicit command failure/type; exact pending notice/source proof and fresh reply intake preserving old rows/dedup. Existing image tests now check text-only classification with unavailable-pixel markers and scoped failure instead of asserting a whole-case fallback ticket.

Opt-in actual-image smoke (fake GitHub/Telegram/upload, memory SQLite):

```text
python scripts/telegram-thinking-model-smoke.py --config /etc/tg-triage/listener.toml --case image_caption_context --image-path /path/to/reviewed.jpg --sources-fixture /path/to/ignored-sources.json
```

The source fixture is optional; exact historical source export and pixels remain ignored/local and are never committed. Assert one actionable task/feedback for source442, matching reply, grounded Salon/visibility/selected Chat only context, no unneeded design-layout/deadline/ordering questions, source443 Q&A and444 ack classified chat/noise. Actual provider transport must receive the exact image SHA under existing consent and remain within8000 text bytes. Reports expose sanitized generated wording, JSON diagnostics and counts, never private reasoning. Run all four earlier model cases too. No real tickets/notices/live DB replay. Code tests alone do not establish vision grounding; failed semantic previews remain evidence until corrected package proof passes.

## Performance Benchmarks

Semantic model work has a shared 40-second deadline including model-lock queue, bounded DNS, TCP/TLS and response reading; each attempted model request gets at most 20 seconds of remaining time. Two attempts total, three logical scoped retrieval batches and conservative 8000 UTF-8 text bytes per request. Source-role decisions cap 512 output tokens; complete topic/followup drafts retain configured output limits. Images have provider-specific token costs outside the text bound. Attachment/GitHub/Telegram operations keep their existing timeouts: **40 seconds is not an end-to-end ticket/reply SLA**. Assert budgets with deterministic clocks plus real queue/DNS stop tests; small live-model smoke is not p95 or accuracy measurement.

## Security Tests

Verify evidence IDs/sender/root/repository before tools; sanitize generated fields; journal excludes raw transcript, bio, secrets and private reasoning. DNS accepts only public pinned addresses for hosted providers, local Ollama stays loopback. The model-smoke runner exposes no real GitHub/Telegram client. Run existing consent/redaction and profile tests unchanged.

## Test Data Requirements

Synthetic fixtures in `listener/tests/test_thinking.py` and existing `test_followups.py`; recognizable short request phrases reproduce the user's reported failure without copying private profile/account data. Screenshot IDs are fixture labels, never production replay inputs. The opt-in model smoke uses synthetic IDs 901..904. No claim that the proposed 60-conversation human-labelled corpus has been completed.

## Environment Setup

From `listener`, set `PYTHONPATH=src` (PowerShell: `$env:PYTHONPATH='src'`) then run:

```text
python -m unittest tests.test_thinking -q
python -m unittest discover -s tests -t . -q
```

From repo root:

```text
python scripts/check-telegram-triage-policy.py
python scripts/test_telegram_triage_policy.py
```

Opt-in on THINKPAD, with the configured listener user/venv and the reviewed package/source:

```text
python scripts/telegram-thinking-model-smoke.py --config /etc/tg-triage/listener.toml
```

## Acceptance Criteria

- [ ] Default THINKING wire has one USER context bundle containing captions, all source IDs/reply edges and all permitted images; multiple-image order maps explicitly to source IDs for local and cloud. Actual-image smoke reports `single_context_bundle=true`.
- [ ] All P0 deterministic cases and full listener regression pass on reviewed source.
- [ ] Independent code review and verification approve current source.
- [ ] Real configured-model smoke passes; report sample size and limitations.
- [ ] Wheel contains all five prompts and both THINKING modules; installed bytes match commit.
- [ ] Listener active, additive SQLite tables initialized, NailHub/shared modules unchanged.
- [ ] Canonical rule trace checker and negative tests pass.

UI/browser/responsive/a11y changes: none. No merge authorization inferred from a PR update.

## Changelog

| Version | Date/time | Author | Change |
|---|---|---|---|
| v1.1.0 | 2026-10-15 23:29 Asia/Saigon | Codex / AlexChu owner | Image-caption/context, privacy, scoped failure and fresh-intake regression with actual-image proof criteria. |
| v1.0.2 | 2026-10-15 22:26 Asia/Saigon | Codex / AlexChu owner | Intake essentials and DeepSeek sampling wire regression; fixed-label smoke remains required. |
| v1.0.1 | 2026-10-15 22:19 Asia/Saigon | Codex / AlexChu owner | Wire-level existing think=false contract and fixed-label default-budget verification. |
| v1.0.0 | 2026-10-15 21:39 Asia/Saigon | Codex / AlexChu owner | Capture production-path acceptance, model-smoke isolation and honest budget limits. |
