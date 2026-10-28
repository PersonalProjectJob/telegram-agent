---
type: doc
title: "NailBlog QA — nghiệm thu Agy-only trên staging"
owner: AlexChu
created: 2026-10-27
status: draft
---

> **Cập nhật đối chiếu v2.0.1 — 2026-10-28 14:17 (Asia/Ho_Chi_Minh).** Tác giả: Codex; trạng thái: chờ review.

**Đối chiếu hiện trạng source:** commit `89a5ac8` trên nhánh `claude/nailblog-automation-review-091e47`. Đây là trạng thái mã nguồn, không khẳng định cấu hình hay phiên bản đang chạy trên THINKPAD. Các mô tả phía dưới có thể thuộc quyết định cũ; khi khác với bản cập nhật này, dùng phần cập nhật và kiểm tra cấu hình thực tế.

## Ca bổ sung và trạng thái bằng chứng 28/10

| Ca | Tiêu chí | Kết quả hiện có |
|---|---|---|
| Tự duyệt staging | Bài `pass` không issue và ảnh hợp lệ tạo hai quyết định đúng snapshot; bài không đạt bị giữ. | Cần chạy xác nhận theo build và cấu hình thực. |
| Hết hạn ảnh | Run được đánh dấu skipped; resume và CLI requeue không tự khôi phục. | Ảnh user cung cấp thể hiện #1, #3, #5 skipped; chưa có kiểm tra runtime độc lập trong lượt tài liệu. |
| Thumbnail mới | Ảnh tạo thật không có chữ hoặc lớp tối, sáng, sinh động và đúng chủ đề; kiểm ở trang danh sách và bài. | Chưa chạy ảnh thật; chưa nghiệm thu trực quan. |
| Run cũ khi đổi build | Kiểm kê và xử lý ảnh/manifest cũ theo binding mới; không gây đăng trùng. | Chưa kiểm trước triển khai. |
| Đăng một bài | Xác nhận bài xuất hiện và readback đúng. | User xác nhận bài đã lên; chưa đối chiếu bằng chứng đọc lại độc lập. |
| Đủ bốn slot | Bốn bài và trạng thái thực tế, không đăng trùng, kèm chứng cứ. | Chưa đủ bằng chứng, không đánh dấu PASS. |

Kiểm thử offline từng được báo cho commit thumbnail: `Ran 19 tests ... OK (skipped=1)`; không thay thế ảnh thật hay nghiệm thu staging. [Kỹ thuật](../02-engineering/nailhub-blog-noissue_engineering_261027_v1.0.0.md) · [Vận hành](../02-engineering/nailhub-blog-noissue_cutover-runbook_261027_v1.0.0.md) · [Nghiệp vụ](../01-product/nailhub-blog-noissue_business_261015_v1.0.0.md).

**Lịch sử thay đổi:** v2.0.1 — 2026-10-28 14:17 — đối chiếu source, vận hành và bằng chứng; không thay đổi trạng thái phê duyệt cũ.

---

> **Nội dung lịch sử bên dưới:** giữ để truy vết; nếu khác với cập nhật phía trên, dùng phần cập nhật và kiểm tra cấu hình thực tế.


# QA: NailBlog Agy-only — ca kiểm thật trên staging

Version: v1.0.0 · 2026-10-27 · Căn cứ: [Plan v2.0](../04-tasks/nailhub-blog-noissue_tasks_261027_v2.0.0.md) · [Engineering v1.0.0](../02-engineering/nailhub-blog-noissue_engineering_261027_v1.0.0.md) · QA cũ: [qa 261019](nailhub-blog-noissue_qa_261019_v1.0.0.md).

Phạm vi: chạy thật trên staging (`contract.environment = staging`), service khởi động paused + disarmed, Owner resume sau preflight. Unit test (`python -m unittest discover -s tests -t .`) đã chạy ở bước dev; tài liệu này là các ca **không thể** thay bằng mock. Kết quả điền vào cột "Kết quả" kèm bằng chứng (log, id bài, ảnh chụp); QA chỉ báo ở thread 735.

## 1. Điều kiện trước

| # | Kiểm | Đạt khi |
|---|---|---|
| P1 | `python -m nailblog startup-gate` | không lỗi, binding khớp |
| P2 | Worker heartbeat | `worker-ready.json` mới dưới 15 giây, policy text/ảnh đúng hash |
| P3 | Sổ chi API cũ | sha256 dump `paid_intents` trước/sau lượt chạy bằng nhau; không có lời gọi API ngoài |
| P4 | `rg -n -i -e api.deepseek -e api.openai -e generativelanguage -e ai_transport nailblog/src` | 0 dòng |

## 2. Một ngày 4 slot

| # | Ca | Các bước | Đạt khi |
|---|---|---|---|
| D1 | Ngày đủ 4 slot | Chốt 4 chủ đề cho 06:00, 10:00, 15:00, 19:00; Owner duyệt ảnh + Draft cho từng bài | 4 bài `published` đúng slot (trễ ≤ 15 phút), 4 `autoflow_runs` `published`, không bài nào đăng 2 lần |
| D2 | Giới hạn ngày | Thử thêm bài thứ 5 trong cùng ngày | bị từ chối `daily_publish_limit`, không POST |
| D3 | Lệch giờ | Duyệt Draft sau hạn `approve_deadline_hours` | `approval_deadline_passed`, bài `held` + một DM |
| D4 | Tải job | Đếm job agy trong ngày | không vượt `max_daily_jobs` (48); job lỗi vẫn tính cap |

## 3. Ca lỗi

| # | Ca | Cách tạo | Đạt khi |
|---|---|---|---|
| E1 | Timeout Create Draft | Cắt mạng đúng lúc `save_draft` (`create_timeout`) | run chờ `create_unknown_reconciling` (không tính lỗi); tìm thấy đúng 1 Draft khớp thì nhận, không POST lại; không thấy sau 30 phút → `held` `create_requires_manual_reconciliation` + đúng 1 DM |
| E2 | Draft lạ trên site | Có đúng 1 Draft cùng tiêu đề nhưng nội dung lệch | chuyển thẳng `create_requires_manual_reconciliation` + DM, không retry tới `too_many_failures`, 1 POST duy nhất trong cả câu chuyện |
| E3 | Trùng bài | Đã có bài cùng tiêu đề/slug trên staging | `duplicate_on_site` trước `claim_create`: 0 bài mới, 0 POST; quét không đầy đủ cũng không tạo |
| E4 | Override đúng | Bài `fixable` (vd `cliche`), Owner override thẻ ảnh và thẻ Draft kèm lý do | bài lên lịch, verdict vẫn `fixable`, chi tiết ghi người duyệt + lý do |
| E5 | Override một nửa | Chỉ override thẻ ảnh | `quality_pass_required`, bài giữ ở `review` |
| E6 | Draft đổi sau override | Sửa Draft sau khi Owner ok | `draft_approval_snapshot_changed`; không lên lịch dù row approved cũ còn |
| E7 | Không override được | Bài có `facts` / Hán lạ / claim y tế | không có nút override; ép bằng DB giả vẫn bị từ chối |
| E8 | Text `unknown` | Giết worker giữa job text | đúng 1 job mới với purpose `#retry1`; lần `unknown` thứ hai → `held` `text_unknown` + DM; không job thứ ba |
| E9 | Ảnh `unknown`/timeout | Giết worker giữa job ảnh | không tự chạy lại; Owner reject mới được thử một lần |
| E10 | Telegram treo | Chặn gửi DM tới Telegram 2 phút | tick vẫn chạy `publish_pass`; DM vào backoff, gửi lại sau khi thông (cùng `random_id`, Owner nhận 1 lần) |
| E11 | Restart giữa chừng | Restart bot lúc có job text/ảnh chạy | gắn lại cùng `job_id`, không dispatch lần hai |
| E12 | Pause/disarm | `/nailblog pause` giữa run | không dispatch/ lưu/ duyệt tiếp; resume chỉ khi binding khớp |

## 4. Kết quả

Điền sau khi chạy: ngày, build/hash, người chạy, bảng pass/fail cho từng mã ca ở trên, đường dẫn bằng chứng. Chưa chạy thì để trống, không ghi "đạt".
