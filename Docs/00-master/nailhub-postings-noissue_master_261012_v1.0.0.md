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
- Version: v1.0.25
- Created: 2026-10-12 17:09 (Asia/Saigon)
- Updated: 2026-10-14 13:32 (Asia/Saigon)
- Status: Approved — Owner duyệt phương án và yêu cầu tiếp tục triển khai trong chat.
- Implementation worktree: `C:/Users/AD/.codex/worktrees/nailhub-automation/AgentBuilder`, branch `codex/nailhub-automation`.
- Executor hiện được Owner chọn: Codex · gpt-6-sol · high; thay gpt-6.1-sol bị at capacity theo quyết định Owner trong chat.
- Tracking: `.agent-tasks/T-261012-nailhub-postings-automation-on-thinkpad.md`.
- GitHub: PersonalProjectJob/telegram-agent, nhánh codex/nailhub-automation; commit cba37eb đã push theo lệnh Owner. Chưa có issue/PR; bổ sung held-report v1.0.20 và docs/agent handoff v1.0.21 hiện chưa commit/push.
- Harness / data owner: AlexChu; Codex là runtime thực thi.

## 1. Mục tiêu
THINKPAD tự mở https://nailhub.ai/admin/postings, đọc chi tiết tin Draft hoặc Approved chưa Xuất bản,
trích xuất và kiểm tra dữ liệu gốc bằng model local, sửa thông tin, gọi nút Viết bằng AI,
lưu và duyệt tin đạt điều kiện. Owner đã chọn cả quét định kỳ lẫn lệnh Telegram.
Owner chốt: nội dung sau nút AI do flow AI khác phụ trách, ngoài scope worker này;
không kiểm chất lượng nội dung hoặc gọi model đối chiếu sau AI. Ngoại lệ Owner chốt ở BR-14: chỉ sửa cụm nhãn đơn vị đăng tin Nailhub.ai theo đúng ngôn ngữ/danh mục; giữ phần văn bản khác.
Owner chốt 2026-10-12: lệnh NailHub chỉ nhận qua DM, không nhận trong nhóm/kênh.
Tài khoản điều khiển Owner cung cấp: `@SotaThao`, tài khoản B nhắn riêng
cho tài khoản A của listener. Phải resolve và lưu numeric Telegram user ID;
username chỉ để bootstrap, không dùng làm chốt phân quyền runtime.

## 2. Flow đề xuất để duyệt
1. Nhận job từ lịch quét hoặc lệnh Telegram trong DM đã xác thực Owner.
2. Khóa job theo posting ID; xử lý Draft hoặc Approved với isPublic=false được xác nhận, trong ba danh mục hỗ trợ; giữ khóa completed/uncertain.
3. View detail, lưu snapshot nội dung gốc và thông tin cần sửa.
4. Model đọc nội dung gốc, trả dữ liệu có cấu trúc và lý do ngắn cho từng thay đổi.
5. Code kiểm tra dữ liệu/đơn vị và căn cứ, rồi mở Edit.
6. Sửa số liên hệ trùng, bổ sung địa chỉ/ZIP có căn cứ, chọn loại lương/giá trị.
7. Bấm Viết bằng AI, chờ hoàn thành; nhận nguyên kết quả từ flow AI có sẵn.
8. Lưu, mở lại detail và đối chiếu giá trị thực tế với thay đổi dự kiến.
9. Draft: Approve sau khi Save đúng. Approved chưa public: Save kết quả AI khi còn false, rồi bật Xuất bản và Save riêng. Xác nhận cuối Approved và isPublic=true.
10. Dữ liệu mâu thuẫn hoặc thao tác chưa rõ: dừng, lưu chẩn đoán và chuyển Owner xem; không khẳng định còn Draft khi chưa xác nhận. Không tự Reject.

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
- Không có mức lương rõ: Thương lượng. Có mức số không đơn vị: điền nguyên, không tự suy đoán kỳ. Khoảng/mức nhiều loại lấy đầu cao theo quy tắc Owner; giá dịch vụ bị loại; mâu thuẫn thật cần xác nhận.
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
- [Business — quy tắc hiện hành, báo cáo49tin và bản nháp issue](../01-product/nailhub-postings-noissue_business_261013_v1.0.0.md).
- [Hướng dẫn agent NailHub](../../listener/src/tg_triage/nailhub/AGENTS.md).
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

## Bổ sung Owner: phản hồi tin cần kiểm tra thủ công qua DM
Owner yêu cầu worker gửi link tin và lý do phải kiểm tra thủ công qua DM.
- Chỉ gửi đến numeric Owner đã xác thực, bằng chính client Telegram của listener đang chạy.
- Tự gửi cho tin needs-human và uncertain; gửi bù các tin đang chờ khi triển khai.
- /nailhub status trả kèm danh sách link và lý do tiếng Việt; nội dung giới hạn an toàn Telegram.
- Có trạng thái giao thông báo bền vững; chỉ đánh dấu đã gửi sau thành công, retry có nhịp khi lỗi.
- Link dùng UUID đã xác thực; không để title/content thay đổi đích gửi hoặc lệnh; không gửi credential.
- Giữ phân biệt needs-human với uncertain: uncertain cần kiểm tra trạng thái thực tế trước khi thao tác.
- Không thêm kiểm tra nội dung sau AI, không thay lương/date policy, không tự thử lại Save/Approve.
Trạng thái bổ sung: Operational — đã triển khai, gửi bù 2 DM và xác minh message ID thực tế; quét định kỳ đã tiếp tục.

| v1.0.10 | 2026-10-12 | 21:28 | AlexChu (Codex controller) | Owner yêu cầu DM tự động kèm link/lý do tin needs-human/uncertain và danh sách trong status | Approved amendment / Implementation pending |

| v1.0.11 | 2026-10-12 | 21:46 | AlexChu (Codex controller) | DM deployed; hai tin gửi bù có message ID 139/140; resume quét, full623 và independent29 PASS | Operational |

### Bằng chứng triển khai DM thực tế
- Candidate8 files: SHA256 0EAA3080129239C2047C763080CD558199B9094DCDB83958E8DEA426887C8B10.
- Full suite623 PASS40.492s; independent29 PASS5.006s; compile và diff check PASS.
- Bundle manifest94504DA556A5C227263F1C707CDC95D8C00BFB688230406B1397F0FD42DAB9E0.
- Backup /var/backups/agentfleet/nailhub-dm-20261012T144420Z; source+installed5 modules cùng hash; classifier đồng thời giữ nguyên.
- Cùng client đang chạy đã gửi Owner1603321775: tin2fe2ceb9-3d2d-45bd-880f-c99da56de92b message139; tinbde8b947-fd25-4079-8cd1-bb8759e09056 message140.
- Proof .context/nailhub-evidence/dm-delivery-proof.json lưu ACK thực tế, link và lý do tiếng Việt. Không tạo client/session thứ hai.
- Resume paused=False; worker running=True; hai ACK giữ nguyên sau resume. /nailhub status và /nailhub pending hiển thị link/lý do hiện tại.
- Gửi có retry và dedup bền vững; crash sau Telegram nhận nhưng trước lưu ACK vẫn có khả năng gửi lại.
- Harness verify Node-only không hỗ trợ repo Python; CAS same-owner lease vẫn lỗi đã ghi nhận. Không tự sửa harness. Bổ sung này chưa commit/push.

## Owner amendment: input values and actionable DM
Owner explicitly requests field/current value/action in held DM; no-pay posts use hidden salary despite model extraction errors; fields are corrected from posting content. Numeric pay without period is copied unchanged into numeric input (7000 =>7000), with no inferred conversion. Explicit periods retain approved conversion. AI rewritten content remains out of scope. Operational; .context/NAILHUB_FIELD_POLICY_SCOPE.md tracks verification/deployment.


### v1.0.12 — source fields and actionable DM (Operational)
- Tin không nhắc lương: chọn Không hiển thị; lỗi trích xuất lương của model không tự giữ Draft. Lương số không có đơn vị thời gian: điền nguyên số (7000), không suy đoán/quy đổi. Khoảng số lấy mức cao nhất và làm tròn xuống hàng trăm; quy đổi chỉ khi nguồn ghi rõ đơn vị.
- Sửa cả ô đã có giá trị nếu nội dung gốc xác nhận khác; địa chỉ rõ city/state/ZIP có thể sửa state sai dù model trích xuất sai. Các nguồn mâu thuẫn thật vẫn cần kiểm tra.
- DM v2 ghi trường, giá trị đã đọc, nguồn/căn cứ, việc cần làm, phân biệt lỗi dữ liệu với lỗi tự động hóa. Same listener/client, Owner DM numeric authentication không đổi.
- Không kiểm tra nội dung sau nút AI; chỉ giữ kiểm tra lưu/readback/trạng thái và nguồn mới nhất.
- Final full637 PASS31.286s; independent39 PASS2.610s; compile/diff check PASS. State-repair independent72 PASS; field-policy independent88 PASS. DOM fixture bổ sung không được tính là live E2E.
- Guarded deployment: D6AE7modules backup20261012T152238Z; statevalidation-only backup20261012T153950Z; widgetbrowser-only backup20261012T154820Z. Tất cả18 runtimehashes khớp; classifier/telegram concurrent changes giữ nguyên.
- Fresh actual UI read: 2fe2ceb9-3d2d-45bd-880f-c99da56de92b Approved, salary hidden, phone_other rỗng. bde8b947-fd25-4079-8cd1-bb8759e09056 Approved, salon Pure Nails Spa, state WV/city Charles Town/ZIP25414, salary7000.0, hai số khác nhau giữ nguyên.
- Proof: .context/nailhub-evidence/nailhub-field-final-proof.json và nailhub-final-runtime-hashes.json. DM142 có ACK Owner1603321775. Sau fresh read đã resume: paused0/pending0/running0, approved4, không còn needs-human/uncertain. Quét10phút tiếp tục.
- Harness verify chỉ hỗ trợ Node/package.json, repo Python nên không PASS gate đó; dùng kết quả Python/test/reviewer và thực tế ở trên. Không sửa harness; bổ sung này chưa commit/push.


### v1.0.13 — Owner override: lương chưa rõ → Thương lượng (Operational)
Quy tắc này thay thế mặc định Không hiển thị của v1.0.12: nguồn không nhắc hoặc không nêu được mức lương cụ thể thì chọn Thương lượng, amount0; không giữ Draft chỉ vì model trích xuất lương/evidence lỗi. Các câu như “Bao lương trên ăn chia” và “ăn chia” không có số lương được xử lý theo quy tắc này. Tiền tip/thuê/hoa hồng riêng không được lấy làm lương số. Mức tiền rõ, không đơn vị điền nguyên số, quy đổi đơn vị rõ và khoảng lương vẫn giữ chính sách đã duyệt. Xung đột thật của mức lương số vẫn giữ kiểm tra thủ công có chi tiết. Không đổi nội dung flow AI, contact/source/save/readback/permission guards.
- Actual case4148d7cf-d89d-42f5-85f7-7f2712ea2494: nội dung “Bao lương trên ăn chia”, “20 phút lái xe”; trước đó model gây unrecognized amount/range dù form negotiated0. Bản sửa nguồn trực tiếp chọn negotiated;20phút không phải lương.
- Final639 PASS28.861s; independent93 PASS7.664s; compile/diffcheckPASS. Frozen5files3509436744D2D76233B98AA83841C9115765140346B80941AF1FCEB9E1F48483.
- Runtime chỉvalidation.py/model.py source+installed; guardedmanifest507B8575, backup/var/backups/agentfleet/nailhub-field-20261012T162643Z;18 runtimehashesPASS, classifier/Telegram giữ nguyên. Cùng listener/client, không mở Telegram session thứ hai.
- Normalworker actualextractionvalidated133.385s, AI/Save/Approve/readbackPASS. Fresh UI navigation độc lập xác nhận Approved, salary_type negotiated, salary_amount0.
- Proof .context/nailhub-evidence/nailhub-negotiated-final-proof.json và nailhub-negotiated-runtime-hashes.json. Resume paused0/pending0/running0, approved7, không còn needs-human/uncertain. Quét10phút tiếp tục.
- Không sửa hàng loạt tin Approved cũ. Harness verify vẫn Node-only/package.json absent; Python scriptedtest/reviewer/liveproof ở trên. Bổ sung chưa commit/push.


### v1.0.14 — Tự sửa city/ZIP thiếu từ địa chỉ crawl (Operational)
Owner yêu cầu suy luận để sửa crawl khi có căn cứ. Chỉ từ tiểu bang NY không suy ra thành phố New York hay ZIP. Địa chỉ hiện có đầy đủ street/city/state/ZIP có thể làm nguồn riêng để điền ô city/ZIP còn trống. Parser bounded, state phải khớp, kiểm tra mâu thuẫn với trường đã điền/nội dung gốc; ghi audit source_field address. Không nới lỏng nguồn của các model suggestions khác. Thành phố phải tồn tại trong tùy chọn chính xác của form trước AI/Save. Không thay chính sách lương Thương lượng hay kiểm nội dung sau AI.
- Actual2722b005-6226-4503-bce5-299e70e5515b Luxe Spa: address279 E Fairmount Ave Lakewood, NY14750, stateNY, city/ZIPtrống, bodykhôngcóđịachỉ. TựđiềnLakewood/14750,giữNY/haiđiệnthoại/Thươnglượng.
- Freeze4535B0C4FBF893F4D6AC6CEB164C20F9E2B8DF4780EDD318A6E08EBC95E7C28E; full641PASS37.060s, independent77PASS4.263s; compile/diffcheckPASS.
- GuardeddeployONLYvalidation.py source+installed/preimagechecks, manifest4C52C67E, backup/var/backups/agentfleet/nailhub-field-20261012T165538Z.18runtimehashesPASS; otherlistenerfilespreserved.
- Normalworkeractualextractvalidated82.470s thenAI/Save/Approve/readbackPASS. IndependentfreshUIread: Approved, cityLakewood,stateNY,ZIP14750,salarynegotiated.
- Proof .context/nailhub-evidence/nailhub-address-final-proof.json và nailhub-address-runtime-hashes.json. Resume paused0/pending0/running0/countsapproved8, nohumanholds. Quét10phút tiếp tục. Không commit/push bổ sung này.


### v1.0.15 — giá dịch vụ / mức thu nhập cao nhất (Operational)
- Giá dịch vụ khách trả, ví dụ “Giá gội đầu: $70, $110, $170”, không phải lương. Nếu chỉ có các giá này và không có mức thu nhập thợ thì chọn Thương lượng.
- Owner chốt tin có cả giá dịch vụ và thu nhập rõ: bỏ giá dịch vụ, quy đổi các mức thu nhập thợ theo đơn vị cục bộ của từng khoản, lấy cao nhất và làm tròn xuống hàng trăm. Actual2500+/2tuần và2900+/2tuần →1450→1400/tuần.
- Nhiều mức theo tay nghề:1000/1500tuần và700–900/tuần →1500. Monthly6000–8000→8000×12/52→1846.15→1800.
- Không lấy tiền thuê/tip/thưởng/hoa hồng riêng làm lương chính; đơn vị thực sự mâu thuẫn vẫn HOLD có chi tiết. Số lương không đơn vị vẫn điền nguyên không suy đoán.
- Source salary policy tính từ nguồn đủ rõ trước salary model schema/evidence, sửa các lỗi model negotiated-with-numeric và1000/1500 chưa nhận. Dùng raw original offsets/unmasked evidence kể cả Unicode NFD.
- Final644PASS33.168s; independent98PASS7.229s; frozen6manifestC550691669C8FBB91DE889D7F7C3784E62ABD35ED17AC39222104A1C0A99B655. Runtimevalidation/modelonly.
- Guardeddeploymanifest246D6B31 backup/var/backups/agentfleet/nailhub-field-20261013T014511Z;18runtimehashesPASS, classifier/Telegram unchanged. Requeuedonly1a3b/6f03/56dc salaryholds. Existingworker maxbatch1; normalworker liveverificationPASS.
- Các lỗi canonical tiểu bang LA/Louisiana,KY/Ky đã được chẩn đoán nhưng không thuộc diff này. Không commit/push bổ sung này.


2026-10-13 08:58 LivePASS: normalexistingworker3casesactualAI/Save/Approve/readback; independentfreshUI allApproved:1a3bweekly1400.0,6f03weekly1500.0,56dcweekly1800.0. Proof .context/nailhub-evidence/nailhub-service-price-final-proof.json and nailhub-service-price-runtime-hashes.json. Resume paused0/pending0/running0/countsapproved48needs-human12; otherholdsuntouched. Full644PASS33.168s, independent98PASS7.229s,18runtimehashesPASS, freezeC5506916/manifest246D6B31/backup20261013T014511Z. HarnessverifyNode-onlyexit1/no package.json; no forgedgate. No commit/push.


### v1.0.16 — Giá bán/sang nhượng thấp nhất (Operational)
- Owner chốt chỉ lấy giá bán/sang nhượng thấp nhất, không lấy số thấp nhất trong mọi khoản tiền. Khoảng giá bán lấy đầu thấp; K là nghìn. Điền nguyên giá, không quy đổi tuần và không làm tròn hàng trăm.
- Bỏ doanh thu, tiền thuê, giá dịch vụ, tip và lương thợ khỏi các giá bán ứng viên. Tin vừa bán tiệm vừa tuyển thợ ưu tiên giá bán; giữ nguyên danh mục hiện có.
- Phải có ngữ cảnh bán/sang nhượng tiệm rõ ràng; không lấy giá sản phẩm/dịch vụ, giá định kỳ hay câu phủ định việc bán làm giá sang nhượng.
- Actual03908741-40a8-4579-b941-374a1a9c54d2: “Bán tiệm với giá 60k” và doanh thu30k–35k/tháng →60000. Normal worker AI/Save/Approve/readback thành công; fresh UI navigation xác nhận Approved, salary_type weekly (ô số “Có”), salary_amount60000.0. Đây là giá bán nguyên số theo Owner, không phải thu nhập tuần.
- Full646PASS37.006s; independent100PASS8.466s; frozen4manifestD9B62A57A4B4EE070C607CAE10EB08105348AAAF7E3F653D3177773485DB5178. Runtime chỉvalidation/model;18hashesPASS giữ classifier/Telegram.
- Guardeddeploymanifest38855C41, backup/var/backups/agentfleet/nailhub-field-20261013T021021Z. Chỉ requeue tin0390; các hold khác giữ nguyên. Proof .context/nailhub-evidence/nailhub-sale-price-final-proof.json và nailhub-sale-price-runtime-hashes.json. Resume paused0/pending0/running0,approved50,needs-human11.
- Harnessverify exit1: Không thấy package.json — harness-verify hiện chỉ hỗ trợ project Node. Không giả lập gate; Python tests/reviewer/live proof ở trên. Bổ sung chưa commit/push.

Changelog v1.0.16 | 2026-10-13 09:16 (Asia/Saigon) | AlexChu (Codex controller) | Owner sale-price minimum amendment deployed and fresh UI verified.


### v1.0.17 — Danh mục theo nội dung gốc (Operational)
- Owner yêu cầu sửa Danh mục theo ý định bài: tuyển thợ → Cần thợ nail (JobPosting); bán/sang tiệm → Sang nhượng tiệm (SalonForSale); thợ tìm chỗ làm → Tìm việc (Portfolio). Tin vừa bán tiệm vừa tuyển thợ ưu tiên Sang nhượng tiệm. Quy tắc này thay thế việc giữ nguyên danh mục của v1.0.16.
- Phân loại từ nguồn gốc có căn cứ, trước AI; hỗ trợ đủ ba danh mục trong proposal, Save payload, readback và recovery. Giá bán dùng Nhập giá/Fixed; không rõ giá dùng Thương lượng; Tìm việc không có ô lương, gửi null. Giữ chính sách giá bán thấp nhất và thu nhập thợ cao nhất đã duyệt.
- Sửa đúng hai tin Approved đã xác định qua UI, không viết AI hay duyệt lại: 987c7202-2124-4e6d-90d0-05e55dff1f33 Westmont → SalonForSale, Thương lượng/0; 03908741-40a8-4579-b941-374a1a9c54d2 → SalonForSale, Fixed/60000. Không đổi số tiền. Fresh navigation/read xác nhận cả hai vẫn Approved, nội dung/tiêu đề/liên lạc/địa chỉ/ngày lưu và ngày xuất bản giữ nguyên.
- Form đổi danh mục gọi GetTranslation bất đồng bộ; adapter đợi phản hồi rồi khôi phục tiêu đề gốc. Category-only repair giới hạn đúng hai ID, paused+idle, fingerprint hiện tại, checkpoint bền vững và bảo vệ mọi dữ liệu khác; không mở sửa hàng loạt Approved.
- Actual HTTP400 POSTING_START_DATE_CANNOT_BE_PAST: ngày đã lưu 2026-10-12 nhỏ hơn ngày xử lý. Backend AdminUpdatePostingCommand giữ ngày cũ khi request StartDate=null. Category-only repair dùng biểu diễn null có guard khi ngày lưu không đổi; readback vẫn xác nhận ngày lưu và publication nguyên vẹn. Không đổi ngày thật để vượt validator.
- Initial freeze 4B3733E68F6BFD26E078AC83A38B738D0DB341B3AE138509F7513554F0FDBD0C: full656 PASS36.237s, independent110 PASS8.058s. Final date guard freeze60153FCB2D4BEBDB9D1A4CEB389CED622B592C13BF2FC54CB7D733102887B772: full657 PASS47.843s, independent59 PASS5.100s. Compile/diff checks PASS.
- Guarded initial deploy manifest7235FD98 backup/var/backups/agentfleet/nailhub-field-20261013T023857Z; date guard manifest8D30D273 backup/var/backups/agentfleet/nailhub-field-20261013T024535Z. Final22 source/installed runtime hashes PASS; classifier và Telegram giữ nguyên, cùng listener/client.
- Actual UI screenshot hiển thị Westmont: Sang nhượng tiệm. Proof: .context/nailhub-evidence/nailhub-category-final-proof.json, nailhub-category-final-proof.png, nailhub-category-runtime-hashes.json. Sau proof đã resume paused0/pending1/running0, approved50/needs-human11; các hold khác giữ nguyên. Scripted Draft regression PASS; không coi category-only live repair là live full Draft E2E.
- Harness verify exit1 vì Python repo không có package.json (tool chỉ hỗ trợ Node); CAS same-owner expired lease vẫn từ chối takeover/heartbeat. Không giả lập gate hay sửa harness. Code/runtime/evidence ở trên là kết quả kiểm tra thực tế. Bổ sung chưa commit/push.

Changelog v1.0.17 | 2026-10-13 09:51 (Asia/Saigon) | AlexChu (Codex controller) | Owner category amendment deployed; two Approved examples corrected and freshly verified.


### v1.0.18 — Approved chưa xuất bản phải qua AI trước khi public
Owner đã duyệt cập nhật trong chat ngày 2026-10-13. isPublic là checkbox Xuất bản, độc lập Approved. Workflow nhận Draft hoặc Approved với isPublic=false xác định rõ; không nhận Approved public hay isPublic thiếu/không rõ. Durable completed/uncertain locks vẫn giữ, không chạy lại hàng loạt tin hoàn tất.
- Approved chưa public: kiểm dữ liệu/danh mục/lương theo nguồn gốc → Edit → Viết bằng AI → Save giữ isPublic=false → fresh read khớp → bật checkbox Xuất bản và Save → fresh read Approved/isPublic=true. Không gọi Approve cùng trạng thái để thay thế publication vì backend không bật public khi trạng thái đã Approved.
- Draft: giữ flow AI/Save/Approve; backend tự bật public khi chuyển sang Approved, final readback phải xác nhận đúng sự chuyển đổi này cùng publishedAt hợp lệ.
- Không bổ sung lọc nguồn crawl hay semanticcheck sau AI: Owner xác nhận site AI đã loại nguồn crawl. isPublic=false chỉ là điều kiện nhận xử lý, không chứng minh tin chưa từng chạy AI.
- Checkpoint trước mọi write; crash recovery đối soát đúng cả publicflag, không coi Approvedfalse là hoàn tất; không blindretry Save/publish. Ngày hợp lệ đã lưu được bảo toàn, chỉ dùng biểu diễn requestnull khi audited path cho phép và backend giữ nguyên; sentinelpolicy cũ giữ nguyên.
- Một executor hiện có Codex/gpt-6.1-sol/high; review độc lập, scripted regression và actual browser flow trước khi hoàn tất triển khai. Runtime hiện tạm pause để triển khai có kiểm soát; tình trạng triển khai và evidence sẽ được ghi tiếp khi xác nhận.

Changelog v1.0.18 | 2026-10-13 11:20 (Asia/Saigon) | AlexChu (Codex controller) | Owner approved unpublished Approved workflow; implementation and verification in progress.

Bổ sung contract crawler ngày 2026-10-13: ID tuần tự .NET có dạng GUID chuẩn nhưng không mang variant RFC mà worker/frontend deep link từng yêu cầu. Worker nhận GUID hex8-4-4-4-12 và với ID không mở được deep link thì điều hướng listing, ràng buộc ID với đúng row trong cùng phản hồi/trang, kiểm các ô hiển thị, bấm View rồi xác nhận detail ID/nội dung. Không thay frontend NailHub. Link DM trực tiếp của nhóm ID này vẫn bị frontend từ chối; Owner mở danh sách và tìm theo tiêu đề khi cần kiểm tra thủ công. Không khẳng định mọi tin isPublic=false chưa từng qua AI.


#### v1.0.18 xác nhận triển khai thực tế
- Initial publication freeze3808ECD3 full667PASS47.688s, independent121PASS13.693s; guarded6runtime modules source+installed deployB95E07D8 backup/var/backups/agentfleet/nailhub-field-20261013T043013Z.
- Actual firstread946d bị frontend từ chối deep link, chưa gọi AI/Save (pilot0postrows/noevents). Followup browser-only verifiedlisting fallback freeze3EB54ACA full671PASS42.424s, independent60PASS10.504s; deploy9FB81D09 backup/var/backups/agentfleet/nailhub-field-20261013T044038Z. Final26 runtimehashes khớp; classifier/Telegram giữ nguyên.
- Actual946ddda8-db99-4d73-385f-08df28db55a0 Zaza: Approved/isPublic=false → normal Service.process với model thật → siteAI1lần → UI Save false → readback → checkpoint before_publish → UIcheckbox/Save true → freshread Approved/true. Không gọi Approve, không fake model hay inject DOM. Quan sát siteAI đã bỏ footer/link BaoNail; worker không dùng nội dung sau AI làm semanticgate. Lương Thương lượng/0 giữ đúng.
- Screenshot trước/sau đã mở xem: checkboxfalse→true. Separatebrowser freshread khớp toàn bộ bản cuối; actualcandidates348 nhận các sequentialGUID khác và bỏ tin đã hoàn tất.
- Resume capturedpaused0/pending1/running0, countsapproved55/needs-human14. Các hold cũ giữ nguyên. last_result cũ là lỗi schema trước sửa; lượt quét kế tiếp cập nhật, không coi đó là lỗi mới của livepilot.
- Proof: .context/nailhub-evidence/nailhub-publication-final-proof.json, nailhub-publication-before.png, nailhub-publication-final-proof.png, nailhub-publication-final-runtime-hashes.json. Controlled livepilot dùng durablestate riêng khi productionpaused, reconcile đúng một productionrow chỉ sau freshproof; không tạo Telegramclient thứ hai.
- Giới hạn frontend: directlink có sequentialGUID vẫn bị trang quản trị từ chối; worker mở đúng tin qua View trong listing. Owner cần mở listing/tìm tiêu đề nếu link DM thuộc nhóm này không mở được. Không sửa/deploy frontend trong thay đổi này.
- Harnessverify vẫn chỉ hỗ trợ Node/package.json, repo Python không chạy được gate đó. CAS same-owner expiredlease limitation cũ giữ nguyên; không giả lập gate hay sửa harness. Không commit/push bổ sung này.

Operational v1.0.18 finalized | 2026-10-13 11:48 (Asia/Saigon) | AlexChu qua Codex | Actual AI/save/publish + independent fresh read/candidates verified; periodic resumed.


### v1.0.19 — Model diễn giải nghiệp vụ trước khi duyệt
Owner yêu cầu model xử lý nhiều hơn về ý định/ngữ cảnh/ngôn ngữ, dựa trên các quy tắc nghiệp vụ đã có; sửa và chạy lại đúng ba tin bị giữ sai.
- Model đề xuất danh mục có dẫn chứng gốc, đọc tất cả khoản tiền với vai trò và đơn vị cục bộ, phân biệt proper salon name với mô tả. Code kiểm enum/căn cứ/số/đơn vị và phép quy đổi, không chỉ dựa regex từ khóa để quyết định ý nghĩa. Sale priority, thu nhập cao nhất/floor100, giá bán thấp nhất, loại giá dịch vụ và không tự đoán kỳ giữ nguyên.
- a01e7a02-0d95-4a9d-385c-08df28db55a0: Now Hiring Nail Techs ⇒ Cần thợ nail;900–1500/week ⇒1500. Owner xác nhận tên salon Glaze Nails từ URL gốc stores=Glaze-Nails, cho phép quy ước đọc stores có nguồn và chuẩn hóa dấu nối sang khoảng trắng; không dùng BaoNail.com làm tên salon.
- df176e7a-a9c0-4cde-bb0f-3dbd147ffbe1: tiếng Việt1500tới2700/tuần và tiếng Trung每周1300–2700 đều có đầu cao2700. Hiển thị2700/tuần. Địa chỉ hiện có đầy đủ MiddletonWI53562 làm nguồn riêng sửa stateIL→WI và xóa phone_other trùng.
- f94c24d1-f671-4c6b-b230-97bd143639ee: giữ Allure Nail Lounge khi body chỉ nêu mô tả Tiệm Nail Sang Vung HARRISONBURG; không thay bằng mô tả32ký tự rồi gây lỗi form. Không nêu mức lương cụ thể ⇒Thương lượng.
- Bounded feedback/retry khi model đề xuất lỗi; DM phải tách giá trị đang lưu khỏi giá trị đề xuất và phân loại đúng lỗi tự động hóa/dữ liệu. Không thêm kiểm nội dung sau nút AI.
- Cùng một stream thực thi, review độc lập, freeze/test/deploy có preimageguard. Chỉ requeue ba needs-human có source mới đã đọc; không mở khóa Approved/uncertain hay giữ source stale. Các sửa publication/GUID trước đó giữ nguyên. Không commit/push.

Changelog v1.0.19 | 2026-10-13 15:19 (Asia/Saigon) | AlexChu qua Codex | Owner approved model-assisted business interpretation and exact3 rerun; implementation in progress.

#### v1.0.19 kiểm thử và triển khai
- Owner đổi executor sau hai lần gpt-6.1-sol at capacity: Codex/gpt-6-sol/high, một executor. Frozen manifest3FC654DC; full681 tests59.571s OK, independent135 focused26.888s OK và final frozen10 tests0.172s OK. Không còn material findings; businessdoc v1.0.2 và controller runner được review độc lập.
- Guarded deploy đúng categories/model/validation/diagnostics, manifest3B849FAC; backup/var/backups/agentfleet/nailhub-field-20261013T085455Z.26runtimehashes source+installed khớp, classifier/Telegram bảo toàn. Không tạo Telegramclient thứ hai.
- Exact3 controlled real-model/UI campaign đang chạy khi productionpaused/idle; checkpoint trước mọi write được mirror vào productionuncertain để crash không làm tin có thể đã ghi trở lại needs-human retryable. Chỉ kết luận thành công sau freshread đúng trường/status/publicflag.
- Harnessverify tiếp tục exit1 vì chỉ hỗ trợ Node và Pythonrepo không có package.json; không giả lập gate. Hạn chế same-ownerexpiredlease cũ giữ nguyên. Chưa commit/push.

Progress v1.0.19 | 2026-10-13 15:57 (Asia/Saigon) | AlexChu qua Codex | Final suite/review/deployment passed; actual exact3 UI proof in progress.

#### v1.0.19 đối chiếu lỗi runtime và sửa bổ sung
- Lượt v1 không có writeAPI nào cho cả3. a01quote model ghép câu rời; df biểu diễn khoảng có dấu tiền không có trong source; f94 sau extractionvalid lỗi selectcity vìHARRISONBURG khácHarrisonburg. All3 separatefreshread vẫn==original. Exactf94 no-writeproof +sourcefingerprint cho phépcontrolledrequeueđúngID, cácuncertainkhácgiữkhóa.
- Followupfreeze6C6AD626 full685/87.491PASS independent14/0.102PASS, guardeddeploy6448528A backup092741Z;26runtimehashesPASS. Chuẩn hóa biểu diễn chỉkhikhớpduynhất nguồn, không fuzzycâu/đổi số/kỳ; giữcitycanonicalkhi nguồn xác nhận cùngcity/state.
- Actualv2 a01 đã qua realmodel (retry2), salonGlazeNails, weekly1500, siteAI1, Savefalse→Save true, Approved/publictrue; screenshotđãmởxem. dfv2retry2 vẫn bỏChineseincome clause, chưawrite; đang bổsung bounded source_repeat cùngweeklyupper và auditrõ. f94 đang xử lý; không coi testscriptPASS là livePASS.
- Tinyknown HiringNailTechs sourceintent/negation +promptcontiguous được freeze45D7A3E5 full687/64.815PASS independent27/2.131PASS; chưa deploytrongactivecampaign, sẽ gộp exactcurrentV2preimageguard vớibản sửa income sau. Không commit/push.

#### v1.0.19 kết quả cuối — 2026-10-13 17:52 (Asia/Saigon)
- Cả ba tin đã được xử lý qua chức năng AI của trang, lưu và duyệt/xuất bản theo lifecycle hiện tại. Phiên trình duyệt riêng đọc lại xác nhận cả ba Approved và isPublic=true: a01e7a02… Glaze Nails, JobPosting, 1500/tuần; df176e7a… Happy Nails And Spa, JobPosting, 2700/tuần, WI/Middleton/53562, xóa số phụ trùng; f94c24d1… Allure Nail Lounge, Thương lượng/0, Harrisonburg/VA/22801, giữ hai số khác nhau.
- Tin song ngữ dùng phản hồi Qwen thật đã ghi ở lượt v3, được V6 kiểm lại với nội dung gốc vừa đọc và chứng minh không đổi. Đây không phải một lần gọi model mới. Nguồn tiếng Việt và tiếng Trung có cùng đầu cao weekly2700; chỉ chuẩn hóa biểu diễn số/đuôi đơn vị có căn cứ, không bỏ kiểm số hoặc đoán đơn vị. SiteAI thực chạy một lần rồi Save/Approve; fresh read xác nhận kết quả.
- V6 frozen827A2CF5: full692 tests/53.119s PASS; review độc lập78 tests/2.737s PASS; focused21 PASS. Manifest triển khai validation A6D7F6FE, backup /var/backups/agentfleet/nailhub-field-20261013T104123Z. Tổng34 runtime hashes source+installed khớp.
- Phát hiện deployment wheel issue-templates-261013 ngoài luồng lúc16:40 đã bỏ package/hook NailHub. Guard triển khai chặn trước ghi; recovery overlay5AB36E39 khôi phục32 targets, giữ nguyên classifier mới1A13A271, backup /var/backups/agentfleet/nailhub-overlay-20261013T101625Z. Listener hiện tại giữ khóa Owner-bound và chạy NailHub cùng client Telegram; không có instance Telegram thứ hai. Các release listener sau phải đóng gói đủ package và hooks NailHub để tránh tái diễn.
- Quét định kỳ đã tiếp tục: paused=0, pending=1, running=0 lúc resume; approved67, needs-human22, uncertain1. Không mở lại các hold khác; tin6bda5b22… vẫn uncertain/khóa.
- Bằng chứng: .context/nailhub-evidence/nailhub-model-policy-verified-combined-proof.json; nailhub-model-policy-verified-independent-audit.json; nailhub-model-policy-verified-capture-provenance.json; nailhub-model-policy-verified-runtime-hashes.json; ba ảnh verified-glaze/happy/allure đã mở xem. Ảnh phần đầu form xác nhận thông tin liên hệ/địa chỉ/danh mục; salary/publication dựa trên readback và audit, không suy từ phần ảnh không hiển thị.
- Harnessverify Node-only/package.json exit1 và CAS same-owner expired lease vẫn là giới hạn công cụ; không giả PASS hoặc sửa state bằng tay. Các gate Python, review độc lập và fresh UI read ở trên đã chạy thực tế. Các thay đổi bổ sung chưa commit/push.
### v1.0.20 — Rà soát báo cáo kiểm tra thủ công còn lỗi cũ
Owner đưa sáu tin Draft bị giữ. Đọc lại UI xác nhận cả sáu còn nguyên dữ liệu gốc; không phải lỗi của tin đã được sửa và duyệt. State.observe và Service.scan bỏ qua needs-human có cùng fingerprint, nên nâng cấp quy tắc không tự chạy lại các hold cũ. Danh sách pending chỉ bỏ tin khi có kết quả xử lý được xác nhận; DM đã gửi là lịch sử.

Owner bổ sung: lương giờ không có tổng giờ/tuần chắc chắn chọn Thương lượng; khoản bao lương theo đợt /6 ngày trong ví dụ Miss Nails giữ nguyên mức cao nhất1500; tiêu đề crawl Bán tiệm nails nhưng body chỉ tuyển thợ/no askingprice chọn Cần thợ nail, giữ địa chỉ salon đầy đủ Los Angeles/CA90025 thay vì lấy khu vực Santa Monica. Cập nhật Business Doc v1.0.3.

V1 sửa kiểm tra giá basic-pedi/service, bao lương1600+ không kỳ có lựa chọn commission, city/state tương đương Birmingham/ALABAMA và địa chỉ đầy đủ, N-day literal amount, excludedtips/salon-subject revenue/tax wording, cùng bảo vệ giá bán thật ở tiêu đề/nội dung. Full700 tests39.437s PASS; independent63 tests2.055s PASS; b/d bốn phản hồi Qwen thực được revalidate thành công với source không đổi.

Runtime đã tách sang tg-nailhub, UID993/GID971, /etc/nailhub/nailhub.toml; không khôi phục listener cũ. Guarded deploy chỉ categories.py và validation.py, source+installed4targets, backup /var/backups/agentfleet/nailhub-held-20261013T160958Z.38 runtime hashes PASS,34 module khác bảo toàn. Guard trước ghi phát hiện source CRLF khác installed LF; đối chiếu byte chỉ khác newline và thiết lập exact preimage riêng; không bỏ guard. First-five controlled campaign đang chạy, không coi tests PASS là live PASS. Tin f746 tách riêng vì actual model candidate coverage chưa hợp lệ; V2 chưa triển khai tại checkpoint này. Các uncertain/Approved khác giữ khóa, không mass requeue. Chưa commit/push bổ sung này.
#### v1.0.20 hoàn tất live — 2026-10-13 23:33 (Asia/Saigon)
V2 validation b4fe116e bổ sung recovery có đối soát tất cả tiền trong nguồn: khoản quản lý đã tuyển xong, thu nhập thợ đang tuyển và doanh thu salon. Hai phản hồi Qwen thực của f746 đều được revalidate thành2300/tuần với nguồn vừa đọc không đổi; không giả là model call mới. Full702 tests38.661s PASS; independent65 tests1.658s PASS. Guarded validation-only deploy source+installed2targets,36 module khác bảo toàn, backup /var/backups/agentfleet/nailhub-held-20261013T162627Z;38 runtime hashes PASS.

| Tin | Kết quả đã đọc lại |
| --- | --- |
| 8cc9c1ec… Miss Nails | Approved/publictrue, JobPosting,1500 literal, Woodstock/IL60098; xóa số phụ trùng, bỏ giá basicpedi38 khỏi lương |
| 910a37db… Burte Lash & Nail | Approved/publictrue, Thương lượng/0, New York/NY10038; không giả số giờ/tuần |
| aaede12d… tiêu đề crawl Bán tiệm nails | Approved/publictrue, JobPosting, Thương lượng/0; giữ địa chỉ đầy đủ Los Angeles/CA90025, không dùng khu vực Santa Monica thay city |
| b9b5d11e… Lotus Nails | Approved/publictrue,1600 literal không kỳ; Savannah/GA31404 |
| d6a64de1… Gloss Nail Star | Approved/publictrue, Thương lượng/0; Birmingham/AL35242, chuẩn hóa ALABAMA→AL có đối chiếu địa chỉ |
| f746a422… A N C Nails & Spa | Approved/publictrue,2300/tuần từ đầu cao10K/tháng, West Monroe/LA71291; title130K chỉ phục vụ category, tiền từ content theo scope |

Cả6 có timeline thực AI→Save→Approve và publictrue, xác nhận lại bằng Browser riêng, mở Edit xác nhận checkboxtrue, chụp6ảnhsalary, Cancel rồi đọc lại vẫn khớp. Root đã mở xem cả6ảnh. Ba tin đầu dùng model call mới; b/d/f dùng phản hồi Qwen thực đã capture trong đợt read-only, revalidate với source không đổi và ghi rõ hash/index provenance. Không có semanticgate sau siteAI.

Các tin đã rời State.manual_posts/pending. Resume proof paused0/pending1/running0, approved91/needs-human27/uncertain1; last_result controlled rerun complete; approved=6; processed=6. Chỉ xử lý6tin được cung cấp,27hold khác và uncertain6bda giữ nguyên. DM lỗi đã gửi trước đây là lịch sử.

Evidence bundle .context/nailhub-evidence/nailhub-held-final.zip SHA2568761b477824777e5fd2aecde1bcb88e4e97d4aadf86531bf08da2ffa49f72706;17filemanifest kiểm hash PASS, dữ liệu chi tiết ở held-final/. Independent-audit/resume-proof và first-five/last-one-live-proof xác nhận outcome, source/proposal/audit và thứ tự request. Ảnh salary không hiển thị checkbox ở cùng viewport; checkbox được assert trực tiếp trong phiên UI riêng.

Handoff: runtime đã tách package nailhub, còn branch này giữ đường dẫn listener/src/tg_triage/nailhub. Khi tích hợp bản sửa vào release service-split, đồng bộ đúng categories.py/validation.py sang package độc lập; không chép đè model_lock hoặc Telegram của dịch vụ mới. /srv source+installed hiện đã có bản sửa. Quan sát frontend bổ sung Schedule/ApplyBy và form job fields trong lúc campaign;6ID RFC đi direct detail nên đều PASS. Contract header10cột của fallback non-RFC listing chưa được kiểm lại với giao diện mới; cần audit riêng trước khi sửa nhóm sequentialGUID. Không có tuyên bố đã kiểm các trường job mới ngoài scope.

Harness Node-only/CAS expired same-owner limitations vẫn được ghi nhận, không giả PASS/handoff. Token billed/cache metrics của collaboration executor không được tool cung cấp. Chưa commit/push các thay đổi v1.0.20.

### v1.0.21 — Bổ sung kiến thức cho agent và chuẩn bị issue

**Yêu cầu Owner:** nạp bài học từ các hold lặp vào hướng dẫn agent, bổ sung quy tắc nghiệp vụ và tổng hợp Business DOCS để chuẩn bị tạo GitHub issue.

- Business v1.0.4 tổng hợp BR-01…12, BUG-01…09, D-01…05 và ma trận đủ49tin (46needs-human/3uncertain). Counts theo trường chặn đầu tiên, không phải49nguyên nhân độc lập. Snapshotapproved113 và readinesserror là bằng chứng đợt rà soát trước; không tuyên bố website được kiểm mới trong lượt cập nhật docs.
- Cùng nguồn nghiệp vụ cho module agent: entry `listener/src/tg_triage/nailhub/AGENTS.md` dẫn tới Business và Master; `listener/NAILHUB.md` thêm currenthandoff, chỉ rõ tích hợp listener bên dưới là lịch sử. Runtime standalone tg-nailhub không được chép đè bằng triển khai cũ.
- Nạp hướng dẫn cho agent phát triển/tiếp nhận, **không** tuyên bố Markdown tự đi vào model extraction. Đồng bộ runtimeprompt/validation/state/diagnostics và regression/liveproof còn thuộc issue triển khai tiếp theo. Không thay đổi algorithm trong lượt này.
- Giữ nguyên các quyết định Owner: highworkerincome/floor100 khi là khoảng, literal khi thiếu kỳ, negotiated khi không có base, minimumaskingprice, fulladdress source, giữ liên hệ hợp lệ, mandatorysiteAI và nosemanticcheck sauAI.
- Không tự chốt daily/mixedperiod thiếu lịch, typo&3000, phạm vi tràsữa/phở hoặc salon/địa chỉ thật chưa xác minh. Các D là quyết định/dữ kiện còn thiếu, không được dùng default để tự duyệt.
- Issue draft có problem/scope/priority/acceptance và link49tin để đối chiếu nội bộ. Khi tạo issue public, bỏ dữ liệu riêng trong fixture/body, chỉ đưa link/ID nếu phù hợp quyền truy cập. Chưa tạo issue hoặc gán số issue.
- Lượt này chỉ docs/handoff: không gọi model, không requeue, không dừng dịch vụ, không ghi website/deploy, không gửi Telegram, không commit/push. Bốn file code/test đã dirty từ đợt v1.0.20 giữ nguyên.

Changelog v1.0.21 | 2026-10-14 08:58 Asia/Saigon | AlexChu qua Codex | Owner yêu cầu bổ sung business/agent knowledge và gói chuẩn bị issue; chưa triển khai BUG-01…09 hoặc chạy lại49tin.

### v1.0.22 — Lượt chạy lại và quyết định bổ sung đang triển khai

Owner yêu cầu chạy lại các tin cần kiểm tra thủ công. Runtime tg-nailhub được pause/drain, bảo toàn snapshot và khóa 113 Approved/3 uncertain; một tin đang xử lý trở thành hold mới, inventory hiện có 50 tin. Bản sửa đầu đã qua 707 tests, review độc lập và guarded deploy; lượt live chưa hoàn tất. Bản bổ sung alias/proximity đang được hoàn thiện trước review/deploy tiếp theo; không thay code trong khi controller dùng profile.

Owner chốt D-01 Angel Nails500/ngày ×6=3000/tuần, bỏ8000thiếu kỳ cho trường hợp này; D-02 GLOW &3000 là $3000; D-03 cho phép sang tiệm trà sữa/phở theo quy tắc giá bán. Businessv1.0.5 và vault Nexora/Decisions/D-025 lưu đầy đủ phạm vi. D-04/05 còn thiếu nguồn; không coi offline PASS là live PASS. Không tạo issue/commit/push trong lượt này.

Changelog v1.0.22 | 2026-10-14 10:35 Asia/Saigon | AlexChu qua Codex | Bắt đầu rerun có kiểm soát và ghi quyết định Owner mới; còn chờ kết quả cuối, followup review và fresh audit.

### v1.0.23 — Bỏ qua tin quá 45 ngày theo danh sách

Owner xác nhận ngày tính tuổi tin là **ngày đăng trên danh sách NailHub**. Business v1.0.6/BR-13 và vault D-026 ghi quy tắc: >45 ngày bỏ qua, đúng45 vẫn xử lý; ngày hiện tại theo Asia/Saigon; ngày thiếu/lỗi không được giả định mới. Listing Created được xác nhận đúng ID/dòng và đối chiếu raw `created`; không dùng ngày chi tiết sentinel, startDate, lastModified hoặc ngày nguồn crawl. Chứng cứ ngày nằm ngoài fingerprint.

Phase1 và phase2 đã đóng Browser, có 24 tin mới Approved/public=true trước quyết định này. Production đang paused/idle để triển khai age gate có review. Đối chiếu 50 tin:33quá hạn gồm14Approved/19Draft,17trong hạn gồm10Approved/7Draft. Không đổi lại14tin đã hoàn tất trước quyết định. Ba khóa uncertain cũ giữ nguyên; hai uncertain mới do controller phải đối soát request/fresh source trước khi kết luận bỏ qua hoặc tiếp tục.

Bản bổ sung validation/categories đã deploy có bảo vệ, full722tests và independent107tests OK; chứng cứ đó chưa xác minh age gate mới. Runtime riêng tg-nailhub có model lock/pacing khác đường dẫn legacy: chỉ transplant delta đã review, không chép đè cả service.py. Chưa có kết quả cuối, chưa resume, không tạo issue/commit/push.

Changelog v1.0.23 | 2026-10-14 11:59 Asia/Saigon | AlexChu qua Codex | Owner chốt mốc Created danh sách cho BR-13; pause/drain, đọc mới50ngày và ghi phạm vi bảo toàn.

### v1.0.24 — Nhãn đơn vị đăng tin khi chưa xác định tên tiệm

Owner yêu cầu BaoNail.com không được dùng làm tên tiệm: tìm tên thật có nguồn trước; nếu chưa xác định thì Tên tiệm dùng `nailhub.ai`, cả trường hợp tên đang trống. Trong nội dung bỏ từ Tiệm/Salon tương ứng ngôn ngữ trước Nailhub.ai, thêm câu đăng bài tuyển dụng. Owner xác nhận riêng tin sang nhượng dùng câu đăng tin sang nhượng. Businessv1.0.7/BR-14 và vaultD-027 ghi phạm vi; fallback là nhãn đơn vị đăng tin, không phải tên salon được xác minh.

Đây là ngoại lệ chỉnh cụm nhãn nội dung sau AI được Owner yêu cầu rõ, không phải kiểm tra hoặc viết lại toàn nội dung AI. Giữ các điều kiện địa chỉ/liên hệ, kiểm payload/readback và bộ lọc45ngày. Bản agegate đã freeze732testsOK đangreview; brandfallback triển khai riêng. Sáu youngnameholds được xem lại theo quyết định mới, không tiếp tục yêu cầu Owner cung cấp tên chỉ vì dữ liệu đó thiếu; dữ kiện địa điểm mâu thuẫn/khác đối tượng vẫn phải xác minh.

Changelog v1.0.24 | 2026-10-14 12:12 Asia/Saigon | AlexChu qua Codex | Owner bổ sung fallback publisher và xác nhận câu sang nhượng; cập nhật business/handoff trước triển khai. Chưa commit/push/issue.

### v1.0.25 — Đóng lượt chạy lại và triển khai BR-13/14 — 2026-10-14 13:32 (Asia/Saigon)

Business v1.0.8 là chính sách hiện hành và báo cáo kết quả. Inventory 50 = 49 hold lịch sử + tin 293 phát sinh khi drain; 30 hoàn tất (29 Draft được duyệt + một tin đã Approved được xuất bản), 16 skipped-age, một needs-human do địa chỉ nhà cho thuê/salon và ba uncertain lịch sử giữ khóa. Tổng state approved của worker là 143; không phải 143 lượt duyệt mới. Năm fallback tên dùng nailhub.ai và câu đăng tuyển/đăng sang nhượng đúng ngôn ngữ; tên thật có nguồn được giữ.

Bộ Python cục bộ 738 tests/46.384s OK; reviewer chạy độc lập 114 tests/7.559s OK; đối chiếu offline 46 phản hồi Qwen đã capture thật với nguồn. Import/hash standalone và UI live là bằng chứng riêng. Fresh Browser audit 50/50 khớp, 30 public=true/checkbox=true/Cancel không đổi; không có request ghi Admin/AI trong audit; 116 dòng DB bảo vệ nguyên vẹn. 29 tin Draft có AI→Save→Approve; tin 29e6 đã Approved có AI→Save→publish Save. Auditor đầu yêu cầu Approve sai ở nhánh publish, đã giữ partial/log của lần lỗi và sửa/review rồi chạy lại đầy đủ; không bỏ assertion để che lỗi product.

Standalone deployment kiểm preimage chính xác, backup, import, hash và các hook phải bảo toàn. Bản sửa tuổi tin chỉ ghép delta vào service, giữ model lock/progress/pacing; model chỉ đổi EXTRACT_PROMPT. Bản cuối validation/browser/notices ở source/installed: sáu target, 32 module bảo toàn/38 hash, backup `/var/backups/agentfleet/nailhub-rerun-20261014T060416Z`. Freeze cuối 6df649ad24d8ec8fdbcdf0e2e1a340c9ed142ad23138d66d335ac60f13dd7678; không chép Telegram integration cũ lên tg-nailhub.

0ef tiệm nail ở Thibodaux/Louisiana đã sửa grammar có giới hạn và đọc lại giá bán 400.000; Tin 74 timeout ở site AI, chỉ có AI/chưa Save/Approve; đã đối soát nguồn vừa đọc và checkpoint của lượt này trước khi thử lại giá bán 72.000. Ba uncertain cũ không được mở khóa hàng loạt. Tin 362 chỉ thiếu địa chỉ salon thật; DM hiện hành nêu rõ địa chỉ thuộc nhà cho thuê, trường/current/source/action. Không yêu cầu tên thật khi BR-14 đã cho dùng nhãn thay thế.

Audit đã đóng Browser rồi khôi phục paused=0, giữ một yêu cầu quét coalesced và cả hai dịch vụ active. last_result=`scan complete; processed=2` ghi số tin của phase cuối, tổng 30 nằm trong proof riêng. Bằng chứng `.context/nailhub-rerun-20261014/final-independent-audit.json`/`resume-proof.json`; thư mục audit runtime cùng tên trên THINKPAD. Root mở 30 ảnh ô lương/giá; checkbox được xác nhận bằng DOM và ảnh riêng; viewport ô lương/giá không nhất thiết hiển thị checkbox.

Giới hạn: tin 06b89d0c backend MD đúng nhưng Edit hiển thị state trống/city Bowie; probe riêng xác nhận public=true, Cancel không đổi, không có request ghi. Đây là lỗi frontend/readiness còn theo dõi, không bỏ kiểm tra state trước khi ghi. Auditor cuối dùng đường chỉ đọc Edit/checkbox/Cancel, không coi đó là bằng chứng form sẵn sàng ghi. Hai partial/log của lần lỗi được bảo toàn (13 và 37 tin). Editor sửa nhãn giữ phần plain text khác nhưng có thể làm phẳng rich text; chưa xác minh bảo toàn HTML. Harness verify đã chạy và exit 1: “Không thấy package.json … harness-verify hiện chỉ hỗ trợ project Node.” Manifest CAS/lease cũ và gate Node chưa đóng; không tự nhận PASS, căn cứ Python/reviewer/live được ghi riêng. Chưa tạo issue/commit/push.

Changelog v1.0.25 | 2026-10-14 13:32 Asia/Saigon | AlexChu qua Codex | Đóng rerun 50, đối soát 116 dòng DB bảo vệ, triển khai fallback/age, bật lại lịch; Business v1.0.8 và bàn giao module cập nhật.

Evidence bundle riêng tư: `.context/nailhub-rerun-20261014/nailhub-rerun-final-20261014.zip`, 141 file đã đối chiếu manifest; SHA256 `82ba58c4e9c48fc7910bec4ac1227864537f676428946d80e2c6d42b40cb9b39`. Root đã mở bốn ảnh checkbox cuối (0ef,74,2b,29e6) và hai ảnh probe06; ảnh xác nhận checkbox Xuất bản được tích.
