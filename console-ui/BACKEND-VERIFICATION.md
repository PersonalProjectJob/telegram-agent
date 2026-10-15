# Backend integration verification — 2026-10-14

Owner approved the Hallmark UI and authorized real API testing, persistence and per-service apply. Implementation uses the isolated service-split worktree at `C:/Users/AD/.codex/worktrees/agentfleet-settings-ui/AgentBuilder`, baseline `b0f6b0c`. The dirty legacy main checkout was preserved. No commit, push, SSH, host provisioning or deployment was performed.

## Verified execution

Parent ran these commands after integration (Windows, Python 3.12); the final console suite also covers the later bounded-transport error classification changes:

```powershell
python -m unittest discover -s console-ui -p "test_*.py" -q
# Ran 31 tests in 34.325s — OK, exit 0

$env:PYTHONPATH='listener/src;listener'
python -m unittest discover -s listener/tests -t listener -q
# Ran 603 tests in 40.109s — OK, exit 0

$env:PYTHONPATH='nailhub/src;nailhub'
python -m unittest discover -s nailhub/tests -t nailhub -q
# Ran 181 tests in 29.420s — OK (skipped=5), exit 0

node --check console-ui/app.js
node --check console-ui/api.js
git diff --check
# exit 0
```

The backend executor additionally ran `python -m unittest deploy.test_deploy -q`: 45 tests, OK, including real staged runtime suites (603 / 181 with 5 skips), and Python compileall. These are executor outputs, separately source-reviewed; the parent did not repeat deployment fixture tests.

New backend tests cover persistence/isolation, CAS across processes and same-server requests, crash recovery/external drift, unknown TOML table preservation, exact fractional MiB, live/proxy schema without service secrets, protected fields, credential scope/redaction, strict probe JSON boolean, CSRF/Host/Origin/path/body boundaries, custom SSRF/DNS rebinding, redirect refusal, real loopback HTTP errors (401/404/429/JSON/oversize/header and stalled-body timeouts), provider protocol normalization, runtime cloud consent/redaction/images, transport parity and fixed-unit apply receipts/failures. Negative receipt tests reject wrong revision/PID/startup timestamp/mode. Actual NTFS ACL provisioning was exercised. Cloud protocol tests inject recording transports; they are not live vendor acceptance.

## Browser verification

Playwright 1.62 / Chromium headless, fresh backend state and context per viewport. Real click/fill/check/reload interactions; no application state or CSS injection. All provider requests in this automated flow go to a **recording fake Ollama upstream over real HTTP**, with a synthetic JSON prompt. OpenAI key persistence uses a synthetic credential and makes no vendor call.

| Viewport | Flow | PNGs | Page / console errors | API origin |
|---|---|---|---|---|
| Desktop 1440×900 | FLOW_VERIFIED | 12 | 0 / 0 | Local, verified |
| Tablet portrait 768×1024 | FLOW_VERIFIED | 12 | 0 / 0 | Local, verified |
| Tablet landscape 1024×768 | FLOW_VERIFIED | 12 | 0 / 0 | Local, verified |
| Mobile 375×812 | FLOW_VERIFIED | 12 | 0 / 0 | Local, verified |

Each flow covers all tabs of both services, synthetic connection test, review, actual save, reload proof, redacted saved key, overview and history. Forty-four DOM geometry measurements across the four viewport routes/tabs found zero horizontal overflow. Modal evidence uses viewport capture so the native backdrop is represented correctly; other pages use full-page capture.

Additional browser regressions: reload removes replacement key and cancels its pending test; loading read blocks form edits; two real API clients receive 409 without losing the stale draft and can reload the winner; contract-only delayed apply prevents overlapping save/reload and leaves applied revision unknown on failure. **Apply HTTP is mocked in that last browser regression**; no systemd ran on Windows.

Independent `ui_review` source agent found and re-reviewed fixes for file permissions, TOML preservation, thread/process locking, startup environment, exact byte limits, probe priority, frontend races and strict probe boolean typing. Final source review had no remaining findings. Parent performed execution verification; source review alone was not counted as execution evidence.

## Limits and pending acceptance

- Live Claude/OpenAI/DeepSeek/custom provider credentials were not supplied or called. The UI now performs those calls when the Owner enters credentials and explicitly clicks Test.
- Actual Linux/ThinkPad restart, OS rights, upgraded installed wheel, fresh readiness receipt and service modes remain **NOT VERIFIED**. Follow [README.md](README.md) provisioning first. Windows local mode cannot control the remote host.
- Runtime PID/status is the last confirmed acknowledgement, not continuous health monitoring. Remote connector, advanced selectors/options, other service secrets and rollback UI are not delivered in this approved-UI integration slice.
- Canonical harness gate could not run: `harness-verify.mjs` supports Node projects only and this repository has no package.json. Do not mark its gate PASS or add a fake Node manifest. Exact output (exit 1):

```text
Không thấy package.json trong C:\Users\AD\Documents\GitHub\AgentBuilder — harness-verify hiện chỉ hỗ trợ project Node.
```

## Evidence

`C:/Users/AD/.codex/visualizations/2026/10/14/01a1383c-d010-74a3-b746-069fd9cc9d75/agentfleet-settings-backend`

Contains per-viewport run audits and PNGs, recording upstream summary, geometry and race results; local `FEATURE.md`, manifest, SHA256SUMS and sibling ZIP. Private state/key files are excluded from the bundle. Prototype design evidence remains separate in `agentfleet-settings`.

## Working tree at handoff

Exact `git status --short` (all changes within feature scope; private `.agentfleet/` and `.e2e/` excluded):

```text
 M .gitignore
 M config/listener.example.toml
 M config/nailhub.example.toml
 M listener/src/tg_triage/classifier.py
 M listener/src/tg_triage/config.py
 M listener/src/tg_triage/model_lock.py
 M listener/src/tg_triage/pipeline.py
 M listener/src/tg_triage/provider.py
 M listener/src/tg_triage/telegram_io.py
 M nailhub/src/nailhub/config.py
 M nailhub/src/nailhub/model.py
 M nailhub/src/nailhub/model_lock.py
 M nailhub/src/nailhub/telegram.py
 M nailhub/tests/test_nailhub_state_model.py
 M systemd/tg-nailhub.service
?? .hallmark/
?? Docs/00-master/agentfleet-settings-1_master_261014_v1.0.2.md
?? Docs/01-product/
?? Docs/02-engineering/agentfleet-settings-1_engineering_261014_v1.0.0.md
?? Docs/03-qa/
?? Docs/04-tasks/
?? console-ui/
?? listener/src/tg_triage/ai_transport.py
?? listener/src/tg_triage/receipt.py
?? nailhub/src/nailhub/ai_transport.py
?? nailhub/src/nailhub/receipt.py
```
