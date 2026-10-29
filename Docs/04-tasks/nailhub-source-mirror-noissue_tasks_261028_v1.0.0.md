# Source Mirror — Tasks

Owner: AlexChu · Status: implementation authorized · Version: v1.0.11 (original filename retained)

## Plan
- [x] Master business and owner direction received.
- [x] Product, Engineering and QA documents created in sequence.
- [x] Inspect real source and destination forms; document supported mapping.
- [x] Implement isolated storage, capture, UI publishing and operations.
- [x] Execute meaningful automated tests and independent review.
- [x] Install paused on THINKPAD; verify independent resources.
- [x] Execute one controlled canary and verify public URL/content/media.
- [x] Enable the four-per-day schedule initially.
- [x] Correct oversized article cards, verify and resume the paused worker.
- [ ] Observe a full unattended Vietnam calendar day and verify four scheduled outcomes.

Each phase is gated by real evidence. An unresolved write or unrepresentable article is held, not retried blindly.

## Actual progress
- [x] Source and production UI inspected; first article captured on the host.
- [x] Independent unit installed and first canary published and guest-verified.
- [x] Source-only intake update reviewed, tested on host and deployed; scheduler resumed.
- [x] Verify four publications on the partial rollout day and confirm the fifth-attempt quota guard.
Known limitations / future scope: narrow source taxonomy, source-date ordering and gaps caused by archive shifts; the current cursor does not prove exhaustive coverage.

## Update at 19:45 VN

The second article was published; the third pre-submit hold was investigated, fixed, verified in a guarded form and reconciled as not created. Scheduling resumed. Continue to verify remaining publications and quota.

## Update at 19:47 VN

Three publications now exist; the third used the official manual recovery path after reconciliation. The unit is restarted; observe the 20:00 slot and final quota before closing the four-per-day item.

## Update at 20:20 VN

The oversized card issue was corrected for all three articles and verified on desktop and mobile. The v15 worker passed host tests and resumed; observe the fourth article and final quota.

## Update at 20:24 VN

The rollout day reached four actual publications and the fifth-attempt quota guard passed. Two were manual canary/recovery and two were scheduled catch-up. Continue the separate full unattended calendar-day observation; it is not yet a passed gate.

## Documentation sync and URL-command request — 2026-10-29

- [x] Record the owner's `nailblog <URL>` business request in Master §10.1, separately from deployed capabilities.
- [x] Copy the five latest Source Mirror documents to the main AgentBuilder checkout without committing or changing unrelated files.
- [x] Verify source/destination hashes and all local document cross-links: five matching files, 14 valid local links, balanced fences and no trailing whitespace; existing working diff, staged diff and HEAD unchanged.
- [ ] Future implementation: choose a dedicated command entry point, map supported article/listing URLs to Source Mirror jobs, and verify quota, deduplication, summary and guest-card behavior. The command is not implemented by this documentation-only update.

## Localization analysis — 2026-10-29
- Analysis update 2026-10-29: Master §10.3 records the proposed Vietnamese localization flow. The currently deployed publisher still mirrors English in both language tabs.
- [x] Document VI editorial localization, EN source preservation, per-language Meta, shared-tag constraints, immutable revisions and one-blog quota semantics.
- [ ] Future scope: detailed Product/Engineering/QA, content-runtime/provider decisions, glossary, locale/public-head verification and a real bilingual canary before rollout. No content generation, backfill or runtime change was performed by this analysis.

## Related Documents
- [Master](../00-master/nailhub-source-mirror-noissue_master_261028_v1.0.0.md)
- [QA](../03-qa/nailhub-source-mirror-noissue_qa_261028_v1.0.0.md)
- Previous: Product and Engineering
- Issue / PR: none.

## AGY slot integration — active

- [x] Owner explicitly authorized AGY Flash medium localization and HOLD on any failure/technical ambiguity.
- [x] Stop only own source-mirror worker before next slot; baseline today08 published, no unknown.
- [ ] Implement fixed broker + immutable localization revisions + independent content MQM + persistent pre-reservation QC holds.
- [ ] Map and verify per-locale UI body/summary/title/Meta and global hashtags, preserving image/card guards.
- [ ] Run targeted Linux tests and independent code/security review; fix findings.
- [ ] Exercise actual systemd broker + valid/held candidates, no publication before gate.
- [ ] Deploy reviewed version and resume own worker for next slots; record accepted/held outcomes without forcing quota.
- [ ] Verify at least one real localized publication if any candidate passes; otherwise report held backlog honestly.

## Localization deployment — 2026-10-29

Bản v19 đã cài và worker cùng AGY broker active, unpaused. Cấu hình bắt buộc Gemini 3.8 Flash / medium và kiểm glossary/MQM; lỗi hoặc bất định kỹ thuật giữ bài trước reservation. Linux 38 tests PASS; review độc lập APPROVED. Form thật trên fixture đã qua guard VI/EN, Meta, hashtag; request cố ý chặn trước mạng nên không có bài thử PROD. Hai bài nguồn thật trên bản sao thử bị HOLD: thiếu thuật ngữ và JSON model không hợp lệ. Slot 12:00 chưa chạy ở thời điểm triển khai; chưa có bằng chứng bài Việt hóa publish hay public readback VI/EN. Đánh giá MQM tự động bằng model chỉ là heuristic. Không commit.

## Changelog
| Version | Date | Time (VN) | Change |
|---|---|---|---|
| v1.0.11 | 2026-10-29 | 10:40 | Deploy v19; Linux 38 tests and blocked native form guard passed; first actual localized slot pending |
| v1.0.10 | 2026-10-29 | 10:15 | Authorize and specify strict AGY localization gating for future slots; deployment pending checks |
| v1.0.9 | 2026-10-29 | 09:22 | Record proposed VI/EN localization and validation work; keep baseline/runtime and implementation status explicit |
| v1.0.8 | 2026-10-29 | 08:35 | Sync the five documents to the main checkout; record the URL-command business specification and separate unimplemented work; no commit |
| v1.0.0 | 2026-10-28 | 17:56 | Initial document |
| v1.0.1 | 2026-10-28 | 19:32 | Record verified canary, rollout and remaining operating limits |
| v1.0.2 | 2026-10-28 | 19:45 | Record second publication and verified inline-style fix |
| v1.0.3 | 2026-10-28 | 19:47 | Record third publication and manual recovery |
| v1.0.4 | 2026-10-28 | 19:58 | Align task checks with verified work and separate full unattended day |
| v1.0.5 | 2026-10-28 | 20:05 | Track card correction and paused worker |
| v1.0.6 | 2026-10-28 | 20:20 | Record verified card correction and v15 resume |
| v1.0.7 | 2026-10-28 | 20:24 | Record four rollout-day publications and quota proof |
