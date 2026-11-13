---
type: doc
title: "Source Mirror: Tham chiếu vận hành cho agent"
status: current
area: automation
created: 2026-11-01
owner: AlexChu
---

# Source Mirror: Tham chiếu vận hành cho agent

## Cập nhật 1099 — 10/11/2026

Owner quy định khung 12:00 giờ Việt Nam dành cho bài nail hoặc thuế liên bang Mỹ phù hợp ngành nail, đặc biệt 1099 khi nguồn hỗ trợ; giữ bài đã ghim và áp dụng quy trình dự phòng hiện hành. Đây là định hướng nghiệp vụ và biên tập. **Bổ sung chọn nguồn 1099 trong pipeline chưa deploy**: worker hiện chưa được xác nhận tự chọn hoặc tự đăng bài 1099, và thay Markdown không thay thế thay đổi code.

Với từng nhận định thuế, dùng đúng URL trang IRS gốc hỗ trợ nhận định và nêu năm thuế, đối tượng, điều kiện, ngoại lệ và phạm vi liên bang tương ứng. Đối chiếu ấn bản và hiệu lực; ngày truy cập, ngày sửa trang hoặc một năm xuất hiện trong tài liệu không tự chứng minh năm áp dụng. Không mặc định ngưỡng cũ hay đồng nhất 1099-NEC, 1099-MISC và 1099-K. Khi thiếu chứng cứ, bỏ nhận định hoặc giữ bài theo quy trình hiện hành; không bịa số liệu. Trang tham khảo: [Form 1099-NEC](https://www.irs.gov/forms-pubs/about-form-1099-nec), [information returns](https://www.irs.gov/businesses/small-businesses-self-employed/am-i-required-to-file-a-form-1099-or-other-information-return), [instructions](https://www.irs.gov/instructions/i1099mec); chỉ dẫn trang thực sự hỗ trợ nhận định.

Đã cập nhật Rule và Template trên THINKPAD dưới worker lock, có backup; loader `article_guidance.prepare()` dưới tài khoản service đã nạp cả hai file vào prompt thử. Worker active, lịch ghim không đổi tại lần kiểm tra. Không tạo bài hoặc triển khai code.


## Trạng thái so tóm tắt — 05/11/2026, trước 16:35 ICT

Chưa bật. Shadow bootstrap 132 = 122 baseline + 10 attempts local, 9 bài về sau Draft vẫn giữ; nguồn summary Admin cho 10 bài là unique slug+title inferred, summary có thể đã đổi. Không GET URL hay so body/ảnh. 30 fixture integration PASS; AGY 132 summaries FAIL sau 311,94s ở lô 5 (4 lô đầu pass; lỗi model unavailable, chưa rõ nguyên nhân). Qwen 3.5 9B local 3 synthetic pairs client timeout 65s, không có verdict; không dùng hai phép thử đơn lẻ khẳng định model luôn lỗi. Broker subprocess có thể còn chạy sau client timeout. Chuẩn bị history và comparator nằm trong `.context/summary-integration-live`, chưa đưa vào `/opt` worker. Ưu tiên tiếp theo: chẩn đoán broker/deadline, thử lô nhỏ và chất lượng, đối soát union history, test kết nối/ghi atomic; báo cáo rồi mới bật.

## Ưu tiên quy tắc mới — 05/11/2026, 14:15 ICT

Owner thay thế yêu cầu cũ: đối chiếu lịch sử local đã xác nhận đăng thành công (kể cả về sau đổi trạng thái hoặc URL), không quét kho bài chưa đăng, không HTTP/status check sau đăng, không so toàn thân/ảnh. Mỗi bài lưu chủ đề, đề tài, tiêu đề và tóm tắt ý chính; title ý nghĩa >=70/100 hoặc summary trùng → chọn bài khác; cùng chủ đề không tự trùng. Không tự đặt ngưỡng số cho summary. Chưa bật, đang xây/test; đừng nhầm snapshot Published 122 bài trước đó là toàn bộ lịch sử. Cần ghi history bền sau published, recovery khi gián đoạn và đối soát Create lần hiện tại chưa rõ. Xem [hướng dẫn bộ lọc](nailhub-source-mirror-noissue_filter-guide_261101_v1.0.0.md).

## Bốn phần chống trùng — 05/11/2026, khoảng 12:55 ICT

Chỉ candidate `.context/dedup-unified-candidate` và staging THINKPAD, **chưa bật**. Identity official/reissue/migration: 58 tests fixture đạt; comparison 36 legacy baseline 5 fail/7 error và candidate với 4 expectation điều chỉnh cũng 5 fail/7 error (chưa triage). Official closure 36 fixture + Chromium tracker giả; CDC attempt13 released có một intent ảnh legacy thiếu receipt, không xóa và không coi upload đã đóng. Semantic body VI/EN: 4-pair AGY shadow; 1 synthetic long batch 21,79 giây, 122 bài cần khoảng 31 batch lúc cold chưa benchmark. Browser fixture real scheduler/SQLite/Chromium, UI/Admin/AGY/CDN/Create giả, duplicate skip và fake published PASS, không có POST PROD. Legacy IDs thiếu anchor, response Create shape và raw hash không tái xác minh độc lập, các test lỗi và hiệu năng full là những điểm cần giải quyết trước bật. Xem [hướng dẫn bộ lọc](nailhub-source-mirror-noissue_filter-guide_261101_v1.0.0.md).

## Kiểm thử tích hợp 05/11/2026, 11:33 ICT

Candidate cô lập `.context/dedup-unified-candidate` và staging THINKPAD `/tmp/nailhub-dedup-complete-Kn2qRTd9` chưa triển khai: 55/55 kiểm thử mô phỏng đạt; migration SQLite thử trên bản sao giữ 316 articles/36 attempts và rollback, nhưng chưa nối vào `Store`. Quét Admin 3 lượt + HTTP 122 bài mất 65,8 giây, AGY 122 tiêu đề batch 20 mất 137,73 giây (một lần thực, không Create); batch 40 trước đó lỗi chưa rõ nguyên nhân. Browser fixture đã phát hiện và thử sửa giới hạn ContextVar route. Blocker: official autostage/restage ID nguồn, official upload closure, reissue sau unpublish và clone/requeue chưa wired; chưa E2E, không bật worker. Xem [hướng dẫn bộ lọc](nailhub-source-mirror-noissue_filter-guide_261101_v1.0.0.md).

## Quy tắc mới của Owner — 05/11/2026, 10:50 ICT

Chủ đề được trùng; tiêu đề trùng ý nghĩa từ 70/100 hoặc nội dung trùng bài **Published** phải chuyển bài khác; Draft/Archived/bài trong kho không là tập đối chiếu. Kho local `/var/lib/nailhub-source-mirror/published-registry/` đã backfill một lần lúc 10:58:13 VN 05/11/2026: 122 bài, 122 URL suy ra từ slug chưa kiểm HTTP từng URL, 122 ngày, 97 URL nguồn. **Chưa có đồng bộ tự động**, phải refresh đầy đủ/ổn định trước khi dùng; không coi cache cũ là bằng chứng khi đồng bộ lỗi. Không lưu secrets. 70 là điểm mô hình ước lượng cần hiệu chuẩn, không phải xác suất hay thước đo khách quan. **Chưa bật:** `snapshot_identity`, title exact và official source cũ còn chặn; không hứa rule mới hoạt động cho đến khi thay thế từng nhánh, kiểm migration, thử đối soát và kiểm thử worker. Xem [hướng dẫn bộ lọc](nailhub-source-mirror-noissue_filter-guide_261101_v1.0.0.md).

## Cập nhật 04/11/2026, 12:45 ICT — Factual HOLD và correction

- `original_business.validate_factual_review` chỉ chấp nhận `source_quote` rỗng khi được gọi cho một MQM HOLD hợp lệ có issue; quote không rỗng vẫn phải khớp nguyên văn nguồn. PASS vẫn yêu cầu đầy đủ quote đúng cho từng câu, sau đó qua adjudication độc lập. Correction vẫn giới hạn một lần và không tạo PASS từ HOLD.
- Đã cài file sau review độc lập, giữ worker lock trong stop/start; 6 test tổng hợp đạt trước và sau cài, không có `intent`/`unknown`. Bản fraud thật đã vào correction, nhưng review kế tiếp trả PASS với hai quote không có nguyên văn trong SBA; gate strict giữ bài ở **HOLD**, chưa stage/pin. Backup `/opt/nailhub-source-mirror/backups/factual-hold-fix-20261104-123838`.

## Cập nhật 04/11/2026 — Đăng bù 12:00 và dự phòng

- **12:00 đã đăng bù:** [Hướng dẫn vẽ móng sọc negative space với bộ sưu tập Gellux Festival Edit](https://nailhub.ai/blog/huong-dan-ve-mong-soc-negative-space-voi-bo-suu-tap-gellux-festival-edit), attempt 32, trạng thái `published`; kiểm độc lập trang khách HTTP 200, có bốn ảnh trong thân và một liên kết Scratch. Bản dịch giữ tên màu và thời gian theo nguồn. Bản kiểm MQM mới PASS sau khi xác thực lại source và provenance của generation envelope; không dùng audit cũ như PASS.
- **Vì sao cần đăng bù:** bài Abstract được gắn 12:00 bị giữ vì chính nguồn có bước 1–5 rồi đánh số 5 lần nữa; snapshot nguồn không bị sửa. Bài Autumn được thử và giữ do MQM; bài Stripe ban đầu bị giữ sai do mạo từ tiếng Anh bị coi là phần tên sản phẩm. Validator được sửa hẹp, test và review trước khi chạy MQM mới. `slot_runs` 12:00 được đối soát từ `held` thành `published` theo attempt 32 trong transaction dưới worker lock; lịch sử attempt và notice event được giữ nguyên, không gửi thông báo thủ công. Evidence đối soát nằm trong báo cáo vận hành.
- **Dự phòng bốn khung:** luồng gốc của khung chưa gắn bài vẫn chạy trước, bao gồm official và AI original. Nếu chỉ bị `QualityHold` trước khi tạo attempt, còn thời gian và không có slot đã reserve hoặc `intent`/`unknown`, hệ thống có thể thử tối đa ba ứng viên hợp khung chưa thử, không lấy bài đã gắn lịch. Mỗi lần vẫn qua quota, nguồn, trùng lặp và gate hiện hữu; dừng trước giờ kế tiếp 5 phút. Dự phòng không áp dụng khi kết quả đăng có thể chưa rõ và không bảo đảm mọi slot có bài. Candidate có 29 test đạt; chưa xác nhận fallback tự động đi hết publish trong slot thực.
- **Tên màu và mùa:** phân loại bài Tartan nay không coi riêng cụm tên sản phẩm chứa “Pumpkin Juice” trong nội dung là Halloween; từ khóa mùa trong tiêu đề, URL, alt hoặc chỗ khác vẫn được kiểm. Sáu test candidate đạt; snapshot Tartan thực được kiểm lại thành `other`, hợp 12:00, vẫn `ready`; không đăng thử bài này. Khung 16:00 vẫn cần kiểm trạng thái thực tế sau khi chạy, không giả định đã đăng.


## Lịch hiện hành — 04/11/2026

Theo giờ Việt Nam, **08:00, 16:00 và 20:00** dành cho kinh doanh, vận hành và marketing; **12:00** dành cho mẫu nail. Đây là chính sách chọn chủ đề, không bảo đảm mỗi slot có bài được đăng: ứng viên vẫn phải đạt kiểm tra nội dung, nguồn, trùng lặp, quota và thời hạn. Các mốc phân bổ khác trong những cập nhật cũ bên dưới là lịch sử tại thời điểm ghi, không phải lịch hiện hành.


## Cập nhật 04/11/2026, 10:50 ICT — Ảnh minh họa nội dung bài AI

- **Contract:** với bài AI original có các mục H2 phù hợp, lập kế hoạch 3–5 ảnh từ tiêu đề bài, H2 và ngữ cảnh đoạn; ảnh là minh họa chung, không hàm ý nguồn chứng thực. Giữ liên kết và credit nguồn nội dung, không thêm nhãn AI công khai; provenance và evidence được lưu nội bộ. Bài thiếu cấu trúc phù hợp hoặc ảnh thất bại có thể tiếp tục với ít ảnh hơn hoặc không ảnh. Prompt không yêu cầu ảnh chứa số liệu tài chính, kết luận pháp lý, điều trị y khoa, chữ hoặc logo giả.
- **Luồng và giới hạn:** tạo ảnh trước reservation qua `nailhub-body-image.socket` / `nailhub-body-image@.service`; preupload qua `nailhub-body-preupload.socket` / `nailhub-body-preupload@.service`. Assets và evidence nằm trong `articles/<article_id>/localization/<revision>/generated-body-*` dưới data root `/var/lib/nailhub-source-mirror`; ảnh bìa và gallery có contract riêng. Budget tối đa 360 giây tổng, 65 giây mỗi lần, và bị chặn theo thời gian còn lại của slot sau khi dành 80 giây cho uploader cùng 450 giây cho xuất bản. Ảnh không hợp lệ, timeout hay upload lỗi được omit riêng, giữ journal/evidence; không giả thành công. Nếu phải mở lại form, tải lại bìa bắt buộc có thể thất bại và giữ bài trước Create. Chưa chứng minh hủy tác vụ provider từ xa.
- **Trạng thái và kiểm chứng:** rollout guarded đã đối chiếu baseline, giữ worker lock qua restart; sau nhả lock hơn 60 giây worker và hai socket active, 15/15 file khớp candidate, không có `intent`/`unknown`. Policy `/etc/nailhub-source-mirror-body-image/policy.json` hết hạn kỹ thuật 04/11/2031 00:00 UTC; policy lỗi/hết hạn khiến ảnh được omit. Body và cover dùng chung UID/subscription trust boundary. Backup: `/var/backups/nailhub-body-rollout-20261104T035001Z`.
- **Giới hạn bằng chứng:** 60 lượt test tự động đạt (có test kế thừa lặp). Canary dùng DB/snapshot bản sao, bỏ qua dedup chỉ trong fixture bài đã đăng; producer và uploader thực tạo/bind 1 ảnh, renderer có ảnh ở cả VI/EN, editor dừng trước Create. Budget canary 65 giây nên không chứng minh đủ 3–5 ảnh; media thử còn trên CDN. Chưa xác nhận bài scheduled thật được Create, Published và đọc lại công khai có ảnh inline. Khi có lượt phù hợp, kiểm riêng attempt, trang admin và phiên khách, số ảnh thực, nguồn và nội dung; không coi smoke hoặc media upload là bằng chứng bài đã đăng.


## Cập nhật 03/11/2026, khoảng 15:45 ICT

- **Sửa bản bảo mật đang HOLD:** không sửa snapshot hay evidence đã ký của `190ea45c…`. Một bản nháp thay thế đã được tạo trong thư mục báo cáo riêng, giữ nguồn SBA cùng hash; model factual và adjudicator PASS, reviewer đối chiếu 33/33 quote với nguồn. Tuy vậy, reviewer vẫn giữ bài để sửa: câu về EMV có thể bị hiểu là bảo đảm trách nhiệm pháp lý; ẩn SSID có thể tạo cảm giác đủ an toàn; và nhận định nhân viên là đường dẫn chính cần quy rõ nguồn hoặc bỏ. PASS của model không tự giải quyết các điểm này; chưa có bản Việt, ảnh bìa hay bài đăng được xác nhận cho revision này.
- Đã triển khai prompt yêu cầu giữ điều kiện của nguồn, không biến biện pháp giảm rủi ro thành bảo đảm tuyệt đối; hướng dẫn Việt hóa phân biệt mạng Wi‑Fi ẩn và ẩn danh, yêu cầu hashtag có nghĩa và liên quan. Đây là chỉ dẫn model, không thay cho kiểm tra nguồn/MQM. Có 17 test hồi quy và 2 test prompt đạt; backup `/opt/nailhub-source-mirror/backups/accuracy-prompt-20261103`. Worker, broker và timer đã được hậu kiểm.
- Một lượt sửa thêm đã tự dừng trước gọi model/ghi DB vì còn dưới 5 phút tới cutoff 15:50; không reserve/Create. Đến 15:45, bài bù 12:00 vẫn chưa đăng và `190ea45c…` vẫn held. Khung 16:00 giữ lịch cũ; phải kiểm tra trạng thái thực tế sau giờ chạy, không giả định đã đăng.


## Cập nhật 03/11/2026, khoảng 15:20 ICT

- **Bố cục bài mới:** luồng tạo bài gốc yêu cầu các mục với tiêu đề, đoạn văn ngắn và ít nhất một danh sách có ích; hệ thống tạo HTML `h2`, `p`, `ul`, `li`. Mỗi tiêu đề, câu và ý danh sách vẫn phải được đối chiếu nguồn và qua MQM; bản cũ vẫn được xác minh bằng schema cũ. Prompt Việt hóa yêu cầu tiêu đề, mô tả và hashtag phù hợp nội dung, tự nhiên, tránh nhồi từ khóa hay hứa thứ hạng. Prompt mới yêu cầu nêu bối cảnh nguồn một lần, văn phong trực tiếp, hạn chế lặp disclaimer/câu hỏi chung chung; link nguồn vẫn giữ, provenance AI tiếp tục lưu nội bộ.
- **Kiểm chứng và giới hạn:** 17 test chọn lọc đạt; canary trên trình soạn thảo thật chặn mọi request ghi cho thấy H2/P/UL/LI sau paste khớp cấu trúc trước paste. Chưa chứng minh bài gốc có cấu trúc mới đi hết Create → published → đọc lại công khai. Prompt là chỉ dẫn cho model, không bảo đảm mọi bài đều hay hay đúng. Backup deploy gần nhất: `/opt/nailhub-source-mirror/backups/layout-wording-20261103` (backup bố cục trước đó: `layout-20261103`).
- **Bài bù 12:00 chưa đăng:** slot 12 có 3 claim HOLD; các ứng viên location/marketing cũ bị kiểm tra trùng ngữ nghĩa giữ lại. Một draft continuity bị giữ vì các gợi ý không có quote hỗ trợ; draft cyber đầu bị dừng trước reserve khi reviewer phát hiện bỏ điều kiện “nếu có thể” của nguồn SBA. Đã lập proof đối soát riêng `reports/catchup-20261103-1200-cyber-reconciliation.json`: workflow đó không reserve/Create theo bằng chứng DB, khóa và journal; không suy ra hành vi của writer độc lập.
- Một bản cyber khác được chuẩn bị **held** trong transaction để không lộ `ready` khi process lỗi. Review độc lập chỉ ra nhiều câu diễn đạt quá tuyệt đối; MQM bản Việt HOLD do dịch “hidden” thành “ẩn danh” và một hashtag không liên quan. Chưa có candidate được phép đăng bù, chưa reserve/Create. Không nới kiểm tra nguồn, trùng, quota, unknown hoặc deadline để ép đăng.
- **Trạng thái 15:20:** ngày 03/11 có một bài 08:00 published; không có `intent`/`unknown`, worker, hai broker và hai timer đang hoạt động. Lịch 16:00 không đổi; chưa thể cam kết bài sẽ đăng. Hàng đợi và kết quả phải được kiểm tra lại tại thời điểm chạy.


## Cập nhật vận hành ngày 02/11/2026, khoảng 08:15 ICT

- **Hiển thị công khai:** bản chạy mới đã bỏ nhãn cho biết bài hoặc ảnh bìa do AI/Antigravity tạo khỏi footer và nội dung công khai. Link nguồn thực tế vẫn giữ; không gán sai tác giả con người. Provenance, credit, policy và evidence ảnh vẫn lưu nội bộ. Các bài đã đăng trước khi thay đổi không được sửa. Nội dung do mô hình tự sinh vẫn phải được kiểm tra; thay đổi này không chứng minh mọi bài về sau chắc chắn không có chữ AI.
- Với bài nguồn chính thức, payload đã staging theo cách hiển thị cũ được kiểm tra tương thích **trước khi reserve**; nếu không khớp builder mới thì bỏ qua để staging lại, giữ nguyên evidence cũ. Không sửa trực tiếp payload/evidence đã ký.
- **08:00, 02/11:** attempt 20 đã `published`: [Mẫu móng milky glitter ombré lấp lánh](https://nailhub.ai/blog/mau-mong-milky-glitter-ombre-lap-lanh). Kiểm tra browser chỉ đọc trên trang công khai trả HTTP 200, không thấy các chuỗi nhãn AI/Antigravity đã kiểm, và link **Scratch Magazine** dẫn đến nguồn gốc. Lượt chuẩn bị trước đó HOLD do bản dịch lặp từ; worker sáng đã tạo bản mới và đăng thành công.
- **12:00 / 16:00 / 20:00:** ba lượt chuẩn bị nguồn kinh doanh riêng trước giờ đăng đều HOLD, chưa có bài nào được xác nhận sẵn sàng qua toàn bộ bước kiểm tra. SBA Manage: review cuối HOLD 2 major do trích dẫn chưa hỗ trợ hai nhận định; SBA Grow: replay với nguồn fetch sau đó báo một quote không khớp nguyên văn, có ký tự thay thế; California: review cuối HOLD 3 major/8 minor về hỗ trợ và phạm vi. Kết quả này không có nghĩa là ba khung giờ đã đăng hoặc chắc chắn sẽ thất bại; worker vẫn thử theo lịch và kiểm tra lại nguồn, MQM, trùng lặp, quota trước Create.
- Lần hậu kiểm có worker, hai broker ảnh và timer hoạt động, không có attempt `intent`/`unknown`. Triển khai có backup `/opt/nailhub-source-mirror/backups/public-attribution-20261101`; test stage gồm 13 kiểm tra payload/footer và một kiểm tra payload cũ bị dừng trước reserve. Chưa kiểm chứng đầu-cuối bài official có ảnh sinh mới sau thay đổi. Các dòng cũ bên dưới mô tả lịch sử và được cập nhật này ưu tiên khi khác nhau.


**Snapshot:** 01/11/2026, khoảng 22:20 giờ Việt Nam. **Đối tượng:** agent tiếp nhận, reviewer, người vận hành. Đây là tài liệu tham chiếu của AgentBuilder; không phải `AGENTS.md`, luật harness hoặc bằng chứng trạng thái runtime hiện tại sau thời điểm trên.

Đọc [Master](../nailhub-source-mirror-noissue_master_261028_v1.0.0.md) và [hướng dẫn bộ lọc nghiệp vụ](nailhub-source-mirror-noissue_filter-guide_261101_v1.0.0.md) trước khi kết luận. Tài liệu này bổ sung đường dẫn và lệnh điều tra; **hành vi thực tế lấy từ code/config và evidence đang chạy trên THINKPAD**, không suy từ nhánh local, test cũ hoặc đề xuất. Áp dụng chỉ dẫn của user và quy tắc repo/harness có hiệu lực; không chép secret vào log, doc, command history hoặc phản hồi.

## 1. Nguồn chân lý và thứ tự điều tra

1. Xác định máy/giờ, trạng thái unit, timer và candidate/attempt bằng thao tác chỉ đọc. Xem các event, phase và bằng chứng có timestamp; cấu hình có thể đổi sau snapshot này.
2. Đọc code dưới `/opt/nailhub-source-mirror/source_mirror/` và cấu hình/policy liên quan dưới `/etc/nailhub-source-mirror/` hoặc `/etc/nailhub-source-mirror-generated/`. Không in toàn bộ cấu hình nếu có nguy cơ lộ secret.
3. Đối chiếu `/var/lib/nailhub-source-mirror/state.sqlite3`, log systemd, thư mục bài, receipt/guard và phản hồi model đã lưu. DB lịch sử và thông báo slot có thể khác trạng thái attempt hiện tại.
4. Phân biệt **quan sát**, **diễn giải**, **chưa biết** và **đề xuất**. Không dựa vào tên exception chung để suy ra nguyên nhân cụ thể. Đọc evidence thô theo quyền cần thiết và chỉ trích phần cần giải thích.
5. Khi yêu cầu là điều tra/plan, không sửa DB/code/policy, gửi thông báo, restart service, tạo bài hoặc chạy retry. Nếu user yêu cầu thay đổi, làm theo phạm vi ủy quyền và quy trình review tương ứng; không tự bỏ gate.

## 2. Bản đồ runtime

| Mục | Đường dẫn/đơn vị trên THINKPAD | Công dụng |
|---|---|---|
| Worker | `nailhub-source-mirror.service`; `/opt/nailhub-source-mirror/source_mirror/__main__.py` | Khởi động slot, crawl, lọc, reservation và xuất bản. |
| Phân loại | `slot_policy.py`, `topics.py` | Scheduled dùng `slot_policy` khi chọn và kiểm lại; `topics.priority_slots` cũ vẫn được validate nhưng không quyết định thứ tự scheduled; manual không slot dùng fallback luxury. |
| Nguồn | `crawler.py`, `snapshot.py`, `official_sources.py`, `official_autostage.py`, `official_scheduler.py` | Scratch, snapshot, intake và nhánh official; intake rộng hơn policy auto-publish. |
| Bài gốc | `original_business.py`, `original_duplicate_scan.py` | Tạo có nguồn, kiểm claim/quote, một vòng sửa có giới hạn và kiểm trùng. |
| Chất lượng | `localization.py`, `official_*` liên quan | Schema, glossary, số/phủ định, Việt hóa, MQM và ràng buộc nguồn. |
| Ảnh và editor | `scratch_cover.py`, `scratch_cover_ipc.py`, `cover_ipc_common.py`, `scratch_generated_policy.py`, `scratch_cdn_readback.py`, `scratch_native_guard.py`, `publisher.py` | Bìa AI, provenance, HTTP 200, upload/native guard, Create/readback. |
| Lịch / thông báo | `slot_notifications.py`, `telegram_queue_worker.py`; `nailhub-source-mirror-autostage.timer`, `nailhub-source-mirror-official.timer` | Claim/slot event bền vững; outbox và timer. Thông báo held thường chỉ có lý do chung. |
| State và bài | `/var/lib/nailhub-source-mirror/state.sqlite3`; `articles/<id>/`; `reports/` | SQLite, snapshot, output, receipt và nhật ký evidence. Không khôi phục DB cũ chỉ vì rollback code. |
| Evidence AI gốc | `reports/original-generation/<YYYY-MM-DD>/<slot>-<source>-<key>/` | Từng envelope response/error/timeout; xem thứ tự file và thời điểm. |
| Backup | `/opt/nailhub-source-mirror/backups/original-v2-20261101` | Backup trước deploy original; không tự dùng backup này để ghi đè state hiện tại. |

Dịch vụ model `nailhub-source-mirror-agy.service`; bìa Scratch/original `nailhub-source-mirror-scratch-cover.service`; bìa official `nailhub-source-mirror-official-cover.service`. Khóa dùng chung: `/var/lib/nailhub-source-mirror/locks/worker.lock`. Tên node và OS user không thay đổi owner Harness: **AlexChu**.

## 3. Hợp đồng hiện hành cần giữ

| Phạm vi | Hành vi đã cài / giới hạn |
|---|---|
| Giờ và quota | `Asia/Ho_Chi_Minh`; 08:00 nail, 12:00/16:00/20:00 business; workflow tối đa bốn Published/ngày. Không tự cộng bài độc lập vào quota. Slot claim tối đa ba lần. Manual URL không có slot đi theo đường riêng nhưng vẫn chịu các kiểm tra áp dụng. |
| Nguồn thường và AI | Xét official/source candidates trước; nếu thiếu bài phù hợp ở ba giờ business và đủ thời gian thì thử bài gốc AGY. Đây là fallback, không tự chạy original ở 08:00. Scratch crawler có giới hạn media trước download. |
| Official | Intake có sáu URL CDC/NIOSH, IRS, SBA; tại snapshot policy auto-publish/autostage bật CDC nail technicians, IRS tip reporting, SBA business plan. Trạng thái/expiry phải đọc lại khi tiếp nhận. |
| Original và nguồn | Cố định SBA Launch/Manage/Grow và California Board FAQ, ưu tiên xoay theo ngày/slot. Giữ URL thật và provenance AI nội bộ; công khai ghi nguồn tham khảo; không gán Scratch hay cơ quan nguồn làm tác giả. California chỉ áp dụng California. |
| Dữ kiện / MQM | Đối chiếu title và từng câu với quote nguyên văn, reviewer và adjudicator AI mới; kiểm lại hash/tình trạng nguồn. Đúng quote không tự chứng minh entailment; model review có xác suất sai. Một vòng sửa có giới hạn khi HOLD đủ schema/evidence. Localization giữ cấu trúc, số, negation, glossary và zero MQM issues/uncertainties. |
| JSON và regex | Original factual/adjudicator, duplicate và localization review nhận một fence toàn bộ hoặc JSON thô; schema và HOLD vẫn giữ. Generation original đầu vẫn strict. SBA xét acquire/buy/purchase/take over theo đối tượng business/cơ sở, heuristic chưa bao phủ mọi diễn đạt; các chặn pháp lý/technical khác vẫn giữ. |
| Media | Thiếu/broken cover có thể dùng ảnh AI từ title/summary cuối theo policy ràng buộc; original và Scratch có source ID riêng. Ảnh body lỗi có thể bỏ cùng caption theo nhánh hỗ trợ. Kiểm CDN/thumbnail sau upload là **HTTPS HTTP 200 only**; không decode/so sánh nội dung CDN. Kiểm bytes gốc, receipt, host/DNS/TLS là các guard riêng. HTTP 200 không chứng minh ảnh đúng. |
| Trùng và Create | Original quét admin hai lần ổn định, so title và semantic, lặp trước Create; `uncertain` giữ. Không nguyên tử với tác giả độc lập. Tất cả dùng reservation/DB chung; UNKNOWN chặn retry tạo bài. |
| Deadline | Nhánh original có budget trước tạo, reservation và guard; timeout trước tạo không được suy thành Create đã gửi hoặc không gửi nếu chưa đọc phase/evidence. Một timeout sau gửi không cho phép retry mù. |

Chủ đề, schema và quy tắc chi tiết được giải thích trong guide nghiệp vụ. User muốn ưu tiên nội dung kinh doanh nail; không dùng bài hướng dẫn kỹ thuật nail thuần để lấp ba slot business.

## 4. Kiểm tra chỉ đọc trên THINKPAD

Các lệnh giả định đã có quyền truy cập hợp lệ; chạy trong shell THINKPAD, không đặt mật khẩu/token vào lệnh. Không dùng các lệnh này để gửi Telegram hoặc thao tác editor. `journalctl` có thể cần quyền đọc log; lọc trước khi chia sẻ vì log/evidence có thể chứa nội dung nhạy cảm.

```bash
date '+%F %T %Z'
systemctl is-active nailhub-source-mirror.service nailhub-source-mirror-agy.service nailhub-source-mirror-scratch-cover.service nailhub-source-mirror-official-cover.service
systemctl list-timers --all --no-pager | grep nailhub-source-mirror
systemctl show nailhub-source-mirror-autostage.service -p ActiveState -p SubState -p Result -p ExecMainStatus
journalctl -u nailhub-source-mirror.service --since '2026-11-01 15:50:00' --until '2026-11-01 20:20:00' --no-pager -o short-iso
journalctl -u nailhub-source-mirror-autostage.service --since '2026-11-01 20:00:00' --no-pager -o short-iso
```

Query SQLite dưới đây mở URI `mode=ro` và `query_only`, không gọi CLI `status` có thể kèm side effect. Chỉnh **ngày trong code** theo ca cần điều tra; một số bảng chỉ có khi feature đã khởi tạo. Không trích `receipt_json` vào báo cáo nếu không cần.

```bash
python3 - <<'PY'
import sqlite3
c = sqlite3.connect('file:/var/lib/nailhub-source-mirror/state.sqlite3?mode=ro', uri=True)
c.execute('PRAGMA query_only=ON')
day = '2026-11-01'
queries = [
    ('slots', 'SELECT day,slot,state FROM slot_runs WHERE day=? ORDER BY slot', (day,)),
    ('claims', 'SELECT day,slot,count FROM slot_attempts WHERE day=? ORDER BY slot', (day,)),
    ('attempts', 'SELECT id,article_id,state,reserved_day,published_day,target_url FROM attempts WHERE reserved_day=? ORDER BY id', (day,)),
    ('unresolved', "SELECT id,article_id,state,reserved_day FROM attempts WHERE state IN ('intent','unknown') ORDER BY id", ()),
    ('phases', 'SELECT a.id,p.slot,p.phase,a.state FROM attempts a LEFT JOIN attempt_phases p ON p.attempt_id=a.id WHERE a.reserved_day=? ORDER BY a.id', (day,)),
]
for label, sql, args in queries:
    print(label, c.execute(sql, args).fetchall())
c.close()
PY
```

Đường dẫn `reports/attempt-<id>/` và `reports/original-generation/<ngày>/` để kiểm bằng chứng. Với URL/title, kiểm trên admin/public bằng phương thức chỉ đọc khi có quyền; tìm không thấy theo title không chứng minh không tồn tại mọi biến thể do tác giả độc lập tạo.

## 5. Nguyên tắc đối soát UNKNOWN và tránh trùng

1. Xác định chính xác attempt ID, source ID, phase và thời điểm; phân biệt lỗi trước upload, sau upload nhưng trước Create, sau khi request có thể đã gửi, và sau đọc lại. `slot_runs.unknown`/notification là lịch sử slot, không thay cho trạng thái attempt hiện tại.
2. Thu thập guard (`allowed` và reason), journal upload/receipt, request/response nếu có, title VI/EN, admin/public readback và đối chiếu người viết độc lập. Bảo toàn log, hash, source và bằng chứng; không lộ credential hoặc dữ liệu thừa.
3. **Không retry Create hoặc chuyển unknown sang released chỉ vì timeout, không thấy một title, hay model bảo không trùng.** Chỉ reconcile theo quy trình đã được user ủy quyền khi bằng chứng đủ xác định kết quả, có audit và dùng cùng DB/khóa/quota; nếu mơ hồ giữ UNKNOWN.
4. Nếu xác nhận Published, lưu URL đúng và đối chiếu quota/ngày Việt Nam. Nếu chứng minh Create chưa thể gửi, chỉ xử lý slot/retry theo trạng thái và chính sách thực tế; không tự bịa slot hoặc tạo DB song song. Khi có kết quả mơ hồ, báo rõ giới hạn.

## 6. Case đã quan sát: ngày 01/11/2026

| Lượt | Evidence | Kết luận |
|---|---|---|
| 08:00 | Attempt 18 sáu upload receipt; guard `allowed=false`, OSError và phase `pre_create`; đã reconcile `released`. | Không còn unresolved attempt tại snapshot 21:16; event/slot 08 lịch sử vẫn có `unknown`. Không tự đăng bù. |
| 12:00 | Ba claim held, ứng viên nail-art không phù hợp slot business. | Không có attempt đăng thành công 12:00 trong state workflow. |
| 16:00 SBA Manage | `1600-cc364db673741bc1-ce941032d7c31213/01.json`; đầu bài về quản lý salon, chứa acquire/purchase thiết bị/dịch vụ. | `excluded acquisition or technical topic`: regex tại thời điểm 16:00 quá rộng với ngữ cảnh này. Không chứng minh các gate sau sẽ PASS. |
| 16:00 SBA Grow | `1600-638a69ef516689d1-53cc25eacec0a2d1/01..03.json`; factual review PASS, adjudicator fenced JSON có verdict PASS. | Consumer strict báo `invalid model JSON`; lỗi định dạng, chưa qua localization/đăng. |
| 16:00 California | `1600-6f5f272fce89c2ca-b309e2c5a741e498/01..03.json`; adjudicator fenced JSON chứa HOLD 5 major, 2 minor. | Có vấn đề factual/quote; ví dụ thời lượng thi 90 phút không nằm trong quote gắn tương ứng. Không biến HOLD thành PASS bằng cách bỏ fence. |
| 20:00 | SBA Grow fenced HOLD; California fenced PASS ở reviewer đầu nhưng chưa có adjudicator; SBA Launch sau một revision qua review và localization, article ID `0b80b7b276b138c7533140aefec513cf56d7c740fdcadcf2ab9124965fa092ee` còn `ready`. | Bài SBA Launch dừng `original duplicate check held: QualityHold`. Không có reason chi tiết trong log: **chưa biết trùng thật hay lỗi khác**. Không có attempt Create 20:00 trong DB workflow. |

Các audit path trên có gốc `/var/lib/nailhub-source-mirror/reports/original-generation/2026-11-01/`. `candidate held: Error`, `ScheduleUnavailable` và `no article passed localization or upload quality gate` là các nhãn tổng quát; không biến chúng thành chẩn đoán cụ thể khi thiếu evidence.

## 7. Đề xuất chưa triển khai

Đã triển khai fenced JSON hẹp cho original review/duplicate, regex mua theo ngữ cảnh, audit duplicate và route build; xem mục cập nhật bên dưới. Còn plan chưa triển khai: xem xét source metadata/jurisdiction và nhiều quote; tiếp tục test ngữ cảnh/negation/synonym và chủ đề. Bỏ ảnh dư do giới hạn 10 phải được xác minh khả năng editor và phạm vi user cho phép. Giữ HTTP 200-only, reservation/quota, nguồn chính thống, không giả định kết quả sau Create; thay đổi chỉ theo chỉ dẫn user và review/rollout có chứng cứ.

### Hoàn tất khảo sát các ranh giới còn lại — 01/11 khoảng 22:20 VN

`strict_json`, `model_response` và `validate_review` đã được nhiều consumer dùng chung; phạm vi fence được truyền rõ ở consumer. Tách tiếp toàn bộ `localization.py` lúc này có nhiều call site và binding evidence, chưa chứng minh được lợi ích hay tính tương thích nên chưa thay đổi. AST của 81 module, chuẩn hóa tên hàm, chỉ còn hai hàm `main` entrypoint giống nhau từ ba dòng trở lên; chúng nạp hai broker/policy khác nhau, không gộp chỉ để giảm vài dòng. Đây là kiểm tra AST tĩnh, không khẳng định không có đoạn trùng một phần hoặc import động.

Guard có contract khác nhau: đường publish official `official_scratch_publish.verify_media` dùng kiểm HTTP 200 qua `scratch_cdn_readback`; `official_native_guard.readback` trả bytes và còn được dùng bởi `official_visual_evidence.inspect` cho prototype/review. Không thay chúng bằng một contract hoặc khôi phục so sánh CDN vào guard publish. Kiểm kê service/socket/timer của 9 module không có import tĩnh được ghi ở bảng dưới đây; không xóa module nào. Ảnh nguồn quá 10 media chưa được tự cắt. Các tối ưu khác chưa xác minh vẫn để sau; chưa có bài original được quan sát đi trọn Create/Published/readback sau các lát mới.

| Module không có import tĩnh vào | Bằng chứng kiểm đến 22:20 VN | Quyết định |
|---|---|---|
| `__init__` | Python nạp khi import package. | Giữ. |
| `isolated_media_validator` | `nailhub-source-mirror-media-validator@.service`, socket Accept=yes active/enabled; `isolated_media_client` dùng socket. | Giữ entrypoint. |
| `official_auto_gate` | Thấy test `test_official_auto_gate`/`test_official_auto_eligibility`; không thấy import trực tiếp trong scan package/unit đã kiểm. | Chỉ là ứng viên khảo sát; không xóa. |
| `official_autostage` | Service được gọi bởi timer autostage. | Giữ entrypoint. |
| `official_cover_entry` | ExecStart của official-cover service active/enabled. | Giữ entrypoint. |
| `official_review_worker` | ExecStart của review service; timer 05:30 hiện inactive/disabled. | Giữ; disabled không chứng minh không dùng. |
| `publication_gate` | Chưa thấy call site trực tiếp ngoài manifest backup trong phạm vi package/unit/test đã tìm. | Chỉ là ứng viên khảo sát; không xóa. |
| `scratch_cover_entry` | ExecStart của Scratch cover service active/enabled. | Giữ entrypoint. |
| `telegram_command_ipc` | ExecStart của command@ service, command socket Accept=yes active/enabled. | Giữ entrypoint. |

Phạm vi scan: AST 81 file cấp package và đối chiếu tên trong `/etc/systemd/system`, package, test stage; không đủ chứng minh mọi shell/CLI/agent bên ngoài. Không ghi đè/xóa chỉ vì không có import tĩnh. Runtime hiện tại gồm 81 module, 44 `official_*`; `cover_ipc_common.py` là helper đã cài.

### Thống nhất chọn chủ đề theo slot — 01/11 khoảng 22:16 VN

Đã dùng cùng `slot_policy` khi chọn ứng viên scheduled và kiểm lại trước chuẩn bị: 08 nail, 12/16/20 business. Chỉ ứng viên hợp slot mới tham gia ưu tiên bài không theo mùa/Halloween; do đó bài sai giờ không còn che một bài Halloween hợp 08. Thứ tự ứng viên scheduled hợp lệ theo queue ổn định; `topics.priority_slots` business/luxury cũ vẫn được chấp nhận để tương thích, nhưng **không xếp lại scheduled**; đường manual không gắn slot giữ thứ tự cũ với fallback luxury. Giới hạn Halloween, tax, quota và UNKNOWN được giữ. Thay đổi này có chủ ý về thứ tự lựa chọn, không bảo đảm phân loại heuristic luôn đúng.

Stage đạt **326 test, 26 skipped**; probe DB/snapshot live chỉ đọc cho cùng tập hợp hợp slot trước/sau tại thời điểm kiểm (08: 10; mỗi giờ business: 1), không ghi DB. Reviewer độc lập PASS logic/installer/hậu kiểm; sau cài 81 file khớp bundle, DB integrity OK, không unresolved, worker/broker/timer active/enabled. Backup `/opt/nailhub-source-mirror/backups/slot-unify-20261101`; không restore DB. Không có Create/publish thử, và kết quả scheduled thực tế sau thay đổi chưa được xác nhận.

Kiểm kê AST ghi nhận 9 module không có import tĩnh đi vào, gồm `__init__`, `isolated_media_validator`, `official_auto_gate`, `official_autostage`, `official_cover_entry`, `official_review_worker`, `publication_gate`, `scratch_cover_entry`, `telegram_command_ipc`. Nhiều module là entrypoint/hành vi động: **không kết luận unused, không xóa** nếu chưa kiểm mọi service/CLI/call site và evidence. Bài nguồn có hơn 10 media vẫn bị giữ trước tải theo hành vi cũ; không tự cắt ảnh tải được. Các lát refactor khác còn chờ khảo sát, không coi toàn bộ kế hoạch đã hoàn thành.

### Đơn giản hóa scanner kiểm trùng — 01/11 khoảng 22:11 VN

Đã dùng chung hai phép giống nhau: tính timeout và giải mã response admin. Nhánh official vẫn kiểm nguồn URL theo từng trang; nhánh bài gốc vẫn kiểm title/summary và semantic riêng, với kiểu lỗi và lý do riêng. Không gộp kết luận trùng, không thay pagination, quota, guard hay policy. Lưu ý khi instrumentation: alias helper của nhánh original phải được patch ở đúng namespace dùng nó.

Stage đạt **324 test, 26 skipped**. Reviewer độc lập xác nhận hợp đồng đọc giữ nguyên; probe GET-only thật dưới khóa quét ổn định 147 ID/15 trang hai lượt cho mỗi nhánh, không gọi model và không Create. Cài xong 81 file khớp bundle, worker/broker/timer active/enabled, integrity OK, không unresolved; backup `/opt/nailhub-source-mirror/backups/scan-unify-20261101`. Không restore DB. Kiểm trùng vẫn không nguyên tử với writer độc lập; probe không chứng minh một bài đã Published. Những phần đơn giản hóa khác chỉ tiếp tục theo từng lát sau review, không coi toàn bộ plan đã xong.

### Cập nhật bộ lọc — triển khai 01/11 lúc khoảng 22:05 VN

- Consumer kiểm factual/adjudicator của bài gốc và kiểm trùng giờ nhận JSON thô hoặc một hàng rào Markdown bao trọn JSON, vẫn strict schema, cấm duplicate keys/NaN và giữ verdict HOLD; generation đầu không được nới. Replay evidence thật: SBA Grow 16:00 fenced PASS đọc được, California 16:00 vẫn HOLD 7 issue, SBA Grow 20:00 vẫn HOLD 1 issue. Đây là replay, không phải bài đã đăng.
- Bộ lọc SBA phân biệt mua doanh nghiệp/cơ sở với mua thiết bị/dịch vụ theo ngữ cảnh; vẫn chặn các chủ đề pháp lý/thuế và technical như trước. `Build a luxury nail salon` nhận business, ví dụ build gel/acrylic nails được đánh dấu không rõ thay vì business. Hai quy tắc là heuristic, không bảo đảm nhận mọi cách nói; giữ nguồn, glossary và MQM.
- Duplicate precheck và kiểm trước Create ghi audit bền vững theo article/phase, outcome và reason code an toàn, không lưu raw payload hoặc exception text tùy ý; nếu không ghi được audit, bước đăng không tự đi tiếp. Quét admin chỉ đọc lúc khoảng 22:04 trả khác biệt trên 147 bài và ghi audit canary; **không chứng minh lý do HOLD 20:12**. Cạnh tranh với tác giả độc lập vẫn không nguyên tử.
- Stage đạt 321 test, 26 skipped; reviewer độc lập kiểm các ví dụ đối nghịch và installer. Cài bốn module, backup `/opt/nailhub-source-mirror/backups/filters-20261101`; hậu kiểm 81 file khớp bundle, worker/broker/timer active/enabled, integrity OK, không unresolved. Không đổi HTTP 200-only, policy, quota, guard, không tạo/publish bài thử. Lịch và kết quả thật ở slot sau vẫn cần theo dõi.

### Kế hoạch đơn giản hóa Python — Owner yêu cầu 01/11

**Kết quả lát đầu — 21:52 VN:** đã tách sáu hàm framing/socket/kiểm ảnh có AST và source giống nhau của hai broker vào `cover_ipc_common.py`; request, serve, policy, đối chiếu UID/GID và source scope vẫn riêng; helper peer/socket dùng chung. Đây là giảm mã trùng, **chưa giảm số module**: từ 80 lên 81 do thêm helper, vẫn 44 `official_*.py`. Các pha tiếp theo trong plan chưa thực hiện. Không thay bộ lọc, lịch, quota, DB hay policy; không Create/publish bài thử.

Stage đạt 314 test, 26 skipped; một test cũ được cố định budget slot trong stage để không phụ thuộc giờ. Reviewer độc lập xác nhận logic trước cài. Probe IPC tách biệt từ chối đúng source sai ở cả hai broker; một lần tạo ảnh thật `ai_original` PASS (SHA ảnh bắt đầu `a08fcc8c`). Mô phỏng SIGKILL/khôi phục cây cũ và mới dùng service giả lập, không phải thử mất điện thật. Sau cài: 81 file khớp bundle, worker và hai broker/timer active/enabled, peer socket PASS, DB integrity OK, không có intent/unknown; IPC Scratch giữ hash policy cũ. Backup `/opt/nailhub-source-mirror/backups/simplify-cover-20261101`; không restore DB khi rollback. Chưa chứng minh bài được Published hay chạy tất cả nguồn qua model sau thay đổi.

- **Mốc kiểm kê live 21:44 VN:** package Python trên THINKPAD có 80 module cấp đầu, trong đó 44 tên `official_*.py`; worker và hai broker bìa đang active. Số lượng mô tả cấu trúc, không tự chứng minh module nào thừa hoặc an toàn để xóa.
- **Pha 1 — lát cắt nhỏ:** từ bản live có hash, xác định phần xử lý lặp giữa broker ảnh nguồn chính thống và broker Scratch/bài gốc; chỉ tách helper khi so sánh chứng minh cùng hành vi. Giữ riêng đối chiếu UID/GID, policy, nguồn, request và quyền sử dụng; helper peer/socket có thể dùng chung; không hợp nhất scope hay thay contract. Kiểm thử dương/âm và nhờ reviewer độc lập trước deploy.
- **Pha 2 — review tiếp:** thống kê call site, test và đường chạy thật; tiếp tục gộp từng phần thực sự trùng, tránh viết lại hàng loạt, không xóa module/chuyển chức năng chỉ dựa trên tên. Các thay đổi bộ lọc trong guide vẫn là đề xuất riêng, không mặc định được thực hiện trong refactor.
- **Pha 3 — triển khai theo từng lát:** snapshot DB và mã, đối chiếu baseline, giữ khóa dùng chung, kiểm không có intent/unknown và không đụng lịch đang chạy; thay đổi có backup/rollback, kiểm worker, broker, socket và policy sau cài. Không restore DB cũ, không tạo/publish bài thử, không commit.
- **Bất biến:** 08 nail, 12/16/20 business; nguồn/attribution chính xác, glossary/MQM, 4 bài/ngày của workflow, duplicate/unknown, một vòng sửa giới hạn, kiểm CDN HTTP 200-only và thông báo hiện có. Chỉ ghi kết quả được kiểm chứng; chưa coi plan là thay đổi hoàn thành.

| Version | Ngày giờ VN | Ghi chú |
|---|---|---|
| v1.0.6 | 01/11/2026 22:20 | Ghi đồ thị entrypoint và lý do chưa gộp parser/guard. |
| v1.0.5 | 01/11/2026 22:16 | Ghi slot chung, probe read-only và kiểm kê không xóa. |
| v1.0.4 | 01/11/2026 22:11 | Ghi scanner helper đã cài và giữ hợp đồng từng nhánh. |
| v1.0.3 | 01/11/2026 22:05 | Cập nhật hành vi filter và đường audit đã cài; giữ sự cố lịch sử. |
| v1.0.2 | 01/11/2026 21:52 | Ghi helper broker đã cài và cách kiểm/rollback. |
| v1.0.1 | 01/11/2026 21:44 | Bổ sung kế hoạch refactor từng lát và bất biến vận hành. |
| v1.0.0 | 01/11/2026 21:16 | Tham chiếu cho agent, chỉ dẫn đọc state và sự cố có bằng chứng; không thay luật harness. |


### Cập nhật vận hành 03/11/2026 — kiểm tra trùng và bài bù

- Hai lượt kiểm tra đọc PROD sau sự cố cho thấy bản nháp về địa điểm và bản về marketing có góc nội dung trùng đáng kể với các bài đã đăng; việc giữ hai bản đó có cơ sở. Nhật ký lịch sử không lưu ID/lý do khớp nên không thể khẳng định các kết quả kiểm tra sau chính là kết quả của lượt 12:00.
- Đã triển khai audit kiểm tra trùng bản 2: lưu mã định danh phù hợp hoặc hash, hash bằng chứng và các nhãn suy luận thuộc tập cố định; không lưu nguyên văn lý do model. Các nhãn không phải lời giải thích đầy đủ cho quyết định. Giữ nguyên verdict trùng/không chắc chắn và các gate xuất bản. Chưa xác minh mọi hệ thống ngoài có đọc định dạng audit cũ hay không.
- Phân loại chủ đề đã nhận thêm thuật ngữ an ninh mạng/an ninh số khi có ngữ cảnh kinh doanh phù hợp; đây là heuristic, không thay kiểm nguồn hoặc factual.
- Khung 12:00 và 16:00 ngày 03/11 đều kết thúc HOLD, chưa có bài từ hai khung đó. Bài cybersecurity `fc0038...` đã có bản dịch MQM PASS, một lần precheck trùng PASS và ảnh bìa sinh thành công, nhưng vẫn HELD: câu khóa thiết bị có thể nhầm khóa màn hình với bảo vệ vật lý. Bản sửa sau giữ nguồn và điều kiện sao lưu nhưng thêm khẳng định không có nguồn về tần suất vendor phát hành; bản thử cuối còn diễn đạt MFA gây hiểu nhầm. Không coi model PASS là chứng minh factual. Các bản này chưa được đăng.
- Verifier hiện yêu cầu bản nội dung khớp envelope sinh thực tế; không sửa một câu trong proof cũ hoặc tạo envelope giả. Để hỗ trợ biên tập trực tiếp cần thiết kế provenance revision, kiểm nguồn/MQM độc lập và kiểm tương thích trước khi dùng. Chưa có cơ chế này trong production.


### Kết quả đăng bù 03/11/2026 — cập nhật 18:21 ICT

Sau trạng thái HOLD ở mục trên, theo yêu cầu trực tiếp của người dùng, hai bài bù đã được đăng một lần qua editor thực; cả hai đã được kiểm tra lại trên admin và trang khách:

- Bù 12:00: [Biện pháp an ninh kỹ thuật số và bảo vệ hệ thống dành cho tiệm nail](https://nailhub.ai/blog/bien-phap-an-ninh-ky-thuat-so-va-bao-ve-he-thong-danh-cho-tiem-nail), attempt 25. Trước khi đăng, đoạn khóa thiết bị dễ gây hiểu nhầm được xóa ở cả hai ngôn ngữ; audit lưu hash nội dung gốc và nội dung biên tập.
- Bù 16:00: [Kế hoạch quản lý tài chính cho tiệm nail chuyên nghiệp](https://nailhub.ai/blog/ke-hoach-quan-ly-tai-chinh-cho-tiem-nail-chuyen-nghiep), attempt 26. Theo phạm vi nới lọc một lần được yêu cầu, bỏ block có khẳng định dễ gây hiểu sai về dự báo dòng tiền và sửa summary tiếng Việt cho khớp phần giữ lại. Hai bài dẫn [SBA Manage Your Business](https://www.sba.gov/counseling/manage-your-business/).

MQM PASS thuộc revision trước biên tập; phần xóa/sửa được ghi audit riêng, không mô tả là MQM đã đánh giá lại. Mỗi bài qua kiểm trùng và có ảnh bìa được ràng buộc với payload thực tế. Kiểm trùng không atomic với người viết độc lập. Attempt 24 trước đó dừng trước Create, đã đối soát bằng phase, journal và tìm kiếm admin rồi chuyển released, article held trong cùng transaction; evidence nằm tại `/var/lib/nailhub-source-mirror/reports/attempt-24-reconciliation-20261103.json` trên THINKPAD.

Comparator và ngoại lệ biên tập dùng stage một lần, không thay code/timer production. Lúc 18:21 DB integrity OK, không intent/unknown; ba bài workflow đã đăng trong ngày, còn một vị trí theo quota bốn bài. Hai khung lịch sử vẫn ghi HOLD và đã hết số lượt; khung 20:00 vẫn theo lịch bình thường, không bảo đảm sẽ đăng nếu gate giữ bài.


### 2026-11-10 10:32 — 1099, trạng thái candidate

Candidate từ baseline THINKPAD bổ sung ba URL IRS 1099, phân loại ngữ cảnh 12:00, ưu tiên nguồn và kiểm tra cover bảo thủ. IRS form và instructions cho kết quả `missing` ở bộ kiểm ảnh; information return `unknown` nên không được suy là thiếu ảnh. Chỉ có fixture và compile tách biệt; gate năm thuế, fallback official sau pin, cấu hình production và review đầy đủ chưa hoàn tất. Không triển khai hay thay assignment. Xem `.context/source-mirror-tax-progress.md`.

Changelog | v1.0.1 | 2026-11-10 10:36 ICT | AlexChu / Codex | Cập nhật quy định 12:00, nguồn IRS và trạng thái pipeline 1099 chưa deploy.
