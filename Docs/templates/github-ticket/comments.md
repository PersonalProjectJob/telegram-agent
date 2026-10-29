# Template comment và phần nối thêm vào ticket

Dùng khi ghi vào **ticket đã có**. Mỗi khối dưới đây là một comment (hoặc đoạn nối vào body) riêng; copy đúng khối cần dùng. Chỉ ghi khi có **Reply thật** tới tin gốc hoặc notice của bot (TG-16), và URL issue khớp repo cấu hình (TG-17).

Nguồn: [`followups.py`](../../../listener/src/tg_triage/followups.py), [`pipeline.py`](../../../listener/src/tg_triage/pipeline.py).

## 1. Bổ sung thông tin cho ticket đang có

Khi: người gửi reply thêm chi tiết/bằng chứng cùng sự cố. Áp cho ticket open, và ticket closed nếu chỉ là bằng chứng cũ (giữ closed, không reopen).

```markdown
### Bổ sung từ hội thoại Telegram
{{tóm tắt phần bổ sung}}

Phản hồi của người gửi:
> {{nội dung reply nguyên văn}}

Nguồn phản hồi: {{link tin Telegram}}
```

## 2. Xác nhận phạm vi từ người gửi

Khi: người gửi reply trực tiếp tin nguồn với câu rõ ràng kiểu "đây là ticket 2", cùng tác giả (TG-20).

```markdown
### Xác nhận phạm vi từ người gửi
{{mô tả tin nguồn}} thuộc phạm vi ticket {{số thứ tự}}.
Tin nguồn: {{link tin Telegram}}
Ảnh nguồn: {{link ảnh}}
Tài liệu nguồn: {{link tài liệu}}
```

## 3. Điều chỉnh phạm vi sau khi tách ticket

Khi: tách một ticket thành nhiều ticket (tối đa 5). Comment này đặt lên **ticket gốc** (TG-21). Không xóa, không đóng ticket gốc.

```markdown
### Điều chỉnh phạm vi sau phản hồi Telegram
Ticket này theo dõi yêu cầu thứ nhất: {{title yêu cầu 1}}
{{tóm tắt yêu cầu 1}}
ID tin nguồn: {{id1}}, {{id2}}
Cần làm rõ:
• {{câu hỏi, nếu có}}

Tin/ảnh chưa được gán, phạm vi chưa xác nhận (không mặc định thuộc ticket này):
• {{mô tả tin chưa gán}}
Ảnh chưa được gán: {{link ảnh}}
Tài liệu chưa được gán: {{link tài liệu}}
```

Bỏ đoạn "Cần làm rõ" và đoạn "chưa được gán" nếu không có.

## 4. Dòng nối cuối body của ticket tách mới (sibling)

Mỗi ticket tách mới dùng template theo loại (bug/task/feedback), rồi nối thêm ở cuối body:

```markdown

Ticket nguồn: https://github.com/{{owner/repo}}/issues/{{số ticket gốc}}
```

## 5. Dòng nối cuối body của ticket tái diễn

Khi: ticket cũ đã được xác nhận chạy tốt, sau đó lỗi lại. Tạo ticket **mới** theo template loại, nối ở cuối body:

```markdown

Lỗi tái diễn; ticket liên quan: https://github.com/{{owner/repo}}/issues/{{số ticket cũ}}
```

Khi ticket mới chỉ liên quan một ticket khác (không phải tái diễn):

```markdown

Ticket liên quan: https://github.com/{{owner/repo}}/issues/{{số ticket}}
```
