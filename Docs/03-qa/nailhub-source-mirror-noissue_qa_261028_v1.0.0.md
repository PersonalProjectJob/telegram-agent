# Source Mirror — QA

Owner: AlexChu · Status: implementation authorized · Version: v1.0.9 (original filename retained)

## Test Cases
| ID | Scenario | Expected evidence |
|---|---|---|
| Q1 | Duplicate canonical URL, restarts, competing workers | One identity, no duplicate intent; transaction and process-lock tests |
| Q2 | Four reservations/publications, fifth attempt, cross-day unresolved | Fifth blocked; uncertain outcome globally blocks until reconciled; Vietnam date test |
| Q3 | Crash during snapshot, missing or modified media | No ready snapshot; checksum validation fails |
| Q4 | Unsupported content, bad or oversized media | Article held with reason; no content silently removed |
| Q5 | Timeout after mutating action | Unknown outcome persists and no replay; reconciled by verified result |
| Q6 | Source and production real UI | Browser captures complete source, fills actual form, Published and guest URL reads back matching ordered content/media |
| Q7 | Isolation | Separate unit, profile, paths, config; existing service config and status unchanged |
| Q8 | Actual schedule | Four verified public articles in a Vietnam day when eligible; no more than four |

Automated fixtures and mocks cannot prove Q6–Q8. Evidence must be from the actual host and live UI; no screenshots are invented. No responsive, cross-browser or accessibility UI changes are introduced.

## Execution record
- First real canary: published article opened independently as a guest with HTTP 200, exact title, nine ordered text blocks, five loaded 600×600 images and source link; admin and guest evidence retained on host.
- Target host: 16 Python tests passed; 15-image source held before ready, and three more eligible source articles captured in an isolated probe.
- The independent unit was temporarily stopped and the database paused after the user reported oversized article cards. An entire four-article operating day and ongoing archive coverage remain unverified; do not mark Q8 passed yet.
- The initial controlled-rollout check found three existing worker PIDs unchanged. A later check found one PID had changed; the cause is not established. Media was re-encoded; visual comparison is not byte identity.

## Update at 19:45 VN

The second scheduled article was published. Attempt 3 stopped before Create; exact-title admin search found no created article. The semantic inline fix passed guarded real-form preparation and 17 tests on the host. The independent worker resumed initially; it was subsequently stopped for the card issue. Four published in the day is not yet verified.

## Update at 19:47 VN

The third article was published by official CLI recovery, not by the 16:00 scheduler slot. Independent fresh guest audit passed for all three: HTTP 200, expected 5/6/6 loaded inline images and source links. Four publications and the 20:00 scheduled slot are not yet verified.

## Update at 20:20 VN

All three existing articles were corrected through guarded UI edits; six desktop/mobile card audits passed. Independent receipt review found article title, body, media and publication fields preserved. v15 passed 22 Linux tests; the resumed worker has not yet verified a fourth publication.

## Update at 20:24 VN

Final evidence: four actual publications today; all four cards passed fresh desktop and mobile checks (eight records), and the fourth guest article returned HTTP 200 with nine loaded inline images. An official fifth attempt exited 1 with daily quota reached and no new attempt. Host suite: 22 tests passed. Two publications were manual, two scheduled catch-up; full unattended day remains unverified.

## Related Documents
- [Master](../00-master/nailhub-source-mirror-noissue_master_261028_v1.0.0.md)
- [Engineering](../02-engineering/nailhub-source-mirror-noissue_engineering_261028_v1.0.0.md)
- [Tasks](../04-tasks/nailhub-source-mirror-noissue_tasks_261028_v1.0.0.md)
- Issue / PR: none.

## Localization release checks — pending execution

- P0: broker model/effort fixed; only expected SO_PEERCRED UID accepted; malformed/oversized/hung request rejected and time bounded.
- P0: fail broker/auth/timeout, malformed generation or MQM, conflicting PASS+issues, unknown severity/count mismatch, uncertainty, wrong numbers/links/media/steps/HTML all prevent Create.
- P0: known source ambiguity after both layers is held even if model omits it; held article recrawl cannot restore ready. Pre-QC failures create no intent/unknown and consume no slot/quota; next valid candidate may proceed.
- P0: revision/source/gate tampering cannot publish. Freeze exact VI/EN payload; no English fallback when localization is required.
- P0: genuine UI form field checks and request/admin readback for VI/EN body/title/summary/Meta/tags. No product state injection or API writes.
- P1: guest VI body and cards use correct locale; plain summary remains bounded, one cover in image card, no inline excerpt images. Reference/image order unchanged.
- P1: actual systemd broker inference plus restart retains access, worker independent; daily4 and unknown/quota regression tests remain passing.

Pending: machine outputs and independent review, real runtime gate and hold receipts, rollout version/service status. Model assessment is heuristic and does not certify domain accuracy.

## Localization deployment — 2026-10-29

Bản v19 đã cài và worker cùng AGY broker active, unpaused. Cấu hình bắt buộc Gemini 3.8 Flash / medium và kiểm glossary/MQM; lỗi hoặc bất định kỹ thuật giữ bài trước reservation. Linux 38 tests PASS; review độc lập APPROVED. Form thật trên fixture đã qua guard VI/EN, Meta, hashtag; request cố ý chặn trước mạng nên không có bài thử PROD. Hai bài nguồn thật trên bản sao thử bị HOLD: thiếu thuật ngữ và JSON model không hợp lệ. Slot 12:00 chưa chạy ở thời điểm triển khai; chưa có bằng chứng bài Việt hóa publish hay public readback VI/EN. Đánh giá MQM tự động bằng model chỉ là heuristic. Không commit.

## Changelog
| Version | Date | Time (VN) | Change |
|---|---|---|---|
| v1.0.9 | 2026-10-29 | 10:40 | Deploy v19; Linux 38 tests and blocked native form guard passed; first actual localized slot pending |
| v1.0.8 | 2026-10-29 | 10:15 | Authorize and specify strict AGY localization gating for future slots; deployment pending checks |
| v1.0.0 | 2026-10-28 | 17:56 | Initial document |
| v1.0.1 | 2026-10-28 | 19:32 | Record verified canary, rollout and remaining operating limits |
| v1.0.2 | 2026-10-28 | 19:45 | Record second publication and verified inline-style fix |
| v1.0.3 | 2026-10-28 | 19:47 | Record third publication and manual recovery |
| v1.0.4 | 2026-10-28 | 19:51 | Record independent guest audits for three articles |
| v1.0.5 | 2026-10-28 | 20:05 | Record card issue and paused worker |
| v1.0.6 | 2026-10-28 | 20:20 | Record verified card correction and v15 resume |
| v1.0.7 | 2026-10-28 | 20:24 | Record four rollout-day publications and quota proof |
