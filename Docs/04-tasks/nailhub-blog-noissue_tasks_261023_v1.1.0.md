# NailBlog — Kế hoạch tự động hoá full flow (v1.1, ảnh Gemini trả phí)

> **Tạo:** 2026-10-23 · task `.agent-tasks/T-261023-nailblog-full-flow-automation-on-thinkpa` · Route C · trạng thái: **chờ Owner duyệt**
> Thay thế [[nailhub-blog-noissue_tasks_261023_v1.0.0]] (bản Cloudflare Free). Plan do `nx-planner` lập từ code thật; chưa chạy app, chưa gọi API model, chưa đụng THINKPAD.

## Quyết định Owner (23/10/2026)

1. Executor: `nx-executor` (Sonnet 5.5) · **medium**, mỗi stream một worktree cách ly. Review GATE 0b: `nx-verifier` + `nx-reviewer`.
2. Duyệt bằng **lệnh chữ**: `/nailblog ok <id>`, `/nailblog reject <id> <lý do>`. Không bot, không nút. Bản xem trước vẫn gửi album ảnh thật + thẻ chữ.
3. Reject ảnh → thử lại đúng 1 lần với ứng viên chưa dùng, rồi held. Reject Draft → bài held, Draft để nguyên chưa đăng.
4. Ảnh chuyển sang **Google Gemini API trả phí**, mục tiêu chi phí thấp nhất + chất lượng tốt nhất, **trần cứng 2 USD/ngày**.
5. Giữ `review_bypass`, hai lượt duyệt, write Guard, đường CLI `one-post`.

## Hiện trạng code (những điểm đổi so với v1.0)

| Hạng mục | Code hiện tại |
|---|---|
| Đường ảnh Gemini | **Đã có** — `images/provider.py` `GeminiImages`: HTTPS pin chỉ `generativelanguage.googleapis.com`, `countTokens` trước mỗi lượt, `verify_models` qua ListModels, kích thước theo vai qua `image.cover_size`/`image.body_size` (mặc định 2K/1K), 16:9. Dùng lại, không viết lại. |
| Sổ USD | **Đã có** — `paid.py` `PaidLedger`: `pending → settled/uncertain`, trần mỗi run, reserve nguyên tử trước khi gọi; trần ngày `budget.daily_usd` theo **ngày VN**; settle từ `usageMetadata`. |
| Lỗ hổng sổ USD | (a) Không đối soát khi restart: `pending` do crash kẹt mãi. (b) `uncertain` bị tính vào **mọi ngày sau** (không lọc ngày) → vài lần crash là chặn dần trần 2 USD. (c) Sổ nằm trong DB của từng state root → mỗi state root one-post có trần riêng. |
| Nguồn key | Đọc từ `image.key_file` qua `read_key` (chỉ nhận cặp env chung `AGENTFLEET_KEY_SCOPE`/`AGENTFLEET_API_KEY`). Cần env var riêng + đọc trong bộ nhớ + switch config. |
| Kích thước ảnh | Crop cover **1600×900**, thân bài **1200×675**, không phóng to. 1K ở 16:9 ≈ 1376×768 (**chưa kiểm**) ⇒ cover **cần 2K**, thân bài **1K đủ**, 0.5K hỏng cả hai. |
| Bypass | Bật bypass ⇒ lấy ứng viên đầu qua các kiểm tra cố định rồi dừng; không gọi model chấm ⇒ không tốn tiền chấm. |
| Proof Cloudflare | **Bỏ.** Chỉ dùng để mở khoá lượt Cloudflare Free; ảnh chuyển sang Gemini thì không còn usage Cloudflare để đối soát, token chỉ thêm một secret. Code Cloudflare giữ nguyên, chọn được qua `image.provider`. Một lần kiểm tay: DB quota Cloudflare dùng chung có 0 intent `pending`/`uncertain`. |
| Model text | Ollama local `qwen3.5:9b` (0 USD) — không đổi. Lưu ý `budget.daily_usd` dùng chung cho text trả phí và ảnh. |
| Slot | Vẫn hard-code 06:00/19:00 ở `planner`, `daily`, `commands`; slot thứ 3 làm `daily._backlog` lỗi `KeyError`; `daily_publish_limit` chỉ 1..2. |

Không đổi so với v1.0: vòng daily tạo bài placeholder không bao giờ tới được Draft; tự pause mỗi lần start; readback không có trần tổng và không log từng bước, phục hồi chạy trước bài đến hạn; phục hồi `before_publish` khi restart vốn chỉ đọc lại.

### Trần ngày: ngày VN

Dùng `budget.daily_usd = 2.0` theo ngày VN: Google trả theo usage không có mốc reset ngày; slot, hạn duyệt, DM đều theo giờ VN; sổ đã dùng `usage.day_bounds` nên không phải đổi code. Binding pipeline chứa `budget` ⇒ đổi trần giữa một run làm run đó resume fail-closed; chỉ đổi giữa các run.

### Model và chi phí (giá Google công bố 23/10, `image-compare` đo lại)

| Phương án | Cover 2K | 3 ảnh thân 1K | Mỗi bài, lần đầu | 2 slot/ngày | 3 slot/ngày |
|---|---|---|---|---|---|
| A: Nano Banana 2 cả bài | 0,101 | 0,201 | **0,302 USD** | 0,60 | 0,91 |
| B: NB2 cover + flash-lite thân bài | 0,101 | 0,101 | **0,202 USD** | 0,40 | 0,61 |
| C: Pro cover + lite thân bài | 0,134 | 0,101 | 0,235 USD | 0,47 | 0,71 |
| Pro cả bài | 0,134 | 0,402 | 0,536 USD | 1,07 | 1,61 |

Reject ảnh một lần ≈ gấp đôi chi phí bài đó. Trường hợp xấu nhất phương án A, 3 slot, mỗi slot reject một lần ≈ 1,81 USD, sát trần. Chạm trần ⇒ slot giữ lại tới ngày VN kế tiếp. Lite chỉ có 1K nên không làm cover được.

**Batch API: không dùng** — trễ tới 24h, dài hơn khoảng giữa lúc chốt 13:00 và hạn ảnh 03:00 cho slot 06:00; không còn thời gian cho vòng reject-thử lại; cần endpoint mới mà client pin không cho phép.

### Đối soát chi phí Gemini

Response có `usageMetadata.promptTokenCount`, `candidatesTokenCount`, `thoughtsTokenCount` (đã đọc + kiểm). `candidatesTokensDetails` theo modality **chưa kiểm** với model ảnh. Response **không có số USD** — số thật chỉ ở AI Studio / Cloud Billing ⇒ sổ là ước tính thận trọng, Owner đối chiếu tay với console Google. Chưa thấy trần chi cứng phía Google cho Developer API, chỉ có budget alert (**chưa kiểm**) ⇒ sổ local là chốt chặn cứng thật sự.

## Đợt và stream

Tối đa 3 stream song song mỗi đợt; merge xong đợt trước mới chạy đợt sau.

| Đợt | Stream |
|---|---|
| 0 | S0 |
| 1 | S1, S2 |
| 2 | S3, S4, S5 |
| 3 | S6, S7, S8 |
| 4 | S9 |
| 5 | S10 |

Lệnh test (từ repo root): `$env:PYTHONPATH='nailblog/src'; python -m unittest discover -s nailblog/tests -t nailblog -p "<file>"`.

| Stream | File sở hữu | Không được đụng | Đợt | Giờ |
|---|---|---|---|---|
| S0 hợp đồng | `config.py`, `config/nailblog.example.toml`, mới `env_secrets.py`, `__main__.py` (đúng 1 lời gọi), mới `tests/test_config_autoflow.py` | mọi file khác | 0 | 3 |
| S1 gia cố sổ + key env | `paid.py`, `images/provider.py`, `images/pipeline.py`, mới `tests/test_paid_ledger_hardening.py` | `neurons.py`, `cloudflare.py`, `ai_transport.py`, `onepost.py` | 1 | 5 |
| S2 cứng hoá publish | `admin.py`, `service.py`, mới `tests/test_publish_timeout.py` | `admin_state.py`, `state.py`, `publication.py` | 1 | 5 |
| S3 CLI `image-compare` | mới `images/compare.py`, `__main__.py` (subcommand), mới `tests/test_image_compare.py` | `pipeline.py`, `paid.py`, `provider.py` | 2 | 4 |
| S4 duyệt + media | mới `approvals.py`, `telegram.py`, mới `tests/test_approvals.py` | `commands.py`, `onepost.py`, `service.py` | 2 | 5 |
| S5 cổng khởi động | `state.py`, `systemd/tg-nailblog.service`, `deploy/test_nailblog_deploy.py`, `deploy/README.md`, mới `tests/test_startup_gate.py` | `__main__.py` (của S3 ở đợt này — lệnh `startup-gate` để ở `state.py` dạng `startup_gate(config)`, S9 đăng ký) | 2 | 3 |
| S6 slot từ config | `planner.py`, `daily.py` (chỉ phần slot), `commands.py` (chỉ `_pick` + usage), mới `tests/test_slots_config.py` | `autoflow.py`, `onepost.py` | 3 | 4 |
| S7 one-post retry/hook | `onepost.py`, mới `tests/test_onepost_retry.py` | `pipeline.py`, `admin_state.py` | 3 | 4 |
| S8 engine autoflow | mới `autoflow.py`, mới `tests/test_autoflow.py` (fake OnePost/approvals/sender) | mọi file khác | 3 | 6 |
| S9 nối dây + e2e | `daily.py`, `commands.py`, `telegram.py`, `__main__.py`, mới `tests/test_autoflow_e2e.py` | `admin.py`, `admin_state.py`, `paid.py`, `pipeline.py` | 4 | 6 |
| S10 docs + gate | `nailblog/src/nailblog/AGENTS.md`, changelog docs Engineering/QA, vault `Decisions/D-NNN` | code | 5 | 4 (+ review độc lập) |

## Spec từng stream

**S0 — Hợp đồng dùng chung**
- Key config mới:
  - `[image]` `key_source = "file" | "env"` (mặc định `file`); live + `env` không cần `key_file`.
  - `[budget]` `image_unit_prices` (model → {"1K": usd, "2K": usd}); Gemini live bắt buộc có giá cho `model_cover@cover_size` và `model_body@body_size`.
  - `[admin]` `category_ids` (đủ 4 slug → GUID), `publish_timeout_seconds` (60..900, mặc định 300).
  - `[daily]` `slot_categories` (slot → danh sách nhóm), mặc định `{"06:00":["hot-trend","nail-job"],"19:00":["nail-salon","did-you-know"]}`; key phải bằng `service.slots`.
  - `daily_publish_limit`: `1..len(slots)`, tối đa 3.
  - `[autoflow]` `enabled` (mặc định false), `run_usd` (> 0 và ≤ `budget.daily_usd`), `runs_dir` (tuyệt đối).
  - Ràng buộc: `autoflow.enabled` đòi live + `image.provider="gemini"` + `key_source="env"` + đủ `category_ids`. Tên key không kết thúc bằng "token"/"key".
- `env_secrets.py`: `GEMINI_IMAGE_ENV = "NAILBLOG_GEMINI_IMAGE_KEY"`; `capture()` xoá khỏi môi trường, giữ trong bộ nhớ; `gemini_key_reader(path, scope, group_name=None)` chỉ trả giá trị khi `scope == gemini_text.KEY_SCOPE`, ngoài ra `''`; không log gì.
- `__main__.main` gọi `env_secrets.capture()` ngay sau `parse_args`, trước `asyncio.run` (tức trước mọi lần chạy Playwright).
- Xong khi: mặc định không đổi, config cũ vẫn load; mỗi ràng buộc fail-closed; sau `capture` env var biến mất khỏi `os.environ`; reader trả `''` cho scope khác; `assertLogs` không chứa key.

**S1 — Gia cố sổ + key env (chỉ đường Gemini)**
- `GeminiImages` dùng `env_secrets.gemini_key_reader` khi `image.key_source == "env"`.
- `pipeline.paid()` settle ở `max(chi phí theo token, budget.image_unit_prices[model][size])`, ghi cả hai vào metadata; vẫn raise `reported_usage_exceeds_reserved_bound` như hiện nay khi token vượt trần.
- `PaidLedger`:
  - `reconcile_stale(now, older_than_s)`: `pending` cũ hơn timeout ảnh + biên → `uncertain`.
  - `age_out_uncertain(now, after_s=86400)`: settle `uncertain` ở **đúng mức đã reserve**, `usage.at` = thời điểm gốc của intent (tính vào ngày gốc), metadata `assumed_full_bound`. Không bao giờ hạ số đã tính.
  - Tổng ngày tính intent chưa settle của **ngày VN hiện tại + mọi `uncertain` chưa age out**; không bao giờ lặng lẽ hạ chi tiêu.
- Xong khi: crash rồi restart: `pending` → `uncertain` vẫn được tính; sau age out, trần hôm nay được giải phóng và ngày gốc mang đủ mức; chạm trần raise `BudgetExceeded` trước mọi lời gọi transport (transport giả ghi 0 lời gọi); settle lấy mức cao hơn; key không có trong log/exception/manifest; `test_real_images.py` + `test_image_pause.py` vẫn qua.

**S2 — Cứng hoá publish** (như bước 2 bản v1.0)
- `admin.py`: log từng bước (`post=`, `step=`, `outcome=`, `elapsed_ms=`, không URL/payload); `browser.close()`/`context.close()`/`manager.stop()` có `wait_for`.
- `service.py` `publish_pass`: bài `scheduled` đến hạn trước, phục hồi sau (backoff cho `uncertain`); mỗi bài một `asyncio.timeout(admin.publish_timeout_seconds)` + dừng theo stop event; `TimeoutError`/`CancelledError` lúc `before_publish` → `uncertain` (`publish_readback_timeout`/`publish_interrupted`), cancel thì raise lại; còn `scheduled` thì không đổi; khởi động: `before_publish` sót → `uncertain` (`publish_interrupted_restart`) chỉ khi lấy được khoá profile admin; thêm `Service.browser_lock`, `Service.media_send = None` (S9 gắn sau); DM một lần cho `daily_publish_limit` và `outside_publish_window`.
- Xong khi: treo sau claim → `uncertain`, không có `admin.publish` lần hai; treo trước claim → giữ `scheduled`; cancel → `uncertain`; restart đọc lại tới `published` không bấm; bài đến hạn chạy trước bài `uncertain` chậm; log không có `https://`.

**S3 — CLI `image-compare`**
- `python -m nailblog image-compare --article A.json --out DIR --variants m1@2K,m2@1K,m3@1K --max-usd X --paid-enabled`.
- Dựng đúng prompt production (`images/prompts.py`), cover + 1 ảnh thân bài. Mỗi variant: kiểm ListModels, reserve qua `PaidLedger`, run ID `compare-<stamp>`, trần run = `--max-usd` (≤ phần còn lại của `budget.daily_usd` hôm nay), gọi, settle theo luật S1.
- Ghi model, tier, kích thước pixel thật, kết quả crop 1600×900 / 1200×675, token, USD; xuất `board.html` (ảnh nhúng, mode 0600) + `board.json`. Không gọi model chấm, không đụng website, không Playwright.
- Xong khi: transport giả tạo được board có chi phí; trần ngày và `--max-usd` chặn variant thừa trước khi gọi; variant hỏng crop hiện FAIL chứ không phóng to; không có key trong board/log.

**S4 — Duyệt + media**
- `approvals.py`: bảng `approval_requests`; `request(kind, run_id, snapshot, deadline)` giữ 1 `pending` mỗi `(run_id, kind)`, yêu cầu mới thay (`superseded`) yêu cầu cũ; `decide(id, verdict, reason, sender_id, owner_id, now)` chỉ ghi quyết định, gọi lại an toàn, từ chối `expired`/`superseded`/sai người gửi.
- `build_preview(kind, artifact, image_paths)`: JPEG tạo trong bộ nhớ + thẻ chữ + `preview.html` có ảnh nhúng. Thẻ ảnh: tiêu đề, tóm tắt, số chữ, kết quả gate, số dữ kiện chưa có nguồn, dòng "ảnh chưa qua model chấm (bypass)", dự báo USD, slot, hạn duyệt, đầu snapshot. Thẻ Draft: `admin_id`, đầu fingerprint Draft, hạn duyệt. Cả hai kết thúc bằng `/nailblog ok <id>` và `/nailblog reject <id> <lý do>`.
- `telegram.py`: `make_owner_media_sender(client, owner_ids)` gửi `send_file` với danh sách file rồi thẻ chữ; cùng kiểm Owner như `make_owner_notice_sender`.
- Xong khi: quyết định lặp lại, yêu cầu cũ/bị thay, sai người gửi, hết hạn đều đúng như spec; client giả nhận N luồng byte ảnh thật; thẻ không chứa secret.

**S5 — Cổng khởi động**
- `State`: `ALTER TABLE control ADD COLUMN armed_binding TEXT NOT NULL DEFAULT ''` có guard; `arm(b)`, `disarm()`, `armed_binding()`.
- `startup_gate(config)`: binding = SHA `build.json` đã cài (dự phòng: digest file package) + SHA file config + mode. Khác `armed_binding` → pause + disarm; khớp → giữ trạng thái pause trước đó.
- Unit: `ExecStartPre=... -m nailblog startup-gate` (S9 đăng ký lệnh và sửa deploy test).
- Xong khi: migrate DB cũ được; build mới → pause; cùng binding sau crash → không đổi.

**S6 — Slot từ config**
- `planner.py`: `SLOT_GROUPS` lấy từ `config.daily.slot_categories`; `WEEK_PLAN` chỉ giữ ưu tiên 06:00/19:00, slot khác không có nhóm ưu tiên và xếp theo điểm; vòng plan-week đi qua `config.slots`.
- `daily.py`: prompt và kích thước schema theo số slot (4 chủ đề mỗi slot); `GROUP_LABEL` từ config.
- `commands.py` `_pick`: nhận mọi slot đã cấu hình.
- Xong khi: mặc định 2 slot cho đúng output hiện nay (`test_daily*.py`, `test_planner.py` vẫn qua); 3 slot thêm được 12:00, `_backlog` không còn `KeyError`, `/nailblog pick 12 1` chạy được.

**S7 — One-post retry + hook**
- `OnePost.retry_images(run_id, reason)`: đòi phase `images_ready`, chưa `image_approval`, `image_retries == 0`; gọi `ImagePipeline.reject_selected` cho mọi vị trí đã chọn → phase `images_pending` → chạy lại pipeline cùng run ID và giới hạn → `images_ready`, `image_retries = 1`; lần hai raise `image_retry_exhausted`.
- `OnePost.mark_published(run_id)`: chỉ khi `posts.state == 'published'`.
- `prepare(..., before_dispatch=None)`: kiểm thêm tuỳ chọn (stop event) cộng với `self.active()`.
- Xong khi: retry một lần ra snapshot mới với ứng viên chưa dùng; không nới giới hạn nào; retry lần hai bị từ chối; `mark_published` bị từ chối khi bài chưa đăng; `test_onepost.py` vẫn qua.

**S8 — Engine autoflow**
- `autoflow.run_due(service, stop, *, onepost, approvals, now)` chỉ chạy khi live + không pause + đã arm + `autoflow.enabled`. Mỗi chủ đề đã khoá:
  1. Tạo dòng `autoflow_runs`.
  2. `prepare(paid_enabled=True, ceiling=run_usd)`.
  3. `images_ready` → `approvals.request('images')` → DM media.
  4. ok → `accept_images(snapshot đã lưu)` → `save(category_ids[slug])`, giữ `browser_lock`, không trong vòng 20 phút trước bất kỳ slot nào.
  5. → `request('draft')` → ok → `approve(snapshot đã lưu)`.
  6. Reject ảnh → `retry_images` một lần, rồi `held`.
  7. Reject Draft → `State.transition(post, 'held', reason)`; Draft để nguyên chưa đăng.
  8. Bài đã đăng → `mark_published`.
- **Không bao giờ** gọi `publish_pass` hay `OnePost.publish`.
- `BudgetExceeded` → `next_attempt_at` = nửa đêm VN kế tiếp, một DM mỗi ngày (`notice_key('budget', day)`). Lỗi cần đối soát → `held` + một DM, không retry. Lỡ hạn → yêu cầu `expired`, bỏ slot.
- Xong khi: unit test với fake phủ mọi nhánh, kiểm đúng số lời gọi trên fake.

**S9 — Nối dây + end-to-end**
- `daily.run_due` gọi `autoflow.run_due`; `daily.write_articles` không tạo bài placeholder khi autoflow bật; DM 20:00 hiện các bước autoflow.
- `commands.py` thêm `ok`/`reject`/`flow`; `resume` chạy preflight (contract verified, key env đã capture, ListModels, categories, admin `ready()`, ngân sách còn lại) rồi `arm`.
- `telegram.py` gắn `service.media_send`; `__main__.py` đăng ký `startup-gate`; sửa `deploy/test_nailblog_deploy.py` dòng 64.
- Xong khi: test e2e (text/Gemini/Admin/Telegram giả, đồng hồ cố định) đi từ chủ đề tới `published` với đúng 1 upload, 1 Create, 1 PATCH; không Draft trước ok ảnh, không publish trước ok Draft; reject ảnh → thêm 1 bản xem trước → reject → `held`, 0 Create; reject Draft → `held`, 0 PATCH; chạm trần → 1 DM, thử lại ngày sau; restart ở mọi bước không lặp POST/PATCH; `python -m unittest deploy.test_nailblog_deploy deploy.test_deploy` qua.

**S10 — Docs + gate**
- `AGENTS.md`: ngoại lệ key qua env var (quyết định Owner); state root riêng không được dùng ảnh trả phí khi service đã arm. Changelog + `Decisions/D-NNN`.
- Full suite với số test ≥ baseline đo trước S0; review độc lập GATE 0b; nghiệm thu staging: một ngày thật với đủ hai lượt `ok`, kiểm bằng DB đọc read-only + evidence readback.

**Thay đổi deploy (ghi chú):** build lại wheel; unit file mới + `daemon-reload`; thêm `NAILBLOG_GEMINI_IMAGE_KEY` vào `/etc/agentfleet/tg-nailblog.env`; config chính sang `mode=live`, `image.provider="gemini"`, `key_source="env"`, `review_bypass=true`, `budget.daily_usd=2.0` + giá + key mới, `category_ids` lấy từ báo cáo `probe`; Owner chạy `image-compare` trước khi chốt model; bật start-at-boot là bước riêng của Owner.

## Tài nguyên dùng chung ở runtime (mỗi thứ một nơi sở hữu)

| Tài nguyên | Hình dạng | Sở hữu |
|---|---|---|
| Trạng thái bài | `TRANSITIONS` hiện có, không thêm trạng thái | `state.py` |
| Lý do mới | `publish_readback_timeout`, `publish_interrupted`, `publish_interrupted_restart` | `service.py` |
| | `owner_draft_rejected` | `autoflow.py` |
| Phase one-post | Danh sách hiện có + field `image_retries`, `image_retry_reasons` | `onepost.py` |
| `paid_intents.status` | `pending`/`uncertain`/`settled` (không thêm giá trị) + metadata `assumed_full_bound`, `unit_price_basis` | `paid.py` |
| `approval_requests` | `id`, `run_id`, `kind` (`images`/`draft`), `snapshot`, `status` (`pending`/`approved`/`rejected`/`expired`/`superseded`), `reason`, `created_at`, `decided_at`, `decided_by`, `message_ids`; UNIQUE(`run_id`,`kind`,`snapshot`) | `approvals.py` |
| `autoflow_runs` | `run_id` PK, `topic_id` UNIQUE, `slot_at`, `out_dir`, `run_usd`, `stage`, `failures`, `next_attempt_at`, `terminal_reason` | `autoflow.py` |
| `control.armed_binding` | TEXT, mặc định `''` | `state.py` |
| Lệnh chữ | `/nailblog ok <id>`, `/nailblog reject <id> <lý do>`, `/nailblog flow` | `commands.py` (quyết định đi qua `approvals.decide`) |
| Key config | như S0 + `budget.daily_usd` hiện có (ngày VN) | `config.py` |
| Env var | `NAILBLOG_GEMINI_IMAGE_KEY` | `env_secrets.py` |
| `Service.browser_lock`, `Service.media_send` | `asyncio.Lock`, hook gửi media | `service.py` |
| Dòng log publish | `NailBlog publish post= step= outcome= elapsed_ms=` | `admin.py` |
| Loại thông báo | `approval` | `approvals.py` |
| | `autoflow`, `budget` | `autoflow.py` |
| Run ID + thư mục | `auto-YYYYMMDD-HHMM-t<topic>` dưới `runs_dir`; `compare-<stamp>` | `autoflow.py`; `images/compare.py` |

## Rủi ro (GATE 0b: tiền thật, máy trạng thái bài, quyền duyệt của Owner, migration DB)

- **Đăng trùng:** autoflow không bao giờ publish (S9 test); `claim_publish` nguyên tử; khoá profile chặn tiến trình riêng. State root riêng là DB riêng ⇒ né được publish limit, topic claim **và trần 2 USD**; chạy song song khi service đã arm phá cả ba — đây là luật viết, code không chặn.
- **Chi tiêu:** `uncertain` settle ở đủ mức reserve (an toàn nhưng số ghi cao hơn thật); response không có USD, chưa thấy trần cứng phía Google; đổi `budget` giữa run làm resume fail-closed; số token mỗi tier suy từ bảng giá — model "nghĩ" nhiều hơn dự kiến và vượt trần thì run held (`reported_usage_exceeds_reserved_bound`).
- **Timeout sau khi PATCH đã gửi:** bài kết thúc `uncertain`, chỉ đọc lại; test phải chứng minh không có PATCH thứ hai.
- **Snapshot đổi giữa xem trước và `ok`:** bị từ chối vì `accept_images`/`approve` kiểm lại snapshot đã lưu; bản xem trước cũ bị thay, không tự duyệt.
- **Restart do crash vẫn arm:** retry bị claim + sổ giới hạn; build/config mới thì disarm.
- **Restart giữa lúc viết bài:** mất slot (`text_requires_manual_reconciliation`), hành vi an toàn hiện có, có DM.
- **Secret:** key bị xoá khỏi env trước khi Playwright chạy; `TG_API_HASH` vẫn lọt sang Chromium như cũ (ngoài scope).
- Shared layer bị đụng: transport Telegram, router lệnh, schema config, migration `state.py`, systemd unit, sổ trong `paid.py`/`pipeline.py`.

## Câu hỏi cho Owner

1. 2 hay 3 slot? Lần đầu mỗi ngày: phương án B 0,40 vs 0,61 USD; A 0,60 vs 0,91 USD. A + 3 slot + có reject ≈ 1,81 USD, sát trần. Nếu 3 slot: 12:00 dùng nhóm chủ đề nào, trần đăng có là 3 không?
2. Chốt chặn phía Google: có đặt budget alert (hoặc quota nếu tài khoản có) cho project của key không? Không có thì sổ local là chốt chặn cứng duy nhất.
3. Lượt ảnh `uncertain`: đồng ý tính đủ giá sau 24h (vào ngày gốc) để không chặn trần các ngày sau?
4. Lượt so sánh: variant nào, bao nhiêu tiền? Đề xuất: NB2@2K cover; NB2@1K vs flash-lite@1K thân bài; Pro@2K cover; tổng tối đa 0,80 USD trong 2 USD của một ngày.
5. Arm: giữ qua restart do crash, disarm khi wheel/config mới? Khi nào bật start-at-boot?

Nếu không phản đối: bỏ writer proof Cloudflare và token analytics (lý do ở mục hiện trạng).

## Ước lượng

Khoảng 26 file (7 module/test mới + ~19 file sửa), ~1 400 dòng code + ~1 300 dòng test. S0 3 · S1 5 · S2 5 · S3 4 · S4 5 · S5 3 · S6 4 · S7 4 · S8 6 · S9 6 · S10 4 ⇒ **≈ 49h executor** qua 6 đợt, cộng review và một ngày staging thật.

## Chưa kiểm

- ID chính xác của model ảnh Gemini 3, kích thước pixel thật mỗi tier ở 16:9, số token mỗi ảnh, `imageSize` có chạy trên flash-lite không — `image-compare` đo.
- `candidatesTokensDetails` có điền cho output ảnh không.
- Có trần chi cứng nào phía Google không.
- Config/env thật trên THINKPAD và nguyên nhân treo 23/10.
- Thay đổi cover chưa commit trong worktree phải nằm ngoài các commit này.

## Nhật ký thực thi + ghi chú mang sang stream sau

| Stream | Kết quả | Verify |
|---|---|---|
| S0 | 796 test OK (baseline 756) | nx-verifier PASS_WITH_NOTES — giá ảnh chỉ bắt buộc khi `autoflow.enabled` (Owner-side quyết định của orchestrator, giữ config cũ chạy được) |
| S1 | 814 test OK | nx-verifier PASS_WITH_NOTES — digest mặc định trùng khớp base `ea9e342` (đo độc lập), 8/8 mutation bị test bắt |
| S2 | 810 test OK | nx-verifier PASS_WITH_NOTES ×2 — không có đường PATCH lần hai; đã thêm trần cho `_save_remote_draft` + test backoff thật; đang làm chặt test treo lưu Draft |
| Ghép đợt 0+1 | 828 test OK (796 + 18 + 14); **commit cục bộ** (Owner cho phép 23/10, không push): `8cc5860` S0 · `636b5ec` S1 · `f3466a7` S2 trên `feat/noissue_nailblog-autoflow` | orchestrator chạy |
| Đợt 2 | S3 840 OK · S4 853 OK · S5 836 OK (+7 deploy test) | S5 PASS_WITH_NOTES · S3 PASS_WITH_NOTES ×2 (845 test) · S4 PASS_WITH_NOTES ×2 (861 test) — mutation bắt đủ trừ nhánh cắt 4096 (không chạm được) |
| Ghép đợt 2 | 886 test OK, deploy test OK; commit cục bộ `24b7b2d` S3 · `3584eb1` S4 · `f9cb6f1` S5 | orchestrator chạy |
| Đợt 4 | S9: 986 test OK, 9/9 mutation bắt; **lỗi thật**: chạm trần chi trước khi giữ chỗ để lại ứng viên `pending` ⇒ hôm sau `held candidate_requires_reconciliation` (text tương tự) — đã sửa: từ chối trước khi giữ chỗ ⇒ `paused`/`text_paused`, sau khi giữ chỗ giữ nguyên đối soát; 993 test + 53 deploy test OK; nx-verifier PASS_WITH_NOTES; commit `7a27128` | — |
| Đợt 5 | S10: nx-reviewer GATE 0b toàn nhánh `ea9e342..7a27128` ⇒ **CHANGES_NEEDED** (B1 chọn lại chủ đề giữa run ⇒ 2 run/2 bài/tốn gấp đôi; B2 `/nailblog approve` cũ bỏ qua `ok` Draft; B3 `resume` arm theo đĩa không theo process; M4 trần 2 USD tính riêng từng state root; M5 `daily_publish_limit` 1 < 2 slot) — S10a đã sửa 12 hạng mục (1030 test + 53 deploy OK, 20/21 mutation bắt, 1 tương đương); nx-verifier PASS_WITH_NOTES (HIGH deploy: sổ chi chung ngoài ReadWritePaths); re-review GATE 0b **CHANGES_NEEDED** — B1/B2 Telegram/B3 đóng; còn blocker: duyệt Draft qua CLI `one-post approve` qua mặt engine (bài đăng không cần `ok`); MAJOR: digest binding đổi cho mọi config, chuyển sang sổ chung mất chi tiêu cùng ngày — S10b đang sửa | — |
| Đợt 3 | S6 PASS_WITH_NOTES (`67a2235`) · S7 PASS_WITH_NOTES (`ddcbbbf`, merge `b7e3563`) · S8 **FAIL** lần 1 (thiếu mã reconciliation ⇒ prepare thử lại tới 3 lần, có thể gửi thêm lượt ảnh trả phí trong trần `run_usd`) — đã sửa, re-verify PASS_WITH_NOTES (14/15 mutation), commit `415e1f6`; bản ghép S6+S7+S8 964 test OK; mỗi stream worktree riêng từ `f9cb6f1`; hợp đồng S7↔S8: `async retry_images(run_id, reason)`, `mark_published(run_id)`, `prepare(..., before_dispatch=None)` | — |

Ghi chú bắt buộc nhúng vào spec các stream sau:
- **S3 `image-compare`** (và mọi chỗ gọi `paid()` ngoài `ImagePipeline.run`): phải tự gọi `PaidLedger.reconcile_stale` + `age_out_uncertain` trước khi reserve.
- **S8 autoflow:** `Service.browser_lock` là `asyncio.Lock` không reentrant và `save_remote_draft` đã tự lấy lock (S2) ⇒ S8 **không** được giữ `browser_lock` khi gọi `save_remote_draft`.
- **S8/S9:** cũng không được giữ `browser_lock` quanh `publish_pass`; `publish_pass` tính `due_posts()` một lần đầu lượt — S8 đừng xếp thêm việc trình duyệt dài ngay trước slot (giữ luật không tạo Draft trong 20 phút trước slot).
- **S8/S6:** `daily.slot_categories` có thể là `{}` khi config có slot khác mặc định mà không khai bảng (S0) ⇒ phải xử lý mapping rỗng.
- Đã biết, chấp nhận (thận trọng): intent `pending` đang chờ `MODEL_LOCK` quá `timeout+600s` có thể bị tiến trình khác đánh `uncertain` ⇒ mất ảnh đó và trần bị tính dư, không bao giờ tính thiếu.
- Đã biết, mức thấp: chạm trần do `reserved + bound` vượt trần thì `countTokens` vẫn chạy trước khi `reserve()` từ chối (tốn 1 lời gọi đếm token miễn phí, không tốn tiền ảnh).
- **S9 (từ S5):** đăng ký lệnh `startup-gate` trong `__main__.py` (gọi `state.startup_gate(config)` và in dict trả về); `pause-install` ở `__main__.py:146` phải gọi thêm `State.disarm()`; `/nailblog resume` gọi `state.arm(startup_binding(config)[0])`. `build.json` không được cài lên host ⇒ binding dùng digest các file `.py` của package khi không tìm thấy `build.json`; config đọc từ `NAILBLOG_CONFIG` (unit đã set).
- **S8/S9 (từ S4):** `build_preview(kind, artifact, image_paths, *, approval_id=, deadline=)` — bắt buộc truyền `approval_id` + `deadline` (không có thì thẻ hiện `<id>` trần); artifact one-post không có `slot_at` ⇒ S8 phải thêm `slot_at` vào artifact trước khi dựng thẻ; dự báo chi phí ảnh Gemini phải nằm ở key `forecast_usd` (thẻ chỉ hiện USD khi có `usd`/`forecast_usd`/`estimated_usd`/`run_usd`). `ApprovalStore.decide` trả dict `{ok, code, id, status, reason}`; mã từ chối: `wrong_sender`, `unknown_request`, `reason_required`, `already_decided_differently`, `superseded`, `expired`; gọi `record_messages(id, ids)` sau khi gửi album.
- **S9 (từ S4):** `make_owner_media_sender` chưa thử với Telethon thật — cần một lượt gửi thật trên staging (album JPEG `BytesIO` có tên) trước khi bật autoflow.
- **S9 (từ verify S5):** đăng ký `startup-gate` phải truyền `config_path=args.config`; `/nailblog resume` phải **từ chối arm** khi digest config là `"missing"`; nên giới hạn tìm `build.json` chỉ trong thư mục package (không lên `site-packages`). **Không deploy unit file mới trước khi S9 xong** — `ExecStartPre` gọi lệnh chưa đăng ký sẽ làm service không khởi động được.
- **S8/S9 (từ verify S4):** dùng `ApprovalStore.is_approved(run_id, kind, snapshot)` thay vì tự so snapshot; artifact truyền vào `build_preview` phải có `review_bypass`.
- **S8 (từ re-verify S4):** `is_approved` so **đúng snapshot**, không phải chốt chặn snapshot cũ ⇒ S8 luôn truyền đúng snapshot sắp dùng (snapshot đã lưu của run), không bao giờ dùng snapshot của yêu cầu trước. `OwnerMediaSendError.sent_ids` mất khi lượt gửi bị `CancelledError` giữa album ⇒ S8 xử lý cancel bằng cách coi yêu cầu chưa gửi và gửi lại thẻ mới (yêu cầu cũ superseded).
- **S9 (từ S6):** `Service.plan_week` (`service.py:~526`) phải gọi `propose(text, items, posts, week_start, today, self.config)`; sửa ghi chú "14 slot" ở `service.py:530–535` theo số slot cấu hình; chuỗi `USAGE` trong `commands.py` còn ghi `<06|19>` (đang bị `test_commands` ghim) — cập nhật cùng test khi thêm `ok`/`reject`/`flow`.
- **S8/S9 (từ S7):** `retry_images` ghi `image_retries=1` + phase `images_pending` **trước** khi chạy lại pipeline; nếu pipeline lỗi (vd hết ứng viên chưa thử) run nằm `images_pending`, gọi `retry_images` lần nữa bị `image_retry_not_allowed` ⇒ S8 coi lỗi đó là reject lần 2 → `held` + DM, không gọi lại. Run không có `image_run` trong document (run cũ / ảnh import) ⇒ `image_retry_not_allowed`. `before_dispatch` trả False ⇒ `AdminError('dispatch_stopped')` ⇒ `text_paused`.

Đề xuất đổi rule do executor gửi (relay nguyên văn ở bước báo cáo cuối):
- nx-executor S7: `subst` từ Git Bash lỗi `Path not found`, chạy được qua `powershell -NoProfile -Command 'subst J: "C:\..."'` — brief nên ghi dùng PowerShell cho `subst`.
- nx-executor S7: full suite 10–12 phút, dài hơn timeout 10 phút của lệnh foreground — brief nên ghi chạy nền.
- nx-executor S7: các stream song song tự chọn ký tự ổ đĩa ⇒ nên chia sẵn dải ký tự không chồng nhau.
- **Từ verify S7 (chấp nhận, ghi nhớ khi nghiệm thu):** nếu `reject_selected` lỗi sau khi lượt thử lại đã được ghi, ảnh giữ nguyên mà lượt thử lại mất — S8 đưa bài vào `held` nên không bị dùng ảnh cũ. `before_dispatch` chỉ chặn text, không chặn lượt ảnh trả phí (lượt ảnh vẫn bị trần + timeout giới hạn). Dừng sau lượt text đầu tiên ⇒ `text_requires_manual_reconciliation` (mất slot, có DM) — hành vi an toàn có sẵn. `mark_published` là hàm **đồng bộ**; S8 đã được yêu cầu bỏ `await` và thêm guard so sync/async của fake với OnePost thật.
- nx-executor S8: file tạm của executor (vd script mutation) phải nằm trong scratchpad, không ở gốc worktree — hook chặn cả lệnh xoá dọn dẹp.
- **S9 (từ re-verify S8):** gọi `autoflow.run_due(service, stop, ..., now=service.clock())` — engine dùng `service.clock()` cho mọi dòng sau dòng đầu; mã lỗi ảnh không có chữ reconciliation (vd `image_quality_held`, `usage_unknown`) đi đường tạm thời: tối đa 3 lần rồi `held too_many_failures:<code>`, không tốn thêm lượt gọi trả phí (pipeline bỏ qua ứng viên đã bị loại).
- nx-executor S9 (relay): brief mặc định nên ghi `subst` gọi qua `powershell -NoProfile -Command`.
- nx-executor S9 (relay): hook chặn `rm -rf` cả khi không có file khớp ⇒ brief nên bảo dùng tên thư mục scratch mới thay vì xoá.
- nx-executor S9 (relay): khi spec đổi hành vi mà test cũ ghim hành vi đó, nên cho phép sửa test kèm bằng chứng fail/pass, không chỉ "chuỗi bị ghim".
- nx-executor S9 (relay): bảng tài nguyên dùng chung nên ghi `daily.run_due` giữ luồng tick trong lúc engine chạy (lượt ảnh trả phí có thể dài nhiều phút) — S10 quyết có tách task riêng không.
- nx-executor S9 (relay): ghi vào hợp đồng S1/S8 bất biến "từ chối trước khi dispatch ⇒ candidate/text `paused`"; khi tiêu chí nằm ở file ngoài scope thì dùng `expectedFailure` kèm lý do rồi xin mở scope.
- **S10 (từ verify S9):** run-ceiling refusal hiện pause + thử lại mỗi ngày tới hạn ảnh (không bao giờ thành công vì trần run không reset) và DM báo nhầm là hết ngân sách ngày ⇒ nên chuyển thành `held` với mã riêng (`run_ceiling_reached`) + DM đúng nội dung.
- **Deploy (từ S10a):** `budget.ledger_db_path` (sổ chi chung) bắt buộc khi bật autoflow — thư mục phải thuộc user service, mode 0700; kiểm unit systemd có quyền ghi thư mục đó (`ReadWritePaths`) trước khi bật. Mọi config trỏ chung sổ phải cùng `daily_usd`.
- nx-executor S10a (relay): phần verify trong role (`pnpm typecheck`/`build`) không áp cho repo Python ⇒ nên có biến thể Python của role executor.
