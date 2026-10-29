<!-- rule-file: questions · version 1 · status DRAFT · audience: code -->
# Hỏi khi thiếu (trạng thái `NEED_INFO`)

Code chọn câu hỏi theo trường thiếu; **câu chữ** lấy từ template `templates/github-ticket/group-replies.md` (khối ghi ở cột "Khối template"). Model không viết câu hỏi.

## Bảng: ask_order

Hỏi tối đa 3 trường, theo thứ tự tăng dần; bỏ qua trường đã CÓ; không hỏi lại câu đã hỏi trong cùng chủ đề (TG-12).

| Thứ tự | Áp cho `intent` | Trường thiếu | Ý câu hỏi | Khối template |
|---|---|---|---|---|
| 1 | bug, task | `object` | Lỗi/việc này ở màn hình hoặc chức năng nào? | `ask.object` *(cần thêm)* |
| 2 | bug | `observed` | Hiện tượng cụ thể là gì (bấm gì, thấy gì)? | `ask.observed` *(cần thêm)* |
| 2 | task | `work` | Cần làm cụ thể việc gì? | `ask.work` *(cần thêm)* |
| 2 | feedback | `problem` | Vấn đề hoặc nhu cầu là gì? | `ask.problem` *(cần thêm)* |
| — | (R8, không xác định) | — | Câu mặc định | `question.fallback` *(đã có)* |
| — | `MEDIA_ONLY` | — | Hỏi ý định của ảnh | `ask.media_intent` *(cần thêm)* |

Khối đánh dấu *(cần thêm)* sẽ được thêm vào `group-replies.md` khi triển khai; trước đó file này chỉ là đặc tả.
