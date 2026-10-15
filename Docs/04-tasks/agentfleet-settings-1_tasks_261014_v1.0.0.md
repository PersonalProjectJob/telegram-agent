# Tasks: AgentFleet backend integration

Version v1.0.0 · 2026-10-14 11:44 Asia/Saigon · owner AlexChu. Owner approved UI and requested real backend; executor Codex gpt-6.1-sol medium explicitly selected. All four preceding docs verified present. No issue/PR created, so issue links remain not assigned rather than invented.

## Phase 1: Setup
- [x] Record approved UI, API contract and QA cases — AlexChu/Codex — 0.5h — Issue: not created.
- [x] Reuse isolated service-split worktree; preserve unrelated main changes — AlexChu/Codex — 0.5h — Issue: not created.

## Phase 2: Implementation
- [x] Real provider transport + runtime adapters for both services — Codex executor — estimate 4h — Issue: not created.
- [x] Console API, secret/revision persistence and single-service apply implementation — Codex executor — estimate 4h — Issue: not created.
- [x] Replace UI simulation with server calls and retain approved design — Codex integration — estimate 2h — Issue: not created.

## Phase 3: Testing & QA
- [x] Unit/security/HTTP regressions and browser real-backend flow; independent source re-review — estimate 2h — [output](../../console-ui/BACKEND-VERIFICATION.md).
- [x] Existing worker regressions and source delivery check — estimate 1h — [output](../../console-ui/BACKEND-VERIFICATION.md).

## Phase 4: Deployment / Handoff
- [x] Source launch README and host permissions/readiness instructions — Codex — estimate 0.5h — [README](../../console-ui/README.md).
- [x] Report live-provider/ThinkPad acceptance as unverified; no SSH/deploy/commit performed — AlexChu — [verification](../../console-ui/BACKEND-VERIFICATION.md).
- [ ] Actual installed Linux provisioning, separate wheel installation and live provider acceptance — environment credentials/access not supplied; not covered by local PASS.

## Dependencies & Blockers
- No live API credentials supplied; unit/integration probes use harmless synthetic data and recording fake HTTP upstream. Real provider probe is available to Owner through UI when configured.
- Windows has no systemd and cannot control ThinkPad locally. Linux host permissions and upgraded worker installation must be verified on that host before production apply claims.
- Current work is isolated in `codex/agentfleet-console-package` on origin/main d414102. Primary main is pull-only; the historical dirty snapshot and earlier feature worktree are preserved, not applied or modified.

## Related Documents
[Master](../00-master/agentfleet-settings-1_master_261014_v1.0.2.md) · [Product](../01-product/agentfleet-settings-1_product_261014_v1.0.0.md) · [Engineering](../02-engineering/agentfleet-settings-1_engineering_261014_v1.0.0.md) · [QA](../03-qa/agentfleet-settings-1_qa_261014_v1.0.0.md).

## Changelog
| Version | Date | Time | Author | Change |
|---|---|---|---|---|
| v1.0.0 | 2026-10-14 | 11:44 | AlexChu / Codex | Backend/API/UI/test execution breakdown |

## Portable delivery — 2026-10-15

- [x] Integrate onto latest pulled main through a separate worktree, preserving NailHub BR-13/14 and current business gates.
- [x] Stdlib source ZIP packager, exact allowlist/checksum manifest, Windows/Linux launchers and root usage README.
- [x] Clean-extract dependency-free console/bootstrap/save/reload, current-source/exclusion/output/link/path integrity tests.
- [x] Integrated console41/listener622/NailHub227 test results and truthful platform skips recorded in QA addendum.
- [x] Parent independently verified final ZIP and launched its extracted copy.
- [ ] Owner review/commit/PR/merge, if requested later; source UI changes have not been applied to primary main.

Delivery: `dist/agentfleet-console-20261015.zip`, SHA256 `76d77c2ed54f7d78de0c665dc53f0fc71275dd288a5786639cc04f0e9940fe5a`; local state is separate and excluded. Linux installation/live provider acceptance retains the earlier explicit environment requirements.
