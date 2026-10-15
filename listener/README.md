# Telegram Triage Agent

Implements Engineering Doc B6. Defaults to dry-run; production repository issue
creation remains subject to CTO approval in the Engineering Doc. This change
does not deploy or enable the service.

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
The prompts (`classify.md`, `case.md`, `topics.md`) are loaded from `listener/prompts/` in a
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

## Model judgment and the `[model]` section (Assistant definition 12.12)

The model decides what is chat and what is a bug, task or feedback. No keyword list, word list or regex
in code makes that call: the word lists `ACK_WORDS`, `CONTACT_LABEL_WORDS`, `ADMIN_KEYWORDS`,
`PRODUCT_KEYWORDS` and the functions built on them (`is_admin_chat`, `is_contact_or_courtesy_only`, the
old `_gate` hard block) are gone. Tier 1 (`rules.evaluate`) now only parses an explicit `/command` and
sets aside a message with no letter at all (empty text, emoji only, digits only) or an empty command. A
short message such as "ok" is no longer dropped by code: it goes to the model with the rest of its window
and the model decides. `[telegram] min_text_length` is still accepted but no longer used.

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
- **`provider = "claude"` is not supported yet:** it is a `ConfigError` ("model.provider: 'claude' chưa hỗ trợ,
  hiện chỉ có 'ollama'"), as is any other value. When it is built, e-mail and phone numbers must be redacted
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
  `ALTER`; the older `reason` column keeps the code's own reason such as `pending_untagged`) and exported in the
  `model_reason` column of `export-csv`. It is bounded to 300 characters and is never put in an issue, a log
  line or a GitHub call. The model is told not to put e-mail or phone numbers in it.
- **Rules that stay in code** (`Pipeline._safety`, see "Issue creation rules (12.13)" below): a `/command`
  with content or a Telegram tag forces an issue, a confident bug / task / feedback verdict makes one, a
  question is never a ticket, and a group with no product content is blocked; e-mail and phone numbers are
  redacted before an issue is rendered; the hourly issue limit; the reconciliation of every message; dry-run
  never calls GitHub.
- **Not verified here:** how well qwen3.5:9b reasons over real Vietnamese chat, how long a window takes on the
  ThinkPad CPU with reasoning on (set `timeout_seconds` after measuring), and whether Ollama honours the JSON
  `format` together with `think: true` on the installed version. Only fakes were run.

## Listening scope and reactions (Engineering Doc B.16)

`[telegram] listen_all_groups` (boolean, default `false`). When `false`, only the
non-empty `chat_ids` list is listened to. When `true`, every basic group and
supergroup the account is in is listened to (private chats and broadcast channels
are ignored, as are outgoing messages), `chat_ids` must be empty or omitted
(otherwise `ConfigError`), and startup replay of queued messages covers all chats.
Enabling it stores content of every joined group in SQLite; decide deliberately.

The listener never sends text to a group. With its own user account it only adds
reactions (Assistant definition 12.10, 12.11). Only a group that became a ticket is answered, and
a ticket follows the issue creation rules below; chat and pending-review groups get none:

- **Heart:** the first message of every group whose verdict is `ticket` or `needs_human` gets a
  heart before its issue is created, in both dry-run and live. A message starting with `/bug`,
  `/feature`, `/question`, `/task` or `/feedback` (including `/bug@BotName`, any case) still gets its
  heart on receipt; the `reacted_received` flag in SQLite records it, so the same message is never
  hearted twice. Commands still decide the issue type; the heart no longer depends on them.
  (A `/question` gets no heart on receipt: it is never a ticket, 12.13.)
- **OK hand:** in live mode only, after a GitHub issue is created (issue number recorded) the first
  message of that group gets an OK hand (it replaces the heart, since an account holds one reaction
  per message). One per issue group; the `reacted_ok` flag keeps it from repeating, restarts
  included. Dry-run never adds the OK hand.
- **None:** `noise`, `chat` and `pending_review` groups, and follow-up messages that join a group which
  already has an issue.
  (A command message still gets its receipt heart before the listener can know it is a follow-up.)

The pre-issue heart is placed by the pipeline thread through a blocking call to the event loop
(`telegram_io.make_reactor`, bounded by the 10 s reaction timeout plus 5 s). A reaction failure logs
a WARNING with chat id, message id, emoji and the exception class name only (no message text, no
exception text, no token), is attempted once and never retried, is not recorded as sent, and never
changes the pipeline result or stops the worker.

## Case aggregation and image attachments (Assistant definition 12)

One case creates exactly one GitHub issue. `case_key` is `<chat_id>:<first message id>`;
the `cases` table has a UNIQUE constraint on it, and only one caller can ever win a case
(`open` -> `analyzing` -> `done` / `noise` / `error`). Cases are persisted in SQLite
(migration adds the `cases`/`attachments` tables and `messages.case_key` and metadata
columns with safe `ALTER`, so older databases still open).

```toml
[aggregation]
window_minutes = 2        # 0 = no waiting: every message is its own case
max_messages_per_case = 20
max_messages_per_window = 40  # 12.9: longest chunk of a closed window sent to the model at once

[attachments]
max_bytes = 10485760       # per image
max_per_case = 5           # 0 = never download or upload images
max_per_hour = 60          # counts each `gh release upload` (images and documents), separate from [github] max_issues_per_hour
max_documents_per_case = 5 # 12.14: documents per issue, counted separately from images; 0 = documents off
```

Missing sections use these defaults; a value of the wrong type or a negative value is a
`ConfigError` naming the field.

- A reply joins the case of the replied message (even after the window). Otherwise a message
  joins the latest case of the chat whose last message is within `window_minutes` and that
  has the same sender (including a forwarded message). A full case accepts nothing more.
- A command (`/bug`, `/feature`, `/question`, `/task`, `/feedback`) never waits: its case is analyzed when
  it arrives, together with the earlier messages already gathered. Other cases are analyzed by
  the worker (every 15 s) once `last message + window_minutes` has passed.
- When a window closes, the model splits it into topics (see "Topic split" below) and every
  ticket / needs-human topic becomes its own issue. `prompts/topics.md` holds the prompt.
  (`prompts/case.md` and `Classifier.analyze_case`, the one-verdict-per-window request of 12.2,
  are still shipped for classifiers that cannot split topics, e.g. single-message test doubles:
  the pipeline then treats the whole window as one group, as before.)
- A message arriving after its case is `done` is stored on that case (`decision = followup`)
  and never opens a second issue. A new explicit command from the same sender still opens a
  new case. Posting a follow-up comment on the issue is not implemented yet.
- After a restart, a case that was `analyzing` and already called GitHub becomes `error`
  and is never sent again; one that had not called GitHub is analyzed again (at most twice).

Images and a request sent before or after them share the same two-minute inactivity window,
including forwarded images from the same sender. Explicit replies take precedence. The Ollama
provider receives validated JPG/PNG/WEBP bytes on individual user messages carrying their exact
Telegram message IDs; both topic splitting and legacy case analysis support images. The same
private files are reused for issue uploads and deleted after processing, including failures.
Documents remain attachments only and are never sent to the model. Existing attachment size/count
limits also bound vision. No OCR library or extra model request is required.

A photo with no request stays `pending_review` without creating an issue. Missing/invalid media,
vision failure or an ambiguous image/request partition produces one `needs_human` issue and an
owner clarification in the group. An image-bearing case exceeding the model chunk limit also
asks for clarification rather than separating an image from its request. Text-only commands still
run immediately; a command cannot wait for an image that has not arrived yet. Model latency is
additional to the two-minute wait and depends on the host; benchmark the actual Ollama model.
When vision returns one valid whole-case `needs_human` group, its sanitized wording and specific
questions are preserved in the issue and owner notice. Failed/incomplete partitions use a fixed
clarification instead; the code never forces an uncertain visual judgment into a confident ticket.

### Topic split (Assistant definition 12.9)

A window can hold several unrelated matters ("quét không được" and "không đăng nhập được").
When it closes, the model returns `{"groups": [{topic, message_ids, category, type, actionable, title,
summary, priority, confidence, missing_info, reason}]}` (12.12; `verdict` optional) and **code** verifies and
decides everything:

- Messages of a window are sorted by time. A window longer than `max_messages_per_window` (40) is cut
  into consecutive chunks; each chunk is sent, verified and turned into issues on its own.
  (`max_messages_per_case` = 20 caps a window by default, so chunking only matters once it is raised.)
- **Exact partition:** every message id of the chunk must appear exactly once across the groups. A
  missing, duplicated, boolean or unknown id means one retry (a second request); a second failure, a
  model error or unusable JSON turns the whole chunk into ONE `needs-human` issue holding every message of
  the chunk. No outcome loses a message and no foreign id ever reaches an issue.
- **Explicit commands:** a group that contains `/bug`, `/feature`, `/question`, `/task` or `/feedback`
  takes that type (the first command in time order if there are several; `/feature` and `/question`
  are `feedback`); the model only words title and summary. A command group stays an issue even if the
  model calls it noise, unless the model's `category` is `chat` (see below). If the model lists
  `missing_info`, it becomes `needs-human` but keeps its type label. A lone command never calls the model.
- **Tier-1 noise:** only a message with no letter at all (emoji only, digits only, empty) or an empty command
  is never sent to the model or put in a ticket group. It is stored with `decision = noise`,
  `reason = rule_noise` (a model noise group gets `model_noise`). Greetings and short replies ("ok",
  "cảm ơn") are NOT set aside by code since 12.12: the model sees them and decides. A photo or a command is
  never set aside this way, so its image or type still reaches an issue.
- **Not enough information:** `missing_info` non-empty on a confident ticket, a failed model call, a
  failed partition or unusable wording gives an issue labelled `needs-human` (when the group is tagged or
  has a command, see below) with a "Cần làm rõ" section listing the (sanitized, at most 5 x 200 characters)
  `missing_info`, or a fixed line when there are none. The type label is kept only when it is trustworthy
  (explicit command, or a confident ticket that still lacks information). A `needs_human` VERDICT or a
  confidence below `[ollama] confidence_threshold` is the model saying "not sure": since 12.13 that is
  `pending_review` when nobody tagged or used a command, and a `needs-human` issue when somebody did.
- **One issue per group, one group per message:** the issue quotes all messages of its group in time
  order (no 10-message cap) and nothing of any other group. Titles, summaries and questions go through the
  same sanitizer as before; model text is never executed or used as a label. Trusted bug, feedback and
  task issues (including typed needs-human issues) use category templates from optional model
  `details`: bug reproduction/expected/actual/evidence, feedback need/user story/draft acceptance/context,
  or task work/scope/input evidence/DoD. Images and documents appear in bug/task evidence or feedback
  context. Missing required fields show "(chưa có thông tin)" and append fixed clarifying questions
  after model questions (deduplicated, at most 5); these automatic questions never change labels.
  Task DoD always ends with the requirement to attach result evidence. Untyped issues keep the generic
  body. Detail strings are sanitized/redacted and capped at 500 characters; lists at 10 x 200.
- **Keys:** each group is keyed `<chat_id>:<smallest message id of the group>` (UNIQUE in the new
  `groups` table), so two groups of one window can never collide and a restart cannot create one
  twice. The plan is stored before anything is posted; `messages.group_key` is set only while it is
  empty, so a message cannot belong to two groups. Images go to the release `attachments-<group key>`.
- **Quota and failure isolation:** every group's GitHub call is reserved separately (one unit of
  `max_issues_per_hour` each, and the existing F5/F12 `github_called_at` rules, now per group). A failing
  or rate-limited group is quarantined alone; the other groups of the window still post. After a crash,
  a window that never reached GitHub is planned again from scratch; one that may have posted is never
  re-planned: its posted groups keep their issue, the rest become `error` for a human.
- **Reconciliation:** table `reconciliation` stores, per window, `received`, `issue`, `noise`, `chat`,
  `pending_review`, `needs_human`, `failed`, group and issue counts. The window becomes `done` (or the
  state of its only outcome: `noise` / `chat` / `pending_review`) only when
  `received = issue + noise + chat + pending_review + needs_human` and nothing failed; otherwise it is
  `error`, a WARNING is logged and it is never marked `done`. No message can disappear: every message is
  in exactly one of these states.
- **Reactions:** see "Listening scope and reactions": a heart on the first message of every ticket or
  needs-human group (dry-run too, once per message), then the OK hand on the first message of EACH group
  that got an issue (live only; none for noise groups, failed groups or dry-run).
- **Dry-run:** groups, group rows, results and the reconciliation are written (`chat`, `pending_review`
  and `noise` exactly as in live mode); GitHub, `gh` and releases are never called.
- **Logs:** counts only, e.g. `Case split case_key=... groups=2 group_sizes=2,2 noise_groups=0 issues=2
  received=4 accounted=4 failed=0 reconciliation=balanced`; never message text, model output or names.

### Issue creation rules (Assistant definition 12.13, replaces the 12.11 "must be tagged" gate)

Only work for the team becomes an issue: **bug**, **task** or **feedback** (`feature` is feedback). The
decision is taken after the model has judged each group, in `Pipeline._safety`:

1. **Forced issue.** A group with a `/bug`, `/task`, `/feedback` or `/feature` command that has content
   (`reason = forced_command`), or with a message that tags the listener (`reason = forced_mention`), is always
   an issue, even when the model said `chat` or was not sure (then it is a `needs-human` issue with no type
   label). The tag is only Telegram's own flag (`event.message.mentioned`, stored in
   `messages.mentions_listener`); an `@` typed in the text or inside an e-mail address is NEVER a tag, and `/`
   counts only as a command at the start of a message.
2. **Automatic issue.** The model calls the group bug, task or feedback with confidence >=
   `[ollama] confidence_threshold`: an issue without any tag (`reason = auto_bug`, `auto_task`,
   `auto_feedback`). Below the threshold, or when the model is unsure (`actionable = false`) or failed, and
   nobody tagged or used a command, the group is `pending_review` (`reason = pending_untagged`).
3. **Questions are not tickets.** A model `type = question` under `feedback`, or a `/question` command, is
   `chat` (`reason = question_no_ticket`) even when tagged: no issue, no reaction (a `/question` is not hearted
   on receipt either).
4. **Safety block.** A group with no product content is never an issue, even when tagged, unless it holds a
   command with content (`reason = safety_block`, decision `noise`). This is structural, with no keyword list:
   no word is left once e-mail, phone numbers and `@handles` are removed, or the model called the group `chat`
   and it has at most 3 words of its own (5 when it shares contact data). A photo counts as content. An empty
   command (`/bug` alone) is also `safety_block`.
5. A model `chat` without a tag or command is plain `chat` (`reason = model_chat`).

Reactions are unchanged (12.10): heart then OK hand only for groups that become issues; `chat`,
`pending_review` and `noise` get none. Every group logs `Group decision key=... action=... reason=...` (codes
and counts only, never content).

States of a group that is NOT an issue (all stored in SQLite, none calls GitHub, none gets a reaction,
all appear in `export-csv`; the `decision`, `type`, `reason` and `mentions_listener` columns tell them apart):

| decision | meaning | typical `reason` |
|---|---|---|
| `chat` | talk with no work for the team, or a question | `model_chat`, `question_no_ticket` |
| `pending_review` | the model was not sure and nobody tagged or used a command | `pending_untagged` |
| `noise` | no letter at all, an empty command, a model noise group, or the safety block | `rule_noise`, `model_noise`, `safety_block` |

The category gate of 12.11 (tag AND category) no longer applies; the `no_direct_call`, `model_unsure` and
`direct_call_chat` reasons are gone (rows written by older versions keep them).
- **No judgment, no silence:** when the model failed, its partition was unusable or its wording was
  unusable, a tagged or commanded group still gets a `needs-human` issue; without either it is
  `pending_review`.
- **Migration:** `mentions_listener` and the `reconciliation.chat` / `pending_review` columns are added with
  safe `ALTER`; messages stored before the upgrade have no tag recorded (`0`), so an old queued message
  that is replayed after the upgrade is judged as untagged (a confident bug still becomes an issue).
- A window closed as `chat` / `pending_review` / `noise` only does not take late messages as follow-ups
  (there is no issue to follow); the next message opens a new window.
- **Not verified here:** how the real local model assigns `category` (the prompts are updated and carry a
  `[gọi trực tiếp]` hint on tagged lines, but only fakes were run), and that Telegram sets
  `message.mentioned` for every kind of tag (note: a reply to a message of the listener account also counts
  as a mention in Telegram). Check both on the ThinkPad in dry-run before going live.

Images (JPG, PNG, WEBP; <= `max_bytes`; <= `max_per_case` per issue) of a case are downloaded with
Telethon `download_media` into a 0700 temporary directory, checked by file signature,
uploaded with `gh release upload` to release `attachments-<case_key>` (`:` becomes `_`,
because git tags cannot contain it) as `<message_id>-<n>.<ext>` and embedded by code as
`![Ảnh n](asset URL)`. `gh` must be installed and authenticated through the `GH_TOKEN`
environment variable (never passed in argv). Skipped or failed images never block the issue:
it is created with a note line instead, and nothing is retried. Dry-run downloads and
validates but never calls `gh` or creates a release, and logs `attachments_dry_run=N`.
Temporary files are always deleted. Issues are still created through the REST API.

### Document attachments (Assistant definition 12.14)

Besides images, a file the sender attached (`event.message.document`) is downloaded into the same 0700
temporary directory, uploaded with the same `gh release upload` to release `attachments-<group key>`,
and listed in the issue's evidence/context section (12.16: `### 🖼 Minh họa / Evidence`, or `### 🧭 Ngữ cảnh` for feedback; no separate "Tài liệu đính kèm" label) as plain links (`- 📎 [name](asset URL)`), never embedded
with `![]`. The decision uses the declared file name, the declared MIME and the size only: a file is never
opened, unpacked, scanned or executed, and its content decides nothing.

- **Accepted types, fixed in code (not configurable):** `pdf`, `docx`, `xlsx`, `pptx`, `txt`, `csv`, `md`,
  `json`, `html`, `zip` (`DOCUMENT_MIME` in `attachments.py`). The extension is read from the file name or,
  when the name has none, from the MIME. Markdown also accepts Telegram's observed
  `text/x-web-markdown` alias. When both exist they must agree (`a.pdf` with an `exe` or `html`
  MIME is refused): the MIME alone is never trusted. A client that reports a generic
  `application/octet-stream` for a file is therefore refused too.
- **Refused without downloading:** `exe`, `bat`, `cmd`, `sh`, `ps1`, `msi`, `apk`, `js`, `vbs`, `jar`, `dll`,
  `scr`, `com`, `app`, `dmg`, `reg`, `lnk`, `php`, `py` (also when one appears anywhere in the name, as in
  `report.exe.pdf`) and every other extension, MIME or file without a usable name or MIME.
  Audio, video, voice, GIF, sticker and image files are not documents (images keep the image path above).
- **Limits:** `[attachments] max_bytes` per file (default 10 MB; checked on the declared size and again on
  the downloaded file) and `max_documents_per_case` documents per issue (default 5, **separate** from the
  image `max_per_case`). Extra or oversized documents are dropped and the issue says so. Each upload counts
  in `max_per_hour` together with images.
- **Names:** the release asset is `<message_id>-<n>.<extension>` (extension from the allowlist, never from the
  original name). The original name is shown only as link text, after `sanitize`, e-mail/phone redaction
  and removal of Markdown characters (`document_label` in `issue_format.py`), at most 80 characters.
- **Failure:** a download or upload error adds `Tài liệu không tải được: <exception class>` to the issue,
  is attempted once (the first upload failure stops the documents of that group) and never loses the issue.
- **Dry-run:** downloads and validates, never calls `gh` or creates a release; logs `documents_dry_run=N`.
- **Logs:** counts and reason codes only, e.g. `Documents found=3 accepted=2 rejected_type=1 rejected_limit=0
  rejected_size=0 failed=0` and `documents=N documents_dry_run=N` in the decision line; never file names or
  content.

Privacy: documents can hold more sensitive data than screenshots, and they become release assets of the target
repository. Confirm the target repository is private before going live, or set `max_documents_per_case = 0`.

Privacy: images become release assets of the target repository. Do not enable them
until the target repository is private (or set `max_per_case = 0`), and verify once that
release assets render inline for repository members.

Logs contain only chat id, message id, decision / case status, image counts, latency and the
topic-split counts above, plus exception class names; never message text, model output, sender names,
file names or tokens.

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
The hourly quota counts all reserved GitHub calls, including timeout/5xx outcomes
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

Reconnect options were checked against the official v1 client reference:
https://docs.telethon.dev/en/stable/modules/client.html
and MTProxy usage:
https://docs.telethon.dev/en/stable/basic/signing-in.html#using-mtproto-proxies
The adapter also checks the installed client's signature before constructing it.
Real Telegram reconnect/catch-up, Ollama classification quality, GitHub delivery,
Linux permissions and systemd behavior must be verified later on the ThinkPad.


### Group clarification and owner support

In live mode, each topic that ends as `needs_human` receives a plain-text reply to its first
Telegram message, asking the sender or Owner to confirm/add information in that same group.
The reply includes up to three short, sanitized missing-information questions when available
and a GitHub issue link built from the configured repository and positive issue number.
GitHub creation failures (including an ambiguous result) ask Owner to check the repository
before any manual retry. The issue hourly limit also produces a group notice. Successful issues
receive a friendly confirmation with their GitHub ticket link, replying to the first message of
each topic: three messages grouped into one ticket get one reply to the first message; three
messages split into three tickets each get a reply with their own ticket link. Noise, chat and
pending review produce no textual notice; dry-run never sends one.

Notices use the existing Telegram client and event loop, with a 15-second transport timeout.
Private chats and broadcast channels are rejected by the notice sender. SQLite `group_notices`
reserves each chat/first-message pair before the send attempt, then records `sent` plus the
outgoing Telegram message id, or `failed`. `reserved`, `sent` and `failed` are never automatically
resent, including after restart; a crash or uncertain transport result can therefore leave a
notice missing. This intentionally favors avoiding duplicate replies.

Reply directly to the clarification message to associate your answer with the original group:
ingress maps that outgoing message id back to the original first message in the same chat.
Replies backed by a sent clarification always remain followups of their original topic, even
when the case is full or ended in an error/rate limit. Other replies retain existing case rules. Owner answers
are stored for review; they do not approve an issue, retry GitHub, change `needs_human`, or post
a GitHub comment automatically. Outgoing messages remain excluded from listener ingress.

For isolated THINKPAD deployment and rollback, see [deploy/README.md](../deploy/README.md).
