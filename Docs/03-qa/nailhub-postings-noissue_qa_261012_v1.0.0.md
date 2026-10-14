---
type: doc
title: "NailHub qa"
owner: AlexChu
created: 2026-10-12
status: current
---

# NailHub qa

Version: v1.0.9. Approved Master and implementation direction delegated by Owner through controller; no additional scope.

## Test Strategy
stdlib unittest offline fakes exercise policy and negative browser behavior; they are not live UI evidence. Existing full listener suite before/after protects attachment and triage baseline.
## Test Cases
| ID | Steps | Expected |
|---|---|---|
| TC-01 | Send exact command in incoming allowlisted private peer | One scan or status/pause/resume; reply only same DM |
| TC-02 | Other ID, outgoing, forwarded, group/channel or bot | Consumed namespace, no command/job/issue |
| TC-03 | Ordinary group and private message with optional feature | Existing group scope retained, ordinary DM ignored |
| TC-04 | 6-10K, 6K-10K, decimal/Vietnamese separators and month/year/biweekly | Exact Decimal conversion, original range preserved in content; upper weekly display rounded down to USD 100 |
| TC-05 | Missing period, single potential amount, assumed tips/commission sum, contradictory bare pay, incomplete daily/hour schedule | needs-human; original clause qualifiers cannot be hidden by short evidence |
| TC-06 | Duplicate +1/US phone, distinct numbers/extensions | Only canonical duplicates removed |
| TC-07 | Concurrent scans, restart before_save/before_approve | Coalesced durable jobs; readback reconciliation, no blind mutation replay |
| TC-08 | Invalid extraction JSON/model failure/address absent | needs-human before Edit/AI; no approval |
| TC-09 | Preview with fake mutation recorder | No Edit/AI/Save/Approve |
| TC-10 | Missing contract/auth/ID, duplicate locator, wrong category/status | blocked before mutation |
| TC-11 | Saved field mismatch or approval readback mismatch | uncertain, no retry |
| TC-12 | Foreground waiters and background model work/failure | No overlap; waiting foreground first; lock released |
| TC-13 | Completed site AI changes title/body; semantic mock raises if invoked | No content verification call; returned title/body saved exactly; salary still comes from original source; Save/readback and Approve checks remain |
## Acceptance Criteria
All offline P0 authorization, preview isolation, evidence validation and crash safety tests pass. Full listener suite passes. Independent reviewer required. Live sign-off additionally requires actual authenticated View -> Edit -> AI -> Save -> re-read -> Approve -> status evidence on Owner-approved test post. No fakes can satisfy that gate.
## Environment and Test Data
> ⚠️ **Đã thay thế (2026-10-14, D-024):** NailHub giờ là service riêng `tg-nailhub`, code ở `nailhub/src/nailhub/`, test chạy từ `nailhub/`. Listener (`tg_triage`) không còn chứa NailHub. Xem `Docs/02-engineering/worker-agent-fleet-1_service-split_261013_v1.0.0.md`. Phần dưới đây là lịch sử, KHÔNG làm theo.
From listener: `PYTHONPATH=src python -m unittest discover -s tests -t . -v`; `python -m compileall -q src tests`. Synthetic data embedded in tests; no credentials or real user fixtures. Optional Playwright extra and dedicated logged-in profile needed for live. No responsive/a11y product changes. CPU latency/timeout measured on ThinkPad after provisioning.
## Security Tests
Numeric peer authorization, prompt-injection/malformed evidence, unsupported selectors and stale original hash all fail closed. Audit stores only structured rationale, never chain of thought.
## Current Result
Controller verified authenticated listing, stable UUID detail and approval-confirmation read-only contract; final adapter mutation flow remains BLOCKED. Production NOT_READY until independent review and authenticated verification.

## Related Documents
- [master](../00-master/nailhub-postings-noissue_master_261012_v1.0.0.md)
- [product](../01-product/nailhub-postings-noissue_product_261012_v1.0.0.md)
- [engineering](../02-engineering/nailhub-postings-noissue_engineering_261012_v1.0.0.md)
- [tasks](../04-tasks/nailhub-postings-noissue_tasks_261012_v1.0.0.md)
- GitHub issue / PR: none, authorized noissue local work.

## Changelog
| Version | Date | Time | Author | Change |
|---|---|---|---|---|
| v1.0.0 | 2026-10-12 | 17:22 | AlexChu (Codex executor) | Derived approved Master; implementation specification |
| v1.0.2 | 2026-10-12 | 18:08 | AlexChu (Codex executor) | Owner range policy and reviewer safety refinements; accessible-field preservation and live evidence limits |
| v1.0.3 | 2026-10-12 | 18:26 | AlexChu (Codex executor) | Runtime source-evidence correction and canonical salary/title validation; mutation gates retained |
| v1.0.4 | 2026-10-12 | 18:35 | AlexChu (Codex executor) | Empty locations never delete fields; source-proven amount period suffix normalization |
| v1.0.5 | 2026-10-12 | 18:45 | AlexChu (Codex executor) | Enforce existing Master contact/address completeness before any UI action |

## Implementation update
Owner runtime amendment: NailHub qwen3.5:9b uses think=false; original-source extraction and proposal validation remain required; no post-AI content checks or semantic call run. CPU preview with the earlier think=true setting timed out; amended runtime verification is pending.

Owner override 2026-10-12: explicit income/salary range uses its weekly upper value rounded DOWN to nearest USD 100 (6K–10K/month -> 2300/week); preserve original range and qualifiers. Single amounts retain cents. No assumption combining commission/tips. Evidence supports exact unique quotes resolved by code or validated Unicode spans. Original-source salary/contact evidence checks remain active. The site AI output is accepted without content fact, numeric or semantic checking under the latest Owner scope override.

Save originates from UI button. An exact pinned-origin/PUT/UUID guard restores ONLY omitted returned writable fields from freshly verified source, rejects conflicting included fields and verifies persisted readback. API-omitted metadata remains UNKNOWN, is not fabricated or sent, and is disclosed as a scope limitation. A separate UI page fresh-read before Save and inside the PUT guard detects source drift; backend atomic compare-and-swap is unverified, so a small residual concurrent-update race remains. Approved publication timestamp is the sole allowed source-backed server transition; all other exposed facts remain exact.


## Runtime validation amendment
Unchanged location suggestions are ignored only on exact current-field equality; conflicts still hold. Invalid literal evidence may receive one extraction correction with bounded path feedback, never fuzzy matching or mutation retry. Salary formatting must equal the full source Decimal range; explicit days/week schedules do not change monthly pay. Site AI output checking is outside scope; title/body returned by the completed site AI flow are accepted directly. Controller's amended preview JSON was valid but held on wrong source quotes; corrected runtime verification and complete live flow remain pending.


Controller's saved model JSON exposed empty ZIP suggestions and a pay-period suffix inside amount_text. Empty locations now mean no suggestion and preserve all fields. A known period suffix may be split from amount_text; period-plus-qualification tails additionally require the entire excerpt to be literally evidenced. Exact full source Decimal range, qualification and conflicting money/period checks still apply.

Validated entity sentences are reduced to every recognized exact current/source-proven proposed salon/location value. Surrounding prose may paraphrase; names/city/state/address/ZIP remain protected. No recognized source entity means extraction evidence correction or hold. These extraction checks do not validate the subsequent site AI output.


Required contact completeness follows the inspected form: salon, address, city, state, ZIP and main phone must be nonempty and valid before Edit/AI. Original source evidence may fill missing location values; otherwise the post needs human review. Tests cover missing/invalid required fields, source-filled address/ZIP pass and zero UI mutation calls for incomplete live posts.


## Owner scope override: site AI output

2026-10-12: after the built-in AI completes, accept its returned title and body directly. Do not run rewrite invariants, semantic verification, instruction prefixes or paragraph cleanup. Original-source salary/contact/address evidence and proposal checks remain active. Fresh-source checks before Save, exact UI payload and saved readback, pause/identity authorization, and approval/status readback remain required. Existing validator/model.verify helpers may remain for compatibility/offline tests but are not called for site AI output. This supersedes earlier rewrite-check requirements.


Structured extraction scope: model extracts original salary, missing location values and a short rationale only. The required compatibility facts keys all request empty arrays (maxItems=0), avoiding unused content-claim extraction after the Owner delegated rewrite quality to site AI. Salary/location/source proposal validation remains unchanged; previously captured valid responses remain compatible. No post-AI model verification runs.


Literal salary excerpt normalization: if amount_text includes original period/qualification text, code extracts its leading full amount/range only when the period prefix matches the claimed period and the entire excerpt is a literal substring of the already validated source evidence. The original full-range Decimal equality, qualification, extra-money and conflicting-period checks still run. Unsupported/invented tails or cherry-picked endpoints remain held. Monthly conversion ignores unused schedule values; day/hour conversion retains strict explicit schedule validation. This avoids another model request for the observed exact source excerpt and adds no post-AI content checking.


Owner start-date policy: only the exact backend default sentinel 0001-01-01T00:00:00 becomes the current processing date at UTC midnight. Backend validates against DateTime.UtcNow.Date; offset-free ISO matches the observed DTO representation. Proposal records old/new values and Owner policy in metadata_changes.startDate; the UI request normalizer restores that authorized date for omitted/null/sentinel input only. Conflicting UI dates or missing audit are rejected. Real past/future dates and null/missing source dates remain unchanged. An endDate not strictly after the repaired start date holds for review and is never changed. Exact persisted startDate and all other saved fields must match before approval. This date repair adds no post-AI content checking.
