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
Policy: `tools: []`, `subagent: false`, `commandExecutionPolicy: "off"`; the worker
passes `--agent <text_agent>` and never accept-edits or any permission bypass. It checks
the policy SHA before readiness and again under the model lock before dispatch. Text
jobs run up to `subscription.text_timeout_seconds` (default 600). On timeout the worker
never dispatches again: it returns `complete_salvaged` only if the new native
conversation already holds a valid final assistant answer, otherwise `unknown`.
Identical repeated stdout JSON collapses to one; differing copies or any unknown native
record hold the job. Text and image jobs share `subscription.max_daily_jobs`.
The worker refreshes `worker-ready.json` about every 15s even during a long job and
records `busy_job`; a dead worker stops refreshing and the producer fails closed.
Text is advertised in the heartbeat only when the text agent was verified, so a missing
text policy never blocks image jobs.
