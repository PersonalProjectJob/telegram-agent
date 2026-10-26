# Console FE/BE split — phase 1 verification

Owner: AlexChu. Version: v1.0.1. Updated: 2026-10-15 16:53:44 Asia/Saigon. Windows, Python 3.12.

The controller's final main suite succeeded, immutable checks passed, controlled browser parity matched, and independent reviewer `phase1_review` reported **APPROVED**, with no actionable findings. This report consolidates their outputs; the reporting pass changed no product source and ran no tests.

## Scope and integration

Three coordinated streams worked in isolated worktrees: FE moved five assets; BE extracted the existing implementation and compatibility facades; integration updated packaging/tests/docs and combined scoped patches sequentially. The streams produced one integrated result; no competitive winner was selected.

```text
console-ui/
  frontend/
    index.html
    src/app.js
    src/api.js
    assets/styles.css
    assets/tokens.css
  backend/agentfleet_console/
    __init__.py
    paths.py
    store.py
    server.py
    cli.py
  run.py
  backend.py
  package.py
  test_backend.py
  test_package.py
  README.md
  PHASE1-VERIFICATION.md
```

The launch command remains:

```sh
python console-ui/run.py
```

`start-console.cmd` and `start-console.sh` remain entry points. `/`, `/index.html`, `/app.js`, `/api.js`, `/styles.css` and `/tokens.css` keep their public URLs. UI bytes, API routes/shapes and configuration behavior remain unchanged. State defaults to `.agentfleet/` at the checkout/extracted root even when launched elsewhere. `run.py` preserves launcher/server imports; temporary `backend.py` aliases the live storage module, preserving legacy monkeypatches. Facade removal is scheduled for phase 2 after internal import/test migration.

The package allowlist includes new FE/BE paths and compatibility facades, excludes old FE paths, and retains existing worker module lists. ZIP relocation, `-E -S` startup, paths with spaces, checksum manifests, exclusions and refusal to overwrite output were exercised.

## Verification and review matrix

Console suite command:

```sh
python -m unittest discover -s console-ui -p "test_*.py" -v
```

| Evidence owner / run | Recorded result |
|---|---|
| Controller baseline | 51 tests, 123.873s; `OK (skipped=1)` |
| Integration executor | 53 tests, 205.739s; `OK (skipped=1)` |
| Controller final main | 53 tests, 157.427s; `OK (skipped=1)` — 52 passed |
| Independent reviewer `phase1_review` | APPROVED, no actionable findings; separately reported six TEMP-focused tests passing |
| Controller immutable checks | Protected tracked raw hashes 109/109 match main's before-snapshot; protected diff/status empty; FE raw hashes 5/5 exact |
| Controller syntax checks | Python AST 10 files; JavaScript syntax 2/2 PASS |
| Browser executor controlled comparison | All four viewport flows PASS; PNG, accessibility, metrics and forms identical for 96/96 records |
| Controller independent browser rehash | 96 PNG + 96 ARIA files byte-identical; zero differences |

Exact final main output:

```text
Ran 53 tests in 157.427s

OK (skipped=1)
```

`test_source_file_link_rejected` was skipped because Windows denied test symlink creation. Directory-junction rejection passed. Fixtures use TEMP state, fake providers/service runners and loopback HTTP. Coverage includes asset GET/query/HEAD behavior and internal source-path 404s; live backend alias/monkeypatch identity; relocated import closure, default state placement and save/reload of both services. Reviewer's six tests are distinct from the controller's suite and are not added to its count.

```text
PROTECTED_RAW_HASHES=109/109
PROTECTED_CHANGED=[]
PROTECTED_DIFF_STATUS=empty
FRONTEND_EXACT_RAW_HASHES=5/5
PYTHON_AST_FILES=10
JS_SYNTAX=2/2 PASS
```

The isolated integration checkout had 14 initial protected-file LF/CRLF differences from main. Canonical equality and empty diff/status were recorded without rewriting those files. Final main raw checks use main's own initial snapshot and supersede that checkout-specific limitation.

## Controlled browser evidence

The controlled baseline came from original commit `6fdb7db7da22247052782e2450f861375e79668f` via a TEMP Git archive. Five original raw frontend assets were restored only after matching the original captured asset hashes. The finalized runner exercised this original source and compared it with integrated main captures. Product source and product state were not modified to make the comparison pass.

Viewports: desktop 1440×900, mobile 375×812, tablet portrait 768×1024, tablet landscape 1024×768. Real interactions covered navigation, every worker tab/provider, review dialogs and local save/reload with independent worker timeout values. Both runs used private TEMP state.

```text
CONTROLLED_COMPARISON=PASS
PNG_IDENTICAL=96/96
ARIA_IDENTICAL=96/96
METRICS_IDENTICAL=96/96
FORMS_IDENTICAL=96/96
ROOT_INDEPENDENT_REHASH=96 PNG + 96 ARIA identical
UNEXPECTED_RUNTIME_ERRORS=0
LOCAL_HTTP_ERRORS=0
FORBIDDEN_TEST_APPLY_REQUESTS=0
UNEXPECTED_EXTERNAL_REQUESTS=0
```

Google Fonts was intentionally blocked under the same policy: three expected console failures per viewport per run, 12 before and 12 after. Fallback-font rendering was compared; live Google Fonts rendering was not tested.

The first strict comparison recorded **14 mismatches** from baseline drawer state/capture readiness, although all four after-flow runs passed. `after/results.json` retains that historical overall FAIL. Earlier runner/navigation failures also remain preserved. The authoritative controlled comparison is a separate PASS in `ui-evidence/controlled-comparison.json`; usage readiness and the same drawer helper were applied to the original-source baseline. These were harness repairs, not product fixes.

Evidence root: `C:/Users/AD/Documents/GitHub/AgentBuilder/.context/console-fe-be-phase1/`.

- [Baseline suite](C:/Users/AD/Documents/GitHub/AgentBuilder/.context/console-fe-be-phase1/baseline-tests.log), [integration suite](C:/Users/AD/Documents/GitHub/AgentBuilder/.context/console-fe-be-phase1/integration-tests.log), [final suite](C:/Users/AD/Documents/GitHub/AgentBuilder/.context/console-fe-be-phase1/final-tests.log).
- [Structural checks](C:/Users/AD/Documents/GitHub/AgentBuilder/.context/console-fe-be-phase1/main-structural-verification.json), [controller independent rehash](C:/Users/AD/Documents/GitHub/AgentBuilder/.context/console-fe-be-phase1/root-ui-parity-check.json).
- [Controlled provenance](C:/Users/AD/Documents/GitHub/AgentBuilder/.context/console-fe-be-phase1/controlled-provenance.json), [controlled original run](C:/Users/AD/Documents/GitHub/AgentBuilder/.context/console-fe-be-phase1/before-controlled/results.json), [historical comparison](C:/Users/AD/Documents/GitHub/AgentBuilder/.context/console-fe-be-phase1/after/results.json).
- [Controlled comparison](C:/Users/AD/Documents/GitHub/AgentBuilder/.context/console-fe-be-phase1/ui-evidence/controlled-comparison.json), [parity summary](C:/Users/AD/Documents/GitHub/AgentBuilder/.context/console-fe-be-phase1/ui-evidence/parity-summary.json), [browser evidence bundle](C:/Users/AD/Documents/GitHub/AgentBuilder/.context/console-fe-be-phase1/ui-evidence.zip), [contract](C:/Users/AD/Documents/GitHub/AgentBuilder/.context/console-fe-be-phase1/CONTRACT.md).

## Git state and remaining scope

Read-only `git status --short` at 2026-10-15 16:53:44 Asia/Saigon, before copying this report revision into main:

```text
 A Docs/00-master/console-worker-registry-noissue_master_261015_v1.0.0.md
 A console-ui/PHASE1-VERIFICATION.md
 M console-ui/README.md
 M console-ui/backend.py
 A console-ui/backend/agentfleet_console/__init__.py
 A console-ui/backend/agentfleet_console/cli.py
 A console-ui/backend/agentfleet_console/paths.py
 A console-ui/backend/agentfleet_console/server.py
 A console-ui/backend/agentfleet_console/store.py
 R console-ui/styles.css -> console-ui/frontend/assets/styles.css
 R console-ui/tokens.css -> console-ui/frontend/assets/tokens.css
 R console-ui/index.html -> console-ui/frontend/index.html
 R console-ui/api.js -> console-ui/frontend/src/api.js
 R console-ui/app.js -> console-ui/frontend/src/app.js
 M console-ui/package.py
 M console-ui/run.py
 M console-ui/test_backend.py
 M console-ui/test_package.py
```

`git diff --cached --name-only` returned no output; new paths have intent-to-add entries only. Protected `git diff --stat` and `git status --short --untracked-files=all` returned no output.

No SSH/THINKPAD operations, external provider calls, installed-state mutation, commit, push, merge or deployment occurred. Registry, compatibility hash enforcement and fallback UI were not implemented in phase 1. The pre-existing worker CLI `usage.py` packaging import-closure concern remains deferred to phase 3; worker source/module lists were not changed for it. Windows checks do not establish Linux installed-host permissions or real-service readiness.

Related documents: [Console README](README.md), [Master plan](../Docs/00-master/console-worker-registry-noissue_master_261015_v1.0.0.md).

| Version | Date/time Asia/Saigon | Change |
|---|---|---|
| v1.0.1 | 2026-10-15 16:53:44 | Consolidated final main tests, immutable checks, reviewer output, controlled browser parity/provenance and exact Git state. |
| v1.0.0 | 2026-10-15 16:37 | Recorded actual phase 1 integration results and remaining gates. |

Cleanup note: Automatic approval review rejected deletion of the stopped original-source TEMP snapshot with reason "blocked by policy". It remains at `C:\Users\AD\AppData\Local\Temp\agentbuilder-parity-original-51ed95ddbc894774b07e37779b5204bd`; no alternate deletion was attempted. All three managed phase 1 worktrees are archived with recoverable snapshots.
