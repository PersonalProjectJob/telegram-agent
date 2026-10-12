---
type: doc
title: "Master: Tự động duyệt tin NailHub trên THINKPAD"
status: current
area: automation
created: 2026-10-12
owner: AlexChu
plan_status: active
approved_on: 2026-10-12
approved_by: "Owner (user)"
---

# Tự động duyệt tin NailHub trên THINKPAD

## Metadata
- Version: v1.0.9
- Created: 2026-10-12 17:09 (Asia/Saigon)
- Updated: 2026-10-12 21:15 (Asia/Saigon)
- Status: Approved — Owner duyệt phương án và yêu cầu tiếp tục triển khai trong chat.
- Implementation worktree: `C:/Users/AD/.codex/worktrees/nailhub-postings/AgentBuilder`, branch `codex/nailhub-postings`.
- Executor đã được Owner chọn: Codex · gpt-6.1-sol · high.
- Tracking: `.agent-tasks/T-261012-nailhub-postings-automation-on-thinkpad.md`.
- GitHub issue / PR: chưa có; repo hiện chỉ local. Không tự commit/push.
- Harness / data owner: AlexChu; Codex là runtime thực thi.

## 1. Mục tiêu
THINKPAD tự mở https://nailhub.ai/admin/postings, đọc chi tiết tin Draft,
trích xuất và kiểm tra dữ liệu gốc bằng model local, sửa thông tin, gọi nút Viết bằng AI,
lưu và duyệt tin đạt điều kiện. Owner đã chọn cả quét định kỳ lẫn lệnh Telegram.
Owner chốt: nội dung sau nút AI do flow AI khác phụ trách, ngoài scope worker này;
không kiểm nội dung, không gọi model đối chiếu, không thêm prompt hay sửa lại nội dung AI.
Owner chốt 2026-10-12: lệnh NailHub chỉ nhận qua DM, không nhận trong nhóm/kênh.
Tài khoản điều khiển Owner cung cấp: `@SotaThao`, tài khoản B nhắn riêng
cho tài khoản A của listener. Phải resolve và lưu numeric Telegram user ID;
username chỉ để bootstrap, không dùng làm chốt phân quyền runtime.

## 2. Flow đề xuất để duyệt
1. Nhận job từ lịch quét hoặc lệnh Telegram trong DM đã xác thực Owner.
2. Khóa job theo posting ID; chỉ xử lý Draft trong phạm vi danh mục đã hỗ trợ.
3. View detail, lưu snapshot nội dung gốc và thông tin cần sửa.
4. Model đọc nội dung gốc, trả dữ liệu có cấu trúc và lý do ngắn cho từng thay đổi.
5. Code kiểm tra dữ liệu/đơn vị và căn cứ, rồi mở Edit.
6. Sửa số liên hệ trùng, bổ sung địa chỉ/ZIP có căn cứ, chọn loại lương/giá trị.
7. Bấm Viết bằng AI, chờ hoàn thành; nhận nguyên kết quả từ flow AI có sẵn.
8. Lưu, mở lại detail và đối chiếu giá trị thực tế với thay đổi dự kiến.
9. Chỉ Approve khi tất cả kiểm tra đạt; kiểm tra trạng thái sau Approve.
10. Thiếu dữ kiện gốc, thông tin liên lạc/lương thiếu căn cứ hoặc trạng thái không rõ: giữ Draft,
    lưu lý do và chuyển Owner xem. Không tự Reject.

## 3. Quy tắc dữ liệu

### Số điện thoại
So sánh dạng chuẩn hóa, bỏ dấu cách/dấu câu; coi +1 và số Mỹ 10 chữ số là
cùng số khi đủ điều kiện. Nếu số chính trùng số khác, giữ số chính và xóa
đúng số trùng ở Số điện thoại khác. Nếu trường chứa nhiều số, giữ các số
khác biệt. Không tự suy ra số thiếu, không làm mất extension khác nhau.

### Ngày bắt đầu mặc định lỗi
Owner duyệt: nếu ngày bắt đầu là sentinel 0001-01-01, đổi thành ngày hiện tại UTC khi xử lý vì backend từ chối ngày quá khứ. Audit ghi giá trị cũ/mới; kiểm tra lại sau Save. Không thay ngày đã nhập hợp lệ, không tự sửa ngày kết thúc.

### Địa chỉ và ZIP
Ưu tiên dữ kiện trong tin và trường hiện có. Chỉ điền trường còn thiếu khi
nguồn đủ xác định đúng salon; tra cứu công khai phải khớp tên, thành phố/
tiểu bang và địa chỉ hoặc điện thoại. Không suy ra street address chỉ từ
tên salon, không chọn ZIP chỉ vì cùng thành phố. Nhiều kết quả phù hợp,
nguồn mâu thuẫn hoặc không đủ căn cứ: cần Owner xem.

### Lương
- Nội dung GỐC là nguồn chính; giá trị cũ của form có thể sai.
- Ba kết quả: Có lương tuần / Thương lượng / Không hiển thị, theo đúng
  lựa chọn thực tế của form sau khi khảo sát.
- K/k trong ngữ cảnh tiền = nghìn: 6K = 6000; hiểu cả khoảng 6K–10K,
  6–10K, số có phần thập phân và cách viết tiếng Việt khi rõ nghĩa.
- Quy đổi đề xuất: tháng × 12 / 52; năm / 52; hai tuần / 2.
  Lương ngày/giờ chỉ đổi khi tin cho đủ số ngày/giờ làm trong tuần.
- Ví dụ ảnh Owner: $6K–$10K/tháng ⇒ khoảng $1,384.62–$2,307.69/tuần.
  Không nhập thành $6. Không coi tháng mặc định bằng 4 tuần.
- Giữ phân biệt lương bảo đảm, thu nhập dự kiến, commission và tips.
  Không cộng tips/commission vào mức bảo đảm nếu tin không nói rõ.
- Thương lượng chỉ khi nội dung nói rõ; không có lương thì Không hiển thị.
  Có tiền nhưng thiếu đơn vị/kỳ hoặc mâu thuẫn thì chờ Owner.
- Owner cập nhật quy tắc: khi nguồn ghi khoảng lương, lấy mức CAO NHẤT
  sau khi đổi sang tuần, làm tròn xuống hàng trăm USD cho ô Lương tuần.
  Ví dụ $6K–$10K/tháng ⇒ $1,384.62–$2,307.69/tuần ⇒ nhập $2,300/tuần.
  Nội dung giữ khoảng gốc và kỳ gốc; audit lưu hai đầu khoảng, phép quy đổi
  và giá trị hiển thị. Không giữ Draft chỉ vì form nhận một số.
  Quy tắc này áp dụng cả mẫu Owner có chữ Income / tùy khả năng; ô số là
  mức hiển thị. Việc giữ điều kiện thu nhập trong nội dung thuộc flow AI có sẵn,
  worker không kiểm lại nội dung AI. Không tự cộng tips/commission vào ô lương.
- Quy tắc làm tròn phụ thuộc field thật; lưu giá trị chính xác và phép đổi
  để audit. Không tự làm tròn tăng mức bảo đảm.

### Viết bằng AI
Gọi đúng chức năng có sẵn trên NailHub và chờ hoàn thành. Theo Owner, flow AI
khác đã phụ trách nội dung; không kiểm sự thật/ý nghĩa/tiêu đề sau AI, không gọi
model verify, không thêm hướng dẫn hay tự biên tập kết quả. Worker chỉ xác nhận
lệnh lưu thành công và dữ liệu đọc lại khớp đúng bản đã gửi, cùng trạng thái duyệt.

## 4. Kiến trúc đề xuất
- Worker NailHub riêng; dùng Ollama/model đã có của listener, `think=false`.
  Owner chốt 18:09: bỏ chế độ THINKING vì thông tin đã có; chức năng Viết
  bằng AI của website phụ trách viết lại. Giữ model trích xuất dữ liệu gốc
  và code kiểm thông tin liên lạc/địa chỉ/lương. Owner cập nhật: bỏ toàn bộ
  kiểm tra nội dung sau AI; giữ kiểm tra danh tính tin, lưu và trạng thái duyệt.
- Model trả JSON; code kiểm schema và điều khiển hành động có giới hạn.
  Chỉ sinh dữ liệu cần sửa (lương/địa chỉ); các mảng facts phục vụ đối chiếu
  nội dung cũ luôn rỗng vì không còn được worker sử dụng sau quyết định Owner.
  Nội dung tin là dữ liệu không tin cậy, không được ra lệnh cho worker.
- Lịch và lệnh cùng vào một hàng đợi, có khóa theo tin, chống chạy trùng
  và xử lý crash trước/sau Save/Approve bằng đọc trạng thái thật.
- Không chạy instance Telegram thứ hai dùng cùng session của listener.
  Lệnh quản trị NailHub chỉ nhận trong private DM được cho phép; code kiểm
  loại chat thật của Telegram cùng chat/peer ID và sender ID số của Owner.
  Bỏ qua lệnh NailHub trong group, supergroup và channel, kể cả Owner gửi;
  không tạo job hay issue triage từ những lệnh NailHub bị loại này.
  DM của người ngoài allowlist không được điều khiển worker. Không dựa
  vào username, tên hiển thị, nội dung chuyển tiếp hay phán đoán model.
  Route DM quản trị trước bộ lọc private/outgoing và pipeline triage hiện
  tại, nhưng chỉ ngoại lệ bộ lọc khi qua đủ chốt quyền. Không mở rộng
  triage sang các DM thông thường.
  Nếu Owner dùng chính tài khoản listener, outgoing không mặc nhiên là
  lệnh hợp lệ: vẫn phải kiểm đúng DM peer. Saved Messages/self-chat không
  tự được coi là DM điều khiển đã cho phép; cần Owner chốt riêng nếu dùng.
- Lệnh đề xuất: `/nailhub scan`, `/nailhub status`, `/nailhub pause`,
  `/nailhub resume`. Lịch đề xuất 10 phút/lần, một tin mỗi lượt xử lý.
- Chia sẻ hàng đợi model để listener và worker không gọi suy luận cùng
  lúc trên CPU; ưu tiên listener. Đo timeout trên THINKPAD.
- Chạy systemd bằng user riêng, browser profile riêng đã đăng nhập;
  không lưu mật khẩu/token vào repo, log hoặc báo cáo. Không mở cổng
  điều khiển browser ra mạng.
- Có chế độ preview chỉ đọc và tạo đề xuất trước khi bật ghi/duyệt.

## 5. Phạm vi và người dùng
Owner quản lý tin NailHub, điều khiển lịch qua Telegram và xem ca cần xử lý.
Đợt đầu hỗ trợ tin tuyển thợ có schema khớp ảnh. Danh mục sang nhượng/
rao vặt hoặc schema khác giữ nguyên cho tới khi có quy tắc riêng.
Không thay UI NailHub; không có Visual Gate thiết kế. Vẫn phải test flow
browser thật sau đăng nhập, không dùng đọc code thay cho kiểm chứng live.

## 6. Tiêu chí nghiệm thu
- Số trùng khác định dạng được loại đúng; số khác nhau được giữ nguyên.
- Bộ ca lương kiểm tra K, khoảng, tháng/năm/tuần/ngày/giờ, thương lượng,
  không có lương, thiếu kỳ, commission/tips và mâu thuẫn.
- Địa chỉ không rõ và lương thiếu căn cứ trong dữ liệu gốc không
  dẫn đến Approve.
- Chỉ DM đúng Owner và peer được cho phép mới nhận lệnh; DM người khác,
  lệnh chuyển tiếp và outgoing sai peer không tạo job.
- Lệnh NailHub trong group/supergroup/channel không tạo job hay issue,
  kể cả Owner gửi. Tin nhóm thông thường vẫn theo triage hiện tại.
- Lịch và lệnh trùng nhau, restart và timeout không duyệt lặp.
- Bằng chứng thật cho View → Edit → AI → Save → đọc lại → Approve,
  rồi kiểm trạng thái; preview không gọi thao tác ghi.
- Listener hiện tại vẫn chạy và test không có regression.
- Reviewer độc lập kiểm trước khi bật live.
- Kết quả AI được nhận nguyên; worker không đánh giá lại nội dung sau AI.

## 7. Các giai đoạn
1. Owner duyệt Master và quy tắc, khảo sát form admin/SSH (1h).
2. Đặc tả Product → Engineering → QA → Tasks và review (2h).
3. Model/schema/validation lương/điện thoại/địa chỉ (3h).
4. Browser worker + job state + kiểm tra Save/Approve (4h).
5. Route Telegram + lịch + hàng đợi model (3h).
6. Test offline, browser thật và review độc lập (3h).
7. Cài THINKPAD, Owner đăng nhập, preview rồi bật live (1h).
Ước lượng ban đầu, phụ thuộc quyền admin và contract thực tế.

## 8. Related Documents
- Hạ tầng: [Worker Fleet Master](worker-agent-fleet-1_master_261007_v1.0.8.md).
- Hạ tầng: [Setup Plan](worker-agent-fleet-1_setup-plan_261008_v1.0.6.md).
- Listener: [README](../../listener/README.md).
- [Product](../01-product/nailhub-postings-noissue_product_261012_v1.0.0.md).
- [Engineering](../02-engineering/nailhub-postings-noissue_engineering_261012_v1.0.0.md).
- [QA](../03-qa/nailhub-postings-noissue_qa_261012_v1.0.0.md).
- [Tasks](../04-tasks/nailhub-postings-noissue_tasks_261012_v1.0.0.md).

## 9. Approval & Sign-off
- [x] Owner duyệt flow, quy đổi tháng ×12/52 và giữ Draft khi không đủ căn cứ.
- [x] Owner duyệt lịch đề xuất và phạm vi danh mục.
- [x] Đăng nhập admin NailHub trên profile riêng THINKPAD bằng credential cục bộ đã được Owner cho phép.
- [ ] Reviewer độc lập kiểm implementation trước live.

## 10. Khảo sát và các phần chưa xác minh
- Owner cung cấp SSH `startechai@100.81.90.91` và xác nhận THINKPAD chưa
  nạp admin NailHub. Host thực tế `gw-01`; SSH mật khẩu đã vào được.
- `ollama` và `tg-triage` đều active; `/opt/agentfleet/listener` và
  `/var/lib/agentfleet/tg` tồn tại. Ollama API version 0.40.1;
  `ollama list` có `qwen3.5:9b` (6.6 GB). Python 3.14.4.
- Config thật `/etc/tg-triage/listener.toml`: provider ollama,
  model qwen3.5:9b, think=false, timeout_seconds=600. Chưa đổi config.
- systemd chạy listener bằng user tglistener. Có venv tại
  `/opt/agentfleet/listener/.venv/bin/python`. Python hệ thống không có
  playwright/selenium/telethon; chưa kiểm package trong venv của listener.
- Probe PATH chưa tìm thấy chromium/chromium-browser/google-chrome;
  có `/usr/bin/firefox`. Node chưa cài. Cần kiểm thêm browser kit/venv
  trước khi kết luận toàn máy thiếu runner.
- Tab Edge có phiên admin đã được khảo sát chỉ đọc; form thật dùng Quill,
  nút Cập nhật, lương Có / Không / Thương lượng, chỉ một giá trị số.
  Source local xác nhận URL chi tiết `?id=<UUID>`, API production
  `https://api.nailhub.ai/api/v1/Admin/postings`; cần xác minh response thật.
- Cache Telegram có @SotaThao / 1603321775; cần xác minh qua client đang chạy.
- Owner cho phép lưu credential admin riêng trên THINKPAD để đăng nhập tự động.
  File ngoài repo, user tglistener, quyền 0600; không ghi nội dung vào tài liệu/log.
- Venv đã cài Playwright 1.63.0, dependencies và Chromium; profile admin
  THINKPAD đã đọc thật API HTTP200 và Posting Management. Probe chỉ đọc
  thấy 386 tin Draft tuyển thợ. Chưa chạy live gate.
- Preview reasoning-mode đầu tiên chạm timeout 600 giây; không lưu/duyệt tin.
  Owner sau đó chốt `think=false`; cần thử lại runtime và review bản mới.
- Đã triển khai cùng listener; numeric Owner binding đã xác thực.
- Live gate PASS: model mới 79.29 giây, AI trên website, Save, Approve và fresh read Approved.
  Tin 3f6ecd9d-1fe9-4cbe-9157-8290f12a3c93: lương tuần 2300 USD; số phụ trùng đã xóa;
  địa chỉ/ZIP giữ đúng nguồn; ngày bắt đầu sentinel sửa thành 2026-10-12 UTC.
- Production đã bật: mỗi 600 giây, tối đa 1 tin mỗi lượt; lượt tự động tiếp theo đang phân tích.
- Owner xác nhận đã nhận phản hồi thực tế /nailhub status trong DM từ @SotaThao.
- Bằng chứng: .context/nailhub-evidence/live-proof.png, live-proof.json và runtime.md.
- Code freeze D117DB54: 611 tests PASS; independent 48 PASS và evidence PASS;
  không kiểm nội dung sau AI. Giữ nguyên công việc classifier đồng thời của agent khác.
- Harness verify Node-only không hỗ trợ repo Python; tracking/handoff bị chặn bởi lease cùng owner đã hết hạn.

## Changelog
| Version | Date | Time | Author | Changes | Status |
|---|---|---|---|---|---|
| v1.0.0 | 2026-10-12 | 17:09 | AlexChu (Codex executor) | Draft từ yêu cầu Owner; lưu flow, căn cứ dữ liệu, điều kiện chặn và khảo sát SSH thực tế | Draft |
| v1.0.1 | 2026-10-12 | 17:15 | AlexChu (Codex executor) | Owner chốt chỉ nhận lệnh NailHub trong DM; bổ sung chốt private peer/sender và ca kiểm tra, giữ quét định kỳ | Draft |
| v1.0.2 | 2026-10-12 | 17:20 | AlexChu (Codex controller) | Owner duyệt, chốt @SotaThao, tạo worktree riêng và giao executor triển khai | Approved / Active |
| v1.0.3 | 2026-10-12 | 17:32 | AlexChu (Codex controller) | Liên kết docs con; cập nhật khảo sát form/source, ID cache Telegram và quyền lưu credential cục bộ | Active / Live gate pending |
| v1.0.4 | 2026-10-12 | 17:36 | AlexChu (Codex controller) | Owner đổi cách nhập khoảng lương: lấy đầu trên sau quy đổi tuần, làm tròn xuống hàng trăm; giữ khoảng gốc trong nội dung | Approved amendment / Active |
| v1.0.5 | 2026-10-12 | 18:09 | AlexChu (Codex controller) | Owner bỏ THINKING; giữ trích xuất/kiểm tra dữ kiện và flow Viết bằng AI trên website; cập nhật bằng chứng profile/probe | Approved amendment / Active |
| v1.0.6 | 2026-10-12 | 20:42 | AlexChu (Codex controller) | Owner loại kiểm tra nội dung sau AI khỏi scope; nhận nguyên kết quả, giữ trích xuất dữ liệu gốc và kiểm tra Save/Approve | Approved amendment / Active |
| v1.0.7 | 2026-10-12 | 20:52 | AlexChu (Codex controller) | Bỏ sinh facts không còn dùng; chỉ trích xuất dữ liệu cần điền, giữ chốt dữ liệu gốc và quyền Telegram | Active / Live gate pending |

| v1.0.8 | 2026-10-12 | 21:09 | AlexChu (Codex controller) | Owner duyệt sửa ngày bắt đầu sentinel 0001-01-01 thành ngày hiện tại; bổ sung lỗi Save400 quan sát thật | Approved amendment / Live gate pending |


| v1.0.9 | 2026-10-12 | 21:15 | AlexChu (Codex controller) | Live Save/Approve + fresh read PASS; Owner xác nhận DM; bật quét 600 giây, 1 tin/lượt | Operational |
