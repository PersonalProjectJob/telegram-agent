---
type: doc
title: "NailBlog engineering"
owner: AlexChu
created: 2026-10-16
status: draft
---

# Engineering Document: NailBlog — Agent tạo bài blog NailHub

Version: v1.0.9 · 2026-10-16 · Owner đã duyệt làm plan Phase 1; đang code.

Căn cứ: [Master v1.0.2](../00-master/nailhub-blog-noissue_master_261014_v1.0.0.md) · [Business v1.0.0](../01-product/nailhub-blog-noissue_business_261015_v1.0.0.md) · khuôn service tách riêng ở [service split](worker-agent-fleet-1_service-split_261013_v1.0.0.md) · package tham chiếu `nailhub/`.

Không có phần Frontend: Agent không thêm giao diện; trang quản trị và trang blog là của nailhub.ai. Phần console hiển thị worker để giai đoạn sau (mục 8).

---

## 0. Quyết định Owner 2026-10-16

| # | Quyết định | Ảnh hưởng thiết kế |
| --- | --- | --- |
| O-1 | Ảnh: Gemini API, **ưu tiên model miễn phí** | Model ảnh chọn theo ListModels + hạn mức free tier; model trả phí chỉ khi bật trong config |
| O-2 | Văn bản: **model local trước**, API key sau | Provider văn bản mặc định `ollama` (`qwen3.5:9b`, `think=false`, qua `MODEL_LOCK`, nhường triage); `deepseek` là tùy chọn config. Rủi ro: CPU THINKPAD chậm với bài 800–1.200 từ → viết theo từng mục, giới hạn token mỗi lần gọi, có timeout và lùi lịch theo TD-5 |
| O-3 | Truy cập THINKPAD qua **SSH** | Probe, deploy và chạy thử thực hiện qua SSH theo `deploy/README.md` |
| O-4 | **Dùng chung tài khoản admin** với worker duyệt tin | Bản sao file `{email,password}` 0600 thuộc user `tgnailblog`; profile trình duyệt vẫn riêng |
| O-5 | **Tạo blog chạy độc lập với Duyệt Posting** | Xem bảng ranh giới ngay dưới |

**Ranh giới độc lập NailBlog ↔ NailHub (duyệt Posting):**

| Thành phần | NailBlog | Duyệt Posting | Chung? |
| --- | --- | --- | --- |
| Package / venv / systemd unit / user | `nailblog`, `tg-nailblog`, `tgnailblog` | `nailhub`, `tg-nailhub`, `tgnailhub` | Không — deploy, restart, rollback riêng |
| State SQLite, thư mục `/var/lib/agentfleet/…` | `nailblog/` | `nailhub/` | Không |
| Browser profile, lock tiến trình | riêng | riêng | Không |
| Telegram session + namespace lệnh | `nailblog.session`, `/nailblog` | `nailhub.session`, `/nailhub` | Không (cùng tài khoản Telegram, mỗi service bỏ qua namespace kia) |
| Code | không import `nailhub`/`tg_triage` (test AST) | — | Không; chỉ `ai_transport.py`, `model_lock.py` là bản sao có parity test để giữ cùng giao thức khóa |
| Tài khoản admin nailhub.ai | O-4 | O-4 | **Có** — rủi ro **thấp**: Owner xác nhận 2026-10-16 tài khoản đang dùng đồng thời ở nhiều nơi (gồm worker duyệt tin và trình duyệt cá nhân) mà không bị đăng xuất. `probe` chỉ ghi nhận nếu mất phiên, không chặn |
| Ollama local + `/run/agentfleet/ollama.lock` | background | background | **Có** — cùng CPU THINKPAD; bài blog dài có thể làm chậm lượt duyệt tin. Giảm thiểu: gọi theo từng mục ngắn, nhường lock giữa các mục, không viết trong khung quét dày của duyệt tin; chuyển `deepseek` nếu đo thấy ảnh hưởng |
| Lỗi/crash | không làm dừng duyệt tin | không làm dừng tạo blog | Không |

## 1. Kiến trúc tổng thể

NailBlog là **service độc lập thứ ba** trong fleet, cùng khuôn với `tg-nailhub`: package riêng, venv riêng, systemd unit riêng, user riêng, Telegram session riêng, state riêng. Không import hoặc sửa package `nailhub`.

```
nailblog/
├── pyproject.toml              # telethon, playwright, Pillow (mới — cần vào wheel cache offline)
├── src/nailblog/
│   ├── __main__.py             # run | login-telegram | login | probe | preview | status | plan-week
│   ├── config.py               # NailBlogConfig (TOML, fail-closed)
│   ├── state.py                # SQLite WAL: control, topics, posts, images, runs, usage, notices
│   ├── service.py              # vòng lặp: lập lịch tạo bài + vòng đăng theo slot
│   ├── schedule.py             # 3 chế độ: fixed | random | adaptive; tính slot, hạn T-2h, cửa sổ trễ
│   ├── sources.py              # đọc RSS allowlist + GET /api/v1/blogs (bài cũ)
│   ├── planner.py              # lên chủ đề (DeepSeek), lọc trùng 90 ngày
│   ├── writer.py               # viết bài → JSON có cấu trúc → render HTML (code dựng HTML, không phải model)
│   ├── facts.py                # dò số/ngày/phí, đối chiếu với đoạn nguồn đã trích
│   ├── quality.py              # cổng chất lượng BB-01…12
│   ├── images/
│   │   ├── providers.py        # GeminiImage, OpenAIImage (chung interface)
│   │   ├── review.py           # chấm ảnh bằng model đọc ảnh
│   │   ├── post.py             # Pillow: crop 16:9, resize, WebP, dHash
│   │   └── templates/          # HTML template thumbnail + infographic (chụp bằng Playwright)
│   ├── admin.py                # Playwright: form Create/Edit Blog, upload ảnh, đổi trạng thái, readback
│   ├── telegram.py, commands.py, notices.py   # namespace /nailblog
│   ├── usage.py                # ghi chi phí + hạn mức ngày (mới so với fleet)
│   ├── ai_transport.py, model_lock.py         # bản sao giống hệt nailhub, có parity test
│   ├── receipt.py              # agentfleet-ready.json
│   └── AGENTS.md
└── tests/
```

### Luồng dữ liệu

```mermaid
flowchart LR
    RSS[RSS allowlist + blog cũ] --> P[planner]
    P -->|DM| O((Owner))
    O -->|/nailblog topics ok| T[(topics)]
    T --> W[writer + facts]
    W --> I[images: tạo → chấm → hậu kỳ]
    I --> Q[quality gate]
    Q --> A[admin: lưu Draft + readback]
    A -->|DM xem trước| O
    O -->|/nailblog approve| S[(posts: scheduled)]
    S -->|06:00 / 19:00| PUB[admin: Published + readback]
    PUB -->|DM kết quả| O
```

---

## 2. Hợp đồng với nailhub.ai

Đọc từ JS công khai của site ngày 2026-10-16 (`BlogManagement-*.js`, `index-*.js`). **Chưa xác minh bằng phiên đăng nhập** — mục 9 Phase 0.

| Việc | Endpoint | Ghi chú |
| --- | --- | --- |
| Danh sách admin | `GET /api/v1/admin/blogs` | Lọc status, category |
| Chi tiết | `GET /api/v1/admin/blogs/{id}` | Dùng để readback |
| Tạo | `POST /api/v1/admin/blogs` | Form mặc định `status=Published`, `isPublic=true` → **Agent phải đổi sang Draft trước khi lưu** |
| Sửa | `PUT /api/v1/admin/blogs/{id}` | |
| Đổi trạng thái | `PATCH /api/v1/admin/blogs/{id}/status` | Draft · PendingApproval · Published · Archived |
| Danh mục | `GET /api/v1/admin/blog-categories` | 4 slug đang dùng: nail-job, nail-salon, hot-trend, did-you-know |
| Upload ảnh | `POST /api/v1/Images/quick-upload` | Trả `imageUrl`, `imageRelativePath`, `thumbnailUrl`, `thumbnailRelativePath` |
| Readback công khai | `GET /api/v1/blogs?pageSize=…` | Không cần đăng nhập |

**Payload bài** (form Create/Edit Blog):

| Trường | Giới hạn | Agent điền |
| --- | --- | --- |
| `categoryId` | bắt buộc | Theo danh mục chủ đề |
| `status` | enum | `Draft` khi tạo; `Published` lúc đăng |
| `publishedAt` | datetime-local | Giờ slot (xem D-04) |
| `isPublic`, `isFeatured` | bool | Theo D-02; `isFeatured=false` |
| `coverImage` | **bắt buộc** | Thumbnail 16:9 đã upload |
| `images[]` | **tối đa 10** | 3–5 ảnh thân bài (xem D-12) |
| `hashtags[]` | mỗi tag ≤ 50 | 3–5 |
| `externalSourceUrl` | ≤ 500 | Để trống |
| `translations[VI]` | title ≤ 200 · summary ≤ 500 · metaTitle ≤ 70 · metaDescription ≤ 160 · content (Quill HTML) | Bắt buộc VI title + content; EN để trống (D-05) |

**Rủi ro đã thấy:** ảnh chèn vào Quill qua nút toolbar chỉ mang `src`, không có `alt`. Editor của form tin tuyển dụng từng làm phẳng rich text. Phase 0 phải kiểm alt có sống qua lưu và trang công khai có hiển thị `images[]` hay không.

---

## 3. Quyết định kỹ thuật

### TD-1 — Ghi qua UI quản trị, không gọi API ghi trực tiếp
- **Bối cảnh:** `nailhub` chỉ ghi qua UI với route guard, không đoán API ghi, không tiêm token.
- **Quyết định:** NailBlog giữ cùng nguyên tắc: Playwright điền form Create/Edit Blog, dùng `set_input_files` cho ô upload, bấm Save; chỉ cho đúng `POST/PUT/PATCH` của blog và `quick-upload` đi qua guard; mọi thứ khác bị chặn. Preview chặn toàn bộ non-GET.
- **Hệ quả:** chậm hơn gọi API, nhưng dùng đúng validation của site và không cần giữ bearer token.
- **Phương án khác:** gọi API trực tiếp bằng token của phiên — nhanh, nhưng trái quy ước fleet.

### TD-2 — Model viết JSON, code dựng HTML
DeepSeek trả JSON theo schema (title_seo, title, summary, sections[{h2, paragraphs[], image_slot?}], faq[], cta, hashtags[], slug, claims[{text, source_id, quote}]). `writer.render()` dựng HTML với `<h2>`, `<p>`, `<figure><img alt><figcaption>`. Model không bao giờ chèn HTML tự do.

### TD-3 — Nhà cung cấp ảnh qua adapter
`ImageProvider.generate(prompt, aspect, n) -> list[bytes]` và `ImageReviewer.score(image, rubric) -> Score`.
- **Giai đoạn code:** Gemini (Owner cung cấp key). Ứng viên đầu: model Gemini Flash Image cho ảnh thân bài, Gemini Pro Image cho thumbnail. **Model ID và tỷ lệ khung hỗ trợ phải đọc từ ListModels khi có key**, không hard-code theo bài blog bên ngoài.
- **Kế hoạch production:** OpenAI GPT Image (Business BB-10). Đổi provider bằng config, không sửa code.
- Endpoint pin cứng: `generativelanguage.googleapis.com`, `api.openai.com`.

### TD-4 — Chữ tiếng Việt do template dựng
Thumbnail = ảnh AI làm nền + lớp chữ dựng bằng HTML template rồi Playwright chụp 1600×900. Infographic dự phòng dùng cùng cơ chế.

### TD-5 — Lập lịch tách khỏi đăng
`schedule.py` chỉ quyết định **lúc tạo ảnh**; vòng đăng chạy độc lập mỗi phút, chỉ đăng bài `scheduled` có `slot_at ≤ now ≤ slot_at + 15 phút`.

| Chế độ | Thời điểm chạy job tạo ảnh cho bài có slot S |
| --- | --- |
| `fixed` | `fixed_at` gần nhất trước `S − 3h` |
| `random` | Đều ngẫu nhiên trong `[S − 24h, S − 3h]`, seed ghi vào `runs` để tái lập |
| `adaptive` | Sớm nhất khi máy rảnh trong khung; lỗi/timeout API → lùi `2^n × 5 phút`, tối đa đến `S − 3h` |

Mọi chế độ: quá `S − 3h` mà chưa xong → ảnh dự phòng; quá `S − 2h` chưa duyệt → D-06.

### TD-6 — Hạn mức chi phí
Fleet hiện chỉ ghi chi phí, chưa có trần. `usage.py` ghi mỗi lần gọi (provider, model, token hoặc số ảnh, USD ước tính theo bảng giá trong config) và từ chối gọi mới khi tổng ngày (Asia/Ho_Chi_Minh) ≥ `budget.daily_usd`. Xuất `agentfleet-usage.json` cùng định dạng listener để console đọc sau này.

### TD-7 — Một lần đăng
Trạng thái `before_publish` ghi bền trước khi bấm. Khởi động lại thấy `before_publish` → chỉ readback, không bấm lại (giống `before_approve` của nailhub).

### TD-8 — Chốt chủ đề hằng ngày theo trend hôm nay (Owner chốt 2026-10-16: giờ 10:00/13:00/20:00, chỉ geo US, TikTok nhập tay; làm ở bước tiếp theo)
- **10:00 GMT+7 mỗi ngày**: DM Owner bảng trend hôm nay + **4 chủ đề ứng viên cho 2 slot ngày mai** (2/slot, đúng nhịp danh mục 06:00/19:00), mỗi chủ đề có điểm trend và nguồn.
- Owner trả lời `/nailblog pick <slot> <số>` trước **13:00**; không trả lời → lấy bài trong backlog tuần (evergreen) đã duyệt; backlog trống → chọn ứng viên điểm cao nhất nhưng vẫn phải qua duyệt bài T-2h.
- Sau khi chọn: viết + ảnh theo chế độ lập lịch (TD-5) → DM xem trước trước 20:00 → hạn duyệt: bài 06:00 trước 04:00 (khuyến nghị duyệt tối hôm trước), bài 19:00 trước 17:00.
- Kế hoạch tuần (`plan-week`) chuyển thành **backlog dự phòng**, không còn là nguồn chính.
- Hiện thực (bước B2): bảng `daily_runs` ghi dấu từng mốc theo ngày (idempotent, lưu nội dung DM để gửi lại); bảng `trend_snapshots`, `tiktok_tags`. Sau mốc chọn chủ đề có bước `write` riêng (viết bài preview vào `previews/<YYYY-MM-DD>/<HHMM>/`, thử lại sau 10′×n, bỏ sau 4 lần). Bài còn lỗi `fixable` sau 1 vòng sửa vẫn vào `review` (DM ghi rõ verdict); chỉ lỗi `hold` mới vào `held`. Danh sách bài đã đăng lấy bằng `GET /api/v1/blogs?pageNumber=N&pageSize=50`, dừng khi gặp trang có bài cũ hơn 90 ngày hoặc hết trang (trần 20).
- **Điểm trend**: hot hôm nay 20% (Google Trending RSS geo=US, lọc nail/beauty) · đà 3 ngày 40% · đà 7 ngày 30% (Google Trends API khi có quyền alpha; tạm thời đếm tần suất từ khóa trong Trending RSS + RSS báo làm đẹp lưu hằng ngày) · TikTok 10% (Owner nhập tay `/nailblog trend add #tag <lượt xem>` từ Creative Center). Không cào TikTok/Google Trends trái điều khoản.

---

## 4. Lưu trữ (SQLite, WAL, thư mục 0700, file 0600)

| Bảng | Cột chính | Mục đích |
| --- | --- | --- |
| `control` | paused, image_mode, last_result | Một dòng |
| `topics` | id, week, title, category, slot_at, score, sources_json, state | Backlog |
| `posts` | id, topic_id, slot_at, state, draft_json, fingerprint, admin_id, reason, details | Vòng đời bài |
| `images` | id, post_id, position, prompt, round, path, sha256, dhash, scores_json, chosen, uploaded_url | Ứng viên và ảnh chọn |
| `runs` | id, post_id, mode, seed, planned_at, started_at, ended_at, n_generated, n_pass_first, rounds, n_fallback, usd, met_deadline | **Số liệu cho báo cáo** |
| `usage` | at, provider, model, units, usd | Chi phí; giữ 90 ngày |
| `notices` | key, sent_at, attempts | Chống DM trùng |

Trạng thái `posts.state` (đồ thị đầy đủ, chốt 2026-10-16 sau verify bước A):

| Từ | Đến | Lý do |
| --- | --- | --- |
| writing | held, review | Thiếu căn cứ / qua cổng chất lượng |
| held | writing | Owner bổ sung căn cứ |
| review | writing, scheduled, held | Owner sửa / duyệt / giữ |
| scheduled | before_publish, held | Tới slot / Owner giữ |
| before_publish | published, uncertain | Readback xác nhận / không xác nhận được (TD-7) |
| uncertain | published, held | **Chỉ khi readback mới chứng minh đã công khai** / cần người xử lý |
| published | — | Kết thúc |

---

## 5. Logic chính

### 5.1 Tạo một bài
1. `writer`: DeepSeek (`deepseek-flash`) viết JSON → validate schema → `facts.check()`: mọi số/ngày/phí trong bài phải khớp một `quote` nằm nguyên văn trong đoạn nguồn; thiếu → `held`.
2. `images`: sinh prompt (không yêu cầu chữ) → `generate(n=2)` → `review.score()` theo 5 nhóm → chọn điểm cao nhất đạt hết; trượt → vòng 2, 3 → dự phòng.
3. `post.py`: crop/resize, WebP (≤ 200 KB cover, ≤ 150 KB thân bài, hạ quality dần), tên file `slug-vi-tri.webp`, dHash so với ảnh 90 ngày (khoảng cách Hamming ≤ 6 = trùng).
4. `quality.gate()` → `admin.save_draft()` → readback `GET /admin/blogs/{id}` khớp từng trường → DM xem trước.

### 5.2 Đăng
T−10′ kiểm fingerprint bài = bản đã duyệt, các `uploaded_url` trả 200 → tại slot ghi `before_publish` → đổi trạng thái Published qua UI → readback admin + public API: status, `publishedAt` lệch ≤ 15′, số ảnh, `/blog/{slug}` mở được → `published`. Không khẳng định được → `uncertain`, DM Owner.

### 5.3 Tình huống biên
- Nguồn RSS lỗi: dùng tư liệu tuần trước, ghi rõ trong DM chủ đề.
- Model văn bản lỗi (Ollama mặc định theo O-2, hoặc DeepSeek nếu bật): lùi lịch theo TD-5; không tự đổi provider giữa chừng. Gemini lỗi: tạo lại theo TD-5, quá hạn thì ảnh dự phòng.
- Phiên admin hết hạn: `login()` một lần như nailhub; lỗi/MFA → dừng, DM.
- Slug trùng (`BLOG_SLUG_ALREADY_EXISTS`): thêm hậu tố ngày, lưu lại một lần.
- Hai bài cùng slot: chặn ở `quality`.

---

## 6. Bảo mật

- Khóa: `/etc/nailblog/private/model-key.json` (DeepSeek) và `/etc/nailblog/private/image-key.json` (Gemini/OpenAI), định dạng scoped JSON của `ai_transport` (`{"scope":"<provider>|<base_url>","key":"…"}`), không group-writable, không world-readable. Không ghi khóa vào TOML, log, DM.
- Đăng nhập admin: file `{email,password}` 0600 như nailhub (Owner tạo).
- Telegram: session riêng `/var/lib/agentfleet/nailblog/nailblog.session`, Owner tự `login-telegram`; chỉ nhận `/nailblog` từ DM đúng ID số đã xác minh, bỏ qua mọi tin khác (mọi service đều nhận toàn bộ update).
- Chỉ gửi nội dung công khai tới API ngoài. Log không chứa nội dung nguồn, prompt đầy đủ hay khóa.
- Model lock: gọi qua `MODEL_LOCK.enter(background=True)` như nailhub để nhường triage.
- Origin pin: nailhub.ai, api.nailhub.ai, api.deepseek.com, generativelanguage.googleapis.com, api.openai.com, danh sách RSS.

## 7. Hiệu năng và tài nguyên

- Mỗi bài: ~1 phút viết, ~1–3 phút tạo/chấm ảnh, ~1 phút thao tác admin. 2 bài/ngày không gây tải đáng kể.
- Không chạy song song Browser profile với chính nó; browser NailBlog dùng profile riêng nên không tranh chấp với nailhub.
- Ảnh tạm lưu `/var/lib/agentfleet/nailblog/images/`, dọn ảnh không chọn sau 30 ngày.

## 8. Thay đổi ngoài package (giai đoạn deploy, làm sau)

| File | Thay đổi |
| --- | --- |
| `deploy/build_wheel.py` | Thêm `nailblog` vào `SERVICES` và fixture tests |
| `deploy/agentfleet_deploy.py` | Thêm đường dẫn code/config/state/unit; chờ log `NailBlog ready owner_verified=1` |
| `systemd/tg-nailblog.service` | User `tgnailblog`, group `agentfleet`, `/opt/agentfleet/nailblog`, `ReadWritePaths=/var/lib/agentfleet/nailblog /run/agentfleet` |
| `config/nailblog.example.toml` | Mẫu cấu hình |
| `console-ui/backend.py` | Thêm service vào `MAPS` (tùy chọn) |
| Wheel cache THINKPAD | Thêm Pillow |

---

## 9. Kế hoạch thực hiện

| Phase | Nội dung | Ghi chú |
| --- | --- | --- |
| **0** | Xác minh hợp đồng **bằng chính worker** như nailhub: lệnh `nailblog probe` (chỉ GET, đăng nhập bằng file credential của service, profile riêng) đọc danh mục + 1 bài qua `GET /admin/blogs/{id}`; sau đó một lượt `live` có kiểm soát tạo **1 bài Draft thử** (không public) có ảnh inline có alt + `images[]` để kiểm alt, cách trang công khai hiển thị, `publishedAt` tương lai. Kết quả ghi `contract.verification_ref` | Không dùng phiên trình duyệt cá nhân; lượt ghi thử cần Owner xác nhận |
| **1** | Package `nailblog` chế độ `preview` (không ghi website): planner, writer, facts, images (Gemini), quality, state, scheduler, Telegram | Unit test với provider giả, không gọi mạng |
| **2** | `admin.py` + chế độ `live`; chạy có kiểm soát 1 slot/ngày; review độc lập | Qua /dispatch, GATE 0b |
| **3** | Deploy THINKPAD, bật 2 slot, thử nghiệm 3 chế độ | |

### Kiểm thử
`unittest` như nailhub (`python -m unittest discover -s nailblog/tests -t nailblog`). Bắt buộc có:
- Schedule: 3 chế độ, ranh giới T−24h/T−3h/T−2h, cửa sổ trễ 15′, đổi giờ Mỹ không ảnh hưởng.
- Facts: số khớp/không khớp nguồn, trích dẫn ghép câu bị từ chối.
- Images: vòng tạo lại, dự phòng, hạn mức ngày, WebP đúng ngưỡng, dHash trùng.
- Admin: preview không gửi non-GET; `before_publish` sau crash chỉ readback; slug trùng.
- Commands: sai người gửi, nhóm, tin chuyển tiếp bị bỏ qua; namespace khác bị bỏ qua.
- Parity: `ai_transport.py`, `model_lock.py` giống hệt bản của nailhub.

---

## 10. Quyết định mở (thêm vào Master)

| ID | Câu hỏi | Đề xuất |
| --- | --- | --- |
| D-12 | Ảnh thân bài đặt inline trong `content` hay trong `images[]` | Kiểm ở Phase 0; ưu tiên inline nếu alt được giữ |
| D-13 | Tên service/user | `tg-nailblog` / `tgnailblog` |
| D-14 | Model ảnh Gemini cụ thể | Chọn sau ListModels + thử 10 prompt |

## Related Documents
- [Master](../00-master/nailhub-blog-noissue_master_261014_v1.0.0.md) · [Business](../01-product/nailhub-blog-noissue_business_261015_v1.0.0.md)
- QA Doc, Tasks Doc: chưa tạo.

## Changelog
| Version | Date | Time | Author | Change |
|---|---|---|---|---|
| v1.0.9 | 2026-10-16 | — | AlexChu (Claude executor) | TD-8: ghi phần hiện thực bước B2 (daily_runs, bước write, phân trang API) |
| v1.0.8 | 2026-10-16 | — | AlexChu (Claude executor) | Sửa 5.3 cho khớp O-2; cập nhật trạng thái doc |
| v1.0.7 | 2026-10-16 | — | AlexChu (Claude executor) | Owner chốt TD-8: 10:00/13:00/20:00, chỉ US, TikTok nhập tay; Owner nộp đơn Google Trends API alpha |
| v1.0.6 | 2026-10-16 | — | AlexChu (Claude executor) | TD-8: DM chốt chủ đề hằng ngày theo trend; plan-week thành backlog dự phòng |
| v1.0.5 | 2026-10-16 | — | AlexChu (Claude executor) | Hạ rủi ro dùng chung tài khoản admin xuống thấp theo xác nhận Owner |
| v1.0.4 | 2026-10-16 | — | AlexChu (Claude executor) | O-5: chạy độc lập với Duyệt Posting, bảng ranh giới + 2 tài nguyên chung |
| v1.0.3 | 2026-10-16 | — | AlexChu (Claude executor) | Đồ thị trạng thái đầy đủ (thêm hold từ review/scheduled, cạnh phục hồi) sau verify bước A |
| v1.0.2 | 2026-10-16 | — | AlexChu (Claude executor) | Quyết định O-1…O-4: Gemini free, model local trước, SSH, chung tài khoản admin |
| v1.0.1 | 2026-10-16 | — | AlexChu (Claude executor) | Owner: chạy cùng mô hình triage/duyệt tin — Phase 0 xác minh hợp đồng bằng `nailblog probe` của worker |
| v1.0.0 | 2026-10-16 | — | AlexChu (Claude executor) | Bản đầu: kiến trúc package riêng, hợp đồng admin blog đọc từ JS công khai, 7 quyết định kỹ thuật, kế hoạch Phase 0–3 |
