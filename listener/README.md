# Telegram Triage Agent

**Documentation version**: v1.0.0 · **Updated**: 2026-10-15 21:12 (Asia/Saigon) · **Owner**: AlexChu

Runtime/operator guide. The single business authority is [Telegram triage TG-01..TG-32](../Docs/01-product/telegram-triage-noissue_business_261015_v1.0.0.md); this README does not define a second policy. Current reviewed runtime baseline is `9746ef2`, deployed on THINKPAD and read-only compared during documentation normalization. Default source configuration remains dry-run; actual installed mode depends on operator configuration.

## Offline verification (Python >= 3.11)

From `listener/`, PowerShell:

```powershell
$env:PYTHONPATH="src"
python -m unittest discover -s tests -t . -v
python -m compileall -q src tests
```

Tests use standard-library unittest, temporary SQLite files and injected HTTP
openers. They do not need packages, sockets, tokens, sessions or a local model.

## Configuration and commands

Use `config/listener.example.toml` as the TOML template. Store deployment config
at `/etc/tg-triage/listener.toml`, root:root mode 644. Do not place it inside the
root-only `/etc/agentfleet` directory. Supply `TG_API_ID`, `TG_API_HASH` through
the environment; live mode additionally requires `GH_TOKEN` and `GH_REPO`.
`TG_TRIAGE_MODE`, if non-empty, overrides TOML; an empty value is ignored.
MTProxy credentials come only from `TG_PROXY_SECRET`, required when proxy host
is configured. A proxy secret key in TOML is rejected. The CLI does not load dotenv.
`check-config` prints only the lengths of credential fields.

```text
python -m tg_triage check-config --config /etc/tg-triage/listener.toml
python -m tg_triage login --config /etc/tg-triage/listener.toml
python -m tg_triage run --config /etc/tg-triage/listener.toml
python -m tg_triage export-csv --config /etc/tg-triage/listener.toml --out /var/lib/agentfleet/tg/review.csv
python -m tg_triage export-csv --db /var/lib/agentfleet/tg/triage.sqlite3 --out /var/lib/agentfleet/tg/review.csv
```

For a dry-run, keep `mode = "dry-run"` or set `TG_TRIAGE_MODE=dry-run`.
Dry-run records classification and decisions; it never initializes or calls the
GitHub client. The owner CSV has a UTF-8 BOM and blank review columns. Formula
prefixes in cells are neutralized with an apostrophe; the file has mode 0600.
CSV export reads only the database path from TOML or `--db`; it does not load
Telegram/GitHub credentials and opens SQLite read-only without recovery or
schema changes. A missing database exits with code 2 without creating paths.

On the ThinkPad, the provisioned environment must have the declared Telegram
dependency and the package (or `listener/src` on PYTHONPATH). The supplied systemd
unit assumes `/opt/agentfleet/listener/.venv/bin/python` can import `tg_triage`.
The prompts (`classify.md`, `case.md`, `topics.md`, `followup.md`) are loaded from `listener/prompts/` in a
source checkout or from `share/tg-triage/prompts/` under the installed Python prefix.
`run` loads `topics.md` at startup and exits if it is missing, so a bad install is not
discovered window by window. Keep the prompts fixed while processing messages.

Run interactive login as `tglistener` before starting the service; phone, OTP
and 2FA prompts do not echo input. The session file is set to mode 600. The
service never prompts for login. It requires a user account, ignores outgoing
and private messages, and processes only configured group IDs.

Ensure `/var/lib/agentfleet/tg` is owned by `tglistener` with mode 700, and
`/etc/agentfleet/tg-triage.env` is root-owned mode 600. Both session and database
contain sensitive data. SQLite files are set to mode 600. Run a single service
instance per database. On Linux, run/login reject data directories unless they
have mode 0700 and belong to the current user. The CLI sets umask 0077. Run and login hold
a non-blocking flock on `tg-triage.lock` in the database directory, acquired
before recovery; a contended lock exits with code 3. Windows skips this OS lock.
CSV export does not perform recovery or change message decisions.

## Provider configuration and telemetry

Business/consent/reasoning boundaries: [TG-30..TG-32](../Docs/01-product/telegram-triage-noissue_business_261015_v1.0.0.md#tg-30--provider-và-privacy-reasoning). Provider fields below are technical examples, not the installed production profile.

```toml
[ollama]                   # server address and the "not sure" threshold
url = "http://127.0.0.1:11434"
confidence_threshold = 0.7

[model]
provider = "ollama"        # or a hosted provider, see below
model = "qwen3.5:9b"
think = true               # turn the model's reasoning on (Ollama "think": true)
timeout_seconds = 600      # reasoning is slow on CPU; measure on the real host before fixing the value
```

- **Provider layer** (`tg_triage/provider.py`): the pipeline talks to a provider with two calls,
  `classify_topics(transcript)` and `analyze_case(messages, forced_type=None)`, both returning the same
  records as before; code validates and decides everything. `OllamaProvider` (built on `classifier.py`) is
  the first one. `classify_topics` accepts the window's messages or an already built transcript string.
- **`provider = "claude"` is not supported yet:** it is a `ConfigError` ("unsupported provider"), as are unrecognized values outside the supported provider list. When it is built, e-mail and phone numbers must be redacted
  before text leaves the machine.
- **Old layout:** `model` and `timeout_seconds` under `[ollama]` are still read when `[model]` does not set
  them; `[model]` wins. Without any timeout the default is 600 seconds. No new dependency.
- **Hosted provider, images, backup, usage and price** (`provider = "deepseek" | "openai" | "anthropic" |
  "compatible"`, see `config/listener.example.toml`). Hosted calls need `cloud_consent = true`; e-mail and phone
  numbers in the text are redacted before it leaves the host.
  - **Images:** a hosted OpenAI-compatible provider (deepseek / openai / compatible) receives images only when
    `cloud_consent` AND `cloud_image_consent` are both `true` (default `false`). They are sent as
    `data:<mime>;base64,...` parts of a user message (mime sniffed from the bytes: jpeg, png or webp; any other
    type, or an `anthropic` provider, never leaves the host). A request with images may be up to 20 MiB; a text-only
    request stays limited to 128 KiB. Without the second consent the case is judged by local Ollama vision.
  - **Backup:** `[model] fallback = "ollama"` (default for a hosted provider; `"none"` keeps the old refusal),
    `fallback_model` (default `qwen3.5:9b`), `fallback_think` (default `true`); the Ollama address and timeout come
    from `[ollama]` / `[model] timeout_seconds`. When the hosted call fails (any provider error, broken JSON, an
    unusable answer after the usual retry, missing key or consent) the very same request runs on local Ollama with
    its local vision. If that fails too the answer is "none" as before (needs-human). The pipeline never sees the
    switch.
  - **Usage:** every model call, errors and fallbacks included, adds one row to the SQLite table `model_usage`
    (`ts`, `provider`, `model`, `role` = `primary` | `fallback`, `outcome` = `ok` or an error code such as
    `rate_limited`, `timeout`, `invalid_json`, `invalid_output`, token counts, `cache_hit_tokens`, `latency_ms`,
    `cost`). Rows are kept 90 days. Message text, images, prompts and keys are never stored or logged.
  - **Price:** `[model.pricing]` (per 1M tokens at peak): `currency` (`USD`), `input_cache_hit` (`0.006`),
    `input_cache_miss` (`0.3`), `output` (`1.2`), `offpeak_factor` (`0.5`), `peak_hours_utc`
    (`["01:00-04:00", "06:00-10:00"]`, Monday-Friday UTC; everything else is off-peak). The defaults are
    deepseek-flash (api-docs.deepseek.com/quick_start/pricing, measured 2026-10-15); openai / anthropic / compatible
    have no built-in price and cost 0 until set. `cost = (cache_hit * hit + (prompt - cache_hit) * miss +
    completion * output) / 1e6`, times `offpeak_factor` off-peak. Local Ollama costs 0.
  - **Console summary:** `agentfleet-usage.json` (schema 1, camelCase) is written next to the database, atomically
    (temp, fsync, replace; mode 0640 and group `tglistener` off Windows) at startup and after a model call, at most
    once per 30 seconds. It holds `today` / `7d` / `30d` windows (local calendar days of the server, with a
    per-model breakdown) and 30 `daily` lines (oldest first, empty days as zero lines).
- **Reasoning stays private:** with `think = true` Ollama returns the reasoning in `message.thinking` next to
  the JSON in `message.content`. Only `content` is parsed (a leading `<think>...</think>` block left in
  `content` is stripped first). The reasoning is never read, logged, stored in SQLite or put in an issue.
- **What the model returns** (topics and single case alike): `category` in `{bug, task, feedback, chat}`,
  `type` (kept for compatibility), `actionable` (bool), `confidence`, `title`, `summary`, `priority`,
  `missing_info` (list) and `reason` (a short why). `verdict` is optional: when absent it is derived
  (`chat` = noise, a ticket category = ticket, or needs-human when `actionable` is false).
- **`reason`** is stored for the reviewer only, in `messages.model_reason` (a new column added with a safe
  `ALTER`; the older `reason` column keeps the code's own reason such as `auto_needs_human`) and exported in the
  `model_reason` column of `export-csv`. It is bounded to 300 characters and is never put in an issue, a log
  line or a GitHub call. The model is told not to put e-mail or phone numbers in it.

## Listening scope and reactions (Engineering Doc B.16)

`[telegram] listen_all_groups` (boolean, default `false`). When `false`, only the
non-empty `chat_ids` list is listened to. When `true`, every basic group and
supergroup the account is in is listened to (private chats and broadcast channels
are ignored, as are outgoing messages), `chat_ids` must be empty or omitted
(otherwise `ConfigError`), and startup replay of queued messages covers all chats.
Enabling it stores content of every joined group in SQLite; decide deliberately.


Text replies and reactions are defined by [TG-11..TG-13](../Docs/01-product/telegram-triage-noissue_business_261015_v1.0.0.md); live mode can send success, clarification, error and quota notices through the existing Telegram client.

## Delivery and failure behavior

Incoming content is committed as `queued` before metadata lookups and queueing.
The worker transitions it to `classifying`. Restart replays queued/classifying
records from stored content, retaining sender and public chat metadata. Each
classification increments `attempts`; startup quarantines queued/classifying
records with attempts >= 2 as error for human review. Invalid UTF-8 ingress
surrogates become U+FFFD; invalid model strings are rejected with a bounded retry.
Unexpected per-message failures are quarantined without classification fields
and the worker continues with the next message. Completed
keys are skipped. Only immediately before calling GitHub does it become `pending`.
The hourly issue quota counts reserved issue-creation attempts (including sibling creates and timeout/5xx outcomes; comments do not consume it)
and timestamps in the future if the clock moves backwards.
GitHub errors are recorded without retry. Old pending records are marked error
on startup and require manual review against their
`<!-- tg:chat:message -->` issue marker before any manual retry. This prevents
automatic duplicate creation after an ambiguous request outcome; it does not
promise delivery of every issue after a crash.
Errors after a durable GitHub reservation warn that an issue may already exist
and require a repository check before any manual retry, including errors while
writing the result to SQLite. Errors before reservation retain the exception
class without blaming GitHub. Failure quarantine clears classification fields.

A single queue worker runs synchronous pipeline work in a thread. SIGTERM and
SIGINT stop taking further queue entries. Incoming messages during shutdown are
still stored as queued without entering the memory queue. The handler remains
registered until Telegram disconnects. The
service waits up to 120 seconds for the active worker, then disconnects Telegram.
Remaining queued content stays durable for restart. If the active call exceeds
the deadline, the service disconnects and exits the process without closing
SQLite under that thread; queued/classifying records replay and pending records
require review on next startup. Systemd allows 150 seconds before forced stop.
Terminal disconnects exit nonzero for systemd restart. Configuration errors,
missing login sessions and bot accounts exit 2, which systemd does not restart automatically.
Startup calls `get_me()` directly: only a missing user means missing login;
RPC and network errors exit 1 so systemd can restart the service.
Queue backlogs and an alive heartbeat are logged without content. Exception
diagnostics include only class names. Transport warnings forward logger name,
severity and exception class, never the library's original message or traceback.
HTTP bodies, headers and raw exception text are never logged. Labels are mapped
exclusively from the fixed code allowlist.
User/model fields disable GitHub references, links and Markdown images; the
defense covers bare `www.` links regardless of case. The
code-generated Telegram source link stays clickable. CSV formula defenses also
cover leading whitespace and full-width formula characters.

Reconnect reference: [Telethon client documentation](https://docs.telethon.dev/en/stable/modules/client.html). The adapter checks the installed client signature before constructing it. Integration evidence is specific to the measured host/version; unit tests do not establish AI accuracy or uptime SLA.

## Storage and operator recovery APIs

See [TG-16..TG-27](../Docs/01-product/telegram-triage-noissue_business_261015_v1.0.0.md) for trusted routing, binding, split, ambiguity and recovery eligibility. `cases`, `groups` and `reconciliation` preserve original accounting; additive `followup_roots`, `followup_jobs`, `followup_operations` and `followup_analysis_retries` hold conversation state and auditable operations. Main SQLite contains original text and context; usage/log privacy does not mean the main database is content-free.

`Store.ensure_followup_job(chat_id, message_id)` is the explicit, single-message legacy seed API. `Store.retry_followup_analysis(chat_id, message_id, configured_repo, expected_notice_id)` is the narrowly guarded terminal-analysis retry API. Neither is a global replay command. Read the eligibility and no-POST-retry restrictions in the canonical rules before operator use. Preserve backups, original IDs and external-operation evidence; never reset original cases to recover uncertain GitHub outcomes.

Profile lookup is performed by `make_public_profile_address_resolver` at notice send/edit time; caches remain transient. See [TG-28..TG-29](../Docs/01-product/telegram-triage-noissue_business_261015_v1.0.0.md) for permitted evidence and fallback.

## Media configuration and packaging

`[aggregation]` controls `window_minutes`, `max_messages_per_case`, `max_messages_per_window`. `[attachments]` controls `max_bytes`, `max_per_case`, `max_documents_per_case`, `max_per_hour`; use the checked [example TOML](../config/listener.example.toml) for current defaults. Image and document counters are separate per-case limits and share upload quota. Uploaded evidence belongs to the configured repository: verify its access before enabling media.

Allowlisted images are signature-checked JPG/PNG/WEBP; document metadata is checked by `attachments.document_extension`. The pipeline downloads into private temporary directories, reuses prepared images for initial vision/upload and deletes temporary files after processing. `gh release upload` uses environment credentials and generated asset names. Followup scope planning reuses stored media provenance; it does not download pixels again. Detailed behavior/limits live in [TG-14..TG-15/TG-22](../Docs/01-product/telegram-triage-noissue_business_261015_v1.0.0.md).

## Documentation drift check

From repository root:

```text
python scripts/check-telegram-triage-policy.py
python scripts/test_telegram_triage_policy.py
```

The checker reads the one trace block in the canonical policy, verifies source/test symbols and Git-LF-normalized runtime/prompt hashes, and flags `REVIEW_REQUIRED` for drift. It never loads policy into the daemon, calls a model or mutates a service. A hash match proves reviewed bytes, not semantic correctness.

For isolated THINKPAD deployment and rollback, see [deploy guide](../deploy/README.md). The [THINKING engineering/research](../Docs/02-engineering/telegram-triage-noissue_thinking-research_261015_v1.0.0.md) distinguishes implemented controller behavior from future pilot targets; [QA acceptance](../Docs/03-qa/telegram-triage-noissue_qa_261015_v1.0.0.md) documents proof criteria.

`[thinking] enabled=true` is the default when absent. Set false and restart the listener to restore the legacy semantic path. Additive `thinking_decisions` and `thinking_decision_events` hold versioned decision metadata; preserve them and the existing external-operation ledger during code rollback. Do not reset reservations to retry uncertain POSTs. The daemon loads packaged `thinking-topics.md`, not the canonical Markdown policy. Model/consent/auth settings remain unchanged.

To run a small synthetic check against the configured model while using memory SQLite and fake GitHub/Telegram tools:

```text
python scripts/telegram-thinking-model-smoke.py --config /etc/tg-triage/listener.toml
```

Run from repository root with the listener venv/user and the reviewed installed wheel or source on PYTHONPATH. This opt-in command uses model resources under existing consent. It does not open the live database or Telegram session, and prints only outcome metadata. It is not a model-accuracy benchmark. Semantic deadlines and current limitations are defined in TG-19/TG-30/TG-32, avoiding a second policy source here.

For reviewed image-caption fixtures, select `--case image_caption_context --image-path /path/to/reviewed.jpg`; optional `--sources-fixture /path/to/ignored-sources.json` supplies the three source texts/metadata. The JPEG and source export remain local/ignored. The report includes image SHA/transport byte counts and sanitized generated wording so semantic grounding can be reviewed. No real upload, issue, notice or live DB is used. See the QA document for fixed acceptance labels.

Technical vision-partition failures can create `context_pending` notice records for unclassified source components. These are not ticket-root authority. Exact sent-notice/pending-source proof routes a reply into fresh intake; see TG-12/TG-16. Preserve original case/group accounting and do not reset reservations to force a retry.

## Changelog

| Version | Date/time (Asia/Saigon) | Author | Change |
|---|---|---|---|
| v1.1.0 | 2026-10-15 21:47 | Codex / owner AlexChu | THINKING feature switch, additive journal operations and isolated configured-model smoke. |
| v1.0.0 | 2026-10-15 21:12 | Codex / owner AlexChu | Keep technical operations; replace evolving duplicated business descriptions with canonical rule links. |
