# Kế hoạch cấu trúc lại listener (nháp để Owner duyệt)

**Version**: v1.0.0 · **Date**: 2026-10-29 (Asia/Saigon) · **Owner**: AlexChu · **Author**: Claude · **Status**: Draft — chưa thực hiện
**Đầu vào**: [Audit listener](../02-engineering/telegram-triage-noissue_listener-audit_261029_v1.0.0.md) · PR #79, #81 (chưa merge, chưa deploy)

## 1. Mục tiêu Owner chốt (2026-10-29)

| # | Mục tiêu | Đo bằng |
|---|---|---|
| D | **Làm rõ Business DOCS** | Một nguồn quy tắc duy nhất, đọc được; Agent yếu tra một chỗ là ra |
| G3 | **Ticket thật tốt hơn** | Ticket trên production có đủ link + ảnh đúng topic; ghi chú ảnh giải thích được |
| G1 | **Bàn giao an toàn** | Mọi PR listener tự chạy 940 test + drift check trong CI |
| G4 | **Code gọn** | Bớt trùng lặp, bớt đường code không dùng, module nhỏ hơn — không đổi hành vi |

Tạm hoãn: G2 (Owner tự sửa mọi câu chữ — W1/W2/W3 trong audit).

## 2. Thứ tự đề xuất và lý do

```
P1 Làm rõ Business DOCS ──┐  (chỉ tài liệu, không đụng runtime)
P2 Ship PR #79/#81 + fix ảnh ─┤  (song song với P1 được: khác file)
                          ▼
P3 CI cho listener  ──────► P4 Code gọn (W5 → W4 → W6), mỗi bước một PR nhỏ
```

- **P1 trước/đồng thời P2**: quy tắc rõ thì mọi việc sau có chuẩn để kiểm; không đụng code nên không cản ship.
- **P2 sớm**: mọi cải tiến (template, link evidence) chưa tới người dùng; G3 chỉ đạt khi deploy.
- **P3 trước P4**: refactor bot production mà CI không chạy test là đánh cược; CI là lưới an toàn của G1 và điều kiện của G4.
- **P4 theo thứ tự rủi ro tăng dần** (strangler, không big-bang): gộp trùng → bỏ legacy (cần quyết định) → tách module.

## 3. Chi tiết từng giai đoạn

### P1 — Làm rõ Business DOCS (mục tiêu D, G1)
Vấn đề hiện tại: ~1.700 dòng nhưng ~1.200 là JSON trace; quy tắc trộn lý do nghiệp vụ, chi tiết kỹ thuật, debug và lịch sử; Rulebook lặp lại quy tắc (hai nguồn).
- Tách **JSON trace** ra file riêng (`…_trace.json`), sửa `check-telegram-triage-policy.py` đọc file đó.
- Mỗi TG viết lại theo khuôn ngắn: **Quy tắc (MUST/MUST NOT) · Vì sao · Ví dụ · Ngoại lệ**; chi tiết kỹ thuật (byte, giây, tên bảng) chuyển sang Engineering doc, TG chỉ trỏ tới.
- Gắn nhãn **[legacy] / [autonomous] / [chung]** cho từng TG.
- Gộp **Rulebook vào Business DOCS** (hoặc Rulebook thành bảng tra cứu sinh từ Business DOCS) ⇒ còn một nguồn quy tắc.
- Phần "Bộ nhớ, truy vấn, debug" chuyển sang runbook vận hành (02-engineering).
- Sửa `POLICY_VERSION` lỗi thời; thêm "TG = Telegram triage" vào thuật ngữ.
- Verify: checker PASS, mọi link nội bộ đúng, không đổi nghĩa quy tắc (đối chiếu từng TG cũ ↔ mới).

### P2 — Ship và sửa evidence ảnh (G3)
- Merge PR #79 → main, PR #81 → main; build release qua `deploy/build_wheel.py`; cài THINKPAD theo `deploy/README.md`; `check-config` + so fingerprint; readback service. *(Owner thực hiện bước cài đặt.)*
- Fix: chỉ ghi chú ảnh bị bỏ thuộc đúng topic của ticket (`attachments.py:213`); log đếm ảnh theo `message_id` (không ghi nội dung).
- Sửa 3 LOW của review PR #81 (IPv6 host, trùng link IDN, comment cũ).
- Verify: golden + test mới; ticket thật sau deploy có link evidence (quan sát tin mới, không replay).

### P3 — CI cho listener (G1)
- Workflow mới: `unittest` listener (Ubuntu + Windows, Python 3.11/3.12) + `check-telegram-triage-policy.py` + `test_telegram_triage_policy.py` + `test_reformat_telegram_issue.py`.
- Đánh dấu/ổn định test hẹn giờ đang thỉnh thoảng fail (`test_usage_fallback`).
- Không sửa console CI đang fail (việc riêng).

### P4 — Code gọn (G4), mỗi bước một PR, golden giữ nguyên
1. **W5 gộp trùng**: một hàm dựng URL issue; một đường gửi notice; một nguồn regex email/phone; một `CASE_STATUS`; xóa `NEEDS_CLARITY_LINE` không dùng.
2. **W4 bỏ legacy** *(Owner đã chốt; điều kiện: đã thử cài lại release cũ ở P2)*: chuyển `resume_case` sang đường autonomous; xóa ~650 dòng + `classify.md`, `case.md`, `topics.md`, `thinking-topics.md`; cập nhật TG đánh dấu [legacy].
3. **W6 tách module**: `pipeline.py` (985) và `classifier.py` (732) tách theo trách nhiệm; gỡ 7 vòng import lười; `store.py` giữ mixin nhưng gom schema về một chỗ.

## 4. Quyết định

**Đã chốt (Owner, 2026-10-29):**
1. **Bỏ đường legacy trong P4** (P4.2). Hệ quả:
   - Rollback không còn là tắt cờ `autonomous_enabled`; rollback = cài lại wheel release trước (theo `deploy/README.md`). P2 phải chứng minh quy trình cài lại release cũ chạy được **trước khi** làm P4.2.
   - P1 chỉ **gắn nhãn [legacy]** cho các TG/đoạn chỉ áp cho legacy, không viết lại chúng; P4.2 xóa chúng cùng code.
   - `resume_case` phải chuyển sang đường autonomous trước khi xóa `_analyze_case`.
2. **Gộp Rulebook vào Business DOCS**: một nguồn quy tắc; Rulebook cũ thành trang trỏ sang.

**Còn mở:**
3. **Deploy P2**: ai thực hiện và lịch nào (cần SSH/sudo trên THINKPAD).
4. Thứ tự P1 ∥ P2 rồi P3 → P4 có đúng ưu tiên không?

## 5. Ngoài phạm vi
G2 (W1 câu chữ, W2 prompt, W3 tham số vào config); console-ui, nailhub; console contract CI.
