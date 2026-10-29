# Quy tắc nạp rules vào bot và model

> **Trạng thái: DRAFT — chờ Owner duyệt.** Runtime **chưa** đọc thư mục này. File này quy định **cách** bot nạp, kiểm và dùng các file quy tắc; nội dung quy tắc nằm ở các file khác trong thư mục.
>
> **Nguyên tắc (Owner, 2026-10-29):** mọi quyết định của bot dựa trên file markdown trong thư mục này với trạng thái liệt kê cụ thể. **Model chỉ trích dữ kiện; code áp quy tắc.** Đổi hoặc nâng cấp model chỉ làm dữ kiện chính xác hơn hoặc nhanh hơn, không đổi kết quả của cùng một bộ dữ kiện.
>
> Nguồn nghiệp vụ: [Business DOCS](../../Docs/01-product/telegram-triage-noissue_business_261015_v1.0.0.md) giải thích **vì sao**; thư mục này là **bảng chạy thật**. Luồng tổng: [tài liệu luồng nghiệp vụ](../../Docs/01-product/telegram-triage-noissue_business-flow_261028_v1.0.0.md) (sequence đích, bước 0–6).

## 1. Danh mục file

| File | Nội dung | Ai dùng | Thứ tự nạp | Trạng thái |
|---|---|---|---|---|
| [states.md](states.md) | Danh sách đóng các trạng thái kết quả | Code | 1 | DRAFT |
| [facts.md](facts.md) | Dữ kiện model phải trích, điều kiện một trường được tính là CÓ, giá trị chung chung bị loại | **Model** + Code | 2 | DRAFT |
| [readiness.md](readiness.md) | Bảng quyết định "đủ rõ" R1–R8 (TG-08), thứ không bao giờ chặn, ảnh chưa xem, confidence | Code | 3 | DRAFT · 2 bảng chờ Owner |
| [questions.md](questions.md) | Hỏi gì khi thiếu, theo thứ tự nào, câu chữ lấy từ khối template nào | Code | 4 | DRAFT |
| [examples.md](examples.md) | Bộ ví dụ chuẩn X1–X10: dữ kiện → kết quả | Code (tự kiểm) + **Model** (chỉ cột tin + dữ kiện) | 5 | DRAFT |

Sẽ thêm theo từng lát (chưa soạn): `classification.md` (TG-07/09) · `topics.md` (TG-05/06) · `lifecycle.md` (Vòng đời ticket) · `priority.md`.

## 2. Định dạng mỗi file quy tắc

1. **Dòng đầu tiên** bắt buộc là header:
   `<!-- rule-file: <tên> · version <số> · status DRAFT|ACTIVE · audience: code | model + code -->`
   - `<tên>` trùng tên file (không đuôi `.md`).
   - `status: DRAFT` ⇒ runtime **bỏ qua** file; chỉ `ACTIVE` mới được nạp.
2. **Chỉ bảng được máy đọc.** Mỗi bảng nằm ngay dưới heading `## Bảng: <tên_bảng>`; hàng đầu là tên cột cố định. Văn xuôi, ghi chú, heading khác là hướng dẫn cho người, runtime bỏ qua.
3. Giá trị máy so khớp (mã trạng thái, tên trường, mã luật, tên khối template) viết trong backtick: `` `READY` ``, `` `object` ``.
4. Không viết nội dung người dùng hay dữ liệu thật vào file quy tắc (ví dụ dùng câu rút gọn, không số điện thoại/email/mật khẩu).

## 3. Trình tự nạp (bước 0 của luồng, lúc `run` và `check-config`)

```
 ┌─ đọc thư mục rules/ (source tree, nếu không có thì bản cài sys.prefix/share/tg-triage/rules)
 ├─ 1. states.md      → tập trạng thái hợp lệ
 ├─ 2. facts.md       → danh sách trường + kiểu + field_valid + generic_values
 ├─ 3. readiness.md   → bảng decision (chỉ được dùng trạng thái ở 1, trường ở 2)
 ├─ 4. questions.md   → ask_order (chỉ được dùng trường ở 2; khối template phải tồn tại)
 ├─ 5. examples.md    → chạy TỰ KIỂM: mỗi ví dụ, áp 3 lên cột "Dữ kiện" phải ra đúng cột "Kết quả"
 ├─ tính fingerprint SHA-256 (tên file + nội dung chuẩn hóa LF, theo thứ tự tên)
 └─ ghi log fingerprint; check-config in ra "rules": {"fingerprint": …}
```

Một bước lỗi ⇒ **dừng, không khởi động** (mã thoát 2), báo `<file>:<dòng>: <lý do>` — giống template TG-33. Không có bản dự phòng trong code, không đọc lại khi đang chạy (đổi rules = restart).

## 4. Kiểm tra lúc nạp

| # | Kiểm | Lỗi khi |
|---|---|---|
| K1 | Đủ file `ACTIVE` theo §1, header đúng định dạng, tên khớp | Thiếu file, sai header, hai file cùng tên |
| K2 | Mỗi file có đúng các bảng bắt buộc, đúng tên cột | Thiếu bảng, thừa/thiếu cột |
| K3 | Mã trạng thái trong `readiness.md` ⊆ `states.md` | Trạng thái lạ |
| K4 | Tên trường trong `readiness.md`, `questions.md`, `examples.md` ⊆ `facts.md` | Trường lạ |
| K5 | Mã luật (R1, R2…) không trùng; bảng decision có dòng cuối "Còn lại" | Trùng mã, thiếu dòng mặc định |
| K6 | Khối template trong `questions.md` có trong `templates/github-ticket/` (trừ dòng *(cần thêm)* khi file còn DRAFT) | Khối không tồn tại |
| K7 | Bảng đánh dấu **CHỜ OWNER CHỐT** không được có trong file `ACTIVE` | File ACTIVE còn quyết định mở |
| K8 | Tự kiểm `examples.md`: mọi ví dụ ra đúng kết quả | Bất kỳ ví dụ sai |

## 5. Phần nào gửi cho model, phần nào không

| Gửi cho model | Không bao giờ gửi cho model |
|---|---|
| `facts.md`: bảng `fields`, `field_valid`, `generic_values` (để model biết trích gì và khi nào để trống) | `states.md`, `readiness.md`, `questions.md` |
| `examples.md`: **chỉ** cột "Tin" và "Dữ kiện" làm ví dụ trích | Cột "Kết quả" và "Luật" của `examples.md` |

- Lý do: model không được biết bảng quyết định, để không "điền dữ kiện cho khớp kết quả" và để quyết định không đổi khi đổi model.
- Bot **sinh schema JSON** trả về của model từ bảng `fields` (đúng tên trường, đúng kiểu, mỗi trường có `source_ids`). Trường thừa ⇒ bỏ; trường thiếu ⇒ coi là trống; `source_ids` không thuộc chủ đề ⇒ trường bị coi là trống.
- Prompt model (`listener/prompts/`) chỉ chứa hướng dẫn trích dữ kiện chung; định nghĩa trường lấy từ `facts.md` lúc nạp, không chép tay vào prompt.

## 6. Code dùng các file thế nào (bước 3 của luồng)

```
dữ kiện model ──► lọc theo facts.field_valid + generic_values ──► chỉ còn trường CÓ
                                                                     │
                                       readiness.decision (dòng đầu khớp) ──► mã trạng thái (states)
                                                                     │
                       NEED_INFO ──► questions.ask_order ──► khối template ──► câu hỏi gửi group
                       READY     ──► chọn template ticket ──► adapter nền tảng
```

Kết quả mỗi chủ đề được ghi vào nhật ký quyết định kèm: mã luật đã khớp (vd `R4`), danh sách trường CÓ và `source_ids`, fingerprint rules. Nhờ vậy mọi quyết định truy ngược được về đúng dòng trong file.

## 7. Đổi model

- Đổi/nâng model **không** cần sửa thư mục này.
- Trước khi dùng model mới: chạy bộ trích dữ kiện trên các tin của `examples.md`; dữ kiện trích ra phải cho đúng cột "Kết quả" khi áp `readiness.md`. Sai ⇒ không đổi model (hoặc sửa prompt trích), **không** sửa quy tắc để chiều model.

## 8. Đổi quy tắc

1. Sửa file trên branch; cập nhật `version` trong header.
2. Thêm/sửa ví dụ trong `examples.md` cho mọi luật bị đổi.
3. Chạy test listener + tự kiểm rules; cập nhật Business DOCS (trace, changelog).
4. Owner duyệt PR. File chuyển `DRAFT → ACTIVE` chỉ khi Owner duyệt và không còn bảng **CHỜ OWNER CHỐT**.
5. Release → cài → `check-config` → so fingerprint → restart.

Không sửa file quy tắc trực tiếp trên máy chạy; không có thư mục override.

## 9. Đóng gói

`listener/pyproject.toml` thêm `"share/tg-triage/rules" = [các file .md trong thư mục này]`, giống prompts và templates. Bản cài tìm rules ở `sys.prefix/share/tg-triage/rules`.

## 10. Còn chờ Owner chốt trước khi ACTIVE

| Mục | File | Phương án |
|---|---|---|
| Ảnh model chưa xem (D7) | `readiness.md` · bảng `unseen_media` | A: không ảnh hưởng trạng thái, ghi chú · B: hỏi lại |
| Ngưỡng confidence 0.7 | `readiness.md` · bảng `confidence` | A: bỏ khỏi quyết định · B: giữ làm điều kiện bổ sung |
| 5 khối câu hỏi mới | `questions.md` → `group-replies.md` | Duyệt câu chữ khi triển khai |
