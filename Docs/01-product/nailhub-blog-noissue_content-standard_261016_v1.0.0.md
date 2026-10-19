# Chuẩn cấu trúc và nội dung bài blog NailHub

**Phiên bản:** v1.0.1 · 2026-10-16 · Asia/Ho_Chi_Minh\
**Trạng thái:** Draft — chờ Owner duyệt\
**Căn cứ:** [Business v1.0.1](nailhub-blog-noissue_business_261015_v1.0.0.md) · [Engineering v1.0.9](../02-engineering/nailhub-blog-noissue_engineering_261016_v1.0.0.md) · audit 33 bài blog hiện có (2026-10-14)\
**Phạm vi:** cấu trúc, SEO on-page, cách viết đoạn văn. **Tạo ảnh tạm gác** (Owner 2026-10-16): bài vẫn giữ 3–5 vị trí ảnh có mô tả + alt để gắn ảnh sau; ở preview các vị trí này hiện khung trống có ghi chú.

---

## 1. Người đọc và giọng văn

- Người đọc: thợ nail và chủ tiệm người Việt ở Mỹ, đọc trên điện thoại, thường lướt nhanh giữa ca hoặc trước giờ mở cửa.
- Giọng: như một người trong nghề nói chuyện thẳng — câu ngắn, cụ thể, có con số, xưng hô trung tính ("thợ", "chủ tiệm", "bạn").
- Giữ thuật ngữ tiếng Anh người đọc gặp hằng ngày: builder gel, cat-eye, booth rent, 1099-NEC, State Board, walk-in, no-show. Lần đầu xuất hiện có thể giải thích 3–6 chữ trong ngoặc.
- **Cấm** các cụm sáo rỗng kiểu văn AI: "Trong thế giới…", "Hãy cùng khám phá", "Không chỉ… mà còn…", "Bạn đã bao giờ tự hỏi", "Tóm lại, có thể thấy rằng", "đóng vai trò quan trọng", "ngày càng phát triển", "vô cùng", "tuyệt vời", "hoàn hảo". Danh sách đầy đủ do cổng chất lượng kiểm.

## 2. Khung bài chuẩn (mọi danh mục)

Thứ tự trong `content` (tiêu đề bài là H1 do website hiển thị — **không** lặp H1 trong nội dung):

| # | Phần | Yêu cầu |
| --- | --- | --- |
| 1 | **Mở bài** | 2 đoạn, 50–90 từ tổng. Câu đầu nói thẳng vấn đề/tin chính; trong **100 từ đầu** có từ khóa chính; đoạn 2 nói bài này giúp gì |
| 2 | **Tóm tắt nhanh** | H2 "Tóm tắt nhanh" + 3–4 gạch đầu dòng, mỗi dòng ≤ 20 từ, có số liệu nếu có. Phục vụ người lướt và đoạn trích của Google |
| 3 | **Thân bài** | 4–6 mục H2 theo khuôn danh mục (mục 3). Mỗi H2 là một câu hỏi hoặc một ý rõ, ≤ 70 ký tự, ≥ 1 H2 chứa từ khóa chính hoặc phụ. H3 khi một mục có ≥ 2 ý con |
| 4 | **Ảnh** | 3–5 vị trí ảnh: sau mở bài/tóm tắt và xen giữa các H2, không 2 ảnh liền nhau. Mỗi vị trí có `brief` (mô tả ảnh cần), `alt` 5–15 từ, `caption` khi ảnh mang thông tin |
| 5 | **Câu hỏi thường gặp** | H2 "Câu hỏi thường gặp" + 3–5 câu hỏi dạng H3, mỗi câu trả lời 1 đoạn 30–60 từ, trả lời ngay câu đầu |
| 6 | **Kết + CTA** | 1 đoạn ≤ 60 từ: điều cần nhớ + một hành động cụ thể (xem tin tuyển, đăng tin sang tiệm, tải app) có link nội bộ |
| 7 | **Nguồn** | Bài có số liệu luật/thuế/phí/giờ học: H2 "Nguồn tham khảo" + danh sách link nguồn chính thức, kèm "cập nhật ngày …" |

**Độ dài:** 800–1.200 từ (hot-trend và did-you-know: 600–1.000).

## 3. Khuôn thân bài theo danh mục

| Danh mục | Slot | Khuôn H2 thân bài |
| --- | --- | --- |
| **hot-trend** (trend màu, kiểu móng) | 06:00 | Trend là gì & vì sao đang hot (có tín hiệu trend) → 1 H2 cho mỗi kiểu/màu (mô tả, hợp ai, thời gian làm, mẹo kỹ thuật) → Cách tư vấn khách + gợi ý giá (giá chỉ khi có nguồn hoặc ghi "tham khảo") → Lỗi thường gặp khi làm |
| **nail-job** (thợ, việc làm, license) | 06:00 | Vấn đề/đối tượng → Các bước cụ thể (H2 hoặc danh sách đánh số) → Giấy tờ/điều kiện (có nguồn) → Lưu ý để không bị thiệt → CTA tìm việc |
| **nail-salon** (vận hành, luật, marketing) | 19:00 | Vấn đề chủ tiệm gặp → Vì sao xảy ra → Giải pháp từng bước → Ví dụ/số liệu minh họa (có nguồn) → Sai lầm cần tránh |
| **did-you-know** (số liệu, tin ngành, sản phẩm) | 19:00 | Con số/tin chính → Giải thích ngắn → Ý nghĩa với tiệm và thợ → Nên làm gì tiếp |

Bài giới thiệu sản phẩm NEXORA: tối đa 1/ngày, phải mở đầu bằng vấn đề của người đọc, không mở đầu bằng tên sản phẩm.

## 4. Chuẩn đoạn văn (paragraph chuẩn blog)

| Quy tắc | Ngưỡng |
| --- | --- |
| Mỗi đoạn một ý | Câu đầu đoạn là ý chính (câu chủ đề) |
| Độ dài đoạn | 2–4 câu, **40–90 từ**; tối đa 1 đoạn/bài được tới 110 từ |
| Độ dài câu | Trung bình ≤ 20 từ; không câu nào > 35 từ |
| Đoạn liên tiếp | Không quá 3 đoạn văn liền nhau không có H2/H3, danh sách hoặc ảnh chen giữa |
| Danh sách | Dùng khi có ≥ 3 ý song song, bước làm, giấy tờ, so sánh; 3–7 dòng, mỗi dòng ≤ 25 từ |
| Nhấn mạnh | `<strong>` tối đa 1 cụm/đoạn, chỉ cho số liệu hoặc cảnh báo |
| Số liệu | Ghi cụ thể ("400 giờ", "$50", "2/11/2026"), có nguồn theo BB-05; khoảng số phải đủ hai vế có nguồn |
| Chuyển ý | Câu cuối đoạn có thể dẫn sang ý sau, không lặp lại ý đã nói |

Không dùng bảng HTML: trình soạn thảo Quill của trang quản trị không hỗ trợ bảng ổn định — so sánh trình bày bằng danh sách hoặc H3.

## 5. SEO on-page

| Thành phần | Quy tắc |
| --- | --- |
| **Từ khóa chính** | 1 cụm (từ trend hoặc chủ đề đã chốt), tiếng Việt có dấu; kèm 2–4 từ khóa phụ (biến thể, tiếng Anh người đọc hay tìm) |
| **Tiêu đề SEO (metaTitle)** | ≤ 60 ký tự, có từ khóa chính, ưu tiên đặt đầu; không kèm hậu tố tên site (website tự thêm nếu cần) |
| **Tiêu đề hiển thị (title)** | ≤ 90 ký tự, hấp dẫn, có thể dài hơn tiêu đề SEO, có số hoặc lợi ích cụ thể |
| **Meta description** | 140–160 ký tự, có từ khóa chính, nói rõ người đọc được gì, kết bằng động từ hành động |
| **Summary** | Có thể trùng meta description; 140–160 ký tự |
| **Slug** | Bỏ dấu đúng, ≤ 60 ký tự, chứa từ khóa chính, bỏ từ thừa ("cho", "của", "và" nếu không mất nghĩa) |
| **Vị trí từ khóa** | Có trong: metaTitle, meta description, slug, 100 từ đầu, ≥ 1 H2. Tổng số lần xuất hiện tự nhiên (gợi ý 0,5–1,5% số từ) — không nhồi |
| **Link nội bộ** | 2–3 link: bài cũ cùng danh mục + trang chức năng NailHub (tin tuyển, sang tiệm, tải app — URL thật chốt ở Phase 0) |
| **Link ngoài** | Bài luật/thuế/license: 1–3 link nguồn chính thức (.gov, State Board, IRS, DOL). Bài trend: 0–2 link báo uy tín. Không link đối thủ |
| **Hashtag** | 3–5, không dấu, liên quan từ khóa chính |
| **Alt ảnh** | 5–15 từ tiếng Việt, mô tả đúng nội dung ảnh, có từ khóa ở ≤ 1 alt (không nhồi) |
| **Dữ liệu có cấu trúc** | BlogPosting + FAQPage JSON-LD **chưa đưa được** vì nội dung Quill không cho script và trang là SPA — chờ DEP-1. Agent vẫn sinh sẵn JSON-LD trong `article.json` để dùng khi nền tảng hỗ trợ |

## 6. Dữ liệu model phải trả về (bổ sung schema writer)

Outline: `primary_keyword`, `secondary_keywords[2..4]`, `title`, `title_seo`, `meta_description`, `summary`, `slug`, `intro[2]`, `key_takeaways[3..4]`, `sections[4..6]{h2, kind: paragraphs|steps|list|mixed, points[], image_slot?{brief, alt, caption?}}`, `faq[3..5]{q, a}`, `cta{text, target: jobs|salon_sale|app|post}`, `hashtags[3..5]`, `sources_used[]`.\
Từng mục: `paragraphs[]` (theo chuẩn mục 4), tùy chọn `bullets[]`/`steps[]`, `h3[]`, `claims[{text, source_id, quote}]`.

Code (không phải model) dựng HTML, link nội bộ, mục Nguồn tham khảo, JSON-LD.

## 7. Cổng chất lượng bổ sung (thêm vào quality gate)

| Mã | Kiểm | Loại |
| --- | --- | --- |
| `kw_title` / `kw_meta` / `kw_slug` / `kw_intro` / `kw_h2` | Từ khóa chính (so sau khi bỏ dấu) có ở từng vị trí | fixable |
| `kw_density` | 0,5–1,5% số từ (cảnh báo nếu > 2,5%) | fixable |
| `intro_len` | Mở bài 50–90 từ, 2 đoạn | fixable |
| `takeaways` | 3–4 dòng, mỗi dòng ≤ 20 từ | fixable |
| `para_len` | Đoạn 40–90 từ (≤ 1 đoạn tới 110), 2–4 câu | fixable |
| `sentence_len` | TB ≤ 20 từ, không câu > 35 từ | fixable |
| `wall_of_text` | > 3 đoạn liền không có ngắt | fixable |
| `cliche` | Có cụm trong danh sách cấm | fixable |
| `faq` | 3–5 câu, trả lời 30–60 từ | fixable |
| `internal_links` | 2–3 link nội bộ hợp lệ | hold nếu 0, fixable nếu 1 |
| `sources_section` | Bài có số liệu luật/thuế/phí/giờ mà thiếu mục Nguồn tham khảo | hold |
| `h1_in_content` | Nội dung có `<h1>` | fixable (code tự bỏ) |

Ngưỡng cũ giữ nguyên: 4–7 H2 thân bài (nay 4–6), metaTitle ≤ 60, meta 140–160, hashtag 3–5, 3–5 vị trí ảnh, slug, trùng bài 90 ngày, căn cứ số liệu.

## 8. Ví dụ dàn ý (hot-trend, slot 06:00)

**Từ khóa chính:** nail mắt mèo nhung · **Phụ:** velvet cat eye nails, sơn mắt mèo mùa đông, nail nhung\
**metaTitle (54):** Nail mắt mèo nhung mùa đông 2026: 5 kiểu khách hay xin\
**Meta (≈150):** Nail mắt mèo nhung đang hot mùa đông 2026. Xem 5 kiểu khách hay yêu cầu, cách làm hiệu ứng nhung không lem và gợi ý tư vấn màu cho từng khách.\
**Slug:** `nail-mat-meo-nhung-mua-dong-2026`

1. Mở bài (2 đoạn) — tín hiệu trend tuần này + bài giúp thợ làm và tư vấn đúng.
2. H2 Tóm tắt nhanh — 4 dòng.
3. [Ảnh 1: cận cảnh móng mắt mèo nhung màu merlot]
4. H2 Nail mắt mèo nhung là gì, vì sao mùa đông này hot?
5. H2 5 kiểu mắt mèo nhung khách hay xin — H3 cho từng kiểu; [Ảnh 2] giữa các H3.
6. H2 Cách làm hiệu ứng nhung không bị lem nam châm — danh sách bước.
7. [Ảnh 3: các bước đặt nam châm]
8. H2 Tư vấn màu theo tông da và dịp
9. H2 Lỗi thường gặp và cách sửa
10. H2 Câu hỏi thường gặp — 4 câu.
11. Kết + CTA (xem tin tuyển thợ làm gel/mắt mèo).

## 9. Bài học từ bài mẫu đầu tiên (2026-10-16, `gemini-3.8-flash`)

Bài mẫu "nail mắt mèo nhung" đạt: từ khóa đủ 5 vị trí, 10/10 đoạn trong chuẩn 40–90 từ và 2–4 câu, câu TB 19,3 từ (dài nhất 21), 0 cụm sáo rỗng trong danh sách, 3 vị trí ảnh, FAQ 4 câu 53–60 từ. Chưa đạt và bổ sung luật:

| Vấn đề | Luật bổ sung |
| --- | --- |
| **1.523 từ** — vượt trần 1.000 của hot-trend | Code cấp **ngân sách từ cho từng mục** khi gọi viết mục (vd mở bài 70, mỗi H2 120–160, FAQ 4×45) và cắt bớt H2/H3 khi vượt; gate vẫn chặn |
| Số kỹ thuật không có nguồn ("2–3 milimet", "10 đến 15 phút") | Prompt cấm mọi con số không có trong tư liệu; mẹo kỹ thuật viết định tính ("giữ nam châm gần mặt móng, không chạm"). Gate `facts` giữ nguyên: số không căn cứ → `held` |
| Tiêu đề viết hoa mỗi chữ ("Nail Mắt Mèo Nhung…") | Tiêu đề và H2/H3 viết **hoa chữ đầu câu** (sentence case), trừ tên riêng/thương hiệu |
| Cụm hoa mỹ chung chung ("tác phẩm khác biệt", "sành điệu", "sang trọng") | Thêm vào danh sách cấm; mỗi câu khen phải đi kèm chi tiết cụ thể |
| Mới chỉ 1 link nội bộ (trong CTA) | Code chèn 2–3 link nội bộ (bài cũ cùng danh mục + trang chức năng) — không để model tự viết link |
| Thời gian sinh 271 giây, 7.987 token suy nghĩ | Viết theo từng mục (như writer hiện tại) và hạ mức suy nghĩ cho lời gọi viết đoạn; outline có thể giữ mức cao |

---

## Changelog
| Version | Date | Author | Change |
| --- | --- | --- | --- |
| v1.0.1 | 2026-10-16 | AlexChu (Claude executor) | Mục 9: luật bổ sung từ bài mẫu đầu tiên |
| v1.0.0 | 2026-10-16 | AlexChu (Claude executor) | Bản đầu: khung bài, khuôn theo danh mục, chuẩn đoạn văn, SEO on-page, schema writer, cổng chất lượng bổ sung; tạm gác tạo ảnh theo Owner |
