<!-- rule-file: facts · version 1 · status DRAFT · audience: model + code -->
# Dữ kiện model phải trích

File này là **hợp đồng** giữa bot và model: model chỉ trả các trường dưới đây, mỗi trường kèm `source_ids` (ID tin nguồn trong chủ đề). Model **không** trả kết luận (đủ rõ / hỏi / tạo ticket). Code dùng bảng `field_valid` và `generic_values` để quyết định trường nào được tính là CÓ.

## Bảng: fields

| Trường | Kiểu | Ý nghĩa | Ví dụ |
|---|---|---|---|
| `intent` | một trong `bug`, `task`, `feedback`, `question`, `chat` | Ý định của chủ đề | `bug` |
| `object` | chuỗi | Đối tượng cụ thể: màn hình / chức năng / nút / setting / thiết bị | "máy in POS (app + web)" |
| `observed` | chuỗi | Hành vi đang xảy ra | "không in được receipt" |
| `expected` | chuỗi | Kết quả mong muốn | "in được receipt" |
| `work` | chuỗi | Việc cần làm (task) | "đưa ảnh Nail cũ vào gallery" |
| `problem` | chuỗi | Vấn đề / nhu cầu (feedback) | "khó tìm giao dịch" |
| `proposal` | chuỗi | Đề xuất (feedback) | "thêm bộ lọc ngày" |
| `media_ids` | danh sách ID | Tin có ảnh/tài liệu thuộc chủ đề | `[843]` |

## Bảng: field_valid

Một trường chỉ được tính là **CÓ** khi thỏa **tất cả**:

| # | Điều kiện |
|---|---|
| V1 | Giá trị không rỗng |
| V2 | Có ít nhất một `source_id` thuộc chủ đề đang xét |
| V3 | Giá trị không nằm trong bảng `generic_values` của trường đó |

Model không chắc ⇒ để trống. Không đoán, không suy ra môi trường, nguyên nhân hay bước tái hiện.

## Bảng: generic_values

| Trường | Giá trị chung chung (không tính là CÓ) |
|---|---|
| `object` | "nút này", "cái này", "chỗ này", "nó", "app" (khi không nói chức năng nào) |
| `observed` | "lỗi", "bị lỗi", "không được", "có vấn đề" (khi không nói lỗi thế nào) |
| `work` | "xử lý", "check giúp", "làm giúp" (khi không nói làm gì) |

**Ngoại lệ:** nếu ảnh cùng chủ đề cho thấy rõ đối tượng/hành vi (heading panel, thông báo lỗi), model ghi giá trị cụ thể lấy từ ảnh; `source_ids` là tin ảnh.
