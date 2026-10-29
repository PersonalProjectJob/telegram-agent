<!--
TEMPLATE: needs-human — yêu cầu chưa đủ rõ, cần người xác nhận
Hai biến thể:
  A. Chưa biết loại:
     title: [cần phân loại] {{mô tả ngắn}}
     labels: needs-human, source:telegram
     Body: chỉ dùng Tóm tắt + Cần làm rõ + Tin gốc (bỏ phần "Mục theo loại").
  B. Đã biết loại (lệnh có nội dung, hoặc loại chắc chắn nhưng thiếu thông tin):
     title: [bug|task|feedback] {{mô tả ngắn}}
     labels: <loại>, triage, needs-human, source:telegram
     Body: Tóm tắt + toàn bộ mục của bug.md / task.md / feedback.md + Cần làm rõ + Tin gốc.
Đường autonomous KHÔNG tạo ticket needs-human: giữ pending và hỏi trong group (TG-08).
Xóa khối chú thích này trước khi upload.
-->
<!-- tg:{{chat_id}}:{{message_id tin đầu}} -->
### 📝 Tóm tắt
{{điều đã biết — hoặc (agent không chắc chắn, cần người đọc)}}

<!-- Biến thể B: chèn ở đây các mục theo loại từ bug.md / task.md / feedback.md -->

### ❓ Cần làm rõ
- {{câu hỏi 1: đúng điểm còn thiếu}}
- {{câu hỏi 2}}

<details><summary>💬 Tin gốc ({{số}} tin) — {{người gửi}}, {{dd/mm HH:MM}}</summary>

**{{người gửi}} · {{dd/mm HH:MM}}**
> {{nội dung tin nguyên văn}}

</details>

---
<sub>Phân loại: {{tầng 2 <model>, confidence không có | confidence <0.00>}} · tạo tự động bởi tg-triage</sub>
