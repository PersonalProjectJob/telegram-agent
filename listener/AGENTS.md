# Telegram triage — hướng dẫn agent tiếp nhận

Owner quy trình/data/state: **AlexChu**. Executor (Codex, Gemini/Antigravity, Claude…) chỉ là runtime tạm thời. File này chỉ dẫn đường cho việc đọc/sửa listener và log GitHub ticket; không phải nguồn quy tắc thứ hai, không thay quyền hoặc quy trình harness của repo. Runtime không load file này.

## Nguyên tắc nền (Owner, 2026-10-29)

Mọi quyết định của bot phải dựa trên **file markdown cụ thể với trạng thái liệt kê cụ thể**, không dựa hoàn toàn vào model. Model chỉ trích dữ kiện; code áp quy tắc. Ba lớp file, mỗi lớp một việc:

| Thư mục | Quyết định gì | Bot đọc lúc chạy? |
|---|---|---|
| [`rules/`](rules/README.md) | **Làm gì**: tạo ticket / hỏi / bỏ qua (bắt đầu từ README: quy tắc nạp rules vào bot và model) | Sẽ đọc khi file chuyển ACTIVE (hiện DRAFT) |
| `templates/github-ticket/` ([hướng dẫn](../Docs/templates/github-ticket/README.md)) | **Nói gì, trình bày ra sao**: form ticket, comment, câu reply (TG-33) | Có |
| `prompts/` (vd [autonomous.md](prompts/autonomous.md)) | **Model trích dữ kiện thế nào** | Có |

Khi sửa hành vi: tìm đúng file quy tắc/template trước; không thêm quyết định mới vào prompt hay vào code nếu nó thuộc về `rules/`. Thiết kế đích và thứ tự làm: [Kế hoạch](../Docs/04-tasks/telegram-triage-noissue_tasks_261029_v1.0.0.md) §0; sequence: [tài liệu luồng](../Docs/01-product/telegram-triage-noissue_business-flow_261028_v1.0.0.md).

## Đọc trước khi xử lý

0. [Overview và bàn giao](../Docs/00-master/telegram-triage-noissue_overview_261027_v1.0.0.md) (phạm vi, thuật ngữ, quy trình theo loại việc) và [Rulebook](../Docs/01-product/telegram-triage-noissue_rulebook_261027_v1.0.0.md) (rule MUST/MUST NOT, bảng quyết định, ví dụ chuẩn). Agent yếu: chỉ làm điều Rulebook cho phép rõ ràng, còn lại dừng và hỏi Owner.
1. [Business DOCS — nguồn nghiệp vụ duy nhất](../Docs/01-product/telegram-triage-noissue_business_261015_v1.0.0.md): **Bảng quyết định hiện hành**, **TG-01…TG-33**, **Vòng đời ticket**, **Ticket đã đóng**, **Quy trình điều tra bắt buộc**, **Tiêu chí bàn giao**. Đọc toàn bộ ngữ cảnh của rule, không hành động chỉ từ tên mã.
2. [THINKING engineering/research](../Docs/02-engineering/telegram-triage-noissue_thinking-research_261015_v1.0.0.md) và [QA acceptance](../Docs/03-qa/telegram-triage-noissue_qa_261015_v1.0.0.md): phân biệt hành vi đã triển khai với đề xuất, và tiêu chí bằng chứng.
3. [Hướng dẫn listener](README.md): cấu hình, dry-run, drift check, trace chỉ đọc, reformat ticket.

Tài liệu Fleet cũ là lịch sử. Riêng §12.11 của [assistant-definition](../Docs/02-engineering/worker-agent-fleet-1_assistant-definition_261012_v1.0.0.md) ("chỉ log ticket khi được tag") **đã lỗi thời**: TG-08 hiện hành không cần tag. Xung đột thì Business DOCS thắng.

## Khi phân loại và log ticket

- **"Đủ rõ" — tạo ticket hay hỏi** (TG-07, TG-08, TG-09, TG-12): tra bảng, không tự suy diễn.
  - Trạng thái được phép: [`rules/states.md`](rules/states.md) (`READY`, `NEED_INFO`, `MEDIA_ONLY`, `CHAT`).
  - Dữ kiện cần có và khi nào một trường được tính là CÓ: [`rules/facts.md`](rules/facts.md).
  - Quyết định: bảng R1–R8 và danh sách thứ không bao giờ chặn việc tạo ticket trong [`rules/readiness.md`](rules/readiness.md).
  - Hỏi gì khi thiếu, theo thứ tự nào: [`rules/questions.md`](rules/questions.md). Ca mẫu đối chiếu: [`rules/examples.md`](rules/examples.md).
  - Khi các file `rules/` còn `DRAFT`, bot chạy thật vẫn theo hành vi hiện tại (model trả `actionable`/`confidence`); bảng trong `rules/` là đích đã duyệt hướng, dùng để đánh giá và soạn ticket tay.
- Gom/tách theo ngữ nghĩa và Reply thật; không gộp vì cùng sender, thời gian hay từ khóa. Ảnh, caption, reply phân tích chung một gói (TG-05, TG-06, TG-15).
- Ticket đã có: open thì bổ sung; closed thì theo bảng **Vòng đời ticket** và **Ticket đã đóng**. Không tự reopen/đóng, không tạo trùng.
- Repo, URL, nhãn, permission do config/code quyết định; model hoặc agent không tự chọn (TG-17, TG-31).
- Reserve trước POST. `reserved`/`ambiguous` không tự retry, nhờ Owner kiểm repo; notice không tự resend (TG-24, TG-26).
- Phản hồi theo mẫu TG-11, xưng hô theo TG-28/TG-29; không đoán giới tính từ tên/ảnh.

- Soạn ticket/comment thủ công: theo đúng khuôn trong [Template log ticket](../Docs/templates/github-ticket/README.md); không tự chế heading, nhãn hay thứ tự mục.

## Khi sửa ticket đã log hoặc debug

- Chuẩn hóa lại nội dung ticket chỉ qua `scripts/reformat-telegram-issue.py`: preview → đối chiếu source marker + body SHA → `--apply` → đọc lại. Không viết body tay, không tạo ticket mới để chữa ticket cũ (TG-27).
- Database live mở `mode=ro`, không khởi tạo `Store`. Lần chuỗi tin → case/group → root → job → operation → issue/notice theo **Quy trình điều tra bắt buộc**; báo cáo theo **Tiêu chí bàn giao**, ghi rõ phần chưa xác minh.
- `planned`/`reserved`/`ambiguous` không phải bằng chứng side effect thành công. Không reset reservation để ép retry.
- Không xuất session Telegram, config chứa thông tin xác thực hay nội dung hội thoại riêng tư vào log, issue hoặc báo cáo công khai.

## Khi sửa code hoặc prompt

- Đổi semantics phải cập nhật Business DOCS cùng lượt. Chạy `python scripts/check-telegram-triage-policy.py` và `python scripts/test_telegram_triage_policy.py` từ repo root; hash khớp không chứng minh đúng nghĩa.
- Mẫu ticket/notice đóng gói trong `listener/templates/github-ticket/`; sửa qua PR và release, chạy test, `check-config` và so sánh fingerprint (TG-33).
- **Đổi quy tắc quyết định** (`rules/`): theo [`rules/README.md`](rules/README.md) §8 — tăng `version` trong header, thêm/sửa ví dụ trong `examples.md` cho mọi luật bị đổi, chạy test + tự kiểm, cập nhật Business DOCS; chỉ chuyển `DRAFT → ACTIVE` khi Owner duyệt và không còn bảng **CHỜ OWNER CHỐT**. Không đưa quyết định vào prompt hay code nếu nó thuộc về `rules/`; model chỉ nhận phần §5 của `rules/README.md`.
- Không replay tin cũ, không tạo issue/notice thật để kiểm thử: dùng dry-run, fake tools và smoke script trong README (TG-32).
- Offline PASS không chứng minh installed/live PASS; deploy THINKPAD cần evidence release, installed tests và service readback riêng.

## Giới hạn quyền

Không tự commit, push, deploy hoặc tạo GitHub issue chỉ vì có checklist trong tài liệu; thực hiện theo yêu cầu Owner và quyền đã được xác lập trong phiên. Task code đi qua `/dispatch`; việc đụng GitHub write hoặc production thuộc GATE 0b.
