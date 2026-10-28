# Template log GitHub ticket

Bộ khuôn chuẩn cho nội dung ticket GitHub của tính năng Telegram triage. Mọi file `.md` soạn ra để upload lên GitHub (issue, comment) phải theo đúng khuôn ở đây, để ticket do bot tạo, do người soạn tay hay do Agent soạn đều giống nhau.

Nguồn thật của khuôn là code: [`issue_format.py`](../../../listener/src/tg_triage/issue_format.py), [`followups.py`](../../../listener/src/tg_triage/followups.py), [`group_notices.py`](../../../listener/src/tg_triage/group_notices.py). Test [`test_ticket_md_templates.py`](../../../listener/tests/test_ticket_md_templates.py) so các file ở đây với output thật của code; lệch thì test fail.

## Chọn template

| Trường hợp | File | Nhãn |
|---|---|---|
| Bug đủ rõ | [bug.md](bug.md) | `bug`, `triage`, `source:telegram` |
| Task đủ rõ | [task.md](task.md) | `task`, `triage`, `source:telegram` |
| Feedback / đề xuất tính năng | [feedback.md](feedback.md) | `feedback`, `triage`, `source:telegram` |
| Chưa rõ, cần người xác nhận | [needs-human.md](needs-human.md) | thêm `needs-human` (xem file) |
| Comment bổ sung, xác nhận/điều chỉnh phạm vi, tái diễn, tách ticket | [comments.md](comments.md) | — |
| Câu bot trả lời trong group Telegram | [group-replies.md](group-replies.md) | — |

Câu hỏi thông tin, `/question`, chat, cảm ơn: **không tạo ticket** (TG-09), không có template.

## Cách dùng

1. Chọn đúng file theo bảng trên, copy ra file nháp (ví dụ trong thư mục scratch, không commit).
2. Thay mọi `{{…}}` bằng nội dung thật. Không đổi heading, emoji, thứ tự mục, nhãn trường.
3. Mục ghi *tùy chọn*: không có dữ liệu thì xóa cả mục, kể cả heading.
4. Trường bắt buộc mà chưa có dữ liệu: ghi đúng `(chưa có thông tin)` và thêm câu hỏi tương ứng vào mục `❓ Cần làm rõ`. Không bịa môi trường, nguyên nhân, bước tái hiện (TG-08).
5. Title: theo dòng `title:` trong khối chú thích đầu file, tối đa 80 ký tự.
6. Che email và số điện thoại. Không dán mật khẩu, token, session (TG-31).
7. Khối chú thích `<!-- … -->` ở đầu file là hướng dẫn: GitHub không hiển thị, nhưng **xóa trước khi upload** cho gọn. Giữ dòng marker `<!-- tg:… -->` nếu ticket có tin Telegram nguồn.
8. Upload bằng file, không dán inline (Windows làm cụt nội dung):

```bash
gh issue create --repo <owner/repo> --title "<title>" --label bug --label triage --label source:telegram --body-file ticket.md
```

## Giới hạn quan trọng

- **Sửa ticket bot đã tạo:** không viết body tay. Dùng `scripts/reformat-telegram-issue.py` (preview → SHA → `--apply` → đọc lại). Script dùng cùng renderer nên output tự khớp các template này (TG-27).
- **Không tạo ticket thật để thử template.** Dùng preview của script hoặc test offline.
- Khi đổi template trong code: sửa file `.md` tương ứng ở đây cùng lượt, chạy test, cập nhật hash trong Business DOCS (drift check).

## Giới hạn số do code áp

| Mục | Giới hạn |
|---|---|
| Title | 80 ký tự |
| Mỗi trường text | 500 ký tự |
| Mỗi danh sách (bước, tiêu chí, link, xong khi) | 10 mục, mỗi mục 200 ký tự |
| Câu hỏi trong `❓ Cần làm rõ` | 5 câu, mỗi câu 200 ký tự |
| Tin gốc trích dẫn | 10 tin (ticket tách/bổ sung có thể nhiều hơn) |
