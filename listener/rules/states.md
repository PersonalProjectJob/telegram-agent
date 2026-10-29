<!-- rule-file: states · version 1 · status DRAFT · audience: code -->
# Trạng thái kết quả

Danh sách **đóng**: code chỉ được trả một trong các mã dưới đây cho mỗi chủ đề. Model không được trả trạng thái.

## Bảng: states

| Mã | Nghĩa | Bot làm gì | Rule nghiệp vụ |
|---|---|---|---|
| `READY` | Đủ rõ | Tạo ticket, hoặc bổ sung nếu có Reply thật tới ticket cũ | TG-08, TG-16 |
| `NEED_INFO` | Là việc nhưng thiếu trường bắt buộc | Hỏi đúng trường thiếu theo `questions.md`, giữ pending, không tạo ticket | TG-08, TG-12 |
| `MEDIA_ONLY` | Chỉ có ảnh/tài liệu, không có chữ nói ý định | Hỏi ý định, không tạo ticket | TG-15 |
| `CHAT` | Không phải việc | Không làm gì | TG-09 |
