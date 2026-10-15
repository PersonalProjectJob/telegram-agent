# Engineering: AgentFleet settings backend

Version v1.0.0 · 2026-10-14 · Owner AlexChu · executor preference explicitly selected: Codex gpt-6.1-sol medium. UI Visual Gate approved in chat. Implementation authorized; no commit/push/deploy requested.

## Solution vs codebase
Baseline: b0f6b0c service split. `console-ui/` is the approved HTML UI; Python stdlib preview launcher. The main checkout still contains legacy mixed-service files and unrelated dirty changes: implement against the attached split worktree, do not overwrite legacy runtime files when delivering the console.

| Area | Scope |
|---|---|
| Console Python | `console-ui/run.py`, new backend modules and tests: real JSON API, provider tests, persistent per-service configuration and scoped apply |
| Console UI | `console-ui/app.js`, `index.html`, narrow CSS adjustments: replace simulation with API and errors, retain approved layout |
| Triage runtime | `listener/src/tg_triage/config.py`, provider/transport modules, tests/example config: cloud adapters behind existing parser/invariants |
| NailHub runtime | `nailhub/src/nailhub/config.py`, model/transport modules, tests/example config: configurable AI with existing semantic/evidence/Owner gates |
| Documentation | Master/Product approved; this Engineering, then QA, then Tasks. No source skill/harness edits. |

## Frontend Section
All requests through `console-ui/api.js`, not scattered raw fetch calls. Bootstrap reads actual redacted settings/revisions; no fake defaults after connection failure. Drafts remain per service; read errors block save/apply but allow retry. Server errors preserve draft/key in memory. API key never enters storage, history, URL or diff. Provider/endpoint edits clear draft key and invalidate test. Cloud consent must be explicit before sending real task content; harmless connection probe contains no Telegram/posting data.

### API contract (owned by console backend)
- `GET /api/bootstrap` → `{csrfToken, services:{triage,nailhub}, history, host}`. Each service: `{settings, revision, appliedRevision, hasKey, keyScope, status}`. `settings` uses existing UI field names (provider, model, baseUrl, timeout, maxOutput, think and each service's operational fields). `keyScope` is provider + endpoint, never key. `status` distinguishes systemd status from proven applied revision.
- `POST /api/services/{id}/test` → `{settings, apiKey?}`; `{status,code,message,elapsedMs}`. Test draft only; a real bounded JSON completion, no task data, no save. Failures have safe fixed messages/codes, not upstream body. An empty key reuses saved key only for matching provider+endpoint.
- `POST /api/services/{id}/save` → `{settings,apiKey?,expectedRevision}`; returns updated redacted service + history. Stale revision returns 409. Preserve unrelated TOML fields and protected values; validate on server and with runtime config contract before commit.
- `POST /api/services/{id}/apply` → `{expectedRevision}`; synchronous redacted `{service,history}`. Applied revision remains unknown until backend evidence establishes it; systemd active alone is insufficient. Receipt matches revision, current MainPID, startup timestamp and effective mode.
- Fixed errors: `{error:{code,message,field?}}` with relevant HTTP status. Browser sends CSRF token header for POST. Same-origin only, strict Host/Origin checks, size limits, no CORS, no key logging.

## Backend Section
### Running from source
Keep `python console-ui/run.py`. Default loopback and browser launch. Default development binding may initialize persistent local configs outside Git under ignored `.agentfleet/`; show that these are local files, not the THINKPAD services. Explicit host paths/host profile are CLI inputs, never request-body paths. Linux installed service paths: `/etc/tg-triage/listener.toml`, `/etc/nailhub/nailhub.toml`; unit allowlist exactly `tg-triage.service`, `tg-nailhub.service`. Windows cannot claim systemd apply works; provide a clear unavailable result while real API tests and local file persistence work. Document required OS permissions without granting wide sudo or running the web handler as root.

### Persistence, validation and apply
Per-service write lock, expected revision conflict checks and external file drift detection. Atomic config write, restrictive secret file storage, redacted audit, no credentials in TOML/public bootstrap. Keep unknown sections/paths/Owner/VERIFIED immutable through API. Switching provider/endpoint requires an appropriate key; never forward old key to new destination. Crash/failure in multi-file updates must not leave new config referring to a missing key. Save creates a revision but never restarts; apply validates the saved revision and exact target service, performs graceful restart through fixed unit commands and verifies revision/config/runtime before reporting applied. Failed restart must preserve truthful disk/running status and not restore business DB or silently retry external mutations.

### Providers/runtime
Support Ollama, Anthropic Messages, OpenAI, DeepSeek and OpenAI-compatible chat endpoints. Use standard library HTTP where practical, no added unrelated packages. Enforce HTTPS for cloud, restrict custom endpoint network access against SSRF/DNS rebinding and redirects; allow loopback only for explicit Ollama. Bound response size/time/output; redact exceptions. Expose only capability actually supported. Triage cloud path redacts e-mail/phone and never sends images unless separately supported/authorized; conservative fail/hold for unsupported imagery. NailHub cloud uses original evidence needed by validation; require explicit cloud consent and preserve all Owner/VERIFIED/category/evidence gates. Model failure continues existing hold/needs-human behavior, never approval bypass.

Cross-package dependencies must not merge the two venvs: transport helpers can be packaged independently inside each service with a parity test and documented reason, or a separately installed reusable package. Console test path should use the same transport contract as runtime, not a third divergent fake. Existing Ollama behavior/tests must remain compatible.

### Research sources
[Anthropic API](https://platform.claude.com/docs/en/api/overview), [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs), [DeepSeek JSON](https://api-docs.deepseek.com/guides/json_mode/), [Ollama chat](https://docs.ollama.com/api/chat). Every returned object remains untrusted and is validated by existing application parsers.

## To-Do List
- [x] Console API security, bootstrap, schemas and real test transport.
- [x] Atomic per-service persistence, private ACL/secrets and revision history.
- [x] Fixed-unit Linux apply implementation and truthful unsupported/failed states; actual host acceptance remains pending.
- [x] Runtime provider adapters and config compatibility for both services.
- [x] Replace demo UI actions with backend requests; preserve layout and drafts.
- [x] Unit/integration/E2E tests, independent source review and host limitations documented in [verification](../../console-ui/BACKEND-VERIFICATION.md).

## Related Documents
[Master](../00-master/agentfleet-settings-1_master_261014_v1.0.2.md) · [Product](../01-product/agentfleet-settings-1_product_261014_v1.0.0.md) · [QA](../03-qa/agentfleet-settings-1_qa_261014_v1.0.0.md) · [Tasks](../04-tasks/agentfleet-settings-1_tasks_261014_v1.0.0.md). GitHub issue/PR not created.

## Changelog
| Version | Date | Time | Author | Change |
|---|---|---|---|---|
| v1.0.0 | 2026-10-14 | 11:44 | AlexChu / Codex | Backend contract and source-launch/runtime integration plan after Owner UI approval |

## Portable source package — 2026-10-15

Owner approved source packaging against origin/main d414102 in isolated `codex/agentfleet-console-package`; primary main remains pull-only. Preserve latest NailHub BR-13/14 and post-site-AI lifecycle.

Plan: exact file allowlist → current source bytes + checksums manifest → safe portable ZIP → extract outside Git → stdlib-only launcher/bootstrap/save/reload checks. No state, credentials, tests, caches, private evidence or Git metadata enters ZIP. Root README and Windows/Linux launchers explain Python 3.11+ and local/installed boundaries.

- [x] Add deterministic stdlib packager and integrity verifier.
- [x] Test extraction, source edits, exclusions, symlinks and output safety.
- [x] Run current backend/listener/NailHub suites and preserve business rules.
- [x] Build distributable ZIP and report checksum and remaining host limitations.

Packaging implementation: `console-ui/package.py` reads57 exact source paths plus generated manifest, uses portable POSIX ZIP paths and deterministic metadata, refuses existing output/source outputs outside dist and symlinks/reparse points, and verifies schema/file set/size/checksums without extraction. Windows/Linux terminal launchers locate source relative to themselves. Console remains Python3.11+ standard-library-only; worker installation remains separate. Latest main's NailHub prompts/BR-13/14 and business paths were retained unchanged by the packaging delta.

Final source ZIP: `dist/agentfleet-console-20261015.zip`,58entries,220565bytes; SHA256 `76d77c2ed54f7d78de0c665dc53f0fc71275dd288a5786639cc04f0e9940fe5a`. Parent independently extracted and launched it. Tests/evidence/state are intentionally not bundled. See QA packaging addendum for exact outputs. No commit/push/PR/deployment.
