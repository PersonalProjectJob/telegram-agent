---
type: doc
title: "Master Document: AgentFleet Settings Console"
status: current
area: platform
created: 2026-10-14
owner: AlexChu
plan_status: active
approved_on: 2026-10-14
approved_by: "Owner (chat)"
---

# Master Document: AgentFleet Settings Console

## Metadata
- **Feature ID**: 1 — kế thừa Worker Agent Fleet; chưa có GitHub issue riêng.
- **Created Date / Last Updated**: 2026-10-14 09:33 (Asia/Saigon).
- **Current Version**: v1.0.2.
- **Author**: AlexChu — executor nghiên cứu: Codex.
- **Status**: Approved — Owner duyệt UI trong chat ngày 14/10 và yêu cầu nối backend test/lưu/áp dụng riêng từng service. Giữ tone technical/Hallmark và lệnh terminal trong repo.
- **Approved By**: Owner, chat ngày 2026-10-14.
- **Task**: `T-261014-unified-settings-ui-for-telegram-triage-`.
- **Implementation worktree**: `C:/Users/AD/.codex/worktrees/agentfleet-settings-ui/AgentBuilder`, detached baseline split `b0f6b0c`; không ghi đè runtime cũ trong main checkout đang có thay đổi khác.
- **Baseline nghiên cứu**: `origin/feat/1_service-split`, commit `b0f6b0cebe558413972e4ee2edbceeed6de45d67` (đã fetch ngày 14/10).
- **Repo profile**: IN-REPO DOCS. Có bộ Docs 5 vai; không có cấu hình vault của AgentBuilder và không thấy project AgentBuilder trong vault đã dò.

## 1. Feature Overview

### Problem Statement
Owner phải vào SSH, sửa TOML hoặc biến môi trường và khởi động lại service để thay đổi cách Telegram Triage và NailHub vận hành. Không có màn hình chung để xem setting đang chạy, kiểm tra giá trị, xem thay đổi và biết lần áp dụng có thành công hay chưa.

### Goals & Objectives
- Một web quản trị tiếng Việt cho cả Telegram Triage và NailHub Worker; dùng được trên desktop và điện thoại.
- Chỉnh các setting được runtime hỗ trợ, lưu và áp dụng cho đúng service từ trình duyệt, không cần SSH trong các thao tác thường ngày.
- Phân biệt rõ **bản nháp**, **đã lưu**, **đang áp dụng**, **đang chạy**, **thất bại**. Không báo thành công chỉ vì ghi được file.
- Có lịch sử thay đổi, khôi phục phiên bản cấu hình và thông báo lỗi đủ để Owner xử lý.

### Hiện trạng đã đối chiếu
| Điều đã xác định | Nguồn và hệ quả |
|---|---|
| Hai service đã tách | Task D-024 ghi nhận cutover ngày 13/10; `systemd/tg-triage.service` và `systemd/tg-nailhub.service` trên baseline. Không quay lại kiến trúc chạy chung. Chưa đọc trạng thái máy thật trong lượt này. |
| Hai cấu hình riêng | `/etc/tg-triage/listener.toml` và `/etc/nailhub/nailhub.toml`; mỗi service có user, venv, state, session và env file riêng. |
| Cấu hình được nạp lúc khởi động | CLI/loaders trên baseline; chưa thấy giao thức reload cấu hình. MVP dùng khởi động lại có kiểm soát, không hứa áp dụng nóng. |
| Env có thể ghi đè TOML | `TG_TRIAGE_MODE` có ưu tiên hơn `mode`. UI phải cho biết giá trị hiệu lực và nguồn ghi đè, không cho lưu rồi giả vờ mode đã đổi. |
| NailHub có policy cố định | Loader chỉ nhận `qwen3.5:9b`, `think=false`, Owner `SotaThao` với tối đa một numeric ID; các category/trạng thái cũng bị ràng buộc. UI không được đưa lựa chọn runtime chắc chắn từ chối. |
| Chưa có nền web | Package hiện tại dùng Python, Telethon và Playwright cho NailHub. Cần một service quản trị riêng; không cài chồng vào hai venv đang chạy. |
| Khoá model liên process đã có | `/run/agentfleet/ollama.lock`; triage được ưu tiên. UI không có nút thử model bằng một đường bỏ qua khoá. |

## 2. Scope

### In-Scope — đề xuất để duyệt

**Điều hướng chung:** Tổng quan · Telegram Triage · NailHub Worker · Lịch sử thay đổi. Thanh trên hiển thị máy đang quản lý, kết nối và thời điểm cập nhật; mất kết nối phải báo rõ dữ liệu cũ.

**Tổng quan:** hai thẻ service, trạng thái systemd riêng với trạng thái sẵn sàng của ứng dụng, mode hiệu lực, phiên bản cấu hình, lỗi gần nhất đã lọc. Nút mở setting và khởi động lại từng service. Trạng thái pause của NailHub phải lấy từ worker; không suy ra từ `active` của systemd.

**Telegram Triage:** các tab Vận hành / Telegram / AI / Gom tin / Tệp đính kèm / Nâng cao. **NailHub:** Vận hành / AI / Quy tắc xử lý / Kết nối / Nâng cao. Mỗi tab có mô tả ngắn, đơn vị đo, lỗi ngay dưới field và nhãn cần khởi động lại.

| Service / nhóm | Setting đưa vào form | Ràng buộc từ loader, không phải giá trị production |
|---|---|---|
| Triage — Vận hành | `mode`, `github.max_issues_per_hour` | dry-run/live; số issue nguyên >= 1; mode có thể bị env ghi đè |
| Triage — Telegram | `listen_all_groups`, `chat_ids`, proxy host/port, `ignored_command_prefixes` | chat ID âm; nghe tất cả thì danh sách rỗng; port 1–65535; prefix không trùng hoặc chồng lệnh của listener |
| Triage — AI | provider, model, thinking, timeout, confidence, Ollama URL | provider chỉ ollama; timeout hữu hạn >= 0.001 giây; confidence 0–1; URL HTTP(S) không có credentials |
| Triage — Gom tin | `window_minutes`, `max_messages_per_case`, `max_messages_per_window` | cửa sổ nguyên >= 0; hai giới hạn nguyên >= 1; giải thích 0 = không đợi gom tin |
| Triage — Tệp | `max_bytes`, `max_per_case`, `max_documents_per_case`, `max_per_hour` | byte nguyên >= 1; các giới hạn còn lại nguyên >= 0; giải thích ảnh và tài liệu có quota từng case riêng |
| NailHub — Vận hành | mode, `interval_seconds`, `max_batch`, `max_pages` | preview/live; chu kỳ nguyên >= 10 giây; batch 1–20; số trang 1–500 |
| NailHub — AI | timeout, `num_predict`, Ollama URL | timeout nguyên 1–1800 giây; token 256–8192; Ollama chỉ HTTP loopback theo loader |
| NailHub — Quy tắc | `range_policy`, `salary_range`, `salary_places` | hold/upper_floor_100; boolean; số chữ số thập phân 0–2; cần xem lại trước khi đổi vì ảnh hưởng dữ liệu bài |
| Nâng cao | proxy/endpoint được phép, selectors, fields, options của NailHub | form có kiểu hoặc editor JSON theo từng section, giữ nguyên section khác; kiểm tra cú pháp không đồng nghĩa đã xác minh selector trên site thật |
| Thông tin bảo vệ | Owner binding, contract, DB/profile/session paths | hiển thị lý do cố định; trạng thái VERIFIED do quy trình kiểm độc lập xác nhận, không có checkbox tự chứng nhận |

### Bổ sung được Owner chốt — AI đa provider
- Mỗi service có cấu hình độc lập: Ollama local, Anthropic/Claude, OpenAI, DeepSeek và OpenAI-compatible. Provider, model, API key/reference, Base URL, timeout và giới hạn output không dùng chung âm thầm.
- Endpoint mặc định theo provider; OpenAI-compatible cho nhập HTTPS Base URL và model thủ công. Ollama giữ đường local. Adapter runtime cần kiểm SSRF/DNS/redirect và không gửi key tới host ngoài endpoint đã chọn.
- API key chỉ thay mới, không trả lại giá trị; giữ key cũ khi bỏ trống, đổi endpoint/provider không tự chuyển tiếp key cũ. Test draft không lưu hoặc áp dụng setting.
- Nút Test kết nối kiểm authentication/model và một mẫu JSON vô hại có giới hạn output. UI thể hiện testing/pass/fail, loại lỗi (auth, model, rate limit, timeout, schema), thời gian đo thực tế. Đổi đầu vào làm mất hiệu lực kết quả test. Test có thể tính phí theo provider; chỉ gọi khi Owner bấm.
- Model dùng text vẫn có thể không hỗ trợ ảnh hoặc JSON schema: thể hiện capability theo model/adapter, không mặc định mọi API-compatible có đầy đủ tính năng OpenAI.
- Với cloud, chỉ gửi nội dung phục vụ tác vụ sau khi xác nhận data policy; triage tiếp tục yêu cầu redaction e-mail/số điện thoại. NailHub cần giá trị gốc để đối chiếu bằng code; phải chốt xử lý PII riêng trong Engineering Doc, không hứa che toàn bộ rồi vẫn giữ nguyên khả năng xác minh.
- Owner đã yêu cầu bỏ giới hạn provider/model cố định NailHub trong phạm vi này. Triển khai phải bổ sung adapter và kiểm thử extraction/semantic invariants; không bỏ Owner binding, VERIFIED, evidence hoặc quy tắc dữ liệu.
- Owner yêu cầu **vibe coding UI, không Pencil**, sử dụng `/hallmark`; tone đã chọn **Technical, gọn như công cụ quản trị**. Bản HTML tương tác là thiết kế pre-implementation cho Visual Gate, chưa chứng minh backend API hoạt động.
- Owner chốt UI **đi kèm repo/source và mở bằng lệnh terminal**, như công cụ local. Launcher bản xem trước: `python console-ui/run.py`; bind localhost, tự mở browser, Ctrl+C dừng. Không yêu cầu deploy một website riêng. Production CLI sẽ chạy UI/API trên host có service hoặc dùng một connector tới host được xác thực; không suy rằng mở localhost trên Windows tự quản lý được THINKPAD.

`telegram.min_text_length` của triage đã không còn dùng để bỏ tin ngắn: hiển thị dưới thông tin tương thích, không làm thành slider có vẻ đang ảnh hưởng xử lý. Các field không nhận diện phải được giữ nguyên khi lưu, không tự xoá.

**Secrets và kết nối:** UI chỉ đọc trạng thái đã/chưa cấu hình của token, API hash, proxy secret và credentials; không trả giá trị bí mật về browser. MVP có luồng **thay thế secret bằng giá trị mới** theo allowlist từng service, chỉ ghi, không prefill, không ghi vào lịch sử hoặc diff. Để trống giữ nguyên; xoá phải là thao tác riêng có xác nhận. Không cho đổi tuỳ ý mọi biến môi trường. `GH_REPO` có form owner/repo; thông tin đăng nhập Telegram/OTP và phiên browser cần luồng onboarding riêng, chưa thuộc MVP.

**Luồng lưu / áp dụng:**
1. Owner sửa một service → có thanh thay đổi chưa lưu; rời trang có cảnh báo.
2. **Kiểm tra & xem thay đổi** → backend validate bằng cùng schema/runtime, hiển thị giá trị cũ → mới; secrets chỉ ghi tên field đã thay thế.
3. **Lưu cấu hình** tạo phiên bản mới, hiển thị “Chưa áp dụng”; hoặc **Lưu & áp dụng** tiếp tục khởi động lại service được chọn.
4. Backend chặn hai thao tác đồng thời và kiểm phiên bản; tab cũ phải tải lại hoặc hợp nhất, không ghi đè âm thầm.
5. Áp dụng có tiến trình, để worker kết thúc theo chính sách shutdown sẵn có. Kiểm systemd, readiness và phiên bản cấu hình worker thực sự đã nạp; nếu chưa có tín hiệu worker cần bổ sung hợp đồng readiness.
6. Nếu fail: ghi nhận trạng thái cấu hình trên đĩa và phiên bản đang chạy; cho khôi phục cấu hình trước. Khôi phục không đảo ngược GitHub issue hoặc bài NailHub đã được sửa/duyệt. Không tự restore DB.

### Phác thảo bố cục để chốt phạm vi
Đây là sơ đồ nội dung lịch sử. Owner đã yêu cầu bỏ Pencil; bản HTML/Hallmark thực tế đã được duyệt trước backend implementation.

```text
AgentFleet                         THINKPAD · Cập nhật ...
┌──────────────────┬──────────────────────────────────────────┐
│ Tổng quan        │ Cài đặt Telegram Triage                  │
│ Telegram Triage  │ Trạng thái ...  Mode ...  Phiên bản ...  │
│ NailHub Worker   │                                          │
│ Lịch sử thay đổi │ Vận hành | Telegram | AI | Gom tin | Tệp │
│                  │                                          │
│                  │ AI xử lý tin nhắn                        │
│                  │ Model              [ ...             v ] │
│                  │ Suy luận            [ Bật / Tắt        ] │
│                  │ Thời gian chờ       [ ... ] giây         │
│                  │ Ngưỡng tin cậy      [ ... ]              │
│                  │                                          │
│                  │ Thay đổi chưa lưu                        │
│                  │ [Huỷ thay đổi] [Kiểm tra & xem thay đổi] │
└──────────────────┴──────────────────────────────────────────┘
```

### Out-of-Scope
- Thay quyền Owner hoặc contract kinh doanh của NailHub; provider/model được mở rộng theo yêu cầu mới ở trên.
- Chạy lệnh shell tuỳ ý, deploy code, sửa unit systemd tuỳ ý, reboot máy hoặc quản lý nhiều máy.
- Duyệt/sửa bài NailHub và quản lý GitHub issue ngay trong console.
- Đăng nhập Telegram/OTP hoặc browser NailHub qua web ở MVP; UI phải báo cần kết nối lại và hướng dẫn rõ nếu session hết hạn.

## 3. Key Personas
- **Primary — Owner:** thay setting bằng browser, biết service nào bị ảnh hưởng và có đường quay lại khi áp dụng lỗi.
- **Secondary — người bảo trì:** xem revision, readiness, lịch sử redacted để tìm nguyên nhân mà không cần truy cập nội dung tin nhắn.
- Data/state owner tiếp tục là **AlexChu**; tài khoản đăng nhập web là danh tính thao tác, không đổi danh tính harness.

## 4. Success Metrics & Acceptance Criteria
- Owner đổi được setting thông thường của **cả hai** service từ browser mà không cần mở SSH sau khi console đã được cài đặt.
- Setting không hợp lệ bị từ chối ở backend; file cấu hình và service đang chạy giữ nguyên khi validation fail.
- Lưu/chạy lại một service không sửa file, venv, state hoặc restart service kia.
- Hai tab cùng sửa không mất thay đổi âm thầm; có conflict theo revision.
- UI phân biệt đã lưu với đang chạy; restart fail, readiness timeout và mất kết nối đều có trạng thái riêng.
- Secrets không xuất hiện trong GET API, diff, localStorage, log hoặc lịch sử.
- Live NailHub chỉ được áp dụng khi contract/evidence/Owner hợp lệ; UI không tự đặt VERIFIED.
- E2E chạy thao tác thật: sửa → review diff → lưu → reload trang; flow apply và rollback qua backend giả lập systemd. Gate Linux độc lập kiểm đúng service, graceful stop và readiness.
- Có wireframe desktop/mobile được Owner duyệt trước code, rồi ảnh sau triển khai cùng viewport để so sánh.

## 5. High-Level Timeline / Plan
- [x] Nghiên cứu loaders, env overrides, service split và tài liệu chính thức; ghi Master draft.
- [x] Owner duyệt phạm vi hai service, bổ sung đa provider và chọn tone technical.
- [x] Viết Product Doc và dựng UI bằng code/Hallmark; Owner duyệt Visual Gate qua chat.
- [x] Viết Engineering Doc: source-bundled API, runtime adapters, schema, quyền OS, revision/apply/readiness và secrets.
- [x] Viết QA Doc rồi Tasks Doc; Owner chọn Codex · gpt-6.1-sol · medium; có agent review source riêng.
- [ ] Tạo worktree mới từ baseline split đã xác nhận; code UI/API; verify build/test/E2E.
- [ ] Review quyền ghi và dữ liệu độc lập; bàn giao gói triển khai. Cài đặt ban đầu và kiểm máy Linux theo scope được duyệt.

## 6. Stakeholders & Responsibilities
- **Owner / approver**: người dùng hiện tại.
- **Harness / data owner**: AlexChu.
- **Research / planning**: Codex trong chat hiện tại.
- **Design / implementation**: Codex theo cấu hình Owner chọn. **Source reviewer**: agent riêng `ui_review`, không ghi code. Gate host Linux và provider thật vẫn chưa chạy.

## 7. Related Documents / Research Sources
- [Worker Agent Fleet Master](worker-agent-fleet-1_master_261007_v1.0.8.md).
- Service split: `Docs/02-engineering/worker-agent-fleet-1_service-split_261013_v1.0.0.md` tại baseline `b0f6b0c`; không tồn tại trong checkout hiện tại. Doc còn có metadata Draft, nhưng task D-024 ghi đã duyệt/cutover: cần ưu tiên kiểm chứng artifact triển khai thay vì coi metadata doc là xác nhận máy thật.
- Code đã đọc bằng `git show b0f6b0c:<path>`: `listener/src/tg_triage/config.py`, `nailhub/src/nailhub/config.py`, `systemd/tg-nailhub.service`, `deploy/README.md`.
- [Python 3.14 tomllib](https://docs.python.org/3.14/library/tomllib.html): chỉ đọc TOML; cần writer riêng, ưu tiên giữ comment và unknown keys.
- [systemctl — nguồn systemd](https://github.com/systemd/systemd/blob/main/man/systemctl.xml): tham chiếu thao tác service; không suy rằng restart đã chứng minh ứng dụng sẵn sàng.
- [OWASP CSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html): cơ sở cho kiểm Origin và CSRF ở thao tác thay đổi cấu hình.
- [Product](../01-product/agentfleet-settings-1_product_261014_v1.0.0.md) · [Engineering](../02-engineering/agentfleet-settings-1_engineering_261014_v1.0.0.md) · [QA](../03-qa/agentfleet-settings-1_qa_261014_v1.0.0.md) · [Tasks](../04-tasks/agentfleet-settings-1_tasks_261014_v1.0.0.md).
- [Claude API overview](https://platform.claude.com/docs/en/api/overview), [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs), [DeepSeek API](https://api-docs.deepseek.com/): nguồn tham khảo adapter, không chứng minh mọi model hỗ trợ cùng capability.
- GitHub Issue / PR: chưa tạo; không phát sinh yêu cầu commit/push.

## 8. Changelog
| Version | Date | Time | Author | Description | Status |
|---|---|---|---|---|---|
| v1.0.0 | 2026-10-14 | 09:33 | AlexChu / Codex | Nghiên cứu baseline split, phạm vi console chung, settings, luồng áp dụng, sơ đồ nội dung, tiêu chí nghiệm thu | Review |
| v1.0.1 | 2026-10-14 | 11:12 | AlexChu / Codex | Owner bổ sung cloud/custom providers riêng từng service, test kết nối; bỏ Pencil, chọn Hallmark/technical; bắt đầu bản UI tương tác | Approved scope |
| v1.0.2 | 2026-10-14 | 11:28 | AlexChu / Codex | Owner chốt UI chạy kèm source qua terminal; launcher localhost, bản HTML tương tác và browser verification | Design review |

## 9. Approval & Sign-off
- [x] Owner duyệt Master scope và bổ sung đa provider qua chat ngày 14/10.
- [x] Owner duyệt UI/Visual Gate qua chat, trước backend implementation.
- [x] Owner chốt executor/model/effort; có independent source reviewer.

## 10. Notes & Assumptions
- **Kiến trúc cập nhật theo Owner:** console local đi kèm repo/source, CLI khởi động server UI/API cùng origin; helper có allowlist chỉ hai config/env paths và hai unit. Web process không chạy root, không có shell hoặc sudo rộng. Không nhập hai runtime package vào chung venv console để tránh lỗi deploy ngày 13/10. Chế độ daemon là tuỳ chọn sau, không bắt buộc.
- **Access cập nhật:** mặc định loopback, mở browser từ CLI. Cần chốt host đích khi nối runtime thật: CLI chạy trên Linux host có hai service, hoặc connector có xác thực nếu UI chạy từ máy Windows. Không tự mở LAN/public. Remote access có HTTPS/VPN và Owner authentication nếu được triển khai về sau.
- Paths, owner binding và VERIFIED hiển thị ở chế độ bảo vệ. Provider/model NailHub mở rộng theo scope mới, phải sửa runtime và kiểm độc lập trước khi chạy thật.
- Backend phải phân biệt lỗi TOML với lỗi env/session và không trả raw exception chứa dữ liệu bí mật.
- Chưa có quyền truy cập production được sử dụng ở lượt này; mọi giá trị trên form mẫu là dữ liệu minh hoạ hoặc defaults, không phải số đo máy thật.
- Checkout hiện tại có nhiều thay đổi có sẵn từ việc khác; chỉ thêm Master và task tracking. Không stash, reset, merge hay sửa các file đó.

### Backend delivery slice — 14/10
Lượt này nối các field và thao tác của UI đã duyệt: provider/model/key, test, lưu và áp dụng riêng service. Local Windows có persistence/API thật; profile installed Linux có fixed-unit restart và receipt proof nhưng chưa cài/kiểm trên ThinkPad. Advanced selectors/options, thay Telegram/GitHub credentials, rollback UI và remote connector trong đề xuất tổng thể chưa nằm trong bản UI đã duyệt và chưa được triển khai ở slice này. Không tự coi các mục đó đã hoàn tất.
