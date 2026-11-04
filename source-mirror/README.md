# Source Mirror operations

This worker is isolated from all existing workflows. The independent worker is installed on the target host and published four real Scratch articles on the rollout day; all four cards passed independent desktop and mobile checks. Two publications were manual canary/recovery and two were scheduled catch-up. Its systemd service is enabled and resumed after oversized article cards were corrected; four publications and the daily quota guard were verified on the partial rollout day; a full unattended calendar day is not yet proven. Unit tests do not establish production behavior.

Requirements: Python 3.10+, Playwright with Chromium, Pillow, timezone data (`tzdata` on systems without an IANA database). Install into a dedicated virtual environment only. Copy `config.example.json` to a private configuration file and choose a dedicated state root. The systemd unit, data root, profile and browser cache are dedicated to this worker. A newly created database starts paused.

From this directory, with a dedicated environment:

```sh
python -m unittest discover -s tests -v
python -m source_mirror --config /etc/nailhub-source-mirror/config.json status
python -m source_mirror --config /etc/nailhub-source-mirror/config.json crawl
python -m source_mirror --config /etc/nailhub-source-mirror/config.json pause
python -m source_mirror --config /etc/nailhub-source-mirror/config.json resume
python -m source_mirror --config /etc/nailhub-source-mirror/config.json once
python -m source_mirror --config /etc/nailhub-source-mirror/config.json run
```

A publish attempt reserves quota before a potentially mutating action. An interrupted or ambiguous attempt blocks all further publishes, including across midnight. Inspect the actual production admin and guest article before resolving; never rely on a timeout or a toast as proof. To record a confirmed published article, supply its actual Vietnam publication date and evidence reference:

```sh
python -m source_mirror --config /etc/nailhub-source-mirror/config.json reconcile 1 --published-url https://nailhub.ai/example --published-day 2026-10-28 --evidence 'admin and public verification reference'
```

Only after verifying no article or draft was created, release the attempt:

```sh
python -m source_mirror --config /etc/nailhub-source-mirror/config.json reconcile 1 --confirmed-not-created --evidence 'inspection reference'
```

Retain database, snapshots and profiles. Never restore an older database over newer attempts; that can lose publication history. Do not put credentials in configuration, command arguments, logs, or evidence. A full day of real verified publications is separate from the one-article canary and from unit tests.

## Current operation and limitations

On the host, run the CLI from the independent installation with its dedicated virtual environment and config. Check `systemctl status nailhub-source-mirror.service` and use the CLI `status` to inspect the database; `pause` and `resume` change only this worker. Avoid starting another `once` while the scheduler is active. `status` rebuilds derived per-article and daily JSON reports when it can acquire the worker lock. After a crash or failed export, SQLite remains authoritative and reports may be stale until rebuilt.

The 08:00, 12:00, 16:00 and 20:00 slots use Asia/Ho_Chi_Minh and permit safe same-day catch-up, with a four-publication ceiling including the canary. An unresolved attempt blocks all future publishing until reconciled. The source adapter accepts a narrow observed layout; unsupported articles and galleries over ten images are held. It follows observed listing order, not a verified source-date sort. Its bounded archive cursor can miss articles when pages shift; it does not prove exhaustive coverage. Uploaded JPEGs are re-encoded, so byte hashes differ even when visual comparison matches.

The v11 publisher compares effective bold and italic styling and link targets by character, allowing the editor to split equivalent inline markup while preserving text and structure. After a pre-submit hold, verify the actual admin state before reconciliation; an uploaded media file alone does not establish that a blog was created.

The previous publisher supplied source-derived English in both language slots. The localized publisher requires an approved immutable VI revision, preserves English source, fills each locale's Meta fields, adds shared hashtags, and verifies the guest Vietnamese article/card. A valid published article may be held for reconciliation if a feed variant is missing or delayed by ranking/cache. All four original rollout-day cards passed desktop and mobile checks; this is distinct from verification of the localized publisher or a full unattended-day test.

## Editorial topic selection

Before generation or publication reservation, the worker scans each ready snapshot's title, URL, body and media alt text. It prefers salon-business topics at 08:00/16:00 and luxury at 12:00/20:00; other non-Halloween topics are valid fallback. Halloween is deferred while a non-Halloween candidate exists, and limited to one published post per seven Vietnam calendar days including today (default; `topics.halloween_max_per_7_days` may be set to `0` for none). If history cannot be classified, Halloween fails closed. These keyword rules are heuristic, not a complete editorial classifier. US tax/payroll/1099/W-2 and related high-stakes content receives a durable `topic:` hold for qualified human review; crawls cannot automatically clear it. Exact domain accuracy cannot be guaranteed by keywords, so any newly added source adapter for high-stakes content must default to hold/review. A held or unavailable topic can leave a slot empty rather than weaken the quality gate. Slot preferences do not create inventory: source adapters determine which topics can actually be discovered.


## Required AGY localization and quality gate

Every future `once`/scheduled slot requires `localization.enabled=true`, model `gemini-3.8-flash-medium`, effort `medium`, and an accessible Unix socket. No English fallback is permitted when authentication, inference, parsing or quality checks fail. The worker examines at most eight ready candidates per call, before creating any publication intent. A rejected candidate gets a durable `localization:` hold reason; crawling cannot clear that hold. Other ready candidates may be examined, but a held article is not automatically retried or published. Manual review and a separately authorized release procedure are needed before reconsidering it.

The independent AGY bridge runs as its own unprivileged Linux account, with private HOME/state and a dedicated subscription login. It must not run as root or receive the worker's PROD browser/config secrets. The operator configures a private runtime directory, socket group permissions, exact allowed worker UID and systemd restrictions. `agy-broker.example.json` contains deployment-specific paths/UID that must be checked on the actual host. Run it with `python -m source_mirror.agy_broker --config /etc/nailhub-source-mirror-agy/config.json`. The bridge accepts only one bounded prompt request per connection from the configured peer UID; model/effort/CLI flags are fixed by code, requests are serialized, and no shell is used. It does not implement a formal harness reviewer role. The content evaluator is a fresh text inference using the same Owner-selected model, independent of the generator's session/output self-assessment; this reduces shared-session bias but is not an independent-model or certified human assessment.

Generation preserves ordered HTML/media blocks, links, numeric facts, protected linked names and glossary terms. A second fresh call evaluates the entire source and candidate using the documented MQM categories. The application rejects any generator uncertainty, evaluator uncertainty, nonzero Minor/Major/Critical, inconsistent counts/issues, non-boolean success flags, schema/markup changes or known technical ambiguity. `curing after both layers` is explicitly held without inference. Missing essential technical information/conflicting product names must be reported rather than silently corrected. Automated PASS is not proof that a technique is professionally safe; the gate is deliberately conservative.

Each attempt writes a new `articles/<id>/localization/revision-*/` outside the immutable snapshot manifest. It retains generation/review envelopes and usage, candidate, source/glossary/prompt/output hashes, gate reasons and frozen bilingual payload. Publishing revalidates the exact approved payload immediately before editing and submitting. Both language tabs are checked again before Create, and a native-request guard aborts mismatched locale content/meta/tags/source/status/date before allowing the UI request onto the network. The worker never issues an API write directly. Failures after the publish intent remain conservatively `unknown` and require actual admin/guest reconciliation, including an aborted native request; they are never automatically retried as if no article existed.

Auth/quota errors hold candidates before reservation; they are not proof of a missing PROD article and do not consume publication quota. A successful SSH trial under a person's account does not prove the dedicated service account can run unattended. Verify a real broker call and quality gate under the actual worker UID, test rejection/ambiguity paths, then verify one authorized localized publication using both admin locale tabs, persisted Meta/tags and guest VI/card rendering before relying on future slots.

## Official-source handoff — 2026-10-30, Vietnam time

THINKPAD's Source Mirror worker and official autostage timer were active after the reviewed rollout. The publication and autostage policies allow CDC/NIOSH and IRS; the SBA business-plan source containing an incorrect quarterly calculation is excluded. The stock-image media policy, comparison policy and publication policy expire at **12:10 on 2026-10-31**. Expiry holds official work; automatic renewal has not been implemented. Check live service state and policy dates again when taking over.

The CDC/NIOSH candidate in the live queue was `official_ready`, with evidence record `18f76c25f28c742c77b5fd4f0be0bcb0aa69bb0e6c751481aeeef434409bf30d` and target slot **20:00 on 2026-10-30**. Automated factual review reported PASS with no recorded errors or uncertainties. This is not a published article: the scheduler must still revalidate the source, rights, payload and duplicate status, submit, and verify admin and public readback. Do not change the existing QA Draft or infer success from a timer or receipt. An interrupted or ambiguous publication must be reconciled against admin and public views before any retry.

SBA's alternative legacy planning page is **not deployed or enabled**. Its isolated THINKPAD stage is `/tmp/official-sba-legacy-stage-20261030`; source snapshot `ba25d4cdc2905fbfa0153f550c76f7abeb2ae3aeb3bfafba86814e1aa34a3c69` has the visible update date 2025-11-13 and seven in-text links. Draft `4b8311a55dbac7da1b0bb1876333e949894bac5e05433b642360af3fd0e58fbe` passed automated MQM. A separate factual trial produced record `fdc3530eb0060f0169b95f14fa1d0a7f78db5f3a3aa36f44ac03a227d63092e5` with AI factual PASS and status `ELIGIBLE_FOR_NATIVE_VERIFICATION_ONLY`; it is not publication approval, and its 20:00 target belongs to a test rather than the live queue. The prototype also binds three SBA example buttons to their original URLs and labels them as examples; it does not assert permission for third-party files. Its separate trial media policy is `/etc/nailhub-source-mirror/official-media-policy-sba-legacy-trial.json`; it is not the live media policy. Temporary `/tmp` evidence may disappear and must not be treated as a durable backup.

Before enabling SBA, independently verify the exact page/date, all seven in-text links and three example buttons, factual meaning and jurisdiction, the source's reuse rights, and the selected image's rights and visual comparison. Run the staged translation, factual gate and native editor checks, hold any uncertainty, and review the final staged changes independently. The Pexels asset page returned 403 on THINKPAD; do not treat the general Pexels licence or the passage of time as proof that this particular image can be renewed.

The CDC candidate binds the digest of the **entire** live media policy. Adding SBA or extending expiry in that file before the CDC slot invalidates the candidate and causes HOLD. After the CDC outcome is reconciled, prepare and review a root-owned policy update, create fresh evidence for any pending candidate using that policy, and verify candidate/version and duplicate handling before changing the live file. Do not overwrite old SQLite state or silently clear HOLD/unknown attempts. The visual policy backup is `/etc/nailhub-source-mirror/official-image-comparison-policy.pre-3-source-20261030.json`; the reviewed activation and lock-fix backups are `/var/backups/nailhub-official-activation-20261030` and `/var/backups/nailhub-official-lockfix-20261030`. Restore code/policy only under the worker lock after stopping dependent services, preserving recorded attempts and checking hashes and service health. A failed restore must leave the affected timer stopped and be reported as incomplete.
