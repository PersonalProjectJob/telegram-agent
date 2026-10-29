# Source Mirror — Engineering

Owner: AlexChu · Status: implementation authorized · Version: v1.0.7 (original filename retained)

## Solution vs codebase
A standalone `source-mirror/` directory, separate configuration, database, browser profiles, unit and data root; no imports, state, credentials or control of existing workers. No changes to existing services.
## Technical
SQLite stores canonical full SHA-256 identities, immutable snapshot references, publication intent, reservations and results. A transaction serializes reservations, includes unresolved outcomes across dates and enforces four per Vietnam day. Any uncertain write globally blocks new publishes until explicit reconciliation. The worker has exclusive process lock. Capture stages into temporary paths, validates bounded media and checksums, atomically promotes snapshot, then marks ready; revalidate before publishing. Playwright uses isolated profiles and real UI; no website write API or application state injection. Credentials remain in environment or dedicated browser profile, never docs or logs. URLs and paths must be constrained; unsupported structure is held without dropping content. Scheduler processes one slot at a time and only current-day missed slots. Initial config remains paused.
### Risks and mitigation
Editor capability, public access and source fidelity must be demonstrated live; unknown forms fail closed. Intent is committed before the first potentially mutating click; timeout never causes replay. An interrupted pre-write capture can safely retry, whereas an interrupted write requires reconciliation. Do not restore old database over later intents.
## To-Do List
- [ ] Observe actual source and destination, implement supported contracts.
- [ ] Implement snapshots, quota, locking, CLI and scheduler.
- [ ] Test crash/replay, duplicates, quota and boundaries.
- [ ] Install isolated unit paused and run one controlled real canary; verify public article.
- [ ] Enable schedule and observe a full actual day.

## Implemented and verified
Independent user, state, browser profiles, unit and browser cache on the host. The successful publisher is unchanged in the v10 rollout. The crawler uses observed article pagination, a five-page budget and persisted cursor; it rejects unsupported layouts, title lengths, image formats and galleries over ten before ready. Current source taxonomy support is narrow, and shifting archive pages may be missed. Listing order has not been proved equivalent to source publication-date order. Derived publication and daily JSON reports are rebuilt from SQLite; after a crash or export failure they may remain stale until a status rebuild. Public uploads are re-encoded; measured dimensions and visual content matched the source, not bytes.

## Update at 19:45 VN

The v11 publisher verifies the effective bold/italic and link target for each non-whitespace character, independent of equivalent editor nesting, in addition to ordered content, media and block checks. A real guarded form preparation passed before rollout; the unresolved pre-submit attempt was reconciled before resume.

## Update at 19:47 VN

The independent unit was stopped during a single official CLI recovery and restarted afterward; no quota counter was reset. The fourth publication, if produced, must count the three existing successes and stay within the daily ceiling.

## Update at 20:20 VN

The v15 publisher supplies and verifies plain summaries and checks both observed home-card variants. A guest-context cover wait is bounded and verified in a two-browser-context regression test. Home-feed absence may conservatively hold an otherwise published article for reconciliation.

## Update at 20:24 VN

The v15 worker is enabled, active and unpaused. A fifth official CLI attempt was rejected at the daily quota without adding an attempt; the daily report records four published and zero unresolved. Source layout/taxonomy is narrow, listing order is not verified date order, and archive shifts can leave gaps.

## Related Documents
- [Master](../00-master/nailhub-source-mirror-noissue_master_261028_v1.0.0.md)
- [Product](../01-product/nailhub-source-mirror-noissue_product_261028_v1.0.0.md)
- [QA](../03-qa/nailhub-source-mirror-noissue_qa_261028_v1.0.0.md)
- Issue / PR: none.

## AGY localization integration — implementation plan

1. Fixed-model Unix socket broker runs as authenticated startechai with own workdir, systemd sandbox and NoNewPrivileges; only worker UID accepted, bounded JSON/timeout/output, no user-controlled executable/argv or shell.
2. Generate VI+locale SEO and global tags; fresh separate content MQM assessment sees source/candidate/glossary without generator self-score. Strict local schema, HTML/link/media/step/protected datum checks override model verdicts. Ambiguity or any unresolved error fails closed.
3. Store append-only localization revision outside immutable snapshot manifest: source manifest digest, payload/glossary/prompt hashes, requested model/effort, raw CLI results/usage, checks, MQM, hold reason. Verify revision binding before publish.
4. Gate candidate before transactional reservation; new persistent QC hold cannot be revived by crawler. Bound scan; no intent/unknown/quota cost for prepublication QC failure. Existing unknown and daily4 invariants remain.
5. Feed exact frozen VI and original EN payload into editor; fill locale Meta and global hashtags; observe actual UI POST then readback persisted locales/meta/tags, verify guest default VI and existing card contracts. Never direct API writes.
6. Deploy only reviewed/tests-passing code, own service/broker; no NailBlog changes. Broker outage means no publication, no English fallback. Systemd auth reuse/restart must be tested; interactive SSH success alone insufficient.

## Localization deployment — 2026-10-29

Bản v19 đã cài và worker cùng AGY broker active, unpaused. Cấu hình bắt buộc Gemini 3.8 Flash / medium và kiểm glossary/MQM; lỗi hoặc bất định kỹ thuật giữ bài trước reservation. Linux 38 tests PASS; review độc lập APPROVED. Form thật trên fixture đã qua guard VI/EN, Meta, hashtag; request cố ý chặn trước mạng nên không có bài thử PROD. Hai bài nguồn thật trên bản sao thử bị HOLD: thiếu thuật ngữ và JSON model không hợp lệ. Slot 12:00 chưa chạy ở thời điểm triển khai; chưa có bằng chứng bài Việt hóa publish hay public readback VI/EN. Đánh giá MQM tự động bằng model chỉ là heuristic. Không commit.

## Changelog
| Version | Date | Time (VN) | Change |
|---|---|---|---|
| v1.0.7 | 2026-10-29 | 10:40 | Deploy v19; Linux 38 tests and blocked native form guard passed; first actual localized slot pending |
| v1.0.6 | 2026-10-29 | 10:15 | Authorize and specify strict AGY localization gating for future slots; deployment pending checks |
| v1.0.0 | 2026-10-28 | 17:56 | Initial document |
| v1.0.1 | 2026-10-28 | 19:32 | Record verified canary, rollout and remaining operating limits |
| v1.0.2 | 2026-10-28 | 19:45 | Record second publication and verified inline-style fix |
| v1.0.3 | 2026-10-28 | 19:47 | Record third publication and manual recovery |
| v1.0.4 | 2026-10-28 | 20:20 | Record verified card correction and v15 resume |
| v1.0.5 | 2026-10-28 | 20:24 | Record four rollout-day publications and quota proof |
