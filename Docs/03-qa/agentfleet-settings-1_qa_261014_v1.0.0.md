# QA: AgentFleet backend integration

Version v1.0.0 · Owner AlexChu · 2026-10-14. Strategy: Python unittest with temp state/configs and injected HTTP/systemd; real localhost HTTP server; browser clicks against backend. No task content or real API keys in fixtures. Live provider credentials/ThinkPad service checks are separate acceptance conditions, not faked PASS.

## Test Cases
| ID | Priority | Precondition / steps | Expected |
|---|---|---|---|
| TC-01 | P0 | Start CLI with temporary state; GET bootstrap | Redacted actual per-service settings, local binding clearly indicated, no key values |
| TC-02 | P0 | Configure each provider; test synthetic JSON against recording fake upstream | Correct protocol/auth/model/schema, real HTTP request, bounded response; test does not persist |
| TC-03 | P0 | Upstream 401/404/429/timeout/invalid JSON/oversize | Stable safe error category; no raw body, key or prompt in API/log |
| TC-04 | P0 | Save triage setting/key; reload backend and browser | Persisted triage revision; NailHub bytes/revision untouched; no secrets in response/diff/history |
| TC-05 | P0 | Two clients edit same revision; external file changes | 409 conflict; no silent lost update |
| TC-06 | P0 | Change provider/endpoint, omit new key | Saved key not forwarded to new endpoint; explicit validation error |
| TC-07 | P0 | Save bad types, NaN, paths, Owner IDs, VERIFIED, unknown UI field | Reject request; protected TOML/config preserved; no restart |
| TC-08 | P0 | Apply selected saved revision via fake systemd | Only fixed target unit; no other service mutation; matching version/readiness required |
| TC-09 | P0 | Missing systemd, privilege denied, inactive/failure/restart timeout | Truthful unavailable/error, saved != proven applied; no DB restore |
| TC-10 | P0 | No CSRF, wrong Origin/Host, GET mutation, traversal, oversized body | Request refused; no settings/key exposure |
| TC-11 | P0 | Cloud custom private/link-local/metadata IP, redirect, DNS change | SSRF blocked, key never sent to unexpected host |
| TC-12 | P0 | Runtime adapters with existing worker tests | Ollama backward compatible; cloud passes existing parser/evidence gates; failure holds work |
| TC-13 | P1 | Browser change → test → review → save → reload → apply result | Real backend flow, revision/error truth, draft retained on failures |
| TC-14 | P1 | Keyboard and mobile 375/768/1024/1440 | Approved UI remains usable, no overflow/errors |
| TC-15 | P1 | Two rapid tests, edit/save while test pending | No stale success or permanently disabled button |
| TC-16 | P0 | Cloud task provider without consent | Runtime fails closed; harmless connection test sends no task data |

## Acceptance Criteria
- [x] Local contract/security/persistence cases pass: 31 console/backend tests, actual NTFS ACL and localhost HTTP. Live-vendor/host-specific acceptance remains pending.
- [x] Existing triage603 and NailHub181 suites pass (5 platform skips).
- [x] Browser proves real server persistence/reload and conflict handling; mock upstream/apply are explicitly labelled. Four viewports, 48 PNGs, 44 geometry measurements without overflow/errors.
- [x] Independent source agent reviewed data/permission paths; findings fixed and re-reviewed.
- [x] Live provider and Linux host checks explicitly NOT VERIFIED. Canonical Node-only harness gate unavailable; no fabricated PASS. See [execution report](../../console-ui/BACKEND-VERIFICATION.md).

## Environment Setup
Python 3.11+; `python console-ui/run.py --no-open --port 0` for temporary port. Unit tests use temporary directories/secret fixtures and standard library only. Commands for new suite will be documented with implementation. No fabricated performance SLA: test one bounded probe, upper HTTP body size and single-service locking; measure actual result time.

## Security / Accessibility
Tests TC-06/07/10/11/16 cover credentials, permission and outbound boundaries. Labels/errors live region, focus and reduced motion remain from approved design. No secrets in browser storage or GET API.

## Related Documents
[Master](../00-master/agentfleet-settings-1_master_261014_v1.0.2.md) · [Product](../01-product/agentfleet-settings-1_product_261014_v1.0.0.md) · [Engineering](../02-engineering/agentfleet-settings-1_engineering_261014_v1.0.0.md). No issue/PR.

## Changelog
| Version | Date | Time | Author | Change |
|---|---|---|---|---|
| v1.0.0 | 2026-10-14 | 11:44 | AlexChu / Codex | API, revision, secret, apply, provider and browser test plan |

## Packaging verification addendum v1.0.1 — 2026-10-15

Owner AlexChu; Codex executor. New isolated worktree `C:/Users/AD/.codex/worktrees/agentfleet-console-package/AgentBuilder`, branch `codex/agentfleet-console-package`, base origin/main `d414102cd222f20e00785d63507ab334a2c01976`. Earlier603/181counts above describe the earlier baseline. Primary main is pull-only; old feature worktree and preserved dirty snapshot were not changed by this executor.

| Command / environment | Actual terminal summary |
|---|---|
| `python -m unittest discover -s console-ui -p 'test_*.py' -q` | Ran41tests109.744s; OK(skipped1) |
| `$env:PYTHONPATH='listener/src;listener'; python -m unittest discover -s listener/tests -t listener -q` | Ran622tests45.448s; OK |
| `$env:PYTHONPATH='nailhub/src;nailhub'; python -m unittest discover -s nailhub/tests -t nailhub -q` | Ran227tests36.205s; OK(skipped5) |
| `python -m unittest discover -s console-ui -p 'test_package.py' -q` | Ran10tests21.986s; OK(skipped1) |
| `python -m compileall -q console-ui listener/src nailhub/src` | exit0 |
| `git diff --check` | exit0 |
| `python console-ui/package.py --verify dist/agentfleet-console-20261015.zip` | Verified57sourcefiles |

Logs on executor host: `%TEMP%/agentfleet-package-console-final.log`, `agentfleet-package-listener-tests.log`, `agentfleet-package-nailhub-tests.log`, `agentfleet-package-tests.log`. PowerShell uses semicolon PYTHONPATH; POSIX uses colon.

Coverage: current uncommitted source bytes and deterministic ZIPs; exact allowlist and secret/config/session/profile/cache/test/evidence/Git exclusions; checksum tampering and malformed manifest; traversal paths/ZIP members; no source/previous-output overwrite; missing source refusal; actual Windows directory-junction input/output refusal; clean extraction to a path with spaces outside Git, launcher from unrelated cwd, assets, bootstrap, per-service save and restart persistence with `python -E -S` (site packages disabled). Platform Windows launcher was exercised. True file-symlink creation requires unavailable Windows privilege and is the one packaging skip; directory junction rejection ran and passed. Five existing NailHub platform skips remain. Linux launcher is supplied and source reviewed, not claimed as live Linux-host acceptance.

Parent independently verified extracted source with harmless local HTTP upstream: both probes real HTTP/no-save; separate fake scoped OpenAI keys persisted per service without public disclosure; reload persistence; local apply disabled. Parent also ran `python -m unittest deploy.test_deploy -v`: Ran45tests116.547s OK; its staged committed-main baseline separately ran listener622/59.951s OK and NailHub227/34.239s OK(skipped5). Those baseline staged counts are distinct from the integrated feature-source suites above. JS syntax checks passed independently.

Artifact:58entries,220565bytes, SHA256 `76d77c2ed54f7d78de0c665dc53f0fc71275dd288a5786639cc04f0e9940fe5a`. No live provider API calls, credentials, SSH, real service restart, installs, commits, pushes, PR or merge. Actual Linux provisioning/apply and live vendor credentials remain unverified. The ZIP is a source console distribution, not an installer. BR-13 age and BR-14 branding gates remain from latest main; packaging did not modify business logic.
