# NailHub — hướng dẫn agent tiếp nhận

Owner quy trình/data/state: **AlexChu**. Executor chỉ là runtime tạm thời. Hướng dẫn này áp dụng khi đọc/sửa module NailHub; không thay quyền hoặc quy trình harness của repo.

## Đọc trước khi xử lý

1. [Business DOCS hiện hành](../../../Docs/01-product/nailhub-postings-noissue_business_261013_v1.0.0.md), đặc biệt **Quy tắc áp dụng cho agent**, **Những quyết định chưa có đủ căn cứ**, **Báo cáo lỗi và gói chuẩn bị GitHub issue**. Các ID BR/BUG/D là tham chiếu chung; đọc toàn bộ ngữ cảnh, không hành động chỉ từ tên mã.
2. [Master và lịch sử triển khai](../../../Docs/00-master/nailhub-postings-noissue_master_261012_v1.0.0.md).
3. [Hướng dẫn NailHub](../../README.md): package độc lập, dịch vụ systemd, phiên Telegram riêng và cổng ghi. Mô tả tích hợp trong listener là lịch sử, đã gỡ khỏi `tg_triage`.

## Tránh lặp lại lỗi đã ghi nhận

- Phân biệt **quy tắc nghiệp vụ đã chốt**, **hành vi code chưa đúng**, **dữ liệu cần xác minh** và **kết quả ghi chưa rõ**. Không biến lỗi model thành bằng chứng bài sai.
- Áp dụng BR-01…BR-10 cho toàn nguồn trước AI: vai trò của từng khoản tiền, đầu cao lương/đầu thấp giá bán, kỳ tại khoản, tên/số liên hệ hợp lệ, địa chỉ cùng salon/chi nhánh. Không chỉ bổ sung regex hoặc whitelist các ID trong ma trận.
- Gợi ý model là đề xuất. Không ghi placeholder vào số điện thoại, tên nguồn crawl như BaoNail.com vào tên salon, hoặc khu vực vào thành phố. Riêng nhãn nailhub.ai được Owner cho phép ở BR-14 khi đã tìm tên thật mà chưa xác định được; đó là đơn vị đăng tin, không phải tên doanh nghiệp đã xác minh. Không đoán kỳ, lịch hoặc ZIP.
- Trích dẫn/vị trí và nguồn trường phải có thật. Chuẩn hóa tên tiểu bang, biểu diễn tiền hoặc lỗi chính tả chỉ khi có căn cứ; không sửa dữ liệu nguồn tùy ý để vượt gate.
- Đọc các D còn treo trước khi kết luận tự duyệt; thiếu quyết định chỉ chặn phần phụ thuộc, không ngăn chuẩn bị/sửa các BR đã được Owner chốt.
- BR-11: bắt buộc gọi Viết bằng AI theo lifecycle; sau đó không kiểm chất lượng/ý nghĩa hoặc tự biên tập toàn nội dung AI. Ngoại lệ BR-14 được Owner chốt: chỉ chỉnh cụm nhãn Nailhub.ai và lời đăng tin tương ứng ngôn ngữ/danh mục. Giữ kiểm tra identity/fingerprint/payload/readback/publication.
- BR-12: pause/readiness là vấn đề vận hành, không tự là lỗi dữ liệu. `uncertain` cần fresh website/checkpoint trước khi tiếp tục. Không mở khóa Approved/publictrue hoặc mass-requeue hold cũ.
- BR-13: tin quá45ngày bỏ qua theo Created trên **danh sách NailHub**; đúng45 vẫn được xử lý. Không dùng Created sentinel trong chi tiết/startDate/lastModified thay thế. Kiểm đúng ID/dòng và ngày trước model/AI, kiểm lại trước duyệt/xuất bản; giữ chứng cứ ngày ngoài fingerprint. Không DM lỗi dữ liệu cho tin chỉ bị bỏ vì quá hạn; giữ khóa kết quả cũ chưa rõ.
- BR-14 (Owner, vaultD-027): BaoNail.com không phải tên tiệm. Tìm tên thật có nguồn trước; tên trống hoặc chưa xác định dùng trườngsalon `nailhub.ai` làm nhãn đơn vị đăng tin. Body dùng Nailhub.ai + lời đăng tuyển/đăng sang nhượng theo ngôn ngữ bài, bỏ từ gọi Tiệm/Salon trước nhãn. Chỉ chỉnh cụm branding sauAI theo yêu cầu rõ này, không semanticgate hoặc biên tập phần khác. Giữ điều kiện địa chỉ/liên hệ và BR-13; không dùng nhà cho thuê làm salon.
- Khi sửa code sau này, chuyển ma trận49tin thành regression fixtures đã bỏ dữ liệu riêng; có test đủ đường đi extraction→validation, không chỉ đáp án model lý tưởng. Đối chiếu các acceptance chưa tick trong Business; offline PASS không chứng minh live PASS.
- Chẩn đoán DM phải tách giá trị đang lưu/đề xuất/nguồn/kết quả mong đợi, bước đã hoàn tất và việc cần làm. Không chỉ trả “model chưa xác nhận” hoặc “xem nhật ký”.
- Runtime là dịch vụ độc lập **tg-nailhub**; mã NailHub chỉ nằm ở `nailhub/src/nailhub` (listener `tg_triage` không chứa mã NailHub). Khi tích hợp release mới phải map đúng vào package này, kiểm hash/preimage và giữ Telegram client riêng cùng `model_lock` của dịch vụ. Không phục hồi tích hợp trong listener, không tạo Telegram instance dùng chung session.

## Trạng thái bản cập nhật 2026-10-14

Business v1.0.8 / Master v1.0.25 ghi trạng thái hiện hành. BR-13/14 và bản sửa nguồn/lương/địa điểm đã deploy có review: 738 tests, 114 tests độc lập (số liệu trước khi tách service; sau khi tách: listener 622, nailhub 227), 46 phản hồi model thực. Rerun 50 đã đóng: 30 hoàn tất (29 Draft được duyệt + một Approved được xuất bản), 16 quá hạn bỏ qua, một thiếu địa chỉ salon thật, ba khóa uncertain cũ. Fresh audit 50 và 116 dòng DB bảo vệ PASS; lịch quét đã bật lại. Không chạy lại tin hoàn tất hoặc mở ba khóa cũ vì tuổi tin. Chứng cứ riêng tư ở .context/nailhub-rerun-20261014; không đưa vào issue công khai. Không chạy song song Browser/profile. Markdown không tự đi vào model runtime; prompt/code đã triển khai và bằng chứng live là các lớp riêng.

GitHub issue chưa tạo; không tự tạo issue, commit, push hoặc deploy chỉ vì có checklist trong tài liệu. Thực hiện theo yêu cầu Owner và quyền đã được xác lập trong phiên.

Quyết định Owner bổ sung 2026-10-14: GLOW &3000 được xác nhận là3000/tuần; sang tiệm trà sữa/phở được chấp nhận theo BR-07; riêng Angel500/ngày×6=3000/tuần, bỏ8000thiếu kỳ trong tổ hợp đã xác nhận. Không tự mở rộng ngoại lệ tiền/lịch sang nguồn khác. Chuẩn hóa tên để vừa form phải giữ thương hiệu có thật và nguồn nguyên văn trong audit; không cắt tùy ý hoặc gán tên nhà xuất bản.
