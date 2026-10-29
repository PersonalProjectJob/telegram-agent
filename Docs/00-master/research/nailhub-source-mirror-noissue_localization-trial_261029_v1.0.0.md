# Thử AGY trên THINKPAD: glossary và MQM cho Việt hóa bài nail

- Version: v1.0.2.
- Owner: AlexChu. Executor kiểm tra: Codex; executor Việt hóa do Owner chọn: AGY / Gemini 3.8 Flash / medium.
- Updated: 2026-10-29 10:06, giờ Việt Nam.
- Phạm vi: thử một bài trong thư mục riêng, đánh giá trước tích hợp. Không thay đổi worker, lịch đăng, tài khoản PROD hay NailBlog. Chưa commit.
- Trạng thái: **ĐÃ CHẠY THỬ — cần sửa 2 Minor và xác minh 1 điểm kỹ thuật nguồn trước canary; chưa tích hợp PROD**.

## Tài liệu liên quan

- [Master / Business](../nailhub-source-mirror-noissue_master_261028_v1.0.0.md), mục 10.3 và 10.4.
- [Glossary máy đọc](nailhub-source-mirror-noissue_glossary_261029.json).
- Kết quả CLI thô được lưu tại máy vận hành, ngoài commit.
- Issue / PR: chưa tạo; đây là nghiên cứu bổ sung trong feature hiện tại.

## 1. Glossary — từ điển dùng chung cho bài nail

Glossary là bảng chốt: **từ tiếng Anh này, trong ngữ cảnh này, Nailhub gọi bằng từ tiếng Việt nào**. Mục đích là cả bài và các bài sau gọi cùng một kỹ thuật bằng cùng tên, tránh dịch đúng ngữ pháp nhưng sai nghề.

Một dòng nên có từ nguồn, nghĩa/ngữ cảnh, cách dịch ưu tiên, cách dịch cần tránh, ví dụ, nguồn tham khảo, phiên bản và người duyệt. Không dùng thay thế từ máy móc: cùng từ “cure” trong bài nail có nghĩa khác trong bài y tế.

Bảng dưới là **đề xuất thuật ngữ biên tập Nailhub**, chưa phải bộ thuật ngữ tiếng Việt đã được chuyên gia nail phê duyệt. Nguồn bên dưới hỗ trợ khái niệm tiếng Anh; không có nghĩa họ chứng nhận bản dịch tiếng Việt này.

| Từ nguồn | Cách gọi đề xuất | Tránh / lưu ý |
|---|---|---|
| cure / curing | hơ đèn để gel đóng rắn; sau lần đầu có thể viết hơ đèn | Không dịch “chữa khỏi”, không coi là phơi khô tự nhiên; không tự thêm số giây hay loại đèn |
| do not cure | chưa hơ đèn ở bước này | Phải giữ phủ định; bỏ từ “chưa” có thể đảo ngược thao tác |
| blooming gel | gel loang (blooming gel) ở lần đầu; sau đó gel loang | Không dịch sát chữ “gel nở hoa”; giữ nhãn sản phẩm nếu là tên riêng |
| gel polish | sơn gel | Không đổi thành sơn thường hoặc bột acrylic |
| fine liner nail art brush | cọ nét mảnh vẽ móng | Không đổi thành bút kẻ mắt |
| glossy gel polish top coat | lớp phủ bóng cho sơn gel | Không nhầm với lớp sơn lót |
| prep the nails | chuẩn bị móng | Không tự thêm công đoạn mài, cắt da hay hóa chất mà nguồn không ghi |
| index / middle / ring fingernail | móng ngón trỏ / ngón giữa / ngón áp út | Đối chiếu từng ngón ở từng bước |
| Alice Nali; Aly Nails; tên giải thưởng | giữ nguyên tên riêng | Không đổi London thành Việt Nam; giữ năm 2025 và 2026 |

Ví dụ: “Do not cure” → “Chưa hơ đèn ở bước này” thể hiện đúng thao tác hơn “Không chữa khỏi”. Đây là ví dụ giải thích glossary của người soạn báo cáo, **không phải đầu ra Gemini**.

Cơ sở chuyên môn: [NailKnowledge Glossary](https://nailknowledge.org/glossary) là điểm tra khái niệm; [hướng dẫn Blooming Gel của hãng Gellifique](https://www.gellifique.eu/en/content/blooming-gel-application-guide(26)) phân biệt bước để gel loang chưa đóng rắn với bước hơ đèn sau khi đạt hiệu ứng. Hướng dẫn hãng chỉ áp dụng hệ sản phẩm của họ; không lấy thời gian/đèn của hãng gán vào bài Scratch.

## 2. MQM — phiếu kiểm lỗi bản dịch

**MQM (Multidimensional Quality Metrics)** là khung đánh giá chất lượng bản dịch theo nhiều mặt. Cách hiểu đơn giản: đặt bản gốc và bản Việt cạnh nhau, chỉ ra lỗi ở đâu, thuộc loại nào, nặng đến mức nào, cần sửa gì. Glossary quyết định cách gọi thuật ngữ; MQM kiểm tra bản dịch có dùng đúng và truyền đạt đúng hay không.

Cơ sở: [MQM Council — giới thiệu](https://www.themqm.org/), [MQM Core Typology](https://www.themqm.org/mqm-pillars/the-mqm-core-typology/), [Scoring Models](https://www.themqm.org/mqm-pillars/the-mqm-scoring-models/). MQM cho phép xây tiêu chí theo mục đích sử dụng; các mức điểm/ngưỡng dưới đây là **đề xuất nội bộ**, không phải ngưỡng chung bắt buộc của MQM hay chứng nhận ISO.

### Kiểm những gì?

| Nhóm kiểm | Câu hỏi dễ nhớ | Ví dụ lỗi |
|---|---|---|
| Đúng nghĩa / Accuracy | Có nói đúng, đủ như nguồn không? | Bỏ chữ “không”, thêm thời gian hơ, đổi hai lớp thành một lớp |
| Thuật ngữ / Terminology | Có dùng đúng glossary và nhất quán không? | Top coat bị dịch thành sơn lót |
| Tiếng Việt / Linguistic conventions | Có dễ hiểu, đúng chính tả, ngữ pháp không? | Câu máy móc, lỗi dấu, chữ bị vỡ mã |
| Văn phong / Style | Có phù hợp bài hướng dẫn cho thợ nail không? | Thêm lời quảng cáo hoặc bảo đảm chất lượng mà nguồn không có |
| Ngữ cảnh và người đọc / Locale & Audience | Có hợp ngôn ngữ mà vẫn giữ sự thật không? | Đổi địa điểm, tiền tệ hoặc tên giải để giả thành tin tại Việt Nam |
| Bố cục / Design and markup | Có đủ bước, đúng ảnh và link không? | Đảo ảnh bước 2 sang bước 3, mất link ghi nguồn |

### Lỗi nặng đến đâu?

- **Neutral (0 điểm):** khác sở thích viết nhưng vẫn đúng; không ép sửa chỉ vì người kiểm thích từ khác.
- **Minor (1 điểm):** lỗi nhỏ, vẫn hiểu và làm đúng được; ví dụ lỗi dấu câu không làm đổi nghĩa.
- **Major (5 điểm):** sai nghĩa hoặc ảnh hưởng đáng kể khả năng làm theo; ví dụ đổi ngón tay, bỏ một bước, nhầm top coat/base coat.
- **Critical (25 điểm):** làm nội dung không dùng được hoặc gây rủi ro nghiêm trọng. Không phải mọi lỗi kỹ thuật đều tự động là Critical; đánh mức độ theo tác động thực tế.

Mỗi lỗi ghi: `block_id | đoạn gốc | đoạn Việt | nhóm lỗi | độ nặng | lý do/tác động | đề nghị sửa | trạng thái`. Một lỗi gốc chỉ tính một lần, không vừa tính Accuracy vừa tính Terminology để tăng điểm; lỗi lặp ghi số lần và quy tắc xử lý nhất quán.

Điểm phạt đề xuất: `Minor × 1 + Major × 5 + Critical × 25`, trọng số nhóm bằng 1. Báo số lỗi và điểm phạt tuyệt đối; nếu so nhiều bài thì bổ sung điểm phạt/1.000 từ nguồn với cùng quy tắc đếm, không so điểm thô giữa bài dài/ngắn. Điểm thấp tốt hơn. Không gọi một điểm AI tự cho là “độ chính xác 98%”.

**Cổng đề xuất trước canary:** Critical = 0, Major = 0; mọi lỗi Minor đã phát hiện được sửa và kiểm lại. Đồng thời phải qua kiểm tra cấu trúc/dữ kiện riêng. Bài ngắn thử đầu nên đối chiếu toàn bộ, không lấy mẫu. Kết luận của Codex là rà soát theo rubric, không tương đương chuyên gia nail/người đánh giá MQM được hiệu chuẩn; lần đầu cần người có chuyên môn nail duyệt thuật ngữ và điểm kỹ thuật còn mơ hồ.

## 3. Bài mẫu và điều kiện trước khi chạy

Chọn snapshot bài **Blooming boo-tiful: simple 4-step ghost nail art tutorial**, có 4 bước, 5 ảnh, 14 block. [Trang nguồn Scratch](https://www.scratchmagazine.co.uk/nail_art/blooming-gel-ghost-nail-tutorial/).

Chỉ sao chép metadata/content công khai sang `/var/tmp/nailhub-localization-trial-20261029/`; không chuyển browser profile, mật khẩu, cookie hoặc config PROD cho AGY. Prompt yêu cầu trả JSON, không dùng tool/browse/sửa file, giữ nguyên block/media/href, tên riêng, dữ kiện, số lượng, phủ định; không thêm chỉ dẫn kỹ thuật. AGY chạy user `startechai`, không chạy root. `--sandbox` không phải bảo đảm filesystem chỉ đọc; thư mục riêng và prompt không thay thế cách ly quyền hệ điều hành.

Đầu ra yêu cầu: title_vi, summary_vi, meta_title_vi, meta_description_vi, hashtags, blocks, uncertainties. EN giữ nguyên nguồn. Summary <=180 ký tự; Meta Title <=70; Meta Description <=160; hashtag 2–50 ký tự, đề xuất 3–5 tag. Hashtag dùng chung ở cấp bài theo schema hiện tại, không giả định có trường riêng từng ngôn ngữ.

Checklist kiểm sau sinh: đủ 14 block, đủ 4 bước và 5 ảnh đúng thứ tự; không mất href/tên riêng/năm; giữ hai lớp sơn, hai mắt và đúng ngón; giữ phủ định ở bước 2, 3; Meta/summary/tag đúng giới hạn. Sau kiểm máy là đối chiếu toàn bộ nghĩa và glossary. Đếm số khớp không tự chứng minh câu dịch đúng nghĩa.

Một điểm cần rà soát từ nguồn: câu bước 1 nói “curing after both layers”. Cần giữ thông tin hai lớp và ghi riêng độ mơ hồ về thời điểm hơ; không tự áp hướng dẫn của sản phẩm hãng khác vào bài. Đây là lưu ý ở **nguồn**, chưa phải lỗi dịch vì chưa có bản dịch.

## 4.1. Lần đầu — lịch sử lỗi xác thực

| Mục | Kết quả |
|---|---|
| Máy SSH được Owner cung cấp | 100.81.90.91; hostname thực tế gw-01 |
| User chạy AGY | startechai |
| Binary / phiên bản | /home/startechai/.local/bin/agy — 1.4.7 |
| agy models | Exit 1: Please sign in to view available models |
| Model/effort yêu cầu | gemini-3.8-flash-medium / medium |
| Lệnh tạo bản dịch | Exit 1; 0,991 giây theo đồng hồ subprocess |
| CLI status / error | ERROR / authentication failed or timed out |
| Số lượt / token theo CLI | 0 lượt; input 0, output 0, thinking 0, total 0 |
| Bản dịch | Không có; response rỗng |
| Glossary / MQM với bản dịch | NOT EVALUATED — không được ghi 0 lỗi/PASS |
| Snapshot nguồn | SHA256 metadata/content trước và sau khớp |
| Tích hợp / publish PROD | Chưa thực hiện trong thử nghiệm này |

Hai SHA256 được lưu trong JSON kết quả. Lỗi điều phối ban đầu do PATH thiếu `runuser` đã được sửa thành đường dẫn tuyệt đối `/usr/sbin/runuser`; không có lượt suy luận nào xảy ra ở lần lỗi đó. Lần thử AGY thực sau đó dừng ở xác thực như bảng trên. Không chuyển token từ máy Windows hoặc tài khoản khác sang THINKPAD, không đổi model hoặc chuyển sang API trả phí.

## 4.2. Chạy lại sau khi Owner đăng nhập — 2026-10-29 10:06 (VN)

Đăng nhập đã hoạt động dưới user `startechai`. Catalog lấy thành công và có `gemini-3.8-flash-medium`. CLI hiện **1.4.9**, khác bản 1.4.7 lần đầu. Giữ nguyên prompt/glossary đã chuẩn bị và bài ghost 4 bước; không đổi model/effort.

| Mục | Kết quả thật |
|---|---|
| CLI / process | SUCCESS; exit 0; 1 lượt |
| Thời gian | 38.714 giây wall clock; 32.985 giây theo CLI |
| Usage do CLI báo | Input 7978; output 4598; thinking 3461; total 12576 |
| JSON | Parse được envelope và payload; đủ trường yêu cầu |
| Cấu trúc | 14/14 block, 4/4 bước, media ID 0–4 giữ nguyên thứ tự |
| Link / dữ kiện kiểm máy | href giữ nguyên; tên riêng/địa điểm/năm và các từ chỉ số lượng/phủ định kiểm được |
| Summary | 165/180 ký tự |
| Meta Title / Description | 51/70 và 147/160 ký tự |
| Hashtag | 4 tag, tất cả trong giới hạn 2–50 ký tự |
| Kiểm máy | 16/16 điều kiện cấu trúc/từ khóa đạt; không thay thế kiểm nghĩa |
| Glossary | Codex đối chiếu đủ 8/8 mục glossary JSON; cách gọi trong ngữ cảnh chấp nhận được, không nhầm top coat/base coat hoặc bỏ phủ định |
| MQM rà toàn bài | 0 Critical, 0 Major, 2 Minor; điểm phạt 2 theo rubric nội bộ |
| Source hold | 1 — câu thời điểm hơ ở bước 1 cần xác minh trước đăng |
| Kết luận | **REVISION_AND_TECHNICAL_REVIEW_REQUIRED — chưa đủ điều kiện đưa vào PROD** |

CLI usage không được quy đổi thành tiền vì chưa có dữ liệu tính phí/quota của tài khoản. Cờ lệnh và catalog xác nhận model được yêu cầu; envelope không có trường độc lập chứng thực backend model thực resolve. Không cộng thinking thêm vào total để tránh tự suy diễn cách CLI tính token.

### Hai lỗi biên tập trong bản dịch

| ID | Vị trí | Nhóm / mức độ | Phát hiện | Đề nghị sửa |
|---|---|---|---|---|
| M01 | title_vi | Accuracy / Untranslated — Minor | Giữ “Blooming boo-tiful” là câu chơi chữ quảng bá tiếng Anh; không thuộc danh sách tên riêng cần bảo vệ | Bỏ câu chơi chữ, dùng tiêu đề Việt giữ chủ đề gel loang/bóng ma/4 bước |
| M02 | blocks[10].html, bước 3 | Linguistic conventions / Textual conventions — Minor | “nhẹ nhàng kéo nhẹ xuống phía dưới” lặp ý | “kéo nhẹ nét vẽ xuống một chút” |

Đề xuất tiêu đề: **Vẽ móng hình bóng ma Halloween với gel loang trong 4 bước**. Đây là gợi ý của người kiểm, chưa sửa đè payload Gemini. Cụm “hơ đèn để gel đóng rắn” lặp nhiều lần có thể rút gọn sau lần đầu, nhưng glossary hiện cho phép nên ghi là góp ý Neutral, không cộng thành lỗi phạt riêng.

### Điểm kỹ thuật cần giữ bài lại

Bước 1 của nguồn dùng “curing after both layers”; bản Việt viết “hơ đèn để gel đóng rắn sau cả hai lớp”. Model có ghi `uncertainties` nêu hai cách hiểu. Cờ này là dấu hiệu cần kiểm tiếp, **không có nghĩa nội dung đã an toàn để tự đăng**: nếu chỉ đẩy trường blocks lên web thì người đọc sẽ không thấy ghi chú ngoài bài. Cần xác minh với nguồn/hướng dẫn chính xác của hệ sản phẩm, không tự gán thời gian hơ hoặc dùng quy trình hãng khác thay thế.

Ghi riêng S01 là vấn đề từ nguồn, không đếm thêm vào MQM của bản dịch. Dù điểm MQM thấp, bài vẫn giữ trạng thái cần xác minh kỹ thuật. Không dùng cổng “0 Major/Critical” đơn lẻ để tự cho đăng.

### Bằng chứng và giới hạn

- [Bản Việt đọc thử](nailhub-source-mirror-noissue_translation-preview_261029_v1.0.0.md); [payload Việt nguyên bản](nailhub-source-mirror-noissue_translation-vi-trial_261029.json).
- [Phiếu MQM và 16 check](nailhub-source-mirror-noissue_mqm-trial_261029.json); kết quả CLI và catalog thô lưu ngoài commit trên máy vận hành.
- Reviewer là Codex, khác executor tạo bản dịch AGY. Đây là rà soát song ngữ theo rubric, chưa phải đánh giá chuyên gia nail hoặc người đánh giá MQM đã hiệu chuẩn.
- Chỉ thử 1 bài; chưa đo độ bền quota, chất lượng nhiều chủ đề, khả năng lưu auth qua reboot hay suy luận unattended dưới service account. Chạy thành công qua SSH bằng startechai không chứng minh systemd user nailhub-source-mirror sẽ dùng được auth đó.
- Chỉ kiểm tham chiếu ảnh; chưa render/đo thumbnail hoặc layout. Snapshot metadata/content SHA256 khớp trước/sau. Không gửi lệnh tạo/sửa bài PROD và không sửa worker/NailBlog.

## 5. Handoff và bước tiếp theo

- [x] Probe CLI, chọn bài và chuẩn bị glossary/prompt/tiêu chí trước suy luận.
- [x] Thử một lệnh thật bằng model/effort Owner chọn, lưu kết quả thất bại xác thực.
- [x] Ghi báo cáo và đồng bộ business DOCS về repo chính.
- [x] Owner đăng nhập; catalog xác nhận Flash medium có trên THINKPAD.
- [x] Chạy lại prompt: SUCCESS, lưu bản dịch/usage/thời gian và kiểm 16/16 điều kiện máy.
- [x] Rà toàn bài theo glossary/MQM: 2 Minor đang mở, 1 source hold; lưu bản đọc thử và báo cáo.
- [ ] Sửa hai Minor và rà lại; xác minh câu kỹ thuật S01 với nguồn/người có chuyên môn. Chưa có bản hiệu đính nào được gắn nhãn PASS.
- [ ] Sau khi thử đạt mới thiết kế tích hợp, freeze revision, readback VI/EN/Meta/hashtag và xác minh public head/locale riêng; không tự coi lưu Meta ở admin là public SEO đã đúng.

## Changelog

| Version | Date | Time (VN) | Nội dung |
|---|---|---|---|
| v1.0.2 | 2026-10-29 | 10:42 | Chuyển bằng chứng CLI thô sang lưu cục bộ ngoài commit; giữ kết quả và phiếu MQM. |
| v1.0.1 | 2026-10-29 | 10:06 | AGY 1.4.9 đăng nhập và tạo bản Việt thành công; 16 check đạt, MQM 2 Minor và 1 source hold; chưa tích hợp PROD. |
| v1.0.0 | 2026-10-29 | 09:54 | Định nghĩa glossary/MQM dễ hiểu, chuẩn bị thử một bài; AGY THINKPAD bị chặn đăng nhập, chưa có bản dịch/chưa chấm MQM. |
