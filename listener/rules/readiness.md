<!-- rule-file: readiness · version 1 · status DRAFT · audience: code -->
# "Đủ rõ" — bảng quyết định (TG-08)

Input: dữ kiện đã qua `facts.md` (chỉ trường CÓ mới được dùng). Output: một mã trong `states.md`. **Model không thấy file này.**

## Bảng: decision

Đọc từ trên xuống, dòng đầu tiên khớp là kết quả.

| # | Điều kiện | Kết quả | Ghi chú |
|---|---|---|---|
| R1 | `intent` = `chat` | `CHAT` | Chào, cảm ơn, "Dạ để em check" |
| R2 | `intent` = `question` và không có `work` | `CHAT` | Câu hỏi thông tin, `/question` (TG-09) |
| R3 | Không có chữ nào nói ý định, chỉ có `media_ids` | `MEDIA_ONLY` | Ảnh đơn lẻ (TG-15) |
| R4 | `intent` = `bug` và có `object` và có `observed` | `READY` | `expected` thiếu ⇒ mặc định "hoạt động đúng như trước", không hỏi |
| R5 | `intent` = `task` và có `object` và có `work` | `READY` | Redesign panel/mode đã nhận diện là `READY` |
| R6 | `intent` = `feedback` và có `problem` hoặc `proposal` | `READY` | |
| R7 | `intent` ∈ {`bug`,`task`,`feedback`} nhưng không khớp R4–R6 | `NEED_INFO` | Hỏi trường thiếu theo `questions.md` |
| R8 | Còn lại | `NEED_INFO` | Hỏi câu mặc định `question.fallback` |

## Bảng: never_blocks

Thiếu những thứ dưới đây **không bao giờ** làm mất `READY`. Có thì ghi vào ticket; thiếu thì liệt kê ở mục "Cần làm rõ" của ticket, không hỏi trong group.

| Thứ không chặn |
|---|
| thiết bị, trình duyệt, bản build |
| log, mã lỗi chi tiết |
| bước tái hiện |
| tài khoản thử |
| deadline, người phụ trách, thứ tự ưu tiên |
| bản thiết kế/layout tương lai (task redesign) |

## Bảng: unseen_media — **CHỜ OWNER CHỐT (D7)**

Áp khi đợt tin có ảnh model chưa xem (vượt giới hạn ảnh gửi model).

| Phương án | Quy tắc |
|---|---|
| A | Không ảnh hưởng trạng thái: dữ kiện từ chữ + ảnh đã xem đủ R4–R6 ⇒ `READY`; ticket đính đủ ảnh, ghi "model chưa xem N ảnh" |
| B | Có ảnh chưa xem ⇒ `NEED_INFO` (hành vi hiện tại) |

## Bảng: confidence — **CHỜ OWNER CHỐT**

| Phương án | Quy tắc |
|---|---|
| A | Bỏ ngưỡng `confidence ≥ 0.7` khỏi quyết định; chỉ dùng "trường CÓ" theo `facts.md` |
| B | Giữ ngưỡng 0.7 như điều kiện bổ sung cho R4–R6 |
