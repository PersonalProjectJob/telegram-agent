<!-- rule-file: examples · version 1 · status DRAFT · audience: code (test) + model (chỉ cột dữ kiện) -->
# Ví dụ chuẩn

Bộ test cố định: cho **dữ kiện** đầu vào, code phải ra đúng **kết quả**, không phụ thuộc model. Khi gửi model làm ví dụ trích dữ kiện, bot chỉ gửi cột "Tin" và "Dữ kiện", **không** gửi cột "Kết quả" và "Luật".

## Bảng: examples

| # | Tin (rút gọn) | Dữ kiện | Kết quả | Luật |
|---|---|---|---|---|
| X1 | "POS không in được receipt, cả app và web, máy in có dây" (#47) | bug · object=máy in POS · observed=không in receipt | `READY` | R4 |
| X2 | "Login đúng pass mà không login được" + ảnh "Invalid username or password" | bug · object=đăng nhập · observed=báo sai mật khẩu dù đúng | `READY` | R4 |
| X3 | Ảnh panel "Salon information & visibility" + "design đổi bảng này" (#46) | task · object=panel Salon information & visibility · work=thiết kế lại | `READY` | R5 |
| X4 | "https://bitcoinnailbar.com/#gallery sao những hình Nail trước đó em không mang qua" + "gửi HTML của web" (#82) | task · object=gallery bitcoinnailbar · work=đưa ảnh Nail cũ vào gallery, gửi HTML | `READY` | R5 |
| X5 | "Nên có bộ lọc theo ngày ở giao dịch" | feedback · proposal=bộ lọc ngày | `READY` | R6 |
| X6 | "Nút này lỗi" | bug · object=∅ (chung chung) · observed=∅ | `NEED_INFO` (hỏi object, observed) | R7 |
| X7 | "Check giúp em cái này" | task · work=∅ | `NEED_INFO` (hỏi object, work) | R7 |
| X8 | Một ảnh, không chữ | media_ids=[…] | `MEDIA_ONLY` | R3 |
| X9 | "Dạ để em check" | chat | `CHAT` | R1 |
| X10 | "/question API có rate limit không?" | question | `CHAT` | R2 |
