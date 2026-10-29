# Template câu bot trả lời trong group Telegram

Dùng khi cần biết hoặc soạn câu bot gửi vào group sau khi log ticket. Bot xưng **Em**, gọi **Anh/Chị**; chỉ gọi riêng Anh hoặc Chị khi bio công khai ghi rõ (TG-28). Câu hỏi có chữ "Bạn" trong code được tự đổi thành "Anh/Chị" trước khi gửi ([`group_notices.py`](../../../listener/src/tg_triage/group_notices.py) `address_question`).

Luôn **reply vào tin đầu** của chủ đề. Mỗi notice gửi một lần, không gửi lại (TG-26). Tối đa 3 câu hỏi, mỗi câu 180 ký tự, cả tin 1100 ký tự.

## Đã tạo ticket

```text
Em đã ghi nhận nội dung này và tạo ticket: {{link issue}} rồi nhé 😊
```

## Cần người xác nhận (needs-human, đường legacy)

```text
Em đã ghi nhận nội dung này rồi nhé. Nhờ Anh/Chị hoặc Owner bổ sung/xác nhận thông tin ngay trong group để làm rõ yêu cầu.
Em đã tạo ticket để tiện theo dõi: {{link issue}}
• {{câu hỏi 1}}
• {{câu hỏi 2}}
```

## Hỏi làm rõ

Nhắc phần đã biết, rồi hỏi đúng phần thiếu. Đây là mẫu câu, model viết lời cụ thể:

```pattern
Em đã ghi nhận {{điều đã biết}}. Anh/Chị cho Em biết {{điều còn thiếu}} nhé?
```

Câu hỏi mặc định khi không có câu hỏi cụ thể:

```text
Anh/Chị muốn team xử lý gì, ở màn hình/chức năng nào và kết quả mong muốn là gì?
```

## Sau khi bổ sung / tách / tái diễn

```text
Em đã bổ sung thông tin vào ticket rồi nhé 😊
```

```text
Em đã tách các yêu cầu thành từng ticket riêng rồi nhé 😊
```

```text
Em đã tạo ticket cho lần lỗi tái diễn và gắn ticket liên quan rồi nhé 😊
```

## Lỗi và kết quả không rõ

```text
Em chưa phân tích được các tin vừa nhận. Anh/Chị reply tin gốc để chỉ rõ yêu cầu cần xử lý giúp Em nhé.
```

```text
Em chưa xác nhận được kết quả thao tác GitHub. Nhờ Owner kiểm tra trước khi thử lại nhé.
```

## Không gửi gì

Chat, cảm ơn, câu hỏi thông tin, `/question`, noise, ảnh đơn lẻ không yêu cầu (đường legacy), chế độ dry-run: không gửi text (TG-12, TG-32).
