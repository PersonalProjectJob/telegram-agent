---
type: doc
title: NailBlog Phase 2 staging QA
owner: AlexChu
status: blocked-staging-acceptance
---

> **Cập nhật đối chiếu v1.0.3 — 2026-10-28 14:17 (Asia/Ho_Chi_Minh).** Tác giả: Codex; trạng thái: chờ review.

**Đối chiếu hiện trạng source:** commit `89a5ac8` trên nhánh `claude/nailblog-automation-review-091e47`. Đây là trạng thái mã nguồn, không khẳng định cấu hình hay phiên bản đang chạy trên THINKPAD. Các mô tả phía dưới có thể thuộc quyết định cũ; khi khác với bản cập nhật này, dùng phần cập nhật và kiểm tra cấu hình thực tế.

**Tài liệu lịch sử:** các kết quả, cấu hình, nhà cung cấp và hướng dẫn dưới đây phản ánh thời điểm lập tài liệu; không dùng như hướng dẫn runtime hiện tại. Đối chiếu [nghiệp vụ hiện hành](../01-product/nailhub-blog-noissue_business_261015_v1.0.0.md), [kỹ thuật Agy](../02-engineering/nailhub-blog-noissue_engineering_261027_v1.0.0.md) và [runbook](../02-engineering/nailhub-blog-noissue_cutover-runbook_261027_v1.0.0.md).

**Lịch sử thay đổi:** v1.0.3 — 2026-10-28 14:17 — đối chiếu source, vận hành và bằng chứng; không thay đổi trạng thái phê duyệt cũ.

---

> **Nội dung lịch sử bên dưới:** giữ để truy vết; nếu khác với cập nhật phía trên, dùng phần cập nhật và kiểm tra cấu hình thực tế.


# NailBlog Phase 2 — QA v1.0.2

## Test Strategy

Scope: Python worker, admin UI adapter và checkpoint; không thay UI NailHub. Unit/integration không gọi provider trả phí hoặc production. Staging browser dùng credential/profile riêng. Baseline: 586 tests, 583 pass, 3 Windows permission skips. Responsive/a11y/CLS không áp dụng vì không thêm hoặc sửa giao diện.

## Test Cases

### TC-01: Môi trường và credential
Priority P0; Type security; Precondition config preview/staging và fixture credential ngoài repo.
Steps: nạp cặp origin đúng/sai, URL chứa userinfo/query/port/path; đọc credential thiếu/rỗng/symlink; đổi môi trường của profile/state/report.
Expected: cặp staging pin đúng, mismatch/credential lỗi bị từ chối, không log email/password, production report không mở khóa staging.

### TC-02: Guard theo thao tác
Priority P0; Type security; Precondition guard chưa mở operation.
Steps: gửi GET, POST/PUT/PATCH/DELETE, redirect, request sai origin/path/ID/payload; thử Create/Publish hai lần; bật preview.
Expected: preview chặn tất cả non-GET; live chỉ cho operation đúng một lần; không request ghi production kể cả popup; service worker bị tắt.

### TC-03: Draft roundtrip
Priority P0; Type happy/error; Precondition bài đủ trường và ảnh thật staging.
Steps: upload bằng file chooser, điền VI/SEO/category/tags/slot, paste HTML, kiểm editor, Create Draft rồi GET readback.
Expected: đúng status Draft, trường bài/ảnh/alt/caption khớp; mặc định Published bị chặn; placeholder hoặc Quill làm mất dữ liệu thì không Create.

### TC-04: Create timeout/restart
Priority P0; Type recovery; Precondition intent đã ghi trước click.
Steps: ngắt sau click trước receipt; khởi động lại, tìm bài theo identity đầy đủ; thử lại lệnh.
Expected: không Create lần hai; chỉ nhận đúng một bài khớp toàn bộ snapshot, thiếu hoặc nhiều match thì chờ xử lý.

### TC-05: Approval và publish claim
Priority P0; Type concurrency; Precondition Draft có readback và quality pass.
Steps: approve, sửa content/media/slot/environment/remote ID, pause, quá cửa sổ 15 phút; hai connection claim cùng bài và hai slot cùng ngày.
Expected: snapshot đổi bị từ chối; claim chỉ một winner và tối đa một bài/ngày; before_publish bền trước click.

### TC-06: Publish recovery và evidence
Priority P0; Type recovery; Precondition before_publish/uncertain.
Steps: timeout, restart, admin/Public response sai identity/content/media/status/timestamp hoặc URL công khai lỗi.
Expected: không click lại; chỉ published khi mọi bằng chứng khớp; còn lại uncertain, có notice.

## Acceptance Criteria

- [x] Unit/integration: `Ran 618 tests in 165.301s`, `OK (skipped=3)`; 615 pass, 3 Windows permission skips. Trong đó 32 tests Phase 2 mới.
- [x] Build wheel, import trực tiếp từ wheel và compileall thành công; diff check sạch.
- [x] Login/probe staging bằng worker profile riêng, authenticated=true.
- [x] Ghi BLOCKED bằng probe thật: editor_roundtrip=false; alt bị xóa và figure/figcaption chuyển thành p/text. Chưa lưu Draft/upload/publish.
- [ ] Publish/recovery staging được kiểm bằng flow thật; fixture tests không thay acceptance này.
- [x] Review độc lập vòng cuối: không còn blocker trong delta đã review; 32 tests `OK`. Không commit/push/deploy.

Kết quả staging chưa đạt TC-03 và TC-06 end-to-end. Ảnh probe dùng URL ảnh mẫu không tồn tại để kiểm cấu trúc editor; không phải bằng chứng ảnh sản xuất. `contract.verified` giữ false. Bằng chứng local: `C:/Users/AD/.agentfleet/evidence/nailblog-phase2/` (contract-probe.json/png, test/build log, review). Ảnh đã mở bằng view_image. Repo này không có source editor nền tảng để sửa dependency Quill trong phạm vi hiện tại.

`harness-verify --json` chưa hỗ trợ project Python (báo thiếu package.json); không khai harness gate PASS. Task metadata cũng chưa chuyển verify vì CLI không cho owner tự phục hồi lease đã hết; handoff chi tiết lưu trong task body. Verification Python và review độc lập ở trên là output thực tế.

## Test Data Requirements

Credential ngoài repo; fixture tài khoản giả trong unit tests; dữ liệu Draft thử có danh tính rõ và ảnh local có quyền sử dụng. Không dùng ảnh placeholder để tick chuẩn ảnh sản xuất.

## Environment Setup

`$env:PYTHONPATH='nailblog/src'; python -m unittest discover -s nailblog/tests -t nailblog`

Browser: Playwright Chromium, 1440×900, timezone Asia/Ho_Chi_Minh. Thời gian chờ có timeout cấu hình 5–120 giây; không benchmark UI bên thứ ba.

## Related Documents

- [Master](../00-master/nailhub-blog-noissue_master_261014_v1.0.0.md)
- [Business](../01-product/nailhub-blog-noissue_business_261015_v1.0.0.md)
- [Engineering](../02-engineering/nailhub-blog-noissue_engineering_261016_v1.0.0.md)

## Changelog

| Version | Date | Time | Author | Change |
|---|---|---|---|---|
| v1.0.2 | 2026-10-19 | 11:05 | AlexChu (Codex executor/review agents) | Create201 GUID, fresh request readback,10new regressions,630tests; full staging article public by explicit exception |
| v1.0.1 | 2026-10-19 | 10:24 | AlexChu (Codex executor) | 618 tests, wheel/import, review; BLOCKED staging roundtrip có evidence thật |
| v1.0.0 | 2026-10-19 | 10:10 | AlexChu (Codex executor) | QA staging trước acceptance Phase 2 |


## Authorized technical staging TEST — 2026-10-19 10:45–10:47 Asia/Saigon

Owner explicitly approved one TEST: Draft → Published immediately → public readback → Archived, waiving known HTML loss and the T−2h schedule for this run only.

Result: TECHNICAL_FLOW_PASS_CONTENT_FAIL. Article `bc99cf34-882a-4e99-8d15-5b273500457a`, title `[TEST E2E 20261019-104513] Kiểm tra luồng blog staging`. Uploaded one cover and three synthetic WebP gallery fixtures through UI. Exactly one Create request (201), one Published status request (204), one Archived status request (204). Draft, public and final Archived readbacks identify the same article. Public screenshot opened and visually checked. No duplicate Create/Publish.

Create exposed an adapter contract bug: HTTP 201 response is not a dict accepted by Admin.json_response. The diagnostic runner stopped and recovered the existing Draft by unique exact title with read-only reconciliation, then completed the same run. Normal Admin.save_draft still needs response-shape handling plus tests; this diagnostic is not a PASS for the unmodified adapter/service/approval/scheduler flow. No production content/image-quality PASS: editor strips alt/figure/figcaption and synthetic TEST content is not a real quality article. Normal contract.verified remains false; production acceptance remains blocked.

Evidence: `C:/Users/AD/.agentfleet/evidence/nailblog-authorized-test-20261019-104513/FEATURE.md`, run.json (durable intents, payloads, receipts and readbacks), PNGs and adjacent ZIP with SHA256SUMS. One-off runner outside repo uses staging-only, exact-path, single-use UI write capabilities authorized by this exception; no direct API writes, credential/token export, paid generation calls, commit or push.


## Create201 follow-up and full article — 2026-10-19 11:05 Asia/Saigon

Owner authorized this exact full article to remain Published on staging, accepting HTML loss and immediate publication. URL: https://staging.nailhub.ai/blog/bang-mau-nail-cach-tu-van-de-khach-de-chon . ID: `7c926810-d25f-4034-8f9f-45ab73f649fd`. Before editor: actual content quality gate PASS,1046words,cover1600×900 plus3distinct1200×675 editorial infographic WebP originals,FAQ3,5sections,SEO/summary151characters. After editor: HTML quality FAIL (approved exception), not a standard acceptance PASS.

### TC-07: Create response shapes and readback race

| Property | Value |
|---|---|
| Priority | P0 |
| Type | Happy path, error handling, recovery |
| Precondition | Uploaded expected snapshot and durable Create intent |

Steps: return201 dict ID/JSON GUID/empty/invalid body; return zero/two/wrong-status/wrong-content Draft matches; simulate stale canceled listing refresh before navigation; return fresh request with missing response. Expected: only one valid fully matching Draft is accepted; known ID mismatch stops; reconciliation is read-only; listing binds the newly observed request rather than an old response; missing response fails closed. Actual: PASS in44targeted tests, independent review no findings. Real staging confirms201 body JSON string GUID.

### TC-08: Restart after an unknown Create outcome

| Property | Value |
|---|---|
| Priority | P0 |
| Type | Error handling, integrity |
| Precondition | Materialized uploaded URLs persisted before timeout |

Steps: timeout after before_create; construct a new State/Service; resolve exact Draft once; reconstruct again and read stored ID; repeat zero/two-candidate recovery. Expected: save_draft invoked once overall, no re-upload/Create on recovery; zero/ambiguous matches stop without writes. Actual: PASS. Service lifecycle unchanged.

### TC-09: Full staging article from anonymous homepage

Priority P0; Type live browser; Precondition explicit per-article Owner exception. Exactly four upload200,oneCreate201,onePublish204. Draft/admin/public content,media,hashtags and metadata readbacks identify the same article. Fresh browser context without admin storage loads public page200 and all4article images. Homepage public listing includes sameID; visible title card clicked via UI opens exact article. Screenshots opened with view_image. Final state Published; no archive requested or performed. Actual reader outcome PASS; normal strict pipeline acceptance remains FAIL.

### Remaining platform contract issues

- HTML: alt/figure/figcaption stripped by Quill. Existing dependency remains open; normal guard unchanged.
- Timestamp: request has timezone (`2026-10-19T03:59:00.000Z`), API returns `2026-10-19T03:59:00` without offset. Diagnostic compared literal components under immediate-publish exception; normal instant parser still rejects it. No timezone assumption silently added.
- Public visibility flag: admin readback isPublic=true, anonymous public DTO isPublic=false, while actual anonymous page/listing/card navigation works. Flag mismatch recorded, not converted into a strict readback PASS. Needs NailHub API contract clarification/fix.

Verification (exit0):

```text
python -m unittest tests.test_admin_phase2
Ran 44 tests in 9.583s
OK

python -m unittest discover -s nailblog/tests -t nailblog
Ran 630 tests in 166.559s
OK (skipped=3)
```

627passed,3Windows POSIX permission skips. git diff --check exit0. No commit/push. Harness verifier raw output (exit1):

```text
Không thấy package.json trong C:\Users\AD\Documents\GitHub\AgentBuilder\.claude\worktrees\nailhub-blog-creation-flow-e75996 — harness-verify hiện chỉ hỗ trợ project Node.
```

Evidence outside repo: `C:/Users/AD/.agentfleet/evidence/nailblog-full-staging-20261019/FEATURE.md`, run.json with durable intents, receipts and readbacks, screenshots, test log, manifest/hash list and adjacent ZIP. No credential/token export. Normal contract.verified=false.

### Real image pipeline test contract (2026-10-19)
P0 offline cases: shared text/image reservation concurrency; stable run ceiling across restarts and days; uncertain reservations retained; unique atomic intent winner; model/params/article cache mismatch; invalid image MIME/base64/bytes/dimensions/decompression; no upscale; bounded WebP crop/hash/dHash; reviewer JSON missing/false rubric; final bytes SHA bind; final template escaped/no external network; paid-disabled and preview/daily do not call image endpoints; pinned origin/no redirects/oversized responses/network errors; resume never repeats unknown network intent; package wheel includes thumbnail template. Actual pass counts must be recorded only after execution. Real smoke is parent-controlled under Owner USD1 ceiling; no automatic upload/publish.

### Real image pipeline execution results — 2026-10-19

Final full suite: `python -m unittest discover -s nailblog/tests -t nailblog`, exit 0, 664 tests in 202.786s, OK (skipped=3): 661 passed / 3 existing Windows POSIX permission skips. Focused hardening/image/usage: 69 tests in 34.992s, OK. Independent reviewer: CLI/hardening 10 tests in 1.877s, OK; no actionable findings after fixes. Wheel build and source/template verification PASS; git diff --check exit 0.

Real preflight ListModels/countTokens PASS. First and only generation request returned HTTP 429 rate_limited; zero AI images, real quality acceptance UNVERIFIED. One uncertain intent retains USD0.124928 reserve under the approved USD1 total ceiling. Settled ledger charge0 is not confirmed invoice cost. Same-run recovery stops candidate_requires_reconciliation without creating a second intent. No automatic retry, upload, publish or deployment. Owner quota/billing check and reconciliation required before next attempt.

Harness verifier exit2 (Node-only verifier cannot find package.json); no harness gate PASS. Sanitized raw outputs/report and wheel: C:/Users/AD/.agentfleet/evidence/nailblog-real-images/FEATURE.md; adjacent ZIP and SHA256SUMS. Synthetic template fixture is excluded from the bundle and is not counted as a generated image.

### Cloudflare free-only regression contract (2026-10-19)
Test explicit free opt-in, pinned account/model endpoint, scoped private credential, fresh/exclusive Workers Free proof and exact shared quota DB path. Prove UTC midnight, account/global quota concurrency, baseline import idempotency, immutable run identity, unknown outcomes/no retry, complete cache/no API, bounded multipart and vision JSON, model refusal/malformed image/HTTP429/redirection/private DNS. Validate four final WebP/SHA/review artifacts against admin contract with fixture providers; fixtures never count as real AI acceptance. Real parent-controlled smoke and inspection remain separate, within server-enforced Free allocation; estimated neurons are not invoice facts.

### Cloudflare V2 editorial-fidelity hold and V3 prompt correction
The parent inspected the completed V2 set and confirmed body3 did not match the article brief: the brief requested matte versus glossy dusty rose, while the fixed slot scene requested different colors/coverage. Model rubric booleans alone therefore did not establish editorial acceptance. V2 is held for this semantic mismatch; its original assets, manifest, reviews and quota ledger remain unchanged as evidence.

V3 removes palette/finish defaults from composition scenes. The original article brief controls every color, material, finish, comparison and placement detail; only the nail-object definition and no-text constraints override it. The reviewer must reject missing or substituted brief details. Cover-template Vietnamese title lettering is explicitly allowed for cover only; body labels remain rejected. Generation prompt and review-rubric versions both change the cache binding. Any V3 run uses a new bound run identity while retaining the same account-wide quota ledger and counting all V2 consumption. Offline regressions verify prompt/review semantics; actual V3 image fidelity still requires inspecting the new images and cannot be inferred from those tests.


### Cloudflare V3 executed results and visual hold

Full suite exit0: 684 tests in 227.845s, OK (skipped=3), hence 681 PASS and 3 existing Windows permission skips. Targeted prompt/Cloudflare/image tests: 54 tests in37.784s, OK. Frozen wheel imports/template and 38 source-file byte comparisons PASS. Independent code review approved the bounded V3 experiment.

Real V3 returned image_quality_held (exit1): body3 exhausted its allowed candidates. Independent actual-image inspection additionally rejected model-approved body1 because multiple sticks have colored tips at both ends and the requested separated groups are absent. Model rubric booleans do not establish visual acceptance. V2/V3 artifacts are preserved unchanged; no upload or publication occurred. V2 accounted2402.048 neurons plus V3 accounted2904.803 neurons =5306.851 against the original9000 task ceiling; no pending/uncertain intents remain. Remaining3693.149 is the maximum for a new V4 attempt, retaining the same account-wide quota DB and original prior baseline208.4. Generation usage is tile-estimated and review usage token-derived, not verified invoice consumption.

V4 introduces an optional SHA-bound local geometry reference (a clearly disclosed drawn diagram, never counted as a generated photograph), smaller than512px, to guide one nail-shaped colored tip at one end and a transparent handle at the other. Reference input tile cost must be included in every generation forecast. Final real photos and matte/gloss fidelity still require visual inspection; implementation tests cannot certify them. Evidence: C:/Users/AD/.agentfleet/evidence/nailblog-cloudflare-full-set/.

### Final Cloudflare verification: manual sample PASS, automation HELD

Final full suite exit0: 688 tests in206.802s, OK(skipped=3):685 PASS and3 existing Windows permission skips. V4 targeted75tests37.475sOK; independent provider57tests33.488sOK; independent operator-veto6tests13.054sOK. Final wheel38 source/template files match; imports/resource/operator helper PASS. Harness verification remains unsupported for Python (Node-only, exit2), no harness gate PASS.

V4 real CLI held at its neuron reservation ceiling. Actual inspection found cover glyphs, a body2 count mismatch and body3 finish mismatches. Its last review reservation failed before outbound; no pending/uncertain outbound ledger intents remain. Original manifests are preserved. A separate bound body3 experiment used the original remaining1393.708 allowance and a two-stick reference; generation161.670 plus review97.382 neurons produced correct glossy/matte imagery. Review input forecast8192 was explicitly reviewed; previous28 reviews reported at most2030 prompt tokens, which is observation rather than a guaranteed bound.

Final selected-sample is manually curated: V2 cover, V4 body1, V3 body2, final isolated body3. Independent visual audit PASS for current alt/captions, exact source bytes/SHA/intents, dimensions/size and duplicates (minimum dHash distance17). Curated body3 metadata uses actual TOP/BOTTOM placement; old LEFT/RIGHT source metadata stays unchanged. This is not a single completed automatic flow. Preview: http://127.0.0.1:5193/preview.html. Evidence: C:/Users/AD/.agentfleet/evidence/nailblog-cloudflare-full-set/.

Task accounting7865.344 neurons plus prior208.4 baseline =8073.744 of10000 Free allocation, below original9000 task ceiling. Generation estimates and token-derived review charges are not invoice verification; Free server allocation remains the fee guard. No paid fallback, upload, publication, deployment or Git writes. Gemini uncertain USD0.124928 remains untouched. Autonomous publication remains HELD.

### Optional Cloudflare geometry reference (2026-10-19)
V3 was held after strict geometry checks and manual inspection; a model-approved body image still depicted painted tips at both ends. Prior artifacts and quota records remain unchanged. The optional Cloudflare reference_image_file + reference_image_sha256 pin a local geometry aid, not an accepted output photograph. The adapter bounds source bytes/pixels, rejects symlinks or changed SHA, downsizes without upscaling to <=510px, and sends frozen image/webp bytes in multipart input_image_0. Source/processed SHA, reference processing version and dimensions enter cache binding; each attempt includes one5.37-neuron input tile in its estimate in the same account ledger. No quota reset or provider change.

The reference controls only flat clear-handle/one-almond-tip geometry, with ring hole at the opposite clear end. Actual photographic rendering, article palette/finish/placement remain mandatory; reference count/colors/layout must not be copied. Review now explicitly rejects two painted ends, painted/opaque handles and ring holes on colored tips. Generation prompt V4-reference and review single-ended-v3 require a newly bound run. Real V4 acceptance still requires final-output review and human inspection; the diagram itself is not AI output evidence.
