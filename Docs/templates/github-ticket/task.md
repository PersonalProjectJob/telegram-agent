<!--
TEMPLATE: task
title: [task] {{việc cần làm, bắt đầu bằng động từ}}   (tối đa 80 ký tự)
labels: task, triage, source:telegram
Redesign panel/mode đã nhận diện là task đủ rõ; layout mới là kết quả, không phải điều kiện (TG-08).
Không hỏi owner/deadline/thứ tự nếu mục tiêu đã rõ. Xóa khối chú thích này trước khi upload.
-->
<!-- tg:{{chat_id}}:{{message_id tin đầu}} -->
### 📝 Tóm tắt
{{1–2 câu: cần làm gì, cho màn hình/chức năng nào}}

### 🛠 Việc cần làm
{{mô tả việc — hoặc (chưa có thông tin)}}
**Lý do:** {{tùy chọn: vì sao cần làm}}
**Phạm vi:** {{tùy chọn: Làm — … · Không làm — …}}
**Hạn / người được nhắc:** {{tùy chọn: chỉ ghi khi người gửi nói rõ}}

### 🖼 Minh họa / Evidence (đầu vào)
<!-- tùy chọn: ảnh, tài liệu, link người gửi cung cấp -->
![Ảnh 1]({{link ảnh}})

- 📎 [{{tên tài liệu}}]({{link tài liệu}})
- {{link tham khảo}}

### 🏁 Xong khi
- [ ] {{tiêu chí hoàn thành}}
- [ ] Đính kèm evidence kết quả (ảnh/link/log) khi đóng issue

### ❓ Cần làm rõ
<!-- tùy chọn: chỉ giữ khi còn thiếu. Thiếu "Việc cần làm" → Việc cần làm là gì? -->
- {{câu hỏi}}

<details><summary>💬 Tin gốc ({{số}} tin) — {{người gửi}}, {{dd/mm HH:MM}}</summary>

**{{người gửi}} · {{dd/mm HH:MM}}**
> {{nội dung tin nguyên văn}}

</details>

---
<sub>Phân loại: {{tầng 1 lệnh | tầng 2 <model>, confidence <0.00>}} · tạo tự động bởi tg-triage</sub>
