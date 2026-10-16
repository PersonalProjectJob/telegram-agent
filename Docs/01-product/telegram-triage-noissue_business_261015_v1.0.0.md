# Telegram triage — nguồn quy tắc nghiệp vụ duy nhất

**Version**: v1.3.1 · **Updated**: 2026-10-16 09:57 (Asia/Saigon) · **Owner/state lineage**: AlexChu · **Executor**: Codex

**Status**: Nguồn nghiệp vụ của code THINKING trong PR #43 theo yêu cầu user. Bản chuẩn hóa trước đó đối chiếu THINKPAD với baseline `9746ef2`; implementation mới thêm controller, journal SQLite và prompt gọn. Kết quả review/test/installed/deploy do controller báo bằng evidence riêng, không suy từ hash tài liệu. Không replay các tin cũ hay tự tạo ticket thật để kiểm thử.

## Quyền và cách sử dụng

User yêu cầu tài liệu này và sau đó yêu cầu triển khai THINKING. Nguồn này là nơi duy nhất định nghĩa nghiệp vụ Telegram triage; README là hướng dẫn vận hành, tài liệu Fleet cũ giữ lịch sử/đề xuất. Code và prompt là implementation/projection, **runtime không load Markdown này**. Nếu code/hash thay đổi, cần review và đồng bộ nguồn; hash không chứng minh semantics. Các mục pilot/metric tương lai ở nghiên cứu vẫn là đề xuất. Không có issue riêng (`noissue`), không giả lập approval Master Draft.

## Related Documents

- [Fleet Master và phạm vi được yêu cầu](../00-master/worker-agent-fleet-1_master_261007_v1.0.8.md)
- [THINKING: nghiên cứu và implementation](../02-engineering/telegram-triage-noissue_thinking-research_261015_v1.0.0.md)
- [QA acceptance](../03-qa/telegram-triage-noissue_qa_261015_v1.0.0.md)
- [Hướng dẫn listener](../../listener/README.md)
- [PR #43](https://github.com/PersonalProjectJob/telegram-agent/pull/43)

## Bảng quyết định hiện hành

| Đầu vào / bằng chứng | Quyết định | GitHub | Reply |
|---|---|---|---|
| Công việc rõ, không tag | ticket | Tạo issue | Link, reply source đầu topic |
| Công việc chưa rõ/model unusable | needs_human | Ticket theo dõi | Hỏi tối đa ba câu trong group |
| Chat/cảm ơn/câu hỏi thông tin | chat | Không write | Không text notice |
| Ảnh đơn lẻ thiếu yêu cầu | pending_review có thể xảy ra | Không create | Có thể im lặng, giới hạn TG-15 |
| Vision partition lỗi, text độc lập chưa phân loại | pending_review/context_pending | Không gộp/tạo ticket cho text chưa rõ | Hỏi ngay theo component; reply có proof mở intake mới |
| Reply có root/provenance đủ, bổ sung | update | Append comment | Reply lượt phản hồi mới |
| Xác nhận tách đủ source/count | split | Giữ gốc + N−1 sibling | Link thực tế của từng ticket |
| Chưa đủ anchor/target | clarify | Không đoán scope | Acknowledge phần biết, hỏi phần thiếu |
| Outcome POST không biết | ambiguous | Không tự POST lại | Owner kiểm tra repo |

## Quy tắc có ID ổn định

### TG-01 — Phạm vi lắng nghe

Chỉ nhận group được cấu hình; chế độ listen_all_groups mở rộng tới group tài khoản tham gia. Bỏ outgoing, private chat và command prefix bị ignore theo cấu hình. Ở all-groups mode kiểm tra group để bỏ broadcast; đường cấu hình ID tin cậy kiểm tra ID, không hứa universal entity validation. Sender notice luôn từ chối private/broadcast.

### TG-02 — Lưu trước, chống trùng

Lưu ingress vào SQLite trước metadata/queue; khóa chat/message duy nhất ngăn xử lý lặp. Khi shutdown vẫn lưu queued để phục hồi, không hứa giao đủ mọi side effect sau crash.

### TG-03 — Phân loại nội dung

Lớp code chỉ nhận command đầu câu và noise không có chữ/command rỗng; từ ngắn, lời xã giao đi qua model. min_text_length không còn quyết định. Media có nhánh riêng, không bị loại chỉ vì không có text.

### TG-04 — Command

Nhận /bug, /feature, /question, /task, /feedback đầu câu, không phân biệt hoa thường, có thể kèm @BotName. Command có nội dung xử lý ngay với các tin đã gom; command rỗng là noise. /question theo TG-09.

### TG-05 — Gom cửa sổ

Reply thật ưu tiên case được tham chiếu; nếu không, gom case mới nhất cùng chat/cùng sender trong cửa sổ và giới hạn số tin. Sender không xác định không tự gom. Reply ngoài cửa sổ vẫn có thể vào case nếu còn chỗ; conversational followup dùng TG-16.

### TG-06 — Đóng và chia topic

Đóng theo thời gian idle, command hoặc đầy case. Chia chunk theo thứ tự thời gian và giới hạn cấu hình, rồi chia topic; mỗi source ID phải được hạch toán đúng một lần. Không coi một case luôn bằng một issue.

Default THINKING dùng projection gọn và packing byte-aware: giữ mọi ID, reply edge, sender/media/pixel flags; chia đều ngân sách cho trích đoạn text, đánh dấu `text_truncated`. Full source vẫn ở SQLite và quote issue theo source scope. Model không được coi excerpt là toàn bộ phạm vi. Nếu mandatory metadata/schema không vừa, báo technical budget failure; không tự gộp thành topic thật. Custom prompt/legacy renderer được giữ.

Default THINKING gửi một USER context packet duy nhất: text JSON chứa caption, mọi source/reply và `images_order` ánh xạ index ảnh bắt đầu từ0 tới source ID; cùng USER chứa toàn bộ pixel được phép gửi. Cloud dùng text + image_url parts trong một USER; local dùng content + images trong một USER. Retry/fallback giữ cùng gói, không phân tích ảnh bằng call riêng rồi ghép kết quả.

### TG-07 — Nhóm nghiệp vụ

Model đề xuất bug/task/feedback/chat; feature được chuẩn hóa thành feedback. Code kiểm tra schema, partition và safety; model không có quyền tự chọn repo, URL hay chạy hành động.

### TG-08 — Yêu cầu không cần tag

Công việc đủ rõ/actionable và confidence đạt ngưỡng tạo issue dù không tag. Yêu cầu thiếu/không chắc hoặc output không dùng được chuyển needs_human, tạo ticket theo dõi và hỏi trong group. Chat vẫn là chat; ngoại lệ ảnh đơn lẻ xem TG-15.

Ghi nhận bug không cần specification/debug report hoàn chỉnh. Module/control/setting đã xác định, hành vi thực tế trái mong đợi và hướng sửa mong muốn đủ nhận diện công việc; device/platform/logs hay recipe tái hiện đầy đủ không tự thành blocker. Chỉ hỏi essential thật sự thiếu để nhận diện phạm vi, không hỏi lại dữ kiện đã có và không bịa môi trường/nguyên nhân/bước tái hiện. “Nút này lỗi” chưa xác định đối tượng/hành vi vẫn cần hỏi rõ.

Yêu cầu DESIGN/redesign cho panel/mode đã nhận diện có thể là task đủ rõ, kể cả người gửi tự thực hiện sau này. New layout là deliverable, không phải prerequisite để log task; owner/deadline/thứ tự không tự thành blocker. Caption “mode/bảng này” phải được giải nghĩa bằng heading và selected option nhìn thấy khi pixel thật được cung cấp; không bịa future layout hay assignee từ screenshot.

### TG-09 — Câu hỏi thông tin

/question và câu hỏi thông tin thuần túy là chat, không tạo issue hay reaction kể cả có tag. Kế hoạch hỏi Docs rồi trả lời tự động trong tài liệu cũ chưa được triển khai.

### TG-10 — Ưu tiên và safety

Command có payload ép loại issue; mention Telegram thật có thể ép needs_human. Chữ @handle/email trong text không đủ chứng minh mention. Nội dung chỉ contact/xã giao bị safety chặn trừ command hợp lệ có nội dung; ảnh có thể là nội dung.

### TG-11 — Xác nhận tạo ticket

Sau khi xác nhận tạo thành công: “Em đã ghi nhận nội dung này và tạo ticket: [Link] rồi nhé 😊”. Reply tin đầu của topic; ba tin/một ticket chỉ một reply, ba topic/ticket thì ba reply với link tương ứng. Link do code dựng từ repo/issue tin cậy.

### TG-12 — Hỏi rõ và báo lỗi

needs_human reply tin đầu topic với tối đa ba câu hỏi ngắn đã sanitize. Outcome GitHub không rõ phải nhờ Owner kiểm tra repo trước retry; hết quota thông báo hỗ trợ. Noise/chat/pending_review không có text notice; không lặng chờ duyệt đối với công việc chưa rõ thuộc TG-08.

Ngoại lệ pending do **vision partition technical failure**: mỗi component text chưa phân loại được lưu `pending_review`/reason `vision_partition_unresolved` và nhận notice `context_pending`, chưa tạo ticket hay gắn với image ticket. Notice yêu cầu reply tin/notice với đầy đủ intent hoặc xác nhận chỉ chat. Reply có exact same-chat pending+sent-notice proof mở intake mới ngay, không sửa kế toán case cũ hoặc cấp quyền cho followup ticket cũ. Bare image không có yêu cầu vẫn theo TG-15.

### TG-13 — Reaction

Command đủ điều kiện có heart khi nhận; topic issue có heart trước tạo và OK hand sau khi đã biết issue number. Waiting/followup không tự có reaction mới; /question không reaction. Dry-run có thể heart nhưng không OK hand.

### TG-14 — Ảnh và tài liệu

Ảnh giữ source ID, được validate signature/size/count rồi upload asset có tên do code tạo. Tài liệu chỉ nhận extension+MIME allowlist; không mở, unpack hay execute. Media lỗi/quá giới hạn ghi chú; không làm mất ticket. Quota upload tách khỏi issue.

Ảnh mới trong reply tới root tin cậy được chuẩn bị cùng caption và hội thoại để phân tích một gói context. Sau khi xác định đúng intent/target, dùng reservation upload hiện có và lưu asset/source provenance bền vững; uploaded/reserved/unknown không tự upload lại. Không đọc lại pixels ảnh cũ chỉ để sửa phạm vi. Ảnh không khả dụng phải nói rõ, không ghi nhận OCR hay báo cập nhật thành công giả.

### TG-15 — Vision và ảnh thiếu ngữ cảnh

Phân tích ảnh ban đầu phụ thuộc provider/consent/fallback. Ảnh không caption và không yêu cầu có thể pending_review, không tự tạo ticket/hỏi. Vision lỗi hoặc partition mơ hồ của yêu cầu tạo needs_human; câu hỏi thuần túy về ảnh vẫn chat.

Valid partition giữ riêng từng group: uncertainty của image group không nuốt task/chat khác. Khi partition/model lỗi, chỉ component có reply edge thật được xử lý chung; không dùng adjacency hay cùng sender để gộp unrelated text vào image ticket. Image-command chưa rõ giữ type nhưng needs_human; command text độc lập vẫn theo rule. Pixel không khả dụng vẫn cho phép phân loại caption/text với marker rõ, không gửi ảnh vượt consent hay tuyên bố OCR. Budget/provider/unavailable-pixel/semantic-uncertainty là reason khác nhau; không nói “chưa đọc ảnh” cho lỗi chưa dispatch do budget.

### TG-16 — Root hội thoại tin cậy

Reply được chứng minh bằng sent notice cùng chat hoặc source member thuộc topic có sent notice/issue đã chứng minh mới có quyền xử lý hội thoại. Chuỗi notice giữ cùng root. URL do người/model đưa, notice chưa sent và cross-chat không cấp quyền. Late reply thường không có tuyến notice có thể chỉ lưu followup.

`context_pending` không cấp quyền conversational followup. Chỉ exact pending source/no-issue + notice đã sent cùng chat được nhận diện để **fresh intake**, bypass aggregate vào case cũ; không chọn repo/issue từ notice hoặc source ngoại lai. Unknown/failed/foreign notice và positive ticket ack vẫn theo route cũ. Ingress dedup và original pending row/group vẫn được giữ nguyên.

### TG-17 — Chứng minh repository

Trước model và trước resume write, issue URL gốc phải canonical khớp repo cấu hình và issue number lưu tin cậy. Thiếu/mismatch thì hỏi Owner, không write/đoán link. Chấp nhận khác hoa thường cùng repo; query/URL giả không hợp lệ.

### TG-18 — Bổ sung và intent

Intent được kiểm tra là update/split/clarify/chat. Update thông thường append comment đã sanitize vào issue/ordinal tin cậy. Update kèm case_analysis hợp lệ có thể dựng lại cùng ticket bằng `format_issue` khi chỉ có một target/root không tách và có managed snapshot do lần tạo thành công hoặc recovery đã đối chiếu ghi nhận. Nội dung mới giữ facts đã cung cấp và media/source đã chứng minh, cho phép sửa giả thuyết cũ; model không chọn repo/issue/URL hay cấp quyền nguồn. Role IDs của enrichment chỉ thuộc đúng packet đã cung cấp, gồm reply hiện tại; quyền split vẫn giới hạn originals/bindings đã kiểm. Chat/cảm ơn không ghi GitHub; thiếu intent/target thì hỏi rõ.

Refresh reserve trong operation ledger trước ghi, GET kiểm repo/issue/source marker + SHA body + title + tập nhãn triage sở hữu so với snapshot, PATCH đúng issue rồi đọc lại xác minh. Nội dung/title/nhãn sở hữu đã bị sửa thì dừng, giữ sửa đổi đó; nhãn ngoài triage được giữ nguyên. Đây là preflight best-effort, **không phải remote atomic CAS**: sửa tay đồng thời giữa GET và PATCH vẫn có race. PATCH chưa gửi mà hết budget là known-not-sent; sau khi đã gửi nhưng chưa xác minh thì ambiguous và không tự retry. Legacy root chưa có snapshot không tự nhận body hiện tại làm baseline, chỉ comment evidence/hỏi rõ. Không tạo ticket thay thế khi enrichment thất bại.

### TG-19 — Bộ nhớ có provenance

Context mang reply edge, sender, source IDs và media metadata. Model history giới hạn năm job; binding xác nhận bền vững sống qua trim/restart và được kiểm lại với evidence. Legacy reconstruction giới hạn 100 job; không coi model hypothesis là user confirmation.

Managed snapshot và media_evidence giữ revision đang quản lý, source IDs và asset refs qua history trim; draft của model trong ticket vẫn là giả thuyết có thể sửa, tách khỏi human bindings. Context dài dùng metadata chung/columns để giữ ID/reply/sender/time/flags trong budget; excerpts bị cắt có marker rõ. Redaction chỉ xử lý string trong packet do code đánh dấu nội bộ, không biến numeric source/sender/reply IDs thành phone text; user tự đưa JSON không được nhận dấu nội bộ này.

THINKING đóng gói confirmed facts riêng khỏi model hypotheses, nguồn chưa gán và conflict. Journal `triage.decision/v1` giữ owner AlexChu, version CAS, policy/prompt/model lineage, ID bằng chứng, partition/reply targets và operation refs. Lưu quyết định trước tool và kết quả sau tool, có event theo revision; không lưu transcript, profile, private reasoning hay dùng journal thay quyền reserve hiện hành. Root/sender/repo/partition vẫn phải được code chứng minh.

### TG-20 — Xác nhận ticket 1/2

Câu ordinal rõ ràng cùng tác giả reply trực tiếp source có thể bind source→ticket; desired count có evidence riêng. Xác nhận một phần phải acknowledge rồi hỏi phần còn thiếu. Conflict chặn đến khi cùng tác giả xác nhận lại đúng anchor; quoted/negated label không xác nhận.

### TG-21 — Tách ticket

Tối đa năm ticket tổng: giữ yêu cầu đầu trên issue gốc, tạo N−1 sibling và append comment sửa scope kèm link trên issue gốc. Mỗi source gốc đúng một lần trong nhóm hoặc bucket chưa gán hợp lệ; reject ID ngoại lai/trùng/thiếu/conflict. Không tự xóa/đóng issue gốc.

### TG-22 — Nguồn chưa gán

Khi số lượng và anchor đã xác nhận đủ, ảnh không caption có thể còn chưa gán và phải hiện rõ. Text chưa gán cần semantic role ancillary đủ tin cậy trong request hẹp anchors+text; request thứ ba/unknown không được giấu. Followup dùng media provenance/asset cũ, không đọc lại pixel.

### TG-23 — Sửa sau khi tách

Gán media được xác nhận sau đó append vào đúng sibling. Yêu cầu tách lại không tạo thêm batch. Không biết target thì hỏi đúng phần còn thiếu. Provider failure giữ anchor và phân biệt với uncertainty ngữ nghĩa bằng reason code, không lặp câu hỏi chung mất context.

### TG-24 — Side effect bền vững

Lưu plan đã validate trước side effect; reserve create/comment trước một POST. Reserved sau restart hoặc outcome không biết là ambiguous, không tự gửi lại. Giữ link của kết quả đã biết khi lỗi một phần; chỉ resume operation planned và các kết quả done đã chứng minh.

### TG-25 — Đồng thời và sweep

Lock thực thi hội thoại tách khỏi lock SQLite; không giữ DB lock trong lúc model/Telegram/GitHub chờ. Sweep tiếp tục job hợp lệ sau restart kể cả không có ingress mới; không dùng sự im lặng của user làm approval.

### TG-26 — Gửi và sửa notice

Reserve notice một lần: sent/failed/reserved không tự resend. Recovery analysis được duyệt sửa đúng notice đã gửi, theo planned→reserved→done/ambiguous. Edit lỗi không fallback thành send mới; unknown edit cần operator kiểm tra.

### TG-27 — Recovery do operator

ensure_followup_job chỉ seed một stored followup đã chứng minh một lần, không global backfill. retry_followup_analysis chỉ cho terminal provider_unavailable, zero operations/siblings/split plan, không active root job, repo và notice chính xác; lưu audit bất biến. Không rearm original case hay retry POST ambiguous.

Khi operator chuẩn hóa lại nội dung một ticket hiện có, phải dựng từ source/details đã đối chiếu qua `format_issue` cùng template đang dùng trong daemon; không viết Markdown thay thế thủ công. Công cụ `scripts/reformat-telegram-issue.py` preview mặc định, GET đối chiếu marker nguồn và SHA body trước PATCH đúng issue rồi đọc lại kết quả; không tạo issue mới, gửi Telegram hay sửa liên kết nguồn trong SQLite. Giữ nguyên ảnh/caption đúng phạm vi và provenance xác nhận thủ công. Hướng dẫn thao tác ở README listener.

### TG-28 — Xưng hô theo profile

Bot xưng Em. Chỉ self-description/pronoun/preferred-address rõ trên bio công khai khả dụng: nam/he-him gọi Anh, nữ/she-her gọi Chị. Thiếu/mâu thuẫn/không đọc được dùng Anh/Chị; không suy từ tên, avatar hay lời chat. Lookup hai giây, cache tạm 256 mục/năm phút; không lưu/log/gửi bio cho model.

### TG-29 — Giới hạn cá nhân hóa

Chỉ đổi prose do assistant tạo. Giữ nguyên source quote, URL và nội dung GitHub. Tra sender thật lưu từ source, kể cả edit notice; không lấy tác giả forward hoặc bot làm người nhận.

### TG-30 — Provider và privacy reasoning

Provider local/hosted theo cấu hình. Cloud text cần consent và redact; cloud ảnh cần thêm image consent và provider hỗ trợ. Lỗi/unusable theo fallback cấu hình. Chỉ parse content, strip think block; private thinking không được lưu/log/đưa issue. Local think không hứa parity với hosted.

Khi THINKING bật, hosted trả `invalid_json` hoặc output không qua parser dùng slot còn lại để retry validation trên cùng host, nguyên context bundle/schema. Không gửi output lỗi vào repair prompt, không nới parser. Lỗi transport/auth/timeout chuyển fallback cấu hình ngay nếu còn slot. Hai format failures tiêu hết hai actual attempts: dừng, không gọi local lần thứ ba; fallback hỏi rõ theo scope hiện có. THINKING tắt giữ hành vi legacy.

Đường THINKING bounded transport gửi `thinking: {type: disabled}` cho DeepSeek để thực thi `model.think=false` đã cấu hình: bỏ tham số này sẽ dùng chế độ reasoning mặc định của nhà cung cấp. Không đổi model/consent, không thêm hỗ trợ hosted `think=true`, không đọc/lưu reasoning_content. Đây là sửa wire request riêng listener; rollback semantic path cũ vẫn dùng shared transport cũ. [DeepSeek Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/), [Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/).

DeepSeek non-thinking request gửi `temperature: 0` để giữ ý định sampling tập trung đã đặt ở classifier; trước đó hosted adapter bỏ sót nên server dùng mặc định riêng. Không đổi threshold hay tự coi sampling bằng không là đảm bảo đúng. Các provider khác giữ wire contract cũ; giới hạn semantics vẫn theo code validation. [DeepSeek Chat Completions API](https://api-docs.deepseek.com/api/create-chat-completion/).

Khi THINKING bật, một lượt dùng chung tối đa hai model attempts thực sự, gồm retry/fallback; lock wait không tiêu attempt. Deadline ngữ nghĩa 40 giây tính cả queue/DNS/TCP/TLS/response, mỗi request tối đa 20 giây còn lại. Tối đa ba batch đọc context có scope; text envelope tối đa 8000 byte UTF-8 bảo thủ cho cả prompt, không hứa đếm token ảnh. Source-role hẹp tối đa 512 output tokens; draft topic/followup giữ output cấu hình. Hết budget giữ xác nhận và fallback hỏi rõ; không cấp quyền retry POST. Attachment/GitHub/Telegram vẫn có timeout riêng: 40 giây không phải SLA kết thúc toàn bộ ticket/reply.

### TG-31 — Quyền write và sanitization

Repo API/nhãn do config/code cố định. Redact email/phone và sanitize source/model để tránh mention/link/reference/Markdown injection. Model/user không thể tự chọn permission, repo hay executable action. Original Telegram source link do code tạo có thể click.

### TG-32 — Quota, dry-run và dữ liệu

Quota issue tính create attempts kể cả sibling/unknown outcome; comment không tính issue quota. Upload quota riêng. Dry-run không GitHub write/upload/text notice, có thể download/validate và reaction TG-13. SQLite giữ text/context gốc được bảo vệ; logs/usage không giữ content. Usage giữ 90 ngày; test regression không phải accuracy AI hay SLA.

`[thinking] enabled` mặc định true khi thiếu cấu hình; false + restart quay về semantic path cũ, giữ mọi journal/reservation/result bền vững. Migration chỉ thêm bảng, không xóa/đổi dữ liệu cũ. Journal quyết định hiện chưa có automatic retention; dữ liệu ngắn chỉ gồm IDs/labels/provenance, operator phải quản lý storage theo chính sách SQLite đang có.

## Ví dụ đa lượt và giới hạn

Ví dụ tổng hợp, ID dưới đây là giả lập:

1. Tin 101/102/103 cùng yêu cầu → issue A, bot reply 101 với link A. Ba yêu cầu độc lập → A/B/C và reply từng source đầu tương ứng, không gắn cả ba link cho sai source.
2. User reply notice: “cần lên 2 ticket riêng”; bot giữ desired_count=2. User reply source 101: “đây là ticket 1”; bot lưu anchor 101, acknowledge và chỉ hỏi anchor còn lại. User reply source 103: “đây là ticket 2”; giữ issue A cho yêu cầu đầu, tạo B khi accounting hợp lệ. Tin 102 chưa gán phải hiển thị; nếu là text công việc khác thì hỏi, không âm thầm bỏ.
3. User reply trực tiếp ảnh nguồn đã lưu: “đây là ticket 2” với source evidence hợp lệ → comment B, không tạo C. Câu tách lại sau khi đã tách không tạo batch trùng.
4. Bio ghi he/him → Anh; she/her → Chị; không có/không rõ → Anh/Chị. Assistant luôn Em, quote “em” trong tin gốc vẫn giữ nguyên.

Known limits: parser xác nhận ordinal chỉ hỗ trợ một số câu rõ như “đây là ticket 1”, không phải hiểu mọi biến thể tiếng Việt. Binding deterministic yêu cầu cùng source author; nguồn đa tác giả có thể cần semantic partition/clarification. Late reply không có proven notice route có thể chỉ được lưu. Followup không xem lại pixel ảnh. At-most-once tránh lặp nhưng có thể mất notice sau crash, không hứa exactly-once. 727 kiểm thử regression trước đợt tài liệu không đo accuracy model; một lượt source-role 1.961 giây/177 output tokens là n=1, không phải p95/SLA.

## Bộ nhớ, truy vấn và debug vận hành

Mục này cụ thể hóa TG-19/TG-24/TG-27/TG-32 cho operator và agent tiếp nhận. Đây là hướng dẫn điều tra trên schema hiện có; không cấp quyền replay hoặc sửa dữ liệu. Runtime không load tài liệu này. Mọi báo cáo phải phân biệt dữ liệu live, snapshot có thời điểm và thiết kế chưa triển khai.

### Nguồn dữ liệu và vị trí

Các đường dẫn sau là cấu hình triển khai THINKPAD đã đối chiếu trong đợt sửa #47 ngày 2026-10-16; không hardcode thành mặc định cho mọi máy. Trước mỗi lần điều tra, xác nhận host, service, config đang dùng, `db_path`, runtime commit/fingerprint và thời điểm lấy bằng chứng. Không in toàn bộ config vì có thể chứa thông tin xác thực.

| Thành phần | Vị trí triển khai THINKPAD | Vai trò |
|---|---|---|
| Database chính | `/var/lib/agentfleet/tg/triage.sqlite3` | Tin gốc, context, liên kết ticket, quyết định và operation ledger |
| Config | `/etc/tg-triage/listener.toml` | Xác định database, session, phạm vi group và provider đang dùng |
| Log dịch vụ | `journalctl -u tg-triage` | Lỗi vận hành, startup, thời gian và trạng thái service |
| Backup | `/var/backups/agentfleet/` | Bản phục hồi; kiểm nội dung/thời điểm trước khi sử dụng |
| Audit sửa #47 | `/var/lib/agentfleet/tg/maintenance-issue47-context-20261016.json` | Lịch sử sửa thủ công đã đối chiếu; không thay journal gốc |
| Telegram session | `/var/lib/agentfleet/tg/gateway.session` | Thông tin đăng nhập nhạy cảm; không phải bộ nhớ nghiệp vụ, không xuất/chia sẻ để debug |

SQLite chính chứa text/context hội thoại, không được coi là content-free chỉ vì `model_usage` và log không lưu content. Ảnh được tải tạm rồi dọn; ảnh đã upload dùng GitHub asset refs và metadata/provenance trong state, không giả định có BLOB ảnh gốc trong database. `.context/` tại worktree chứa snapshot/evidence cục bộ, không phải database live và không tự là nguồn nghiệp vụ. Journal không lưu private reasoning; lý do nghiệp vụ ngắn không phải chain-of-thought.

### Bản đồ bảng và khóa truy vết

| Bảng | Dữ liệu cần kiểm tra |
|---|---|
| `messages` | Khóa `(chat_id, message_id)`, text, sender, `reply_to_id`, media metadata, `case_key`, `group_key`, decision/reason/error, issue number/URL |
| `cases`, `groups`, `reconciliation` | Gom nguồn, chia yêu cầu và đối soát đầu vào; phân biệt originals với followup khi đếm |
| `followup_roots` | Root tin cậy, ticket/siblings, `context_state`: human bindings, managed snapshot và media evidence |
| `followup_jobs` | Tin bổ sung, root, plan/result và trạng thái job |
| `followup_operations` | Từng thao tác theo `(job_id, ordinal)`, kind/payload, trạng thái, reservation và result |
| `followup_analysis_retries` | Audit retry analysis và trạng thái sửa notice được cho phép |
| `thinking_decisions` | Revision mới nhất theo `turn_key`, `root_key`, record quyết định/budget/provenance |
| `thinking_decision_events` | Lịch sử revision quyết định; giữ nguyên khi sửa ticket |
| `group_notices` | Source message, kind/status và `notice_message_id` thực tế |
| `attachments` | Reservation, filename và trạng thái upload; đối chiếu thêm asset refs trong context |
| `model_usage` | Provider/model, outcome, token, latency và cost; telemetry, không thay bằng chứng phân loại |

Chuỗi truy vết: `(chat_id, message_id)` → `case_key/group_key` → `followup_roots.root_key` → `followup_jobs.id` → `(job_id, ordinal)` → `(repository, issue_number)` và notice Telegram. Lấy root từ liên kết đã lưu, không tự nối chuỗi rồi đoán: case/root có thể khác định dạng. Issue number đơn lẻ không duy nhất giữa các repository; mọi truy vấn issue phải có chat/repository đã đối chiếu. Chỉ có URL được user/model đưa ra không chứng minh liên kết nguồn.

### Quy trình điều tra bắt buộc

1. Ghi nhận hiện tượng, kết quả mong muốn, host/runtime, thời gian kèm timezone, repository/issue và chat/message IDs. Giữ ảnh + caption + reply edges thành một gói evidence; không chỉ đọc tiêu đề ticket hoặc câu cuối.
2. Mở database bằng kết nối SQLite `mode=ro`, kiểm schema thực tế trước truy vấn. Không khởi tạo `Store` để debug live: constructor có thể migration/recovery và làm đổi trạng thái. Nếu cần snapshot nhất quán, dùng SQLite backup API hoặc quy trình backup đã kiểm chứng; không copy riêng file `.sqlite3` đang ghi rồi coi đó là snapshot đầy đủ.
3. Đọc nguồn và case/group để kiểm tra gộp nhầm, thiếu nguồn, role và ảnh. Kiểm human bindings riêng khỏi giả thuyết model trong managed snapshot. Followup mới không làm thay đổi nghĩa vụ đối soát originals.
4. Đọc decision journal và events: reason code, version, evidence IDs, budget, operation refs. Phân biệt context bị chặn trước model, provider/parse lỗi và uncertainty ngữ nghĩa. `model_calls_used=0` không chứng minh model đã phân tích sai; cần xác định bước chặn.
5. Đọc job/operation/notice ledger, sau đó GET issue/comment/asset và đọc đúng tin Telegram để xác minh kết quả thực tế. `planned`, `reserved`, `ambiguous` không phải bằng chứng side effect thành công; thiếu notice không chứng minh chưa tạo issue. Không reset reservation hay tự retry POST chưa rõ kết quả.
6. Báo cáo nguyên nhân có evidence, mức chắc chắn, các khóa liên quan, bước sửa dự kiến và giới hạn kiểm tra. Nếu sửa theo TG-27: backup, preview, kiểm source marker/body SHA, dùng renderer chuẩn, đọc lại kết quả; ghi audit mới và giữ lịch sử cũ. Không tạo ticket mới để chữa lỗi ghi nhận ticket hiện có.

### Truy vấn mẫu chỉ đọc

Ví dụ #47 đã xử lý: chat `-5264814171`, source `448`, hội thoại `449–452`, ảnh bổ sung `454`, notice `453` reply source `448`. Case `-5264814171:448`; followup root đã lưu `-5264814171:-5264814171:448`. Các ID này là ví dụ lịch sử, không phải giá trị mặc định.

Chạy trên THINKPAD bằng Python đã cài; truy vấn `schema` trước, rồi chọn từng mục để giới hạn dữ liệu xuất. Kết quả nhiều câu SELECT riêng không được coi là cùng một snapshot nếu service đang ghi.

```bash
sudo -u tglistener /opt/agentfleet/listener/.venv/bin/python - <<'PY'
import sqlite3

queries = {
    "schema": ("SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name", ()),
    "messages": ("""SELECT message_id, reply_to_id, case_key, group_key,
        decision, reason, issue_number, error FROM messages
        WHERE chat_id=? AND issue_number=? ORDER BY message_id""", (-5264814171, 47)),
    "roots": ("""SELECT root_key, source_message_id, case_key, group_key,
        issue_number, context_state FROM followup_roots
        WHERE chat_id=? AND issue_number=?""", (-5264814171, 47)),
    "decisions": ("""SELECT turn_key, root_key, version, record
        FROM thinking_decisions WHERE root_key IN (?,?) ORDER BY turn_key""",
        ("-5264814171:448", "-5264814171:-5264814171:448")),
    "events": ("""SELECT e.turn_key,e.version,e.record
        FROM thinking_decision_events e JOIN thinking_decisions d USING(turn_key)
        WHERE d.root_key IN (?,?) ORDER BY e.turn_key,e.version""",
        ("-5264814171:448", "-5264814171:-5264814171:448")),
    "operations": ("""SELECT j.id AS job_id,j.message_id,j.status AS job_status,
        j.plan,j.result AS job_result,o.ordinal,o.kind,o.status AS operation_status,
        o.payload,o.result AS operation_result FROM followup_jobs j
        LEFT JOIN followup_operations o ON o.job_id=j.id
        WHERE j.root_key=? ORDER BY j.id,o.ordinal""",
        ("-5264814171:-5264814171:448",)),
    "notices": ("""SELECT message_id,kind,status,notice_message_id FROM group_notices
        WHERE chat_id=? AND message_id IN (?,?) ORDER BY message_id""",
        (-5264814171, 448, 454)),
}
selection = "schema"  # Sau khi đối chiếu schema, chọn messages/roots/decisions/events/operations/notices.
with sqlite3.connect("file:/var/lib/agentfleet/tg/triage.sqlite3?mode=ro", uri=True) as db:
    db.row_factory = sqlite3.Row
    sql, params = queries[selection]
    for row in db.execute(sql, params):
        print(dict(row))
PY
```

Lọc log theo thời điểm có timezone rõ; khoảng dưới dùng UTC tương ứng 08:20–08:45 Asia/Saigon:

```bash
sudo journalctl -u tg-triage \
  --since "2026-10-16 01:20:00 UTC" \
  --until "2026-10-16 01:45:00 UTC" --no-pager
```

Output roots/job payload có thể chứa nội dung riêng tư: chỉ lưu evidence trong nơi được kiểm soát, che nội dung nhạy cảm trước khi chia sẻ; không commit database/session/config bí mật. Không ghi thêm raw prompt, ảnh hoặc private reasoning vào log chỉ để debug. Bản sửa #47 là ví dụ phân biệt `context_budget_exhausted` trước model với lỗi hiểu yêu cầu; giữ journal lỗi cũ và audit sửa mới để kiểm chứng được cả hai.

### Tiêu chí bàn giao và phạm vi hiện có

Báo cáo debug phải có: thời điểm/timezone; host/runtime; repository/issue; chat/source/reply IDs; case/group/root/turn/job/operation keys tìm được hoặc ghi rõ chưa tìm thấy; nguồn ảnh/caption đã kiểm; reason/budget; trạng thái ledger và readback bên ngoài; backup/audit nếu đã sửa; kết quả còn chưa xác minh. Không báo PASS chỉ từ hash hoặc đọc code.

Hiện có SQLite, journal và các ledger nêu trên; chưa có lệnh tổng hợp `trace --issue`, dashboard debug, hoặc bộ nhớ topic/facts/semantic search mới. Những phần đó là đề xuất, không được ghi như tính năng đã chạy. Giữ automatic retention 90 ngày chỉ cho usage theo TG-32; không áp con số này cho toàn bộ hội thoại/journal hay tự xóa mapping cần cho reply tin cũ.

## Truy vết và kiểm tra drift

Một block JSON duy nhất bên dưới là chỉ mục truy vết của chính tài liệu này, không có policy JSON thứ hai. `python scripts/check-telegram-triage-policy.py` kiểm ID/path/AST symbols/link/hashes; `python scripts/test_telegram_triage_policy.py` kiểm các trường hợp drift sai. SHA256 chuẩn hóa CRLF→LF theo Git. `REVIEW_REQUIRED` nghĩa cần review lại quan hệ policy/implementation, không tự kết luận code vi phạm rule. Không sửa runtime để làm checker xanh, không tự cập nhật hash khi chưa review. Human semantic review và test nghiệp vụ vẫn bắt buộc.

`baseline_commit` là mốc trước implementation mới; không tuyên bố các hash mới thuộc commit cũ. `source_kind` và `runtime_fingerprint` mô tả projection worktree được review trong PR này. Commit rollout và byte installed được ghi riêng trong release evidence.

```json telegram-triage-trace
{
  "schema": "telegram-triage-policy/v1",
  "owner": "AlexChu",
  "baseline_commit": "e4de1d5",
  "rules": [
    {
      "id": "TG-01",
      "code": [
        {
          "path": "listener/src/tg_triage/telegram_io.py",
          "symbol": "enqueue_event"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_telegram_io.py",
          "symbol": "TransportTests.test_own_private_and_unconfigured_messages_are_ignored"
        }
      ]
    },
    {
      "id": "TG-02",
      "code": [
        {
          "path": "listener/src/tg_triage/store.py",
          "symbol": "Store.enqueue"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_cases.py",
          "symbol": "OneIssueTests.test_only_one_caller_wins_a_case"
        },
        {
          "path": "listener/tests/test_telegram_io.py",
          "symbol": "TransportTests.test_cancellation_waits_for_active_thread_before_shutdown"
        }
      ]
    },
    {
      "id": "TG-03",
      "code": [
        {
          "path": "listener/src/tg_triage/rules.py",
          "symbol": "evaluate"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_rules.py",
          "symbol": "RulesTests.test_no_keyword_list_or_keyword_helper_decides_chat_or_ticket"
        },
        {
          "path": "listener/tests/test_rules.py",
          "symbol": "RulesTests.test_short_messages_and_courtesies_are_left_to_the_model"
        }
      ],
      "prompts": [
        "listener/prompts/classify.md",
        "listener/prompts/topics.md"
      ]
    },
    {
      "id": "TG-04",
      "code": [
        {
          "path": "listener/src/tg_triage/rules.py",
          "symbol": "command_type"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_rules.py",
          "symbol": "RulesTests.test_all_commands_are_confident_and_keep_payload"
        },
        {
          "path": "listener/tests/test_cases.py",
          "symbol": "GroupingTests.test_command_never_waits_and_gathers_earlier_messages_of_the_sender"
        }
      ]
    },
    {
      "id": "TG-05",
      "code": [
        {
          "path": "listener/src/tg_triage/store.py",
          "symbol": "Store.assign_case"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_cases.py",
          "symbol": "GroupingTests.test_reply_joins_the_case_even_beyond_the_window"
        },
        {
          "path": "listener/tests/test_cases.py",
          "symbol": "GroupingTests.test_message_from_another_sender_without_reply_is_a_separate_case"
        },
        {
          "path": "listener/tests/test_cases.py",
          "symbol": "GroupingTests.test_unknown_sender_ids_never_group"
        }
      ]
    },
    {
      "id": "TG-06",
      "code": [
        {
          "path": "listener/src/tg_triage/pipeline.py",
          "symbol": "validate_partition"
        },
        {
          "path": "listener/src/tg_triage/thinking.py",
          "symbol": "preflight_initial"
        },
        {
          "path": "listener/src/tg_triage/thinking.py",
          "symbol": "pack_messages"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_topics.py",
          "symbol": "ChunkTests.test_27_window_of_100_messages_is_cut_into_chunks_of_at_most_40"
        },
        {
          "path": "listener/tests/test_topics.py",
          "symbol": "PartitionTests.test_validate_partition_accepts_only_an_exact_cover"
        },
        {
          "path": "listener/tests/test_thinking.py",
          "symbol": "ThinkingTests.test_three_requests_three_tickets_corresponding_replies"
        },
        {
          "path": "listener/tests/test_thinking_image_context.py",
          "symbol": "ThinkingImageTests.test_actual_caption_long_backend_ack_fits_and_sends_image_not_merged"
        },
        {
          "path": "listener/tests/test_thinking_image_context.py",
          "symbol": "ThinkingImageTests.test_two_images_captions_reply_edges_and_independent_work_share_one_user_bundle"
        }
      ]
    },
    {
      "id": "TG-07",
      "code": [
        {
          "path": "listener/src/tg_triage/classifier.py",
          "symbol": "parse_topic_groups"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_direct_call.py",
          "symbol": "CategoryMappingTests.test_model_feature_is_folded_into_feedback"
        },
        {
          "path": "listener/tests/test_direct_call.py",
          "symbol": "CategoryMappingTests.test_the_model_category_wins_over_a_conflicting_type"
        }
      ],
      "prompts": [
        "listener/prompts/topics.md",
        "listener/prompts/case.md"
      ]
    },
    {
      "id": "TG-08",
      "code": [
        {
          "path": "listener/src/tg_triage/pipeline.py",
          "symbol": "Pipeline._safety"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_model_judgment.py",
          "symbol": "SafetyLockTests.test_a_no_tag_group_the_model_calls_a_bug_is_an_automatic_issue"
        },
        {
          "path": "listener/tests/test_direct_call.py",
          "symbol": "SingleCaseModelTests.test_untagged_unsure_ticket_asks_for_clarification"
        },
        {
          "path": "listener/tests/test_direct_call.py",
          "symbol": "ChatAndReviewStateTests.test_a_failed_model_without_a_tag_asks_for_clarification"
        }
      ]
    },
    {
      "id": "TG-09",
      "code": [
        {
          "path": "listener/src/tg_triage/pipeline.py",
          "symbol": "Pipeline._safety"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_direct_call.py",
          "symbol": "CategoryMappingTests.test_a_question_command_is_chat_without_an_issue_or_a_reaction"
        },
        {
          "path": "listener/tests/test_direct_call.py",
          "symbol": "CategoryMappingTests.test_a_model_question_is_chat_even_with_a_tag_and_gets_no_reaction"
        }
      ]
    },
    {
      "id": "TG-10",
      "code": [
        {
          "path": "listener/src/tg_triage/pipeline.py",
          "symbol": "Pipeline._safety"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_direct_call.py",
          "symbol": "ChatAndReviewStateTests.test_a_command_the_model_calls_chat_is_a_forced_issue_with_the_commands_type"
        },
        {
          "path": "listener/tests/test_direct_call.py",
          "symbol": "Test37To42.test_41_a_tagged_group_that_is_only_contact_data_is_blocked_even_when_the_model_says_bug"
        },
        {
          "path": "listener/tests/test_direct_call.py",
          "symbol": "AtSignTests.test_an_at_handle_typed_in_the_text_without_the_flag_is_not_a_tag"
        }
      ]
    },
    {
      "id": "TG-11",
      "code": [
        {
          "path": "listener/src/tg_triage/group_notices.py",
          "symbol": "notice_text"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_group_notices.py",
          "symbol": "NoticeTests.test_three_messages_one_ticket_replies_only_to_first"
        },
        {
          "path": "listener/tests/test_group_notices.py",
          "symbol": "NoticeTests.test_three_messages_three_tickets_reply_to_each_with_matching_link"
        }
      ]
    },
    {
      "id": "TG-12",
      "code": [
        {
          "path": "listener/src/tg_triage/group_notices.py",
          "symbol": "notice_text"
        },
        {
          "path": "listener/src/tg_triage/pipeline.py",
          "symbol": "Pipeline._vision_failure_plans"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_group_notices.py",
          "symbol": "NoticeTests.test_untagged_uncertainty_always_asks_once"
        },
        {
          "path": "listener/tests/test_group_notices.py",
          "symbol": "NoticeTests.test_questions_bounded_redacted_and_mentions_disabled"
        },
        {
          "path": "listener/tests/test_group_notices.py",
          "symbol": "NoticeTests.test_error_safe_and_ambiguous_warning"
        },
        {
          "path": "listener/tests/test_group_notices.py",
          "symbol": "NoticeTests.test_rate_limit_without_issue"
        },
        {
          "path": "listener/tests/test_thinking_image_context.py",
          "symbol": "ThinkingImageTests.test_invalid_partition_isolates_sources_and_pending_reply_creates_fresh_intake"
        }
      ]
    },
    {
      "id": "TG-13",
      "code": [
        {
          "path": "listener/src/tg_triage/pipeline.py",
          "symbol": "Pipeline._acknowledge"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_cases.py",
          "symbol": "CaseWorkerTests.test_three_messages_one_issue_and_one_ok_hand_on_the_first_message_only"
        },
        {
          "path": "listener/tests/test_cases.py",
          "symbol": "CaseWorkerTests.test_followup_and_waiting_messages_get_no_reaction"
        },
        {
          "path": "listener/tests/test_direct_call.py",
          "symbol": "Test37To42.test_39_an_automatic_bug_in_dry_run_is_hearted_but_never_ok_handed"
        }
      ]
    },
    {
      "id": "TG-14",
      "code": [
        {
          "path": "listener/src/tg_triage/attachments.py",
          "symbol": "document_extension"
        },
        {
          "path": "listener/src/tg_triage/followup_store.py",
          "symbol": "FollowupStoreMixin.record_followup_evidence"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_attachments.py",
          "symbol": "ImageInIssueTests.test_every_image_of_the_case_is_uploaded_with_message_id_and_ordinal_names"
        },
        {
          "path": "listener/tests/test_documents.py",
          "symbol": "RejectedDocumentTests.test_disguised_files_are_refused_when_name_and_mime_disagree"
        },
        {
          "path": "listener/tests/test_documents.py",
          "symbol": "DocumentFailureTests.test_nothing_is_ever_opened_or_unpacked"
        },
        {
          "path": "listener/tests/test_followup_enrichment.py",
          "symbol": "EnrichmentTests.test_new_photo_is_joint_on_wire_and_refreshes_canonical_bug_once"
        },
        {
          "path": "listener/tests/test_followup_enrichment.py",
          "symbol": "EnrichmentTests.test_upload_error_is_honest_and_no_reupload_after_ledger_exists"
        }
      ]
    },
    {
      "id": "TG-15",
      "code": [
        {
          "path": "listener/src/tg_triage/pipeline.py",
          "symbol": "Pipeline._images"
        },
        {
          "path": "listener/src/tg_triage/pipeline.py",
          "symbol": "Pipeline._plan_chunk"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_image_context.py",
          "symbol": "ImageContextTests.test_photo_before_request_idle_window_and_real_payload_reuses_download"
        },
        {
          "path": "listener/tests/test_image_context.py",
          "symbol": "ImageContextTests.test_uncertain_pure_information_question_about_image_stays_silent"
        },
        {
          "path": "listener/tests/test_thinking_image_context.py",
          "symbol": "ThinkingImageTests.test_uncertain_image_group_does_not_swallow_independent_task_or_chat"
        }
      ]
    },
    {
      "id": "TG-16",
      "code": [
        {
          "path": "listener/src/tg_triage/followup_store.py",
          "symbol": "FollowupStoreMixin.followup_source"
        },
        {
          "path": "listener/src/tg_triage/store.py",
          "symbol": "Store.pending_context_source"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_followups.py",
          "symbol": "FollowupTests.test_cross_chat_forged_url_and_unsent_notice_do_not_resolve"
        },
        {
          "path": "listener/tests/test_followup_context.py",
          "symbol": "ContextTests.test_direct_nonfirst_reply_routes_and_partial_label_ack_does_not_call_model"
        },
        {
          "path": "listener/tests/test_thinking_image_context.py",
          "symbol": "ThinkingImageTests.test_pending_direct_source_requires_sent_notice_and_foreign_ids_do_not_authorize"
        }
      ]
    },
    {
      "id": "TG-17",
      "code": [
        {
          "path": "listener/src/tg_triage/followups.py",
          "symbol": "_run"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_followups.py",
          "symbol": "FollowupTests.test_repo_identity_change_and_forged_or_missing_source_url_fail_closed"
        },
        {
          "path": "listener/tests/test_followups.py",
          "symbol": "FollowupTests.test_persisted_plan_resume_under_changed_repo_never_posts"
        },
        {
          "path": "listener/tests/test_followups.py",
          "symbol": "FollowupTests.test_canonical_source_url_casefold_still_accepts_same_repo"
        }
      ]
    },
    {
      "id": "TG-18",
      "code": [
        {
          "path": "listener/src/tg_triage/followup_model.py",
          "symbol": "validate_followup"
        },
        {
          "path": "listener/src/tg_triage/github_issues.py",
          "symbol": "GitHubIssues.refresh_issue"
        },
        {
          "path": "listener/src/tg_triage/followup_store.py",
          "symbol": "FollowupStoreMixin.register_managed_issue"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_followups.py",
          "symbol": "FollowupTests.test_update_comment_only_uses_trusted_number_and_keeps_body"
        },
        {
          "path": "listener/tests/test_followups.py",
          "symbol": "FollowupTests.test_uncertain_intent_failure_and_chat_never_write"
        },
        {
          "path": "listener/tests/test_followup_enrichment.py",
          "symbol": "EnrichmentTests.test_existing_manual_body_title_or_owned_label_edits_preserved"
        },
        {
          "path": "listener/tests/test_followup_enrichment.py",
          "symbol": "EnrichmentTests.test_update_roles_accept_actual_reply_but_reject_unprovided_ids"
        },
        {
          "path": "listener/tests/test_followup_enrichment.py",
          "symbol": "EnrichmentTests.test_preflight_get_failure_is_not_sent_but_readback_failure_is_ambiguous"
        }
      ],
      "prompts": [
        "listener/prompts/followup.md"
      ]
    },
    {
      "id": "TG-19",
      "code": [
        {
          "path": "listener/src/tg_triage/followup_store.py",
          "symbol": "FollowupStoreMixin.followup_context"
        },
        {
          "path": "listener/src/tg_triage/thinking.py",
          "symbol": "Turn.record"
        },
        {
          "path": "listener/src/tg_triage/thinking.py",
          "symbol": "pack_messages"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_followup_context.py",
          "symbol": "ContextTests.test_general_followup_serialization_retains_reply_and_chronological_history"
        },
        {
          "path": "listener/tests/test_followup_context.py",
          "symbol": "ContextTests.test_restart_and_trimmed_model_history_retain_prior_binding"
        },
        {
          "path": "listener/tests/test_thinking.py",
          "symbol": "ThinkingTests.test_screenshot_confirmed_context_survives_restart_and_history_trim"
        },
        {
          "path": "listener/tests/test_thinking_pos_context.py",
          "symbol": "POSContextTests.test_six_and_twenty_sources_fit_without_losing_mandatory_metadata"
        },
        {
          "path": "listener/tests/test_thinking_pos_context.py",
          "symbol": "POSContextTests.test_structured_cloud_redaction_preserves_identity_numbers_and_removes_text_contacts"
        },
        {
          "path": "listener/tests/test_followup_enrichment.py",
          "symbol": "EnrichmentTests.test_packet_marks_source_and_reply_excerpts_as_truncated"
        }
      ]
    },
    {
      "id": "TG-20",
      "code": [
        {
          "path": "listener/src/tg_triage/followups.py",
          "symbol": "_apply_context"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_followup_context.py",
          "symbol": "ContextTests.test_historical_conflict_persists_and_requires_same_anchor_repair"
        },
        {
          "path": "listener/tests/test_followup_context.py",
          "symbol": "ContextTests.test_foreign_chat_author_and_unproven_binding_block"
        },
        {
          "path": "listener/tests/test_followup_context.py",
          "symbol": "ContextTests.test_negated_or_quoted_labels_never_confirm_binding"
        }
      ]
    },
    {
      "id": "TG-21",
      "code": [
        {
          "path": "listener/src/tg_triage/followups.py",
          "symbol": "_plan"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_followups.py",
          "symbol": "FollowupTests.test_split_two_total_and_reply_new_message_preserves_original_fields"
        },
        {
          "path": "listener/tests/test_followups.py",
          "symbol": "FollowupTests.test_invalid_partition_asks_no_new_issue"
        },
        {
          "path": "listener/tests/test_followup_context.py",
          "symbol": "ContextTests.test_foreign_or_duplicate_source_role_ids_cannot_bypass_full_partition"
        }
      ]
    },
    {
      "id": "TG-22",
      "code": [
        {
          "path": "listener/src/tg_triage/classifier.py",
          "symbol": "Classifier.analyze_source_roles"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_followup_retry.py",
          "symbol": "RetryTests.test_minimal_role_request_never_calls_full_followup"
        },
        {
          "path": "listener/tests/test_followup_retry.py",
          "symbol": "RetryTests.test_role_ids_must_exactly_cover_requested_subset"
        },
        {
          "path": "listener/tests/test_followup_context.py",
          "symbol": "ContextTests.test_third_request_or_unknown_role_never_suppressed"
        },
        {
          "path": "listener/tests/test_followup_context.py",
          "symbol": "ContextTests.test_captionless_only_unassigned_bypasses_provider_with_complete_anchors"
        }
      ]
    },
    {
      "id": "TG-23",
      "code": [
        {
          "path": "listener/src/tg_triage/followups.py",
          "symbol": "_plan"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_followup_context.py",
          "symbol": "ContextTests.test_later_confirmed_media_assignment_comments_correct_sibling_without_new_issue"
        },
        {
          "path": "listener/tests/test_followups.py",
          "symbol": "FollowupTests.test_repeat_split_does_not_duplicate_then_ordinal_update"
        },
        {
          "path": "listener/tests/test_followup_context.py",
          "symbol": "ContextTests.test_truncated_provider_json_is_identified_not_true_uncertainty"
        }
      ]
    },
    {
      "id": "TG-24",
      "code": [
        {
          "path": "listener/src/tg_triage/followup_store.py",
          "symbol": "FollowupStoreMixin.reserve_followup_operation"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_followups.py",
          "symbol": "FollowupTests.test_crash_reserved_create_never_reposts_even_new_reply"
        },
        {
          "path": "listener/tests/test_followups.py",
          "symbol": "FollowupTests.test_resume_done_create_only_remaining_planned_comment"
        },
        {
          "path": "listener/tests/test_followups.py",
          "symbol": "FollowupTests.test_partial_split_keeps_confirmed_links_no_unknown_link"
        }
      ]
    },
    {
      "id": "TG-25",
      "code": [
        {
          "path": "listener/src/tg_triage/followups.py",
          "symbol": "sweep"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_followups.py",
          "symbol": "FollowupTests.test_two_parallel_replies_share_root_one_split"
        },
        {
          "path": "listener/tests/test_followups.py",
          "symbol": "FollowupAsyncTests.test_notice_transport_does_not_hold_ingress_database_lock"
        },
        {
          "path": "listener/tests/test_followups.py",
          "symbol": "FollowupTests.test_restart_sweep_queued_no_new_incoming"
        }
      ]
    },
    {
      "id": "TG-26",
      "code": [
        {
          "path": "listener/src/tg_triage/group_notices.py",
          "symbol": "notify"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_group_notices.py",
          "symbol": "NoticeTests.test_restart_reserved_failed_sent_never_retry"
        },
        {
          "path": "listener/tests/test_followup_retry.py",
          "symbol": "RetryTests.test_edit_failure_never_falls_back_to_send_or_retries"
        },
        {
          "path": "listener/tests/test_followup_retry.py",
          "symbol": "RetryTests.test_reserved_edit_crash_is_terminal_after_restart"
        }
      ]
    },
    {
      "id": "TG-27",
      "code": [
        {
          "path": "listener/src/tg_triage/followup_store.py",
          "symbol": "FollowupStoreMixin.retry_followup_analysis"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_followups.py",
          "symbol": "FollowupTests.test_done_operation_idempotent_mapping_and_legacy_seed_once"
        },
        {
          "path": "listener/tests/test_followup_retry.py",
          "symbol": "RetryTests.test_retry_refuses_unsafe_or_unmatched_state"
        },
        {
          "path": "listener/tests/test_followup_retry.py",
          "symbol": "RetryTests.test_explicit_retry_edits_existing_notice_once_with_immutable_audit"
        }
      ]
    },
    {
      "id": "TG-28",
      "code": [
        {
          "path": "listener/src/tg_triage/group_notices.py",
          "symbol": "address_from_public_bio"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_profile_address.py",
          "symbol": "BioTests.test_only_explicit_self_description_sets_address"
        },
        {
          "path": "listener/tests/test_profile_address.py",
          "symbol": "ProfileTests.test_profile_cache_is_bounded_ttl_and_has_no_bio_persistence"
        },
        {
          "path": "listener/tests/test_profile_address.py",
          "symbol": "ProfileTests.test_timeout_blocked_failure_and_unknown_profiles_fallback"
        }
      ]
    },
    {
      "id": "TG-29",
      "code": [
        {
          "path": "listener/src/tg_triage/group_notices.py",
          "symbol": "personalize_notice"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_profile_address.py",
          "symbol": "BioTests.test_personalization_preserves_quoted_source_and_urls"
        },
        {
          "path": "listener/tests/test_profile_address.py",
          "symbol": "ProfileTests.test_normal_send_and_notice_edit_resolve_actual_stored_source_author"
        }
      ]
    },
    {
      "id": "TG-30",
      "code": [
        {
          "path": "listener/src/tg_triage/provider.py",
          "symbol": "CloudProvider._ask"
        },
        {
          "path": "listener/src/tg_triage/thinking.py",
          "symbol": "Turn.attempt"
        },
        {
          "path": "listener/src/tg_triage/thinking_transport.py",
          "symbol": "complete"
        },
        {
          "path": "listener/src/tg_triage/__main__.py",
          "symbol": "_build_classifier"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_usage_fallback.py",
          "symbol": "HostedImageTests.test_without_image_consent_the_images_go_to_local_ollama_only"
        },
        {
          "path": "listener/tests/test_usage_fallback.py",
          "symbol": "FallbackTests.test_primary_error_runs_the_same_request_on_ollama_and_marks_roles"
        },
        {
          "path": "listener/tests/test_model_judgment.py",
          "symbol": "ThinkingTests.test_the_reasoning_never_reaches_the_logs_the_issue_or_sqlite"
        },
        {
          "path": "listener/tests/test_thinking.py",
          "symbol": "ThinkingTests.test_cloud_invalid_retries_share_budget_and_cannot_fallback_third_attempt"
        },
        {
          "path": "listener/tests/test_thinking.py",
          "symbol": "ThinkingTests.test_deepseek_false_is_explicitly_disabled_on_wire_only_for_deepseek"
        },
        {
          "path": "listener/tests/test_thinking_image_context.py",
          "symbol": "ThinkingImageTests.test_hosted_format_failure_retries_same_bundle_before_local"
        },
        {
          "path": "listener/tests/test_thinking_image_context.py",
          "symbol": "ThinkingImageTests.test_hosted_network_failure_keeps_second_slot_for_local"
        },
        {
          "path": "listener/tests/test_thinking_image_context.py",
          "symbol": "ThinkingImageTests.test_two_hosted_format_failures_stop_without_third_or_local_call"
        },
        {
          "path": "listener/tests/test_thinking_pos_context.py",
          "symbol": "POSContextTests.test_exact_five_pos_messages_use_default_hosted_pipeline_one_bug_first_reply"
        },
        {
          "path": "listener/tests/test_thinking_pos_context.py",
          "symbol": "POSContextTests.test_previous_startup_default_prompt_injection_exhausted_five_message_budget"
        }
      ]
    },
    {
      "id": "TG-31",
      "code": [
        {
          "path": "listener/src/tg_triage/issue_format.py",
          "symbol": "sanitize"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_followups.py",
          "symbol": "FollowupBoundaryTests.test_comment_transport_targets_fixed_repo_no_retry"
        },
        {
          "path": "listener/tests/test_cases.py",
          "symbol": "AnalysisTests.test_model_text_goes_through_the_sanitizer"
        }
      ]
    },
    {
      "id": "TG-32",
      "code": [
        {
          "path": "listener/src/tg_triage/store.py",
          "symbol": "Store.reserve_github_call"
        },
        {
          "path": "listener/src/tg_triage/thinking.py",
          "symbol": "decision_turn"
        }
      ],
      "tests": [
        {
          "path": "listener/tests/test_followups.py",
          "symbol": "FollowupTests.test_quota_extended_and_comments_not_charged"
        },
        {
          "path": "listener/tests/test_followups.py",
          "symbol": "FollowupTests.test_dry_run_no_external_writes_or_reservations"
        },
        {
          "path": "listener/tests/test_documents.py",
          "symbol": "RejectedDocumentTests.test_hourly_quota_counts_documents_like_images"
        },
        {
          "path": "listener/tests/test_usage_fallback.py",
          "symbol": "RetentionAndConfigTests.test_rows_older_than_90_days_are_pruned_and_the_table_is_additive"
        },
        {
          "path": "listener/tests/test_thinking.py",
          "symbol": "ThinkingTests.test_kill_switch_keeps_legacy_path_without_decision_journal"
        }
      ]
    }
  ],
  "runtime_inventory": {
    "listener/src/tg_triage/__init__.py": "a0d704101dd43989f394a4c0ca7d54624c9e13eec981f932e47b3b0106cff595",
    "listener/src/tg_triage/__main__.py": "081827c01240834076ec6e313b686577f143b447336506ad913a5e2797f2148e",
    "listener/src/tg_triage/ai_transport.py": "3534ca68a62e76328bb4118d79277aa21ce57d2a60dcc300a50a070a26f08f43",
    "listener/src/tg_triage/attachments.py": "77a0efaf7f7c44a3c2efe69135d9717f9b1035a967fb95a32973041aca4e40ff",
    "listener/src/tg_triage/classifier.py": "34c5d87ba3f5bd9a8279d2f3ca659bee6aaa7f425fe5e35bde95a6bff776b0b1",
    "listener/src/tg_triage/config.py": "40391fd623f45a4218e4f2155ff971704f44558e701407b728dcbf6e21c56336",
    "listener/src/tg_triage/followup_model.py": "7c8933824eab78abcb8934777079ff451eb379dc89e6866234c4d8541e78a029",
    "listener/src/tg_triage/followup_store.py": "75e3554e24d0decb581dbd2e5c2fa0f3de22e9d16e4cc79de9a06a000a9923d1",
    "listener/src/tg_triage/followups.py": "03834bfc805b42985c2e61de180196c02347b3816ba4d8210ca3e0322ed87543",
    "listener/src/tg_triage/github_issues.py": "360489dd34ad6f9080d6dd2b080da3cc192501157bbbf67319b6d5885b803cd3",
    "listener/src/tg_triage/group_notices.py": "8f52a9f1f375191a5374a645da4ae82b1e32b8882ce7e756b7d07ef5f2b91ce8",
    "listener/src/tg_triage/issue_format.py": "04b274fc419a57bb0e4e59636303c8762ec0c295d9029130b85ebb6e7936c3d5",
    "listener/src/tg_triage/model_lock.py": "52a02869c560575355d448856001f799cc3579b52d5eea8687fb2c1e1179675c",
    "listener/src/tg_triage/models.py": "4cdde0d6f3d0af4571fe032c90a2c423143d69512a6ddeea5d541e844fea4e14",
    "listener/src/tg_triage/pipeline.py": "73f63b347f9576cb124f8f5b6a2e632411944115ca1eb743d7ae26c971125da8",
    "listener/src/tg_triage/provider.py": "b72f72d305d738fe183fac3a81a3141b3cdca8bffe88bc181ae04535d2a25f1a",
    "listener/src/tg_triage/reactions.py": "eb5e1f13db0127412239cdc0275bc4ffbc90f287eb87c060124328a0dcc53e71",
    "listener/src/tg_triage/receipt.py": "ff455fdfb9424cc3e802641607dfe3c872ddc65490b2705bb804ca92f282d51a",
    "listener/src/tg_triage/rules.py": "93778eef5df2abe88ae4424179ec509595cd6dcf14d5d94ddb70d5852454bccd",
    "listener/src/tg_triage/runtime.py": "558a876aa86f2e2c7be8d54587ea9510e0762686bbf2a3a8dd9742db596b1862",
    "listener/src/tg_triage/store.py": "236f2b42e0ba66af22c6de148cb20661e262534b4e28315bb208c38f4e9b1a6f",
    "listener/src/tg_triage/telegram_io.py": "fdc4233f77571589b0ab551ff577acba58e2c9556bbdcfc7522e277aadef1b76",
    "listener/src/tg_triage/thinking.py": "3a000d19fe0cadf720e9a6d97ad198b0961a891a713fa3aa6622d5196dd8008d",
    "listener/src/tg_triage/thinking_transport.py": "6a8921188e21c8424c445c03b2ff903f941885b5d4b6930306bbffd3842a6d56",
    "listener/src/tg_triage/usage.py": "421146bd0ac181a2ff45be7c2d7e82cca1ff54cafc2941bc958f20eb9622828e",
    "listener/prompts/case.md": "2a5f671cd9a9dbfb1316497dc6dfcd07c404a5abd038411bc66b4ccc9e6f190b",
    "listener/prompts/classify.md": "1af9f58ef64737dfe47e480dd47c047d8c682d7c8b0c573d98fb13cfd469d249",
    "listener/prompts/followup.md": "adf0d2b3bee7cd4dfdb26259cbf56653b6ab4ec2a9854a251d3887e3f1a491eb",
    "listener/prompts/thinking-topics.md": "f3295f8cdf8d3707d8844ffe61c2eeca0efc1763a0f9879c86b375c1a8b15a58",
    "listener/prompts/topics.md": "99dca8ee4272cca18bb283d489b709542661694639aad6ec720d63c7a2b47dc8"
  },
  "legacy_documents": [
    "Docs/00-master/worker-agent-fleet-1_brief_261007_v1.0.7.md",
    "Docs/00-master/worker-agent-fleet-1_master_261007_v1.0.8.md",
    "Docs/00-master/worker-agent-fleet-1_setup-plan_261008_v1.0.6.md",
    "Docs/02-engineering/worker-agent-fleet-1_assistant-definition_261012_v1.0.0.md",
    "Docs/02-engineering/worker-agent-fleet-1_engineering_261009_v1.0.3.md",
    "Docs/02-engineering/worker-agent-fleet-1_engineering_261009_v1.0.4.md",
    "Docs/02-engineering/worker-agent-fleet-1_service-split_261013_v1.0.0.md"
  ],
  "related_documents": [
    "Docs/02-engineering/telegram-triage-noissue_thinking-research_261015_v1.0.0.md",
    "Docs/03-qa/telegram-triage-noissue_qa_261015_v1.0.0.md"
  ],
  "source_kind": "reviewed_worktree_projection_after_baseline",
  "runtime_fingerprint": "764fb4de24bc1f3d404c27aba51b29bf8730cc5025e0983011b30823360fed04"
}
```

## Changelog

| Version | Ngày giờ (Asia/Saigon) | Tác giả | Thay đổi |
|---|---|---|---|
| v1.3.1 | 2026-10-16 09:57 | Codex / owner AlexChu | Bổ sung vị trí bộ nhớ, bản đồ schema/khóa, truy vấn chỉ đọc, quy trình debug và audit recovery theo TG-19/24/27/32; phân biệt triển khai hiện có với đề xuất. |
| v1.3.0 | 2026-10-16 09:11 | Codex / owner AlexChu | Sửa startup prompt override, đóng gói/redaction provenance và enrichment ảnh reply vào cùng ticket qua canonical renderer/managed snapshot; kiểm preflight và giới hạn race được nêu rõ. |
| v1.2.1 | 2026-10-16 01:08 | Codex / owner AlexChu | Chuẩn hóa xử lý lại ticket bằng renderer đang chạy, preview/source-marker/body-SHA guard và provenance thủ công; kiểm thử sửa TASK #46 không tạo trùng. |
| v1.2.0 | 2026-10-15 23:29 | Codex / owner AlexChu | Compact image/context packing, scoped visual uncertainty/failure và visible pending→fresh intake recovery; real-image proof tách khỏi code tests. |
| v1.1.2 | 2026-10-15 22:26 | Codex / owner AlexChu | Phân biệt essentials ghi nhận bug với debugging extras; thực thi temperature0 classifier trên DeepSeek wire. |
| v1.1.1 | 2026-10-15 22:19 | Codex / owner AlexChu | Thực thi DeepSeek model.think=false ở bounded wire transport; giữ budget, đồng bộ regression/API references. |
| v1.1.0 | 2026-10-15 21:42 | Codex / owner AlexChu | Đồng bộ controller THINKING, budget, journal/CAS, rollback và QA acceptance; rollout cần evidence riêng. |
| v1.0.0 | 2026-10-15 21:12 | Codex / owner AlexChu | Chuẩn hóa 32 quy tắc, truy vết và giới hạn baseline. |
