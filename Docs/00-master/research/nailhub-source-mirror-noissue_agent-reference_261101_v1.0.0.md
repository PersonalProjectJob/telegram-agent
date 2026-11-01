---
type: doc
title: "Source Mirror: Tham chiếu vận hành cho agent"
status: current
area: automation
created: 2026-11-01
owner: AlexChu
---

# Source Mirror: Tham chiếu vận hành cho agent

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
| Original và nguồn | Cố định SBA Launch/Manage/Grow và California Board FAQ, ưu tiên xoay theo ngày/slot. Giữ URL thật, ghi bài gốc do AI tạo; không gán Scratch hay cơ quan nguồn làm tác giả. California chỉ áp dụng California. |
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
