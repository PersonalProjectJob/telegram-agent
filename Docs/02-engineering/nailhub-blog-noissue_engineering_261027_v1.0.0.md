---
type: doc
title: "NailBlog engineering — Agy-only"
owner: AlexChu
created: 2026-10-27
status: draft
---

> **Cập nhật đối chiếu v2.0.1 — 2026-10-28 14:17 (Asia/Ho_Chi_Minh).** Tác giả: Codex; trạng thái: chờ review.

**Đối chiếu hiện trạng source:** commit `89a5ac8` trên nhánh `claude/nailblog-automation-review-091e47`. Đây là trạng thái mã nguồn, không khẳng định cấu hình hay phiên bản đang chạy trên THINKPAD. Các mô tả phía dưới có thể thuộc quyết định cũ; khi khác với bản cập nhật này, dùng phần cập nhật và kiểm tra cấu hình thực tế.

## Bổ sung source 28/10: tự duyệt và thumbnail

`autoflow.owner_approval` mặc định `true`: Owner duyệt ảnh và Draft. `false` chỉ hợp lệ trên staging; source tự ghi hai quyết định gắn run và snapshot sau khi bài `pass`, không issue và ảnh qua kiểm tra xác định. Không tự override. Hạn ảnh, hạn Draft và cửa sổ đăng vẫn áp dụng; không mặc định đăng bù sau hạn. `image.review_bypass` có thể bỏ chấm ảnh bằng model, không loại bỏ các kiểm tra xác định.

Commit `89a5ac8` bỏ chữ và lớp phủ tối trong renderer subscription, điều chỉnh prompt cover sáng/sinh động và tăng phiên bản prompt/xử lý. Binding thay đổi: phải đánh giá các run và manifest cũ trước triển khai; không tự ép resume hoặc sửa DB. Chưa có bằng chứng deploy hay ảnh thực sau sửa. [QA](../03-qa/nailhub-blog-noissue_qa_261027_v1.0.0.md) · [Runbook](nailhub-blog-noissue_cutover-runbook_261027_v1.0.0.md) · [Nghiệp vụ](../01-product/nailhub-blog-noissue_business_261015_v1.0.0.md).

**Lịch sử thay đổi:** v2.0.1 — 2026-10-28 14:17 — đối chiếu source, vận hành và bằng chứng; không thay đổi trạng thái phê duyệt cũ.

---

> **Nội dung lịch sử bên dưới:** giữ để truy vết; nếu khác với cập nhật phía trên, dùng phần cập nhật và kiểm tra cấu hình thực tế.


# Engineering: NailBlog Agy-only (kiến trúc đích sau S8)

Version: v1.0.0 · 2026-10-27 · Căn cứ: [Plan v2.0](../04-tasks/nailhub-blog-noissue_tasks_261027_v2.0.0.md) (Stream S8, Tài nguyên dùng chung) · [Engineering cũ v1.0.0 (2026-10-16)](nailhub-blog-noissue_engineering_261016_v1.0.0.md), phần API trả phí của bản cũ **không còn hiệu lực** · [Business](../01-product/nailhub-blog-noissue_business_261015_v1.0.0.md) · Quy tắc cho agent: `nailblog/src/nailblog/AGENTS.md`.

## 1. Kiến trúc đích

- **Chữ và ảnh chỉ qua Agy** (worker subscription agy 1.2.11, user riêng). Không API trả phí, không fallback, không khóa API.
- **Bot** (`tg-nailblog`) tạo job vào queue và đọc receipt; **worker** (`startechai`) chạy CLI, giữ heartbeat và giữ khoá `/run/agentfleet/ollama.lock`. Bot **không** giữ `MODEL_LOCK` khi gọi agy (bot giữ thì deadlock).
- **4 slot/ngày** 06:00, 10:00, 15:00, 19:00 (giờ Việt Nam), mỗi slot 1 bài; `daily_publish_limit` = 4.
- **Luồng một bài:** chủ đề đã chốt → text job (`tools: []`) → ảnh job (`generate_image`) → duyệt 1 (ảnh + tiêu đề/tóm tắt) → `find_duplicates` (chỉ đọc) → `claim_create` → tạo Draft → readback → duyệt 2 (Draft) → lên lịch → đăng một lần.
- **Override 2 lớp:** bài `fixable` chỉ đi tiếp khi Owner override cả thẻ ảnh lẫn thẻ Draft, đúng snapshot. `facts`, `unexpected_han`, `medical_claim` và mọi `hold` không bao giờ override được (hằng `NEVER_OVERRIDABLE` ở `override_policy.py`).
- **Create UNKNOWN** chỉ đối soát bằng thao tác đọc, không POST lại; mã `create_requires_manual_reconciliation` → `held` + một DM.
- **Outbox DM:** mọi DM tới Owner qua `owner_outbox` (`random_id` cố định, backoff); mỗi lần gửi tối đa 30 giây, quá hạn tính là lỗi gửi.

## 2. Module (`nailblog/src/nailblog/`)

| Nhóm | File | Vai trò |
|---|---|---|
| Vào/ra | `__main__.py`, `telegram.py`, `commands.py`, `notices.py` | CLI, bot Owner-only, lệnh `/nailblog`, outbox |
| Lịch/chủ đề | `schedule.py`, `planner.py`, `daily.py`, `trends.py`, `sources.py` | slot, đề xuất/chốt chủ đề, trend RSS, nguồn |
| Viết bài | `text.py`, `agy_text.py`, `writer.py`, `facts.py`, `quality.py`, `override_policy.py` | adapter text, producer job text, dựng HTML, kiểm số liệu, cổng chất lượng |
| Ảnh | `images/subscription.py`, `images/subscription_*.py`, `images/agy_native.py`, `images/post.py`, `images/prompts.py` | broker queue/worker, receipt native, hậu xử lý ảnh |
| Đăng | `onepost.py`, `autoflow.py`, `approvals.py`, `admin.py`, `admin_state.py`, `publication.py`, `service.py` | run một bài, engine tự động, duyệt snapshot, trình duyệt staging, trạng thái đăng, vòng scheduler |
| Nền | `config.py`, `state.py`, `net.py`, `model_lock.py`, `subscription_lock.py`, `usage.py`, `_runtime.py`, `_errors.py` | config nghiêm ngặt, SQLite, DNS/HTTPS ghim, khoá, usage 0 USD |
| Đọc-only | `paid.py` | đọc sổ chi API cũ qua bản sao (`mode=ro`), mọi ghi bị từ chối |

## 3. Tài nguyên dùng chung ở runtime

| Tài nguyên | Hình dạng | Sở hữu |
|---|---|---|
| DB | `/var/lib/agentfleet/nailblog/nailblog.sqlite3`, không đổi schema bảng cũ (`autoflow_runs.run_usd` = 0, cột chết) | `state.py` |
| `autoflow_runs.stage` | giữ `STAGES`; lý do `text_quality_held`, `duplicate_on_site`, `create_unknown_reconciling`, `create_requires_manual_reconciliation` | `autoflow.py` |
| `onepost_runs.phase` | như cũ + `text_jobs` | `onepost.py` |
| `approval_requests`, `control.paused/armed_binding` | không đổi | `approvals.py`, `state.py` |
| `paid_runs`, `paid_intents` | chỉ đọc | `paid.py` |
| bảng `cf_*` | để nguyên, không đọc, không drop | — |
| Queue | `/var/lib/nailblog-subscription/queue/<job>/…` + `worker-ready.json`; ảnh `nailblog-subscription-v1`, text `nailblog-agy-text-v1` | `images/subscription.py` |
| Policy agent | thư mục agents trong Snap của `startechai`: `nailblog-text-only` (`tools: []`), `nailblog-image-only` (`generate_image`) | S1, `nailblog/deploy/` |
| Lệnh Telegram | `ok/reject/flow/status/pause/resume/pick/topics`; `/nailblog approve` vẫn bị chặn với bài của run | `commands.py` |
| Notice/outbox | `notices` + `random_id`, `message_ids`, `SEND_TIMEOUT_SECONDS` = 30 | `notices.py` |
| Khoá | `/run/agentfleet/ollama.lock` (chung triage/nailhub, do worker giữ), `browser_lock`, khoá profile admin | `model_lock.py`, `service.py` |

## 4. Quyết định trong S8

- `ai_transport.py` bị xoá; `net.py` giữ đúng `resolve` và `PinnedHTTPS` mà `admin.py` cần. `test_parity.py` chỉ so `model_lock.py` với nailhub.
- Config: gỡ field dataclass legacy; key cũ trong TOML vẫn bị từ chối bằng `removed_key:<path>`.
- `NEVER_OVERRIDABLE` nằm ở `override_policy.py` (không đặt ở `quality.py` vì `quality.py` import `writer.py`, sẽ vòng import).
- Seam test của `TextModel` đổi tên `_test_transport`; `__main__` production luôn truyền `None`.

## 5. File legacy còn giữ

| File | Lý do giữ |
|---|---|
| `staging_immediate.py`, `admin_images.py` | còn được `admin_state.py`, `onepost.py`, `service.py` import (kiểm waiver/ảnh test, cửa sổ publish ngay trên staging); gỡ cần đổi logic `admin*`/`approvals` ngoài phạm vi S8 |
| `images/pipeline.py` | `onepost.py`, `autoflow.py`, `subscription_pipeline.py` còn dùng `ImagePipeline`, `DispatchPaused`, `check_bypass_manifest` |
| `paid.py`, `usage.py` | đọc sổ API cũ để Owner đối soát intent `uncertain`; `commands.py`/`daily.py` còn dùng `report` |
| `receipt.py` | `telegram.py` gọi `ready` khi khởi động |
| `autoflow_runs.run_usd` | cột chết, DB staging hiện có khai báo `NOT NULL` |
| `tests/test_paid_readonly.py` | khoá hành vi chỉ-đọc của `paid.py` |
