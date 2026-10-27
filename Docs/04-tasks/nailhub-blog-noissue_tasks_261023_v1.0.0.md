# NailBlog — Kế hoạch tự động hoá full flow trên THINKPAD (staging)

> **Tạo:** 2026-10-23 · task `.agent-tasks/T-261023-nailblog-full-flow-automation-on-thinkpa` · Route C · trạng thái: **đã thay bằng v1.1.0** (Owner 23/10: ảnh chuyển sang API key trả phí → S1 + phần quota viết lại khi có key)
> Liên kết: [[nailhub-blog-noissue_master_261014_v1.0.0]] · [[nailhub-blog-noissue_engineering_261016_v1.0.0]] · [[nailhub-blog-noissue_qa_261019_v1.0.0]]
> Plan do `nx-planner` lập từ code thật trong worktree (chưa chạy app, chưa gọi Cloudflare, chưa đụng THINKPAD).

## Mục tiêu

Flow hằng ngày tự chạy: chủ đề → bài → ảnh → **Owner duyệt #1 (bài + ảnh) qua Telegram** → Draft trên admin → **Owner duyệt #2 (Draft) qua Telegram** → tự Publish đúng slot → đọc lại công khai. Hôm nay flow này chỉ chạy được khi có người lái CLI `one-post` từng bài, mỗi bài một state root riêng, cộng timer systemd đặt tay.

## Quyết định Owner đã chốt (23/10/2026)

1. Hai lượt duyệt vẫn bắt buộc, làm qua Telegram với bản xem trước có ảnh thật; còn lại tự động.
2. Bằng chứng quota Cloudflare Free do bot tự ghi qua API token chỉ đọc (GraphQL analytics), token đọc từ EnvironmentFile, không bao giờ vào log.
3. Giữ switch `review_bypass` (D-032), không gỡ.

## Đối chiếu các thiếu sót với code

| Thiếu sót | Hiện trạng trong code |
|---|---|
| Vòng chạy không có bước ảnh/Draft | Đúng. `daily.write_slot` → `Service.draft` → `placeholder_images` → `add_post(state="review")`. Không tạo `admin_operations`/`admin_id`, nên `publish_pass` bỏ qua: **bài viết bằng daily ở live mode không bao giờ đăng được.** |
| DM duyệt chỉ có đường dẫn file | Đúng. `daily._post_block` in `File xem trước: <path>`. |
| **Nút bấm Telegram** | **Chặn mới.** Service đăng nhập bằng tài khoản người (từ chối bot login). Telethon chỉ hiện `buttons` khi đăng nhập bằng bot. Gửi album ảnh thật bằng tài khoản người thì được. → Câu hỏi Q1. |
| Không có writer cho proof Cloudflare | Đúng. `NeuronLedger.attest` chỉ đọc và kiểm. **Bẫy:** proof đầu tiên của ngày UTC chốt `baseline` trong `cf_baselines`; proof sau khác baseline bị `cloudflare_baseline_changed`. Writer không được chép số API vào baseline mỗi lần làm mới. |
| `daily_publish_limit` + slot | Đúng. Giới hạn 1..2 (`config.py:529`), đếm theo ngày VN; vượt giới hạn thì bài nằm `scheduled`, chỉ log, **lỡ slot im lặng**. Slot 06:00/19:00 hard-code ở `planner`, `daily`, `commands`. Ledger Cloudflare chỉ giữ 2 lượt × 4 500 neurons/ngày UTC. → Q2. |
| Tự pause mỗi lần start | Đúng. Unit chạy `ExecStartPre=... pause-install` mỗi lần start, kể cả restart do crash. |
| Readback treo | Phần lớn đúng. Không log từng bước, không có trần tổng (từng lệnh Playwright có timeout nhưng có hàng chục lệnh; `browser.close()` không có trần). Bị kill thì kẹt `before_publish`. **Đã có sẵn:** restart coi `before_publish` là chỉ đọc lại, không bấm lại. **Phát hiện thêm:** `publish_pass` chạy phục hồi bài cũ trước bài đến hạn và chặn tick phút → readback chậm có thể đẩy bài đến hạn ra khỏi cửa sổ 15 phút. |

Đã có và dùng lại, không viết lại: duyệt gắn snapshot (`accept_images`, `approve`), mỗi chủ đề một lượt (`onepost_topic_claims`), create intent + publish claim bền (`AdminStore`), khoá profile liên tiến trình, image run tiếp tục theo cùng run ID.

## Các bước

Lệnh test mỗi bước (từ repo root): `$env:PYTHONPATH='nailblog/src'; python -m unittest discover -s nailblog/tests -t nailblog -p "<file>"`.

**S0 — Hợp đồng dùng chung (chạy trước, một mình).** `config.py`, `config/nailblog.example.toml`, test mới `test_config_autoflow.py`.
- Key mới: `[autoflow]` `enabled` (mặc định false), `run_neurons`, `runs_dir` (tuyệt đối), `approval_transport` (`bot`|`user`, theo Q1); `[admin]` `category_ids` (slug → GUID cho đủ 4 slug), `publish_timeout_seconds` (60..900, mặc định 300); `[cloudflare]` `auto_proof` (mặc định false).
- Ràng buộc: `autoflow.enabled` đòi `mode=live`, `image.provider=cloudflare`, `auto_proof=true`, đủ 4 GUID, `free_proof_file` tuyệt đối. Tên key tránh chữ "token". Tên env var là hằng trong code.
- Xong khi: test mới qua — mặc định tắt, mỗi ràng buộc fail-closed, key lạ/giống secret bị từ chối, config cũ vẫn load.

**S1 — Writer proof Cloudflare.** Mới `images/cloudflare_proof.py`, `test_cloudflare_proof.py`.
- `refresh_proof(config, *, clock, transport, token)`; HTTPS pin riêng, chỉ cho `https://api.cloudflare.com/client/v4/graphql`. Không nới allowlist của `cloudflare.request`, không sửa `ai_transport.py`.
- `TOKEN_ENV = "NAILBLOG_CF_ANALYTICS_TOKEN"`; `capture_token(environ)` đọc rồi xoá khỏi `os.environ` để Chromium không thừa hưởng.
- Baseline: proof đầu ngày UTC lấy `observed` (chỉ khi ledger chưa có intent nào trong ngày, nếu không thì `ledger_activity_before_baseline`); proof sau dùng lại baseline đã lưu; từ chối `cloudflare_external_usage_detected` nếu `observed > baseline + ledger + dung sai`; số thấp hơn (analytics trễ) được chấp nhận.
- Ghi file 0600 nguyên tử, đúng các field `attest` kiểm + `source: "graphql_auto"`. Lỗi chỉ là mã ngắn; token không bao giờ vào exception/log/file.
- Xong khi: proof ghi ra qua được `NeuronLedger.attest` thật; lần làm mới thứ hai giữ baseline; usage ngoài bị từ chối; lỗi HTTP/JSON hỏng/số không hữu hạn không ghi file; log + file không chứa token; token biến khỏi `os.environ`.

**S2 — Cứng hoá publish.** `admin.py`, `service.py`, mới `test_publish_timeout.py`.
- Log từng bước (tên bước, post id, mã kết quả, thời gian; không URL, không payload). `browser.close()`/`context.close()`/`manager.stop()` có `asyncio.wait_for`.
- `publish_pass`: bài đến hạn trước, phục hồi sau (có backoff cho `uncertain`); mỗi bài một trần `publish_timeout_seconds`, dừng khi service nhận lệnh stop. Timeout/cancel lúc `before_publish` → `uncertain` (`publish_readback_timeout` / `publish_interrupted`); còn `scheduled` (chưa claim) thì không đổi gì.
- Khởi động: `before_publish` sót lại → `uncertain` (`publish_interrupted_restart`), **chỉ khi lấy được khoá profile admin**; sau đó chỉ đọc lại.
- `Service.browser_lock` dùng chung cho `publish_pass` và `save_remote_draft`. Vượt `daily_publish_limit` / ngoài cửa sổ → DM Owner một lần.
- Xong khi: Admin giả treo sau claim kết thúc `uncertain` và lượt sau không gọi `admin.publish`; treo trước claim giữ `scheduled`; cancel → `uncertain`; restart với `before_publish` → `published` mà không bấm; bài đến hạn được xử lý trước bài `uncertain` chậm; log có tên bước, không có `https://`.

**S3 — Yêu cầu duyệt + gửi Telegram.** Mới `approvals.py`; `telegram.py`, `commands.py`; mới `test_approvals.py`, `test_telegram_approvals.py`.
- Bảng `approval_requests`; `request(kind, run_id, snapshot, deadline)` — tối đa 1 yêu cầu chờ cho mỗi `(run_id, kind)`; `decide(...)` chỉ ghi quyết định, gọi lại an toàn, từ chối khi hết hạn hoặc đã bị yêu cầu mới thay.
- Bản xem trước: album cover + ảnh thân bài (JPEG tạo trong bộ nhớ, không đổi file WebP đã duyệt) + thẻ chữ + file HTML xem trước có nhúng ảnh. Thẻ duyệt ảnh: tiêu đề, tóm tắt, số chữ, kết quả gate, số dữ kiện chưa có nguồn, dòng "ảnh chưa qua model chấm (bypass)" khi bypass bật, dự báo quota, slot, hạn duyệt, 12 ký tự đầu snapshot. Thẻ duyệt Draft: các field đọc lại từ admin + đầu fingerprint Draft.
- Handler nút (nếu Q1 chọn bot): chỉ nhận chat riêng của Owner đã xác minh, đúng tin nhắn do yêu cầu đó gửi; trả lời nút và sửa tin nhắn để hiện kết quả.
- Lệnh dự phòng (luôn có): `/nailblog ok <id>`, `/nailblog reject <id> <lý do>`.
- Xong khi: bấm hai lần, snapshot cũ, sai người gửi, nhóm/tin chuyển tiếp, yêu cầu hết hạn đều bị từ chối/bỏ qua; callback data ≤ 64 byte; bản xem trước mang byte ảnh thật; không secret nào trong DM.

**S4 — Cổng khởi động + "arm".** `state.py` (cột `control.armed_binding`, `arm()/disarm()/armed_binding()`), `__main__.py` (lệnh `startup-gate`; `pause-install` giữ và disarm thêm), `systemd/tg-nailblog.service` (`ExecStartPre` → `startup-gate`), `deploy/test_nailblog_deploy.py`, `deploy/README.md`, mới `test_startup_gate.py`.
- Binding = digest `build.json` đã cài + SHA config + mode. Khác `armed_binding` → pause + disarm. Khớp (restart do crash) → giữ trạng thái pause trước đó.
- Xong khi: build/config mới khởi động ở trạng thái pause; cùng binding sau crash vẫn chạy; DB cũ không có cột được migrate; `python -m unittest deploy.test_nailblog_deploy` qua.

**S5 — Ghép autoflow (sau đợt 1).** Mới `autoflow.py`; `daily.py`, `onepost.py`, `commands.py`, `__main__.py`; mới `test_autoflow.py`, `test_autoflow_e2e.py`.
- `autoflow.run_due(service, stop)` chạy cuối `daily.run_due`, chỉ khi live + không pause + đã arm + `autoflow.enabled`. Mỗi chủ đề đã khoá của slot sắp tới:
  1. Tạo dòng `autoflow_runs`, run ID `auto-YYYYMMDD-HHMM-t<topic>`.
  2. `refresh_proof` ngay trước `OnePost.prepare(..., free_enabled=True, ceiling=run_neurons)`; proof lỗi thì backoff + DM, không gọi prepare.
  3. `images_ready` → `approvals.request('images', snapshot)`.
  4. Duyệt → `OnePost.accept_images` → `OnePost.save` (giữ `browser_lock`); không tạo Draft trong vòng 20 phút trước slot.
  5. `draft_verified` → `approvals.request('draft', ...)`.
  6. Duyệt → `OnePost.approve(run_id, snapshot)`.
  7. Publish để cho `publish_pass` của scheduler. **Autoflow không bao giờ gọi publish**; chỉ gọi `OnePost.mark_published` khi `posts.state == 'published'`.
- Lỗi cần đối soát (`text_requires_manual_reconciliation`, `candidate_requires_reconciliation`, `quality_pass_required`, `onepost_held`, `create_requires_manual_reconciliation`) → run held + DM một lần, không retry.
- Chế độ autoflow: `daily.write_articles` không tạo bài placeholder; DM 20:00 hiện trạng thái autoflow. `/nailblog resume` chạy preflight rồi `state.arm(binding)`; `/nailblog flow` hiện từng run đang ở bước nào. `__main__.main` gọi `capture_token()` trước `asyncio.run`.
- Xong khi: test end-to-end (text/Cloudflare/Admin/Telegram giả, đồng hồ cố định) đi từ chủ đề tới `published` với đúng 1 upload, 1 Create, 1 PATCH; không Draft trước duyệt ảnh; không publish trước duyệt Draft; reject → held, không Create/PATCH; lỡ hạn → bỏ slot; restart ở mọi bước không sinh POST/PATCH trùng; `test_onepost.py` vẫn qua.

**S6 — Docs, review, nghiệm thu staging.** `AGENTS.md` (ngoại lệ token trong EnvironmentFile + rule mới), changelog trong docs Engineering/QA, `Decisions/D-NNN` trong vault.
- Xong khi: full suite qua (số test ≥ baseline đo trước S0); `deploy.test_nailblog_deploy deploy.test_deploy` qua; review độc lập GATE 0b xong; một ngày staging thật qua với đủ hai lượt Owner bấm duyệt, kiểm bằng DB đọc read-only + evidence readback.

**Thay đổi deploy (chỉ ghi chú):** build lại wheel; unit file mới + `daemon-reload`; thêm `NAILBLOG_CF_ANALYTICS_TOKEN` (và `NAILBLOG_BOT_TOKEN` nếu Q1 chọn bot) vào `/etc/agentfleet/tg-nailblog.env`; `free_proof_file` chuyển vào `/var/lib/agentfleet/nailblog...` (`/etc` read-only dưới `ProtectSystem=strict`); config chính sang `mode=live` + contract đã verify + ảnh Cloudflare + `review_bypass=true` + key mới; bật start-at-boot là bước riêng của Owner; khi đã arm thì **không** chạy one-post trên state root riêng song song với service.

## Stream

| Stream | File sở hữu | Không được đụng | Đợt |
|---|---|---|---|
| S0 hợp đồng | `config.py`, `config/nailblog.example.toml`, `tests/test_config_autoflow.py` | mọi file khác | 0 |
| S1 proof | `images/cloudflare_proof.py`, `tests/test_cloudflare_proof.py` | `images/neurons.py`, `images/cloudflare.py`, `ai_transport.py`, `config.py` | 1 |
| S2 publish | `admin.py`, `service.py`, `tests/test_publish_timeout.py` | `admin_state.py`, `state.py`, `publication.py`, `onepost.py` | 1 |
| S3 duyệt | `approvals.py`, `telegram.py`, `commands.py`, `tests/test_approvals.py`, `tests/test_telegram_approvals.py` | `onepost.py`, `service.py`, `state.py`, `daily.py` | 1 |
| S4 khởi động | `state.py`, `__main__.py`, `systemd/tg-nailblog.service`, `deploy/test_nailblog_deploy.py`, `deploy/README.md`, `tests/test_startup_gate.py` | `service.py`, `commands.py`, `telegram.py` | 1 |
| S5 autoflow | `autoflow.py`, `daily.py`, `onepost.py`, `commands.py`, `__main__.py`, `tests/test_autoflow*.py` | `admin.py`, `admin_state.py`, `neurons.py`, `publication.py` | 2 |

### Tài nguyên dùng chung ở runtime (mỗi thứ một nơi sở hữu)

| Tài nguyên | Hình dạng | Sở hữu |
|---|---|---|
| Trạng thái bài | Đồ thị `TRANSITIONS` hiện có, không thêm trạng thái | `state.py` |
| Lý do mới | `publish_readback_timeout`, `publish_interrupted`, `publish_interrupted_restart` | `service.py` |
| Phase one-post | Danh sách hiện có; autoflow chỉ đọc + `mark_published` | `onepost.py` |
| Bảng `approval_requests` | `id`, `run_id`, `kind` (`images`/`draft`), `snapshot`, `status` (`pending`/`approved`/`rejected`/`expired`/`superseded`), `reason`, `created_at`, `decided_at`, `decided_by`, `message_ids`; UNIQUE(`run_id`,`kind`,`snapshot`) | `approvals.py` |
| Bảng `autoflow_runs` | `run_id` PK, `topic_id` UNIQUE, `slot_at`, `out_dir`, `run_neurons`, `stage`, `failures`, `next_attempt_at`, `terminal_reason` | `autoflow.py` |
| Cột `control.armed_binding` | TEXT, mặc định `''` | `state.py` |
| Callback data | `nb1:<i\|d>:<approval_id>:<a\|r>` (≤ 64 byte) | `approvals.py` |
| Lệnh chữ | `/nailblog ok <id>`, `/nailblog reject <id> <lý do>`, `/nailblog flow` | `commands.py` |
| Key config | như S0 | `config.py` |
| Env var | `NAILBLOG_CF_ANALYTICS_TOKEN` | `images/cloudflare_proof.py` |
| Env var (nếu bot) | `NAILBLOG_BOT_TOKEN` | `telegram.py` |
| Schema proof | các field `attest` kiểm | `images/neurons.py` (không đổi) |
| `Service.browser_lock` | `asyncio.Lock` | `service.py` |
| Dòng log publish | `NailBlog publish post= step= outcome= elapsed_ms=` | `admin.py` |
| Loại thông báo | `approval` / `autoflow` | `approvals.py` / `autoflow.py` |
| Run ID + thư mục | `auto-YYYYMMDD-HHMM-t<topic>` dưới `runs_dir` | `autoflow.py` |

## Rủi ro

GATE 0b bắt buộc: đụng tiền (quota Free, phát sinh trả phí nếu sai), toàn vẹn dữ liệu (máy trạng thái bài), quyền duyệt, và migration (`ALTER TABLE` trên DB đang chạy).

- **Đăng trùng:** ba đường publish có thể chạy cùng lúc (scheduler, `OnePost.publish`, CLI tay). Autoflow không bao giờ gọi publish (S5 test). State root riêng là DB riêng → né được daily limit và topic claim; chạy song song khi service đã arm có thể đăng hai lần.
- **Timeout sau khi PATCH đã gửi:** phải kết thúc `uncertain` và chỉ đọc lại; test phải chứng minh không có PATCH thứ hai.
- **Đổi `before_publish` lúc restart khi tiến trình khác đang publish:** guard chặn lượt bấm của tiến trình kia (không trùng, nhưng mất slot) → cần khoá profile.
- **Snapshot đổi giữa lúc xem trước và lúc bấm:** đã xử lý vì `accept_images`/`approve` kiểm lại; bản xem trước cũ phải được thay, không bao giờ tự duyệt.
- **Restart do crash giờ tiếp tục chạy:** crash loop khi đã arm sẽ retry (bị claim + ledger giới hạn); build/config mới thì disarm. Cân nhắc `StartLimitBurst`.
- **Restart giữa lúc viết bài:** sau lệnh text đầu tiên, restart → `text_requires_manual_reconciliation`, mất slot (hành vi an toàn hiện có), Owner nhận DM.
- **Proof:** analytics trễ ⇒ usage ngoài sau baseline chỉ bị bắt ở lần làm mới sau; "Workers Free" + dùng độc quyền vẫn là khẳng định của Owner nếu token không đọc được billing (Q4); Chromium thừa hưởng token nếu không xoá khỏi env trước khi chạy Playwright (`TG_API_HASH` đang có cùng vấn đề, ngoài scope).
- Shared layer bị đụng: transport Telegram, router lệnh, schema config, migration state, systemd unit.

## Câu hỏi cho Owner

1. Nút bấm cần bot: tạo bot riêng qua BotFather (token vào EnvironmentFile, Owner gửi `/start` một lần) hay dùng lệnh chữ `/nailblog ok <id>`?
2. Slot + quota: 3 slot (06/12/19)? 12:00 thuộc nhóm chủ đề nào? Trần đăng 3 bài/ngày? Quota Free 10 000 neurons/ngày UTC chỉ đủ 2 lượt × 4 500 — hạ trần mỗi lượt (~3 000, ít ảnh dự phòng hơn) hay giữ 2 slot?
3. Reject làm gì: ảnh — thử ứng viên chưa dùng trong giới hạn của run (thêm một bản xem trước) hay bỏ slot? Draft — giữ bài held và để Draft chưa đăng trên admin?
4. Phạm vi token Cloudflare: chỉ Account Analytics Read (Workers Free + dùng độc quyền vẫn là khẳng định của Owner) hay thêm Billing Read để API tự kiểm plan?
5. Arm: giữ trạng thái arm qua restart do crash, disarm khi wheel/config mới — được không? Khi nào bật start-at-boot?

## Ước lượng

Khoảng 22 file (7 module mới + ~15 file sửa), ~1 500 dòng code + ~1 200 dòng test. S0 2h · S1 5h · S2 5h · S3 8h (+3h nếu bot) · S4 3h · S5 10h · docs + review 4h ⇒ **≈ 37–40h**, cộng một ngày staging thật để nghiệm thu.

## Chưa kiểm

- Tên dataset/field GraphQL của Cloudflare cho "neurons dùng hôm nay" — kiểm bằng một query read-only với token của Owner, lưu fixture đã làm sạch, trước khi viết parser.
- Lệnh `/command` có bấm được trong DM giữa hai tài khoản người không (chỉ quan trọng nếu Q1 = không bot).
- Config/env/log thật trên THINKPAD — nguyên nhân treo ngày 23/10 vẫn chưa rõ; plan chỉ giới hạn và log nó.
- Worktree còn thay đổi cover chưa commit (`images/post.py`, `prompts.py`, `templates/cover.html`) và 3 test untracked — giữ ngoài commit của tính năng này.

## Phản hồi Owner tại cổng duyệt (23/10/2026)

- Executor: **Sonnet 5.5 (`nx-executor`) · medium** (Owner chọn, thấp hơn mức `high` đề xuất cho ≥4 file + test).
- Duyệt: **lệnh chữ** (`/nailblog ok <id>`, `/nailblog reject <id> <lý do>`), không tạo bot ⇒ bỏ phần handler nút của S3.
- Reject: ảnh thử lại 1 lần với ứng viên chưa dùng; Draft bị từ chối ⇒ bài held, Draft để nguyên chưa đăng.
- Ảnh: **không** dùng Cloudflare trả phí — Owner sẽ cấp **API key trả phí mới** cho model ảnh. Yêu cầu: chi phí tối ưu nhất, chất lượng tốt nhất ⇒ S1 (proof Cloudflare Free) và phần quota/neurons phải lên plan lại khi có key.
- Giữ token Cloudflare chỉ đọc để **đối soát chi phí** (không còn là điều kiện bắt buộc trước mỗi lượt ảnh) — cần xem lại khi đổi provider.
- Chưa chốt: số slot/ngày, trần chi tiêu USD/ngày, provider + model ảnh.
