---
type: research
title: "SEO công khai và lựa chọn nguồn nội dung nail cho Nailhub"
status: current
created: 2026-10-29
owner: AlexChu
---

# SEO công khai và lựa chọn nguồn nội dung nail cho Nailhub

- Version: v1.0.0.
- Ngày kiểm tra: 29/10/2026; tổng hợp lúc 09:05 giờ Việt Nam.
- Phạm vi: 3 URL ban đầu và các nguồn bổ sung; nghiên cứu chỉ đọc, không đăng bài, không đổi cấu hình THINKPAD, không commit.
- Master: [Source Mirror Business](../nailhub-source-mirror-noissue_master_261028_v1.0.0.md).
- Kết quả là đề xuất lựa chọn nguồn; không phải quyết định bật thêm nguồn hoặc bằng chứng adapter đã hỗ trợ chúng.

## 1. Kết luận để chọn nguồn

Giữ Scratch Step-by-steps cho flow đã kiểm chứng. Với nguồn bổ sung, ưu tiên khảo sát adapter cho **Professional Beauty, Young Nails và Kiara Sky Learn**; dùng **NAILS Nail Design/How-To** thay cho việc lấy mọi bài trên trang chủ. OPI, The GelBottle và Glitterbels bổ sung chủ đề/mẫu nail; NailKnowledge bổ sung kiến thức chuyên môn có chọn lọc.

Beauty Launchpad còn giá trị nội dung nhưng lượt kiểm tra trực tiếp và Chromium đều gặp challenge HTTP 403. Nailpro đã chuyển về Beauty Launchpad, nên không tính thành nguồn độc lập. Blog Daily Charme được kiểm tra là kho cũ, không phù hợp kỳ vọng tin mới hằng ngày.

Đây là thứ tự ưu tiên **khảo sát nội dung và khả năng tích hợp**, không phải bảng xếp hạng organic traffic hoặc sức mạnh domain.

## 2. Dữ liệu và giới hạn phương pháp

### Đã thực hiện

- [x] Kiểm tra 15 URL đầu vào bằng HTTP chỉ đọc; lưu HTML, HTTP status, redirect, hash và thời điểm.
- [x] Đọc robots.txt của 12 origin: 10 trả 200, hai origin Beauty Launchpad/Nailpro trả 403 ở lượt kiểm tra.
- [x] Kiểm tra 14 URL bài mẫu được chọn đúng nội dung, gồm một mẫu Beauty Launchpad bị chặn. Bốn trang điều hướng do lượt khám phá ban đầu chọn nhầm không được dùng làm mẫu bài viết.
- [x] Kiểm tra thêm 7 trang/template trong context Chromium mới, không đăng nhập, không tương tác giải challenge.
- [x] Đối chiếu chủ đề, ngày bài mẫu, metadata, canonical, schema và khả năng đọc nội dung.

### Không có trong báo cáo

- Không có organic traffic, search volume, keyword difficulty, DA/DR, backlink counts, CTR hay vị trí từ khóa được đo.
- Không có dữ liệu Search Console/GA4 của các publisher; không có Lighthouse/PageSpeed/Core Web Vitals.
- Không crawl toàn bộ website, không kiểm chứng tất cả sitemap URL hoặc tần suất xuất bản cả năm.
- Không suy ra Google đã index từ HTTP 200, canonical, robots cho phép hoặc việc kết quả xuất hiện trong công cụ tìm kiếm.
- Có Article/FAQPage trong JSON-LD chỉ là quan sát khai báo, chưa phải kiểm định rich-result hợp lệ hoặc lời hứa hiển thị trên Google.
- Số H1 là số trong toàn tài liệu; có thể gồm khối gợi ý, không phải mọi trường hợp nhiều H1 đều làm mất khả năng index.
- Đếm ảnh trong vùng article/main/body rộng có thể gồm điều hướng/gợi ý; không dùng con số đó để kết luận số ảnh thực thuộc bài hoặc khả năng qua giới hạn 10 ảnh của Nailhub.
- Thông tin từ kết quả tìm kiếm/bản trích dẫn có thể được cache; HTTP/Chromium được ghi riêng, không dùng bản cache để phủ nhận challenge quan sát trực tiếp.
- Không đăng nhập, vượt paywall/CAPTCHA hay giả danh Googlebot để tải nội dung. Robots.txt không phải giấy phép tái bản.

## 3. Ba nguồn ban đầu

| Nguồn | Quan sát SEO công khai | Điểm phù hợp với Nailhub | Điểm hạn chế / lựa chọn |
|---|---|---|---|
| [Scratch Step-by-steps](https://www.scratchmagazine.co.uk/step-by-step/) | HTTP 200; title 59 ký tự, description 142 ký tự, một H1; canonical tự tham chiếu. Bài mẫu cũng có title/description/H1/canonical, không thấy JSON-LD trong HTML mẫu đã tải. Listing có tác giả/ngày; bài mẫu không cung cấp ngày qua các trường metadata/time mà bộ kiểm tra tìm. | Chủ đề hướng dẫn theo bước rõ, có ảnh; đã có bằng chứng crawl/publish ở lượt triển khai 28/10. | Chỉ một số layout đã được worker hỗ trợ; nội dung theo mùa, ảnh/CDN và giới hạn media cần đối soát. Không coi nguồn này bảo đảm bốn bài mới mỗi ngày. |
| [NAILS Magazine](https://www.nailsmag.com/) | HTTP 200, canonical, title/description; bài mẫu có Article, Person, ngày published/modified. Hai category How-To và Nail Design có H1/canonical nhưng không thấy meta description trong HTML được tải. Bài mẫu có thêm H1 ở khối gợi ý. | Kỹ thuật, thiết kế và kinh doanh salon; đa dạng hơn nguồn chỉ chuyên nail art. | Trang chủ có nội dung giới thiệu thương hiệu/sản phẩm; cần chọn chuyên mục. Mẫu đầu How-To có published 03/07/2025 trong khi mẫu Nail Design có 20/10/2026; vị trí đầu danh sách không đủ chứng minh mới nhất. |
| [Beauty Launchpad Nails](https://www.beautylaunchpad.com/nails) | HTTP trực tiếp 403 ở category và bài mẫu; Chromium category cũng 403/challenge. Metadata `noindex,nofollow` nhận được thuộc trang challenge, không được gán cho nội dung publisher. | Kết quả đọc công khai qua công cụ web cho thấy hướng dẫn, nail art và runway, có bài tháng 10/2026. | Không chấm metadata/category khi bị chặn. Bài mẫu trong bản trích dẫn có nhắc tạo tài khoản để đọc tiếp. Cần xác minh truy cập được phép và adapter riêng trước automation. |

Các nguồn và bài mẫu để đối chiếu: [Scratch mẫu](https://www.scratchmagazine.co.uk/nail_art/blooming-gel-ghost-nail-tutorial/), [NAILS mẫu thiết kế](https://www.nailsmag.com/articles/nail-artist-natalie-minerva-uses-opi-fall-collection-including-fan-favorites), [NAILS mẫu How-To](https://www.nailsmag.com/articles/sweeten-your-clients-summer-mani-with-zola-ganzorigts-lemon-drop-manicure), [Beauty Launchpad mẫu](https://www.beautylaunchpad.com/nails/art/how-to/article/22976220/how-to-wearable-halloween-nail-art).

## 4. Nguồn bổ sung và thứ tự khảo sát

Ưu tiên dưới đây là nhận định từ mức phù hợp chủ đề, độ rõ thông tin, truy cập công khai và bài mẫu; không phải điểm SEO từ công cụ đo thứ hạng.

| Ưu tiên khảo sát | Nguồn / URL | Tín hiệu quan sát | Ưu điểm và nhược điểm |
|---|---|---|---|
| Cao | [Professional Beauty — Nail Art](https://professionalbeauty.co.uk/nail-art) | Category và bài mẫu HTTP 200, canonical. Mẫu cat-eye có một H1, description 136 ký tự, NewsArticle/Person/FAQPage và ngày; Chromium đọc được. | Bao phủ câu hỏi, kỹ thuật, xử lý lỗi và giá dịch vụ, phù hợp ý định tìm kiếm chuyên môn. Mẫu có nhiều Instagram embed: adapter phải xử lý đủ nội dung hoặc giữ bài; không được tự bỏ embed. |
| Cao | [Young Nails — Blog](https://www.youngnails.com/blogs/news) | Category/mẫu HTTP 200; title/description/H1/canonical; Chromium thấy ngày 19/10/2026 và tác giả Habib Salo. Không thấy JSON-LD trong mẫu raw hoặc rendered. | Nhiều nội dung kỹ thuật acrylic/gel, vấn đề nghề nghiệp, dạng câu hỏi/hướng dẫn lâu dài. Nội dung thương hiệu; một số bài dựa vào video, cần kiểm tra bài có đủ thông tin độc lập. |
| Cao | [Kiara Sky — Learn](https://kiarasky.com/blogs/learn) | HTTP 200; Blog/BlogPosting ở listing; bài mẫu một H1, canonical, description 157 ký tự, BlogPosting/Person/FAQPage, ngày 21/08/2026. | Có chủ đề chuyên sâu và liên kết nội bộ theo kỹ thuật/vấn đề. Mẫu cũng là giới thiệu sản phẩm; không coi mọi tuyên bố của thương hiệu là đánh giá độc lập. Nhiều bài cũ, không suy ra nguồn tin mới mỗi ngày. |
| Bổ sung xu hướng | [The GelBottle — Blog](https://thegelbottle.com/blog/) | Category/mẫu HTTP 200, canonical; mẫu có BlogPosting/Person/ngày 23/10/2026. Không thấy H1 trong bài mẫu cả raw lẫn Chromium. | Có nail trends, cat-eye, BIAB và chủ đề theo mùa. Thiên về sản phẩm/thị trường của hãng; tiêu đề dài và heading trong bài cần mapping rõ. Không có H1 trong một mẫu không đồng nghĩa toàn domain không index. |
| Bổ sung hướng dẫn | [OPI — Nail Art](https://www.opi.com/nail-art) | Raw HTML listing không có title/H1; Chromium render thành title, description, một H1 và canonical đúng. Bài mẫu có title/H1/canonical. | Phù hợp hướng dẫn bằng ảnh, từ khóa kỹ thuật và cảm hứng. Phải dùng browser/render, không đánh giá là mất title chỉ từ raw HTML. Chưa xác minh ngày cho toàn bộ kho, chưa có adapter. |
| Bổ sung kiến thức | [OPI — Blog](https://www.opi.com/blog) | HTTP 200; title/description/H1/canonical; bài mẫu cũng có các trường này. Không thấy JSON-LD trong mẫu raw. | Có cụm Nail Care, Nail Trends, Inspiration; hữu ích cho nội dung lâu dài. Sản phẩm hãng xuất hiện nhiều; cần lọc bài theo giá trị cho độc giả, không đồng loạt lấy thông báo bán hàng. |
| Bổ sung theo mùa | [Glitterbels — Blog](https://glitterbels.com/blogs/the-glitterbels-blog) | HTTP 200; bài mẫu có một H1, canonical, Article/Person/ngày 20/10/2026. Listing description ngắn, 16 ký tự; bài mẫu description dài, 320 ký tự. | Có hướng dẫn chọn hệ nail, mẫu nail và đào tạo. Nhiều CTA/sản phẩm, nội dung gắn với hãng; các URL tag và blog cũ dễ bị nhầm thành nguồn hoặc bài độc lập. |
| Chọn lọc chuyên môn | [NailKnowledge — Blog](https://nailknowledge.org/nail-knowledge-blog) | Listing HTTP 200 nhưng không thấy H1/meta description cả raw và Chromium. Bài mẫu có một H1, description 147 ký tự, Article/Person, tác giả, published/modified ngày 26/10/2026. | Có nội dung giải thích câu hỏi chuyên môn và tác giả rõ. Chỉ xét blog công khai, không xét khóa học có đăng nhập. Nội dung sức khỏe/an toàn cần lựa chọn và kiểm chứng chuyên môn, không tự coi là lời khuyên phù hợp mọi thị trường. |

Bài mẫu bổ sung: [Professional Beauty cat-eye](https://professionalbeauty.co.uk/cat-eye-nails-what-are-they-and-how-to-create-them), [Young Nails](https://www.youngnails.com/blogs/news/seo-optimized-headline-master-cuticle-care-essential-steps-for-a-healthier-manicure-and-long-lasting-nail-health), [Kiara Sky](https://kiarasky.com/blogs/learn/hema-free-tpo-free-gel-polish), [The GelBottle](https://thegelbottle.com/blog/hailey-biebers-brown-cateye-nails-are-the-autumn-trend-everyones-copying-heres-why-e85bb6/), [OPI kỹ thuật](https://www.opi.com/nail-art/how-to-use-sticker-stencils), [OPI kiến thức](https://www.opi.com/blog/nail-care/how-to-find-the-best-nail-shape-for-your-hands), [Glitterbels](https://glitterbels.com/blogs/the-glitterbels-blog/which-nail-system-is-right-for-me), [NailKnowledge](https://nailknowledge.org/blog/japanese-manicure-nails-repair-damage).

## 5. Danh sách URL đề xuất sau khảo sát

Danh sách gồm 12 URL trên 10 domain; một domain có nhiều category không được tính thành nhiều nguồn xuất bản độc lập.

| Vai trò | URL |
|---|---|
| Nguồn đã có flow, phạm vi layout hẹp | https://www.scratchmagazine.co.uk/step-by-step/ |
| Thay feed trang chủ NAILS bằng chuyên mục thiết kế | https://www.nailsmag.com/categories/nail-design |
| Kho hướng dẫn NAILS, có bài cũ | https://www.nailsmag.com/categories/how-to |
| Giữ trong shortlist, đang bị chặn ở lượt kiểm tra | https://www.beautylaunchpad.com/nails |
| Nguồn chuyên môn cần adapter mới | https://professionalbeauty.co.uk/nail-art |
| Nguồn đào tạo/kỹ thuật cần adapter mới | https://www.youngnails.com/blogs/news |
| Nguồn kỹ thuật/câu hỏi cần adapter mới | https://kiarasky.com/blogs/learn |
| Nguồn xu hướng theo mùa cần adapter mới | https://thegelbottle.com/blog/ |
| Nguồn hướng dẫn cần render và adapter mới | https://www.opi.com/nail-art |
| Nguồn kiến thức/xu hướng cần adapter mới | https://www.opi.com/blog |
| Nguồn thương hiệu cần adapter mới | https://glitterbels.com/blogs/the-glitterbels-blog |
| Nguồn kiến thức công khai, cần chọn lọc và adapter mới | https://nailknowledge.org/nail-knowledge-blog |

### Không ưu tiên làm feed tin mới

- [Nailpro](https://www.nailpro.com/): công cụ web quan sát redirect về [thông báo sáp nhập Beauty Launchpad](https://www.beautylaunchpad.com/business/news/22939516/important-news-regarding-nailpro). Thông báo ngày 28/04/2025 nêu kế hoạch chuyển nội dung và đóng website. Đây là nguồn trùng hệ xuất bản, không phải nguồn mới độc lập; lượt HTTP trực tiếp hiện gặp 403 nên không ghi một mã redirect HTTP đo trực tiếp giả định.
- [Daily Charme blog](https://dailycharme.com/blogs/daily-charme): ngày mới nhất hiển thị trên listing được đọc là 09/05/2015. Có thể dùng tham khảo kho cũ, không chọn cho mục tiêu bài mới mỗi ngày. Kết luận này chỉ nói về blog được kiểm tra, không nói cửa hàng ngừng hoạt động hay domain có traffic thấp.

## 6. Ý định tìm kiếm và giới hạn SEO cho Nailhub

Các nhóm dưới đây được suy ra từ tiêu đề/chuyên mục, **chưa có volume, độ khó hoặc vị trí từ khóa**:

| Nhóm ý định | Chủ đề tham khảo | Nguồn phù hợp |
|---|---|---|
| Hướng dẫn thao tác | cat-eye, chrome, dip powder, acrylic, nail art từng bước | Scratch, Young Nails, Kiara Sky, OPI Nail Art |
| Giải thích / so sánh | builder gel vs hard gel, chọn hệ nail, chọn dáng nail | Glitterbels, Kiara Sky, OPI Blog, NailKnowledge |
| Xu hướng theo mùa | autumn, Halloween, Christmas, wedding nail art | Scratch, NAILS Design, The GelBottle, OPI |
| Kinh doanh / nghề nghiệp | định giá nail art, ảnh nail, giữ khách salon | Professional Beauty, NAILS; chọn đúng chuyên mục |

SEO của publisher không tự chuyển sang Nailhub chỉ bằng việc đăng lại. Google giải thích rằng canonical/redirect là tín hiệu giúp chọn URL đại diện cho nội dung giống hoặc tương tự; Google vẫn có thể chọn phiên bản khác với URL ưu tiên. Việc sao chép nhiều nội dung mà không thêm giá trị còn được đề cập trong chính sách spam khi có mục đích thao túng tìm kiếm. Không kết luận cứ đăng lại là vi phạm hoặc bị phạt, cũng không hứa số bài đăng sẽ tạo tăng trưởng SEO.

Nguồn chính thức: [Google về canonical](https://developers.google.com/search/docs/crawling-indexing/consolidate-duplicate-urls), [Google về spam policies](https://developers.google.com/search/docs/essentials/spam-policies).

Flow hiện tại vẫn giữ nguyên yêu cầu mirror nội dung nguồn; báo cáo này không tự bật AI viết lại, dịch, thay canonical trên PROD hoặc đổi chính sách xuất bản. Nếu sau này muốn thêm nội dung gốc/biên tập để theo đuổi SEO, đó là quyết định business riêng. Chưa có xác nhận quyền tái bản toàn văn/media của các nguồn mới.

## 7. Dữ liệu gốc và khả năng kiểm tra lại

[Bảng metadata CSV — 40 quan sát](nailhub-source-mirror-noissue_seo-metadata_261029.csv) được lưu cùng báo cáo để có thể đọc trong repo chính hoặc worktree. Phải giữ cột scope/state khi phân tích; các hàng trang chặn không đại diện metadata của bài thật.

Evidence local:

`C:/Users/AD/.codex/visualizations/2026/10/28/01a1820c-13c8-7641-9460-51a0f19a5d77/seo-audit-20261029/`

- `seed-results.json`: 15 seed URL và metadata HTTP/HTML.
- `robots-results.json`: 12 origin và sitemap được khai báo; chưa kiểm toàn bộ XML.
- `article-results.json`: 10 kết quả khám phá ban đầu; bốn mục là trang điều hướng, loại khỏi cohort bài viết.
- `manual-article-results.json`: tám mẫu đã chọn bổ sung để sửa lỗi nhận diện trang điều hướng.
- `rendered-results.json`: bảy lượt Chromium; challenge giữ nguyên, không tương tác để vượt.
- Các file HTML/robots được đặt theo hash URL; mỗi HTML có SHA-256 trong JSON.
- `technical-summary.csv`: bảng gộp metadata, có nhãn listing/article/exploratory-navigation để không nhầm scope.

Script nghiên cứu chỉ đọc nằm ngoài repo ứng dụng, cùng thư mục evidence cha. Các số liệu là snapshot của lượt kiểm tra, không phải dịch vụ theo dõi SEO liên tục.

## 8. Công việc tiếp theo được đề xuất

1. Owner chọn nhóm nội dung ưu tiên; các URL trong báo cáo chưa tự động được thêm vào worker.
2. Xây và test adapter riêng cho từng nguồn được chọn, gồm URL bài/chuyên mục, nội dung xen ảnh, caption, embed và Summary/card.
3. Xác minh nguồn được sử dụng và không bỏ qua nội dung bị khóa hoặc thành phần không thể biểu diễn đầy đủ.
4. Chỉ đưa vào lịch hoặc lệnh `nailblog <URL>` sau E2E thật; lệnh này hiện mới là đặc tả business.
5. Muốn xếp hạng theo hiệu quả tìm kiếm, cần bổ sung dữ liệu chuyên dụng về traffic/từ khóa/backlink và đo Search Console của chính Nailhub. Không dùng điểm ưu tiên khảo sát trong báo cáo làm điểm SEO.

## Changelog

| Version | Date | Time (VN) | Author | Nội dung |
|---|---|---|---|---|
| v1.0.0 | 2026-10-29 | 09:05 | AlexChu / Codex | Kiểm tra SEO công khai 15 seed URL, 14 bài mẫu được chọn và bảy lượt render; đề xuất 12 URL trên 10 domain, ghi giới hạn đo và chưa thay runtime. |
