# NailBlog — Gom code về một nhánh, chỉ dùng Agy CLI (v2.0, 27/10/2026)

> **Tạo:** 2026-10-27 · task `.agent-tasks/T-261023-nailblog-full-flow-automation-on-thinkpa` · Route C · GATE 0b · trạng thái: **chờ Owner duyệt**
> Plan do `nx-planner` (xhigh) lập từ code thật; Claude ghi lại. Thay thế [[nailhub-blog-noissue_tasks_261023_v1.1.0]] (Gemini API trả phí, đã bỏ).
> Sẽ chuyển vào `Docs/04-tasks/` của nhánh `feat/noissue_nailblog-agy` sau R0.

## Quyết định Owner 27/10/2026

1. Agy CLI (`/snap/bin/agy`, gemini-3.8-flash-medium / medium) cho **cả text lẫn ảnh**. Không API trả phí.
2. Gỡ hẳn: ảnh Cloudflare/Gemini API, text Ollama/DeepSeek/Gemini API, script one-post/makeup/provision chạy tay. Giữ `paid.py` **chỉ đọc** (intent uncertain 0,124928 USD).
3. Claude tiếp quản từ phiên Codex daily4 (Owner đã dừng phiên Codex). Đích: THINKPAD staging tự chạy 4 bài/ngày 06/10/15/19 giờ VN, boot-enabled.

## Nguồn code

| Ký hiệu | Vị trí | Ghi chú |
|---|---|---|
| A | nhánh `feat/noissue_nailblog-phase1` @ `ea9e342` + cover v2 chưa commit | đã nằm trong B |
| B | nhánh `feat/noissue_nailblog-autoflow` @ `7a27128` | S0–S9 |
| C | worktree Temp `wt-s10a` (chưa commit: 23 sửa + 27 mới) | **= Freeze7 đang chạy trên THINKPAD** (diff -rq với `cli-freeze7-stage/source` chỉ khác 4 file deploy + `uv.lock`) |
| D | `~/.codex/visualizations/2026/10/26/01a175d0-…/nailblog-automation` (ngoài git) | daily4 = wrapper root nạp package nailblog từ `reference-source` (= C, trừ alt 5–40); `daily-four-config-repair-stage` dở (4 file 0 byte) |
| Backup | `C:/Users/AD/.agentfleet/backups/nailblog-20261027/` + `SHA256SUMS` | patch/tar của A, C, D |

Lỗi trong file sắp commit: rác `var/lib/x/ledger.sqlite3` (do `test_s10a_gate0b.py`); thiếu `.gitattributes` (autocrlf=true đổi byte ⇒ lệch hash binding/build).

Runtime THINKPAD 09:51: `tg-nailblog.service` đang chạy `report_bridge.py` của daily4 (thay chuỗi trong `nailblog.telegram` lúc chạy rồi gọi `nailblog run`); worker `tg-nailblog-subscription` chạy; agy PID 545512 từ 26/10 09:20; `nailblog-daily4.timer` chưa enable; outbox rỗng.

## Kiểm kê theo phần

| Phần | Nguồn tốt nhất | Quyết định |
|---|---|---|
| Text | C `writer.py`/`quality.py`; D `makeup_agy_text_once.py` (agy 1 lượt), `daily-four-stage/generation.py` (provenance native) | GỘP: giữ writer/quality, thêm agy 1 lượt + provenance, qua worker. GỠ `gemini_text.py`, nhánh Ollama/DeepSeek/Gemini trong `text.py`, nhánh tiền text trong `Service.model_call` |
| Ảnh | C `images/subscription*.py`, `agy_native.py`, `subscription_sqlite.py`, `post.thumbnail_cli`, `cover-cli.html` | GIỮ C. GỠ `cloudflare.py`, `neurons.py`, `provider.py`, `compare.py`, chấm qwen, nhánh paid/free trong `pipeline.py` |
| Chủ đề/slot | C `daily.py`, `planner.py`, `trends.py`, `sources.py`, `schedule.py` | GIỮ C, mở 4 slot (`MAX_PUBLISH_PER_DAY` 3→4). Catalog daily4 chỉ archive |
| Engine | C `autoflow.py`, `onepost.py`, `Service.scheduler` | GIỮ C. GỠ D `runtime.py` |
| Duyệt/Telegram | C `approvals.py`, `/nailblog ok\|reject\|flow`, `make_owner_media_sender` | GIỮ C; GỘP ý outbox `random_id` + id tin server của D `reporting.py`. GỠ `control.py`, `report_bridge.py` |
| Draft/publish/readback | C `admin.py`, `admin_state.py`, `publication.py`, `publish_pass` | GIỮ C; GỘP timeout POST Draft + đối soát chỉ đọc có giới hạn, quét trùng site trước Create (`publisher.duplicate_row`). Alt giữ 5–15 |
| Sổ chi | C `paid.py`, `usage.py`, `env_secrets.py` | `paid.py` viết lại chỉ đọc (sqlite `mode=ro`, chỉ `report`). GỠ reserve/settle, `env_secrets.py`, key giá. Trần mới: `subscription.max_daily_jobs` |
| Startup gate | C `state.startup_gate`, `commands.preflight/resume` | GIỮ, bỏ `_check_gemini`. GỠ grant/mandate D |
| Deploy | C `systemd/`, `nailblog/deploy/*`, `deploy/agentfleet_deploy.py` | GIỮ, enable 2 service khi boot. D: disable+mask trên host, archive trong git |
| Script tay | D ~80 script + `*-stage`; C subcommand `one-post`/`images`/`image-compare`/`save-draft` | GỠ (D archive 1 commit). `OnePost` giữ làm thư viện. `staging_immediate.py` giữ tạm (tắt) |

## Kiến trúc đích

- `tg-nailblog.service` (UID992, boot-enabled) `python -m nailblog run`: Telegram Owner + scheduler 60s (tick + `publish_pass`) + `daily.run_due` (10:00 D-1 propose, 13:00 decide 4 chủ đề) + `autoflow.run_due` mỗi slot 1 run: text job → sửa tự động → gate → [≤1 job sửa] → 4 job ảnh → thẻ ảnh → `ok` → Draft → thẻ Draft → `ok` → scheduled → publish đúng slot → readback (admin + ẩn danh + click trang chủ + SHA ảnh); 20:00 báo cáo qua outbox.
- Một DB `/var/lib/agentfleet/nailblog/nailblog.sqlite3`; artifact theo run chỉ là file.
- `tg-nailblog-subscription.service` (UID1000, boot-enabled): worker FIFO, heartbeat 15s **kể cả khi đang chạy job**, 1 job/lần dưới `/run/agentfleet/ollama.lock`; `kind=image` (chỉ `generate_image`) | `kind=text` (`tools: []`, `commandExecutionPolicy: off`).
- Không timer rời, không state root riêng. **Bot không giữ `MODEL_LOCK` khi chờ worker** (`service.py:198`, nếu không sẽ deadlock).

## Git

Nhánh `feat/noissue_nailblog-agy` từ `7a27128`, worktree mới `.claude/worktrees/nailblog-agy` (không đụng checkout chính).

1. **R0** `git worktree add -b feat/noissue_nailblog-agy <path> 7a27128`.
2. **R1 cứu C** (commit 1): kiểm `SHA256SUMS`, áp patch + untracked, bỏ `var/`, `__pycache__`; `git add` đích danh `nailblog/ config/nailblog.example.toml systemd/`; script so sha256 blob stage với `cli-freeze7-stage/build.json → source_files` = 100% (+4 file deploy).
3. **R2 phần còn lại của A** (commit 2): Docs tasks v1.0/v1.1, `Docs/05-changelog/`, 3 test chưa track nếu qua trên R1 (không qua → archive), cover v2 → `nailblog/archive/phase1-cover-v2.patch`.
4. **R3 cứu D** (commit 3) → `nailblog/archive/codex-20261026/`: chỉ `.py/.md/.toml/.service/.timer/catalog.json` của `daily-four-stage`, `daily-four-config-repair-stage` (ghi rõ dở), `host-autonomous-recovery-v2-stage`, `parallel-local-publish-v4-stage`, `makeup_agy_text_once.py`, `nailblog-text-only-20261026.md`, `Docs/`, `HANDOFF.md`, `THINKPAD-STATUS.md`, `PLAN.md`, `AGY-TEXT-*.md`; kèm `MANIFEST.json`; quét secret trước commit.
5. **R4+**: mỗi stream một commit. S8 gỡ archive sau nghiệm thu.

Push là bước riêng sau nghiệm thu.

## Stream

Test (PowerShell, gốc repo): `$env:PYTHONPATH='nailblog/src'; $env:PYTHONUTF8='1'; python -m unittest discover -s nailblog/tests -t nailblog -p "<file>"`. Test worker chỉ chạy POSIX ⇒ chạy bản copy ở `/tmp` THINKPAD. Full suite 10–12 phút, chạy nền.

| # | Đợt | File sở hữu | File cấm | Xong khi | Giờ |
|---|---|---|---|---|---|
| R | 0 | như mục Git | mọi file code | so hash R1 = 0 lệch; không còn `var/`; baseline full suite ghi số test + lỗi có sẵn | 2 |
| S0 config + 4 slot | 1 | `config.py`, `config/nailblog.example.toml`, `tests/test_config*.py` | khác | chỉ nhận `text_model.provider="agy"`, `image.provider="subscription"`; `removed_key:<tên>` cho key cũ; 4 slot, `daily_publish_limit=4`; key mới `subscription.text_agent`, `text_timeout_seconds` (120..900, mặc định 600), `admin.create_timeout_seconds`, `max_daily_jobs` mặc định 48 | 3 |
| S1 worker text + heartbeat | 2 | `images/subscription.py`, `subscription_io.py`, `agy_native.py`, `subscription_sqlite.py`, `subscription_worker.py`, mới `agy_text.py`, `nailblog/deploy/*`, test subscription/agy_text | `pipeline.py`, `subscription_pipeline.py`, `config.py`, `writer.py`, `text.py`, `service.py`, `onepost.py` | runner chặn 300s vẫn ready; stdout trùng 4 JSON → 1; timeout + DB có câu trả lời → `complete_salvaged`, không → `unknown` không tự dispatch; `AgyText.ask` không dưới `MODEL_LOCK`; job cũ v1 và `ccc058…` không chạy lại | 6 |
| S2 gỡ đường tiền, sổ chỉ đọc | 2 | `images/pipeline.py`, `subscription_pipeline.py`, xoá `provider/cloudflare/neurons/compare.py` + `env_secrets.py`, `paid.py`, `usage.py`, `admin_images.py`, `__main__.py`, `commands.py` (preflight/status) | `service.py`, `onepost.py`, `autoflow.py`, `text.py`, `writer.py`, `gemini_text.py`, `config.py`, `admin.py` | `rg cloudflare\|neurons\|GeminiImages\|generativelanguage` = 0; CLI không còn `images`/`image-compare`/`one-post`/`save-draft`; `test_paid_readonly`: thấy 0,124928, sha DB/-wal/-shm không đổi | 6 |
| S3 writer agy + 4 slot | 2 | `writer.py` (`write_agy`, `repair_agy`), `daily.py` (propose 4 slot), `planner.py`, `quality.py` (nếu cần) + test | `text.py` (tên công khai), `service.py`, `onepost.py`, `images/*`, `config.py` | ≤2 lượt gọi/bài; `daily_schema(4)`; `pick 10 1`, `pick 15 2` chạy; `_backlog` không `KeyError` | 5 |
| S4 nối engine | 3 | `service.py`, `onepost.py`, `autoflow.py`, `text.py`, xoá `gemini_text.py` + test | `admin*.py`, `publication.py`, `telegram.py`, `notices.py`, `commands.py`, `daily.py` | `text_model()` = `AgyText`; text unknown → ≤1 job mới; quét trùng **trước** `claim_create`; op < 30 phút + 0 ứng viên → `create_unknown_reconciling`, quá → `create_requires_manual_reconciliation`, không POST lại; e2e 4 slot → 4 published; prepare fail → 0 publish | 6 |
| S5 gia cố Draft/publish | 3 | `admin.py`, `admin_state.py`, `publication.py` + test admin/publish | `service.py`, `onepost.py`, `autoflow.py` | POST treo → raise trong `create_timeout_seconds`, đúng 1 click; `find_duplicates` quét mọi trang/trạng thái; readback treo hết giờ trong budget; evidence có `mime_defect`; alt 5–15 | 5 |
| S6 outbox + báo cáo 20:00 | 3 | `telegram.py`, `notices.py`, `commands.py` (status/flow), `daily.py` (`preview_dm`), mới `test_outbox.py` | `service.py`, `autoflow.py`, `admin*.py` | gửi lại sau crash cùng `random_id`; báo cáo đủ từng slot, URL, giờ, MIME, dòng sổ cũ | 4 |
| S7 deploy/cutover | 4 | `systemd/tg-nailblog.service`, `nailblog/deploy/*`, `deploy/README.md`, `deploy/test_nailblog_deploy.py`, `.gitattributes`, mới `deploy/nailblog_cutover_check.py`, runbook `Docs/` | `nailblog/src/**` | deploy test đạt; cutover_check chạy fixture không ghi | 4 |
| S8 dọn + docs | 4 | `config.py` (xoá field cũ), xoá `ai_transport.py` → `net.py`, `test_parity.py`, mới `test_no_paid_paths.py`, `AGENTS.md`, Docs, vault `Decisions/D-NNN` | `model_lock.py` | full suite ≥ baseline R (trừ test tính năng đã gỡ, liệt kê); `test_no_paid_paths` đạt | 4 |
| G GATE 0b | 5 | diff `7a27128..HEAD` trừ archive | — | nx-verifier + nx-reviewer PASS; full suite Windows + THINKPAD `/tmp` | 4 + review |

Tổng ≈ 49h executor + review + deploy ~3h + 1 ngày nghiệm thu.

## Tài nguyên dùng chung ở runtime

| Tài nguyên | Hình dạng | Sở hữu |
|---|---|---|
| DB | `/var/lib/agentfleet/nailblog/nailblog.sqlite3`, không đổi schema bảng cũ (`autoflow_runs.run_usd`=0) | `state.py` |
| `autoflow_runs.stage` | giữ `STAGES`; lý do mới `text_quality_held`, `duplicate_on_site`, `create_unknown_reconciling` | `autoflow.py` |
| `onepost_runs.phase` | như cũ + `text_jobs` | `onepost.py` |
| `approval_requests`, `control.paused/armed_binding` | không đổi | `approvals.py`, `state.py` |
| `paid_runs`, `paid_intents` | chỉ đọc (`mode=ro`) | `paid.py` |
| bảng `cf_*` | để nguyên, không đọc, không drop | — |
| Queue | `/var/lib/nailblog-subscription/queue/<job>/…` + `worker-ready.json {protocol, config, at, busy_job}`; ảnh `nailblog-subscription-v1`, text `nailblog-agy-text-v1` | `images/subscription.py` |
| Policy agent | `~startechai/snap/antigravity-cli/common/.gemini/config/agents/` ảnh + text | S1, `nailblog/deploy/` |
| Lệnh Telegram | `ok/reject/flow/status/pause/resume/pick/topics`; bỏ `/nailblog daily4` | `commands.py` |
| Notice/outbox | `notices` + `random_id`, `message_ids` | `notices.py` |
| Khoá | `/run/agentfleet/ollama.lock` (chung triage/nailhub), `browser_lock`, khoá profile admin | `model_lock.py`, `service.py` |

## Rủi ro GATE 0b

- **Tiền:** `test_no_paid_paths` + `rg` = 0; gỡ `NAILBLOG_GEMINI_IMAGE_KEY`; sha256 dump `paid_intents` trước/sau cutover bằng nhau. Bẫy: `PaidLedger.__init__`/`State()` ghi schema ⇒ bản đọc không đi qua chúng.
- **Dữ liệu:** kiểm onepost thiếu claim trước cutover; giữ nguyên state root makeup/daily4 (Create UNKNOWN 26/10).
- **Đăng trùng:** mask daily4/makeup, gỡ drop-in daily4 (`systemctl cat tg-nailblog` không còn `report_bridge`); quét trùng trước `claim_create`; Create UNKNOWN không POST lại.
- **Quyền Admin:** UID1000 không đọc được `/etc/nailblog/private/admin-credentials.json`; policy text không tool, không accept-edits.
- **Duyệt Owner:** gắn snapshot; `/nailblog approve` vẫn bị chặn.
- **Production:** giữ ràng buộc `autoflow.enabled` cần `contract.environment=staging`.
- **Khoá:** deadlock nếu bot giữ `MODEL_LOCK`; job agy giữ `ollama.lock` tới 10 phút.
- **Text unknown tự chạy lại ≤1 lần** — reviewer phải xác nhận.

## Deploy + nghiệm thu

1. `nailblog_cutover_check.py` chỉ đọc (unit/drop-in/timer, control, onepost thiếu claim, queue, sổ sha256, PID 545512, không còn phiên Codex ghi).
2. Backup `/etc/nailblog*`, unit, DB (`.backup`), sổ.
3. Dừng lane cũ (cần Owner): gỡ drop-in daily4 + `daemon-reload`; disable+mask `nailblog-daily4.*`, `nailblog-makeup-20261026-*`; dừng PID 545512 nếu không giữ job.
4. Cài wheel bằng `deploy/agentfleet_deploy.py --service nailblog` (full suite trên host; startup-gate ⇒ paused+disarmed).
5. Config mới (live, staging, 4 slot, `daily_publish_limit=4`, `autoflow.enabled`, `ledger_db_path`, feed `https://www.nailsmag.com/rss`), policy text, restart worker, gỡ key Gemini.
6. `systemctl enable tg-nailblog tg-nailblog-subscription`; `systemd-analyze verify`.
7. Owner `/nailblog resume` (preflight → arm).
8. Nghiệm thu ngày D: 4 dòng `autoflow_runs` + 4 `posts` `published`; readback có `anonymous_homepage_click` + 4 SHA ảnh; curl 200 cả 4 URL; journal không `not_ready`/`cli_timeout_unknown`/traceback; sha sổ không đổi. Tuỳ chọn 1 lần reboot.

Rollback: `pause-install` → cài lại wheel Freeze7 từ backup → khôi phục config/unit. Không xoá intent/journal.

## Câu hỏi Owner

1. Mô hình duyệt: **A** hai lượt `/nailblog ok` (8 lệnh/ngày; hạn ảnh slot−3h, Draft slot−2h) hay **B** mandate tự duyệt kiểu daily4.
2. Bài trượt gate sau 1 lượt sửa: (a) held + bỏ slot + DM; (b) thẻ HELD, Owner `/nailblog ok <id> override <lý do>` cho lỗi fixable (facts/Han/claim không override).
3. Nhóm chủ đề 10:00/15:00; cover (i) có chữ tiêu đề (đang chạy) hay (ii) ảnh sạch + logo (v2).
4. Quyền: (a) commit cục bộ R1–R3; (b) cutover THINKPAD; (c) 1 lần reboot nghiệm thu.

## Chưa kiểm

Chưa chạy suite/baseline; chưa đọc drop-in daily4, config thật, `ledger_db_path` thật, DB chính, bản chất PID 545512; chưa biết agy viết xong 1 bài trong 600s ổn định, quota ~41 job/ngày; mới so C với stage, chưa so với byte cài trên host; chưa đọc hết `publisher.py` daily4; chưa rõ cần bỏ `viewCount`/`lastModified` khỏi `publication.matches`; chưa xác minh `trends` không gọi model; cache bài đã đăng (`published_list_required`, host `feeds=[]`); độ công khai repo (hostname/machine-id trong archive); MIME WebP và Draft UNKNOWN 17:03 26/10 phía NailHub.

## Changelog

| Phiên bản | Ngày | Thay đổi |
|---|---|---|
| v2.0.0 | 27/10/2026 | Agy-only, gom A/B/C/D, chờ Owner duyệt |
