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
- Version: v1.0.17
- Created: 2026-10-12 17:09 (Asia/Saigon)
- Updated: 2026-10-13 09:51 (Asia/Saigon)
- Status: Approved — Owner duyệt phương án và yêu cầu tiếp tục triển khai trong chat.
- Implementation worktree: `C:/Users/AD/.codex/worktrees/nailhub-automation/AgentBuilder`, branch `codex/nailhub-automation`.
- Executor đã được Owner chọn: Codex · gpt-6.1-sol · high.
- Tracking: `.agent-tasks/T-261012-nailhub-postings-automation-on-thinkpad.md`.
- GitHub: PersonalProjectJob/telegram-agent, nhánh codex/nailhub-automation; commit 7944fe1 đã push theo lệnh Owner. Chưa có issue / PR; bổ sung DM đang chưa commit.
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
