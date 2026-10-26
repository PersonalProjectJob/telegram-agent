# Phase3 Console drift and portable verification

Owner: **AlexChu**. Reporter: isolated integration stream, recording root outputs and independently attributed review/verifier results. Version v1.0.0;updated2026-10-15 18:45 Asia/Saigon. **Implemented and verified locally.** Root full regression, independent final source review and independent scoped artifact verification passed with the Windows symlink skip and Linux/harness limits stated below.

Approved [Master plan](../Docs/00-master/console-worker-registry-noissue_master_261015_v1.0.0.md), phaseC/T6–T7. Scope only Console contract gates, packaging, tests, CI and documentation. No commit/push/deployment.

## Implemented

`contracts.py`, `workers/contract-review.json` and `scripts/check-contracts.py` check current source against reviewed metadata and canonical pinned Git blobs. The source-owned `PINNED_COMMIT`/`PINNED_MANIFEST_SHA256`, descriptor semantic hashes, shared module text hashes and contract revisions must agree. Missing/extra/modified/ambiguous/unreadable/link/oversized/malformed data fail closed; no automatic regeneration, accept-current option or inferred schema. Source check reads Git provenance; extracted/no-Git check verifies shipped consistency. Installed phase2 exact-byte compatibility remains unchanged.

Packager includes existing unchanged usage.py dependencies, explicit package registrations and reviewed Console files. `PACKAGE_REGISTRATIONS`, `FILES` and `LOCAL_PACKAGES` remain explicit allowlists; static dependency closure handles imported submodules, including package-relative imports. Source and collected-byte gates run before output creation; verification checks archived bytes again. A providerless third-worker fixture requires explicit descriptor/adapter/package entries and demonstrates a missing dependency fails, then the full ZIP saves/relaunches/reloads through actual local HTTP. No core route/form changes required.

`.github/workflows/console.yml` configures Windows/Ubuntu × Python3.11/3.12, full Git history, Python fixtures, existing custom Node regressions and ZIP build/verify. Node is only contributor/CI tooling; Console/ZIP runtime stays Python standard library.

## Actual local outputs

Local Windows/Python3.12; root final full-suite result:

```text
python -m unittest discover -s console-ui/tests -p 'test_*.py' -v
Ran 107 tests in 204.027s
OK (skipped=1)
```

Root executed `node console-ui/tests/test_frontend.cjs`:8/8 PASS. Root drift suite:14 tests in6.192s, OK. Independent verifier drift suite:14 tests in8.174s, OK. Packaging writer's focused closure suite:6 tests in19.154s, OK, including actual worker CLI help and third-worker extracted ZIP save/relaunch/reload. After the final initializer export-closure correction, writer package/closure suite:18 tests in54.271s, OK(skipped1); independent verifier package/closure suite:18 tests in49.247s, OK(skipped1). Pre-fix own/repeated/cyclic initializer import counterexamples failed as expected and the final fix rejects them while accepting genuine exports. These are independently attributed focused checks, not additional counts added to the root107 suite.

Root source CLI returned schemaVersion1/status verified and pinned commit `6fdb7db7da22247052782e2450f861375e79668f`, Triage23 files/NailHub21. Independent verifier enumerated and SHA256-hashed Git blobs: Triage23/281184 bytes and NailHub21/312153 bytes, exact canonical inventory.

Release artifact built and verified by root:

```text
python console-ui/package.py --output dist/agentfleet-console-phase3-final-261015.zip
python console-ui/package.py --verify dist/agentfleet-console-phase3-final-261015.zip
Verified 78 source files.
```

[Portable ZIP](../dist/agentfleet-console-phase3-final-261015.zip):269367 bytes,79 entries (78 source + package manifest), SHA256 `a62ee84aea3425bcd3e0cdbf8cf223ebf2181a3dbd333d83f927eaaac62897b5`. Logs in `.context/console-fe-be-phase3/`:final-release-build.log/final-release-verify.log/final-contracts.log/final-frontend.log/drift-tests.log/final-tests.log. Reporter independently rechecked final ZIP size/SHA256/79 entries/CRC: PASS.

## Independent review and verifier

Final source reviewer: **APPROVED**, no unresolved actionable findings. Independent final verifier: **PASS within its scope**. A drift verifier: **PASS**. Reviewer found that an initializer import could wrongly justify its own missing dependency; owning writer corrected export resolution, added permanent own/repeated/cyclic initializer negatives and genuine-export positives. Final source is refrozen. Final source review explicitly supersedes the earlier CHANGES_NEEDED. Reviewer independently rejected own/repeated/cyclic initializer counterexamples, accepted valid exports and ran the permanent final initializer test:1 test in4.065s OK; earlier four focused regressions in22.493s OK.

Independent A checks include config/receipt/usage/provider/model/prompts drift, extra/missing inventory, explicit descriptor/shared-module review, revision/pin tampering, malformed JSON/path/type/size/duplicate records, unreadable/reparse paths, CRLF canonicalization and no-Git no-cache subprocess CLI. Verifier artifacts: `.context/console-fe-be-phase3/verifier-report.md` and referenced logs.

Historical pre-initializer-fix root suite106 tests in291.701s OK(skipped1), old release ZIP269165 bytes/SHA76e3319ac9bf0602c5fb48b374c7b5a8181d5761a54d910c2515314ea37f002f and old verifier17 tests in79.059s are retained as intermediate evidence, not final-source attestation.

## Baseline, immutability and operational boundaries

A read-only fetch found origin/main at `ed2998afbd1c0e03894342b2cd754cbc4f779a67` (PR42), containing new worker bytes. Those bytes were deliberately not merged into the Owner-approved6fdb7db source baseline. Independent verifier used the actual new group_notices.py blob in memory and the archive contract gate rejected it as worker source drift. No protected file was overwritten merely to run this negative test. This release therefore does **not** claim support for PR42 worker changes; they require deliberate contract review/update.

Root and independent verifier recorded protected109/109 raw hashes unchanged, prior phase2 runtime23/23 raw hashes unchanged, protected Git diff/status empty and staged content empty. This phase adds gates and metadata without changing prior runtime FE/BE behavior or UI; no new browser visual change is claimed. Fresh integration checkout had14 EOL-only differences from main earlier; canonical normalized comparison is not used as a substitute for main's raw immutable checks.

No THINKPAD/SSH/provider network, Telegram jobs, installed service mutation or real restart was used. Worker source/config/prompts/deploy/systemd/.env.example remain unchanged. Tests use private TEMP files and fake service execution; extracted process restarts mean only stopping/relaunching the local test Console.

Both baseline units' opaque EnvironmentFiles remain fail closed: effective binding unverified, Save/Apply disabled with reason even when package hashes match. No secret-bearing environment file is opened and no operational guard is bypassed.

## Limits

- GitHub Actions/Ubuntu jobs are configured, **not executed** in this local task. Root discovery found Docker Desktop WSL without Python/Node and Docker daemon unavailable; no local Linux PASS or production-host certification claimed.
- Final suite skipped one Windows file-symlink negative because OS permissions disallow creating it. Directory-junction/reparse regressions executed and passed; they are separately reported, not claimed equivalent to the skipped case.
- Formal harness script remains unsupported: Node-only verification expects package.json. Actual Python/Node/ZIP gates are reported separately; no formal harness-gate PASS.
- Checksums/review metadata establish consistency/provenance under trusted source ownership, not signatures or cryptographic publisher authentication. Future update requires manual review of worker config/receipt/usage/provider/import changes and deliberate synchronized pins/manifests/descriptors/adapters/revisions/review metadata before rerunning gates.

## Handoff

Final root107 tests OK(skipped1), frontend8/8 PASS; independent source review APPROVED and verifier scoped PASS. Actual final ZIP independently extracted under paths with spaces: packager verify/no-Git source checker/both worker help commands passed under `-E -S -B`, all extracted hashes unchanged before Console boot. Actual extracted Console saved both workers and Triage fallback, stopped/relaunched and reloaded exact settings/revisions. All TEMP processes stopped and directory cleaned. Verifier independently reproduced all3 pre-fix initializer false acceptances, then observed final rejection; final frozen code/tests37/37 unchanged.

Raw main status is retained at `.context/console-fe-be-phase3/final-git-status.log` (copied below). Protected diff/status and staged name list empty. Git diff check passed with autocrlf notices only. No commit/push/merge or production operation. No rule proposal from this stream.

## Raw main Git status

```text
 A .github/workflows/console.yml
 A Docs/00-master/console-worker-registry-noissue_master_261015_v1.0.0.md
 A console-ui/PHASE1-VERIFICATION.md
 A console-ui/PHASE2-VERIFICATION.md
 A console-ui/PHASE3-VERIFICATION.md
 M console-ui/README.md
 D console-ui/app.js
 A console-ui/backend/agentfleet_console/__init__.py
 A console-ui/backend/agentfleet_console/cli.py
 A console-ui/backend/agentfleet_console/compatibility.py
 A console-ui/backend/agentfleet_console/contracts.py
 A console-ui/backend/agentfleet_console/errors.py
 A console-ui/backend/agentfleet_console/paths.py
 A console-ui/backend/agentfleet_console/policy.py
 A console-ui/backend/agentfleet_console/registry.py
 A console-ui/backend/agentfleet_console/schema.py
 A console-ui/backend/agentfleet_console/server.py
 R console-ui/backend.py -> console-ui/backend/agentfleet_console/store.py
 A console-ui/backend/agentfleet_console/worker_sources.py
 A console-ui/backend/agentfleet_console/workers/__init__.py
 A console-ui/backend/agentfleet_console/workers/adapters.py
 A console-ui/backend/agentfleet_console/workers/compatibility.json
 A console-ui/backend/agentfleet_console/workers/contract-review.json
 A console-ui/backend/agentfleet_console/workers/descriptors/nailhub.json
 A console-ui/backend/agentfleet_console/workers/descriptors/triage.json
 R console-ui/styles.css -> console-ui/frontend/assets/styles.css
 R console-ui/tokens.css -> console-ui/frontend/assets/tokens.css
 R console-ui/index.html -> console-ui/frontend/index.html
 R console-ui/api.js -> console-ui/frontend/src/api.js
 A console-ui/frontend/src/app.js
 A console-ui/frontend/src/schema-form.js
 A console-ui/frontend/src/state.js
 M console-ui/package.py
 M console-ui/run.py
 A console-ui/scripts/check-contracts.py
 A console-ui/tests/__init__.py
 R console-ui/test_backend.py -> console-ui/tests/test_backend.py
 A console-ui/tests/test_compatibility.py
 A console-ui/tests/test_contract_drift.py
 A console-ui/tests/test_frontend.cjs
 A console-ui/tests/test_integration.py
 R console-ui/test_package.py -> console-ui/tests/test_package.py
 A console-ui/tests/test_package_closure.py
 A console-ui/tests/test_registry.py
 A console-ui/tests/test_schema.py
```

## Changelog

| Version | Time Asia/Saigon | Change |
|---|---|---|
| v1.0.0 | 2026-10-15 18:45 | Final107-test/root, independent review/verifier, actual final ZIP and immutable evidence; CI/Linux and formal harness limits explicit. |
