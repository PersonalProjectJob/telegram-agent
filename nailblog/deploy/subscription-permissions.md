# Subscription worker installation contract (staging only)

Owner: AlexChu. This is a template; no installation or CLI invocation is automatic.

Use a separate narrow group `nailblog-image`, containing only `tgnailblog` and `startechai`.
Provision `/var/lib/nailblog-subscription` as root:root 0755 and its `queue` as
root:nailblog-image 2770. Do not relax the existing NailBlog 0700 state root and do
not add startechai to agentfleet. The service drop-in adds only the separate queue
to ReadWritePaths; restart both processes after narrow group membership changes.

The worker private_dir must be
`/home/startechai/snap/antigravity-cli/common/nailblog-worker`, owner startechai 0700.
Its per-job marker is immutable before dispatch; retain it and failed receipts.
Use the existing shared model lock with a narrow file ACL on
`/run/agentfleet/ollama.lock`; do not add startechai to agentfleet.
Preserve the existing MODEL_LOCK ownership/mode.
Worker and bot must use the same existing MODEL_LOCK path.
Worker uses a background-only adapter over the canonical process mutex, lock
opening/flock/unlock/wait/PID-liveness primitives and inherited error contract.
It inspects foreground markers read-only before and after flock, retaining dead
or malformed markers without deleting them. This avoids requiring directory write
for canonical stale-marker garbage collection. Live PIDs retain priority; a PID
that cannot be signalled due to permissions remains conservatively live. Inspection
is bounded and stop-aware. Canonical NailBlog/NailHub model_lock.py copies are
unchanged, and no canonical module global is monkeypatched or separate lock used.
The observed2775 /run/agentfleet already grants other::r-x traversal; no directory
ACL is needed. Add only user startechai:rw- to the existing0660 root:agentfleet
ollama.lock file. Do not change its owner/group/base mode or grant other runtime
files. Since /run is volatile, install the reviewed template
`nailblog-subscription-tmpfiles.conf` as `/etc/tmpfiles.d/nailblog-subscription.conf`.
Its a+ fileACL rule runs lexically after the existing agentfleet.conf provisioning.
Apply `systemd-tmpfiles --create /etc/tmpfiles.d/agentfleet.conf /etc/tmpfiles.d/nailblog-subscription.conf`
after both files are installed, then verify getfacl on only ollama.lock. Parent must
validate this rule with actual systemd-tmpfiles syntax/host preflight before rollout.
The template enables no service and changes no existing root provisioning file.

Install identical [subscription] settings into the bot's config and the separate
`/etc/nailblog-subscription/worker.toml` (only that table), root:nailblog-image0640
under the separate root:nailblog-image0750 parent. The fd-safe reader opens every
ancestor directory read-only; traverse-only permission on the private bot config
parent would not suffice. Do not add an ACL to /etc/nailblog or expose its entries.
Worker config is readable by its narrow supplementary group and not group/other
writable. Keep API secrets in the original service
EnvironmentFile; worker does not read it, bot state, browser profile, or OAuth files
belonging to another user. CLI uses its existing login under startechai.

Pin `/snap/bin/agy` version 1.2.11, model gemini-3.8-flash-medium, effort medium.
Install the exact reviewed `nailblog-image-only.md` bytes as
`/home/startechai/snap/antigravity-cli/common/.gemini/config/agents/nailblog-image-only.md`,
owned by startechai, mode0640, with non-group-writable ancestors. This adds only
one restrictive named agent; do not change global settings/authentication/permissions.
The worker checks its exact bytes/SHA and requires `/snap/bin/agy agent` to list
the exact name before readiness, and explicitly passes `--agent nailblog-image-only`.
agy1.2.11 did not discover the workspace-only definition in read-only preflight;
it did discover the unique Snap config agent. The workspace mirror is additional
documentation, not the discovery authority. Agent tools are exactly `[generate_image]`,
with `mainAgent: true`, `subagent: false`, and `commandExecutionPolicy: "off"`.

The worker omits accept-edits and all permission-bypass flags. Prompt wording is
not a permission boundary. Native tool creation under its own brain directory is
the only permitted tool action. Any denied action,
missing receipt, changed backend/wire format, unknown outcome or timeout holds the
job; never clear markers to retry. Count failed/dispatched jobs toward caps.
The worker holds verified directory descriptors for all relative job I/O and
checks opened-file ownership/mode/link count/size. Retain private dispatch markers
even when a producer removes a queue entry. Expired/cancelled requests are checked
while waiting for the shared model lock and immediately before generation.

The template intentionally uses `NoNewPrivileges=no` for this separate unprivileged
worker: actual read-only systemd version probes returned1.2.11 with no, but Snap
snap-confine failed with missing cap_dac_override under yes. Do not copy this setting
to the bot, run the worker as root, or add capabilities to bypass Snap confinement.
The bot retains its existing UID992/agentfleet/ProtectHome=yes and security settings.

Before deployment validate the unit with systemd-analyze and confirm the Snap CLI
works under these restrictions using an explicitly approved bounded native smoke.
Confirm the initial native conversation context actually exposes only the selected
native image tool: listing/hash checks alone cannot prove this old CLI selected
the custom primary agent. Require a fresh session, exact typed15 invocation paired
with typed132 successful native image result, all successful statuses, no error or
permission columns, no other tool calls/denials, one original raster, and matching
receipt/source SHA. Source database reads are bounded in-memory snapshots; if WAL
or an unknown record shape cannot be verified, hold rather than guess success.
Do not call local fixture tests proof of native worker runtime acceptance. Rollout
must back up existing unit/drop-ins/config and preserve the API uncertain ledger;
startup-gate remains paused/disarmed. Both Owner snapshot approvals remain required.

## Text jobs (`nailblog-agy-text-v1`)

The same worker also runs `kind=text` jobs, so Agy serves both images and text.
Install the exact reviewed `nailblog-text-only.md` bytes as
`/home/startechai/snap/antigravity-cli/common/.gemini/config/agents/<subscription.text_agent>.md`
(default `nailblog-text-only`; the name must match `^[a-z0-9][a-z0-9-]{0,63}$` and the
file's `name:` line is generated from it by `text_policy()`), owned by startechai, mode 0640.
Policy: `excludeDefaultComponents: true`, `tools: []`, `subagent: false`,
`commandExecutionPolicy: "off"`; Agy's 1.2.1 changelog describes the first option
as excluding default prompt sections and built-in tools while preserving hooks. Its
effect with the pinned 1.2.11 worker still requires a controlled runtime smoke; the
native transcript verification continues to reject every tool call. The worker
passes `--agent <text_agent>` and never accept-edits or any permission bypass. It checks
the policy SHA before readiness and again under the model lock before dispatch. Text
jobs run up to `subscription.text_timeout_seconds` (default 600). On timeout the worker
never dispatches again: it returns `complete_salvaged` only if the new native
conversation already holds a valid final assistant answer, otherwise `unknown`.
Identical repeated stdout JSON collapses to one; differing copies or any unknown native
record hold the job. Text and image jobs share `subscription.max_daily_jobs`.
The prompt and JSON schema are passed in the agy argv, so they are visible to other
local users through `ps`/`/proc/<pid>/cmdline` while the job runs; never put secrets
in a text prompt. Prompt plus schema are capped at 100000 UTF-8 bytes (E2BIG guard),
checked before the job is created and again before the dispatch marker is written.
`--print-timeout` is `text_timeout_seconds - 15` so the CLI stops before the hard kill.
Salvage also applies when the CLI process fails after writing a complete native answer.
The worker refreshes `worker-ready.json` about every 15s even during a long job and
records `busy_job`; a dead worker stops refreshing and the producer fails closed.
Text is advertised in the heartbeat only when the text agent was verified, so a missing
text policy never blocks image jobs.

Policy updates change the text policy SHA in request identity and heartbeat. Before
updating, stop the worker and reconcile pending jobs; preserve the queue and private
dispatch markers. Install the exact new policy in the Snap agents directory and update
the existing `private_dir/policy-preflight-text/.agents/agents/<text_agent>.md` mirror
to the same bytes before restarting the matching worker and producer. An old mirror or
installed policy fails readiness. Existing requests retain their job IDs. The producer
can read a terminal receipt under the exact previous policy identity; a completed
receipt must still pass the full native transcript and output checks. A pending legacy
request is held, and the worker cannot dispatch it under the new policy. Unknown jobs
remain final. Do not delete or rewrite those requests or markers.

## Candidate staged text-policy rollout — not executed

This is a review checklist, not an approval to deploy. It covers exactly the installed
`nailblog/agy_text.py` and `nailblog/images/subscription.py`, plus the text-only policy
and its preflight mirror. Do not install a wheel or copy a whole package: that could
deploy the separate thumbnail work. The installed package paths, service configuration,
artifact hashes and actual host state must be verified before any replacement. The
existing 28 October decisions and completed daily steps must not be reopened.

1. Independently review the final diff and offline test results. Record hashes of the
   two candidate modules and policy; resolve the actual imported module paths for both
   service users, verify they refer to the intended shared installation, and stop if
   deployed versions or paths do not match the reviewed baseline. With the default
   agent, old policy SHA is `b6951cc249463c9c89a82affaa233df48e8db7479f82c689e7dcbb5eb94ccac7`
   and new SHA is `d5e28534d7f28dec13a71eefa2a956f1f61bda789277cbdb51ecfdd58dc5e3a2`;
   a nondefault agent name requires computing its own SHA from `text_policy()`.
2. Confirm staging configuration, current pause/arm status, service identities and
   time windows. Pause and disarm using the existing operator procedure; stop the bot
   and worker gracefully and verify both inactive and no Agy job in flight. Pausing the
   bot alone does not stop daily scheduling. Inventory queue receipts and dispatch
   journal without changing either; resolve or explicitly hold every unfinished job
   before restarting the worker. Preserve all unknown jobs, markers and daily state.
3. Back up the exact two installed modules, installed policy and existing preflight
   mirror with metadata and SHA, into a protected location outside the package. Record
   whether each mirror existed, plus service state and current hashes. Do not restore
   an old queue, journal or database snapshot over later state during rollback.
4. Install only the two reviewed modules and text policy; update the existing
   `private_dir/policy-preflight-text/.agents/agents/<text_agent>.md` mirror to the
   identical policy bytes. Preserve required ownership and modes. Verify all four
   destination hashes against the reviewed artifacts, and confirm thumbnail modules,
   image policy, configuration and service units are unchanged. Start the worker;
   check fresh heartbeat, matching text identity and the agent discovery/preflight.
   Keep the bot stopped and disarmed while validating.
5. Only after the rollout is separately authorized, submit one harmless, schema-bound
   text smoke with a fresh, explicitly recorded purpose through `AgyText` as the real
   producer user and existing queue. Use the real worker, journal, model lock and quota;
   never invoke Agy directly or use a previously unknown job. Verify the new marker,
   fresh conversation, policy SHA, receipt and full native transcript, zero tool calls,
   schema-valid output and producer-side receipt verification. Confirm the request is
   independent of daily-planner and does not modify topics, website or 28 October
   decisions. If any step fails, or a tool call occurs, record the outcome and stop;
   do not retry an unknown smoke job. A smoke result proves only that run, not that
   future model calls cannot attempt tools.
6. Before restoring bot service, check the existing startup gate and verify paused and
   disarmed state; only the Owner can decide to arm/resume. Do not run an old daily
   proposal or bypass its deadline. Old completed receipts remain readable only after
   full proof verification; legacy pending requests hold and unknown requests are final.

Rollback: stop both services gracefully, verify no job is in flight, restore the four
backed-up files (or prior absence of the mirror) with recorded metadata and compare
hashes. Preserve all current queue, journal and database data, including smoke results;
do not turn a new unknown into a new dispatch. Restart only after reviewing compatibility
of any requests created under the new policy and confirm paused/disarmed state. If that
compatibility or a clean stop cannot be established, leave services stopped for operator
reconciliation. A concrete host-specific install/smoke script must be reviewed after
the installed paths, live state and queue inventory are known; none is supplied here.
