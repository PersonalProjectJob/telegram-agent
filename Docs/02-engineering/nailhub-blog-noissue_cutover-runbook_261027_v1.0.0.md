---
type: doc
title: "NailBlog Agy-only cutover runbook"
owner: AlexChu
created: 2026-10-27
status: draft
---

# Runbook cutover NailBlog Agy-only (THINKPAD)

Phiên bản nội dung v1.0.1, 27/10/2026 (giữ đường dẫn đã liên kết). Nguồn: plan `Docs/04-tasks/nailhub-blog-noissue_tasks_261027_v2.0.0.md`, mục "Deploy + nghiệm thu", "Rủi ro GATE 0b", "Rollback".

Mục tiêu: hai service boot-enabled, không timer rời.

| Service | User | Lệnh | Ghi chú |
|---|---|---|---|
| `tg-nailblog.service` | `tgnailblog` (UID 992) | `python -m nailblog run` | `ExecStartPre` startup-gate, `TimeoutStopSec=660` |
| `tg-nailblog-subscription.service` | `startechai` (UID 1000) | `python -m nailblog.subscription_worker` | drop-in `subscription.conf`, `TimeoutStopSec=630` |

Quy ước: chạy bằng root trên host. Ký hiệu `[DỪNG - OWNER]` là điểm phải có Owner duyệt bằng tin nhắn trước khi chạy bước kế tiếp. Runbook này chỉ là tài liệu: không bước nào tự chạy.

Biến dùng chung (đặt một lần ở đầu phiên):

```sh
export CUT=/var/backups/nailblog-cutover-$(date +%Y%m%d-%H%M)
export SRC=/srv/agentfleet-split          # bản repo đã review trên host
mkdir -p "$CUT" && chmod 0700 "$CUT"
```

## Bước 0. Điều kiện trước

- Source ở commit đã review hoặc working-tree snapshot có inventory SHA256 được Owner duyệt; `python -m unittest deploy.test_nailblog_deploy deploy.test_deploy` và `cd nailblog; PYTHONPATH=src python -m unittest discover -s tests -t .` đều xanh trên máy build.
- `.gitattributes` đã có trong commit (nailblog/** và `systemd/*.service` là LF), wheel build từ byte LF.
- Owner đã trả lời bốn câu hỏi trong plan (mô hình duyệt, bài trượt gate, nhóm chủ đề, quyền cutover và reboot).
- Resume ngày **D-1 trước 10:00 giờ VN** để propose 10:00 và decide 13:00 cho ngày D cùng chạy trên config bốn slot. Nếu qua mốc đó, dời ngày nghiệm thu sang D+1; không chạy bù bằng script tay hoặc tự ghi `daily_runs`.

## Bước 1. Kiểm kê (chỉ đọc)

Chạy trước mọi thay đổi trên host (ngoài thư mục báo cáo riêng `$CUT`):

```sh
sudo -l -U startechai
id startechai
stat -c '%a %U:%G %n' /var/lib/agentfleet/nailblog /etc/nailblog /etc/nailblog/private \
  /etc/nailblog/private/admin-credentials.json /var/lib/agentfleet/nailblog/nailblog.session \
  /etc/agentfleet/tg-nailblog.env
findmnt -no OPTIONS /proc
```

Xác nhận tên credential/session thật trong config rồi kiểm đúng file đó: data/config/private directory 0700, credential/session/env 0600; bot đọc được credential, worker không đọc được. Không in nội dung file. Nhóm `agentfleet` không được cấp quyền đọc admin/session. `[DỪNG - OWNER]` nếu `startechai` có sudo **NOPASSWD**, vì worker Snap chạy `NoNewPrivileges=no`; không tự nới quyền để vượt gate. `sudo (ALL:ALL) ALL` có yêu cầu mật khẩu không đồng nghĩa NOPASSWD; kiểm cả group-derived grants. `/proc` thiếu `hidepid` nghĩa là user cục bộ có thể đọc argv/prompt; ghi nhận và để Owner quyết việc hardening riêng, tuyệt đối không truyền secret qua argv. Không tự remount `/proc` trong cutover.

```sh
python3 $SRC/deploy/nailblog_cutover_check.py > $CUT/inventory-before.json
python3 -m json.tool $CUT/inventory-before.json | less
```

Đọc và ghi lại ở báo cáo cutover:

- `systemd.report_bridge_present` (phải là true trước khi gỡ, false sau khi gỡ), `systemd.failed`;
- `database.control` (kỳ vọng `paused=1`), `database.onepost_missing_claims` (phải rỗng, nếu không rỗng thì DỪNG);
- `database.legacy_topic_claim_conflict` (hai run cùng topic), `database.admin_binding` (chỉ SHA256; lưu để so binding mới), `database.unfinished_posts` (id/state/slot/topic/autoflow membership) và `database.approved_unpublished_admin_operations`; không xuất nội dung bài/approval;
- `queue.jobs`: mỗi job có `marker` và `receipt` hay chưa; `queue.worker_ready.busy_job`;
- `queue.jobs[].request` có `kind`/`expires_at`/`protocol`: đối soát từng job lịch sử, không dispatch lại job `unknown`/hết hạn;
- `ledger.paid_intents_sha256` và `paid_intents_rows`: chép lại, đây là số đối chiếu sau cutover;
- `processes.agy` và `processes.lock_holders`;
- `config.category_ids.pattern_suspect` (chỉ cảnh báo dạng seed UUID, **không phải verdict placeholder**), `env.gemini_image_key_present`.

Script lấy `list-units --all '*nailblog*'`, `list-timers --all`, quét `/etc/systemd/system`, `/run/systemd/transient`, `/lib/systemd/system`; mỗi unit có `active`, `enabled`, `masked`, `path` và `paths`. `path=null` vẫn là unit runtime phải xử lý. ExecStart chỉ xuất hash và cờ `report_bridge` để không lộ argv. `--no-systemctl` chỉ dành fixture; `legacy_gate.pass=false` khi không lấy được trạng thái runtime.

Script chỉ mở DB bằng `mode=ro` trên bản copy riêng của DB và `-wal`, không mở SQLite trên file gốc hay tạo sidecar gốc. Nếu cần chứng minh, chạy `find /etc/nailblog* /var/lib/agentfleet/nailblog /var/lib/nailblog-subscription -type f -exec sha256sum {} + | sort > $CUT/sha-before.txt` trước và sau, rồi `diff`.

`[DỪNG - OWNER]` nếu DB có `error`, thiếu các bảng được kỳ vọng, `onepost_missing_claims` không rỗng, conflict topic, job `dispatched` không có `receipt`, hoặc thấy phiên Codex còn ghi vào host. Bảng chưa tồn tại không được coi là đã kiểm PASS: đối chiếu version Freeze7 thật và Owner xác nhận.

JSON listing admin: chạy `probe --out` chỉ đọc theo bước 5a **sau wheel**, lấy category/report. `probe` hiện tại **không xuất raw listing hay metadata phân trang**. Trong phiên admin staging đã xác thực, mở browser Network, refresh danh sách Blog (GET), lưu riêng response JSON listing vào file 0600 `$CUT/admin-listing.json` (không HAR, không header/cookie/token). Không dùng Create/Publish để lấy mẫu. Sau đó:

```sh
python3 $SRC/deploy/nailblog_cutover_check.py --admin-listing-sample "$CUT/admin-listing.json" > "$CUT/inventory-listing.json"
```

Đọc `admin_listing_sample.object_keys` để xác nhận các tên key phân trang thực tế ở root/nested object; file raw giữ kín. Nếu chưa có mẫu trước thay đổi, ghi BLOCKED cho kiểm shape, hoàn tất sau probe và trước resume; không suy diễn shape từ fixture. Không có flag `probe --listing`.

## Bước 2. Backup

```sh
mkdir -p "$CUT/units" && chmod 0700 "$CUT/units"
cp -a /etc/nailblog /etc/nailblog-subscription "$CUT"/ 2>/dev/null
cp -a /etc/agentfleet/tg-nailblog.env "$CUT"/
cp -a /etc/systemd/system/tg-nailblog.service /etc/systemd/system/tg-nailblog.service.d \
      /etc/systemd/system/tg-nailblog-subscription.service "$CUT"/
ls /etc/systemd/system | grep -i nailblog | while read u; do cp -a "/etc/systemd/system/$u" "$CUT"/; done
sqlite3 /var/lib/agentfleet/nailblog/nailblog.sqlite3 ".backup '$CUT/nailblog.sqlite3'"
sqlite3 "$CUT/nailblog.sqlite3" 'PRAGMA integrity_check;'       # phải in: ok
cp -a /var/lib/agentfleet/nailblog/ledger "$CUT"/ledger          # sổ cũ, KHÔNG mở bằng State()/PaidLedger
sha256sum "$CUT"/ledger/*.sqlite3* > $CUT/ledger.sha256
pip freeze --path /opt/agentfleet/nailblog/.venv/lib/python3*/site-packages > $CUT/freeze7-packages.txt
```

Giữ wheel Freeze7 đang chạy (`/var/backups/agentfleet/` do `agentfleet_deploy.py` tạo) để rollback. Không xoá intent hay journal.

## Bước 3. Dừng lane cũ

`[DỪNG - OWNER]` trước khi chạy cả bước này (plan: cần Owner).

3a. Gỡ drop-in daily4 của Codex:

```sh
rm /etc/systemd/system/tg-nailblog.service.d/zz-daily4-reporting.conf
systemctl daemon-reload
systemctl cat tg-nailblog | grep -c report_bridge     # phải in 0
```

3b. Dùng union inventory file + runtime, xử lý **từng tên chính xác** `nailblog-daily4*`, `nailblog-makeup*`, `nailblog-staging-*` và mọi timer NailBlog rời. Giữ nguyên state root (Create UNKNOWN 26/10). Không chỉ dựa `list-unit-files`: makeup publish.timer/transient có thể vẫn đang active.

Với unit file thật trong `/etc/systemd/system`, lặp lại thủ công cho từng tên đã kiểm:

```sh
u=nailblog-daily4.timer                    # thay bằng đúng MỘT tên từ inventory
systemctl disable --now "$u"
test -f "/etc/systemd/system/$u" && test ! -L "/etc/systemd/system/$u" \
  && mv -n "/etc/systemd/system/$u" "$CUT/units/$u"
test ! -e "/etc/systemd/system/$u" || { echo 'DUNG: file chua duoc backup'; exit 1; }
systemctl daemon-reload
systemctl mask "$u"                      # chỉ sau khi không còn file thật tại /etc
systemctl is-active "$u"; systemctl is-enabled "$u"    # inactive; masked
```

Với vendor unit dưới `/lib/systemd/system`, `disable --now` rồi `mask` tạo symlink `/etc` (không chuyển/xoá vendor file). Với mask symlink đã có, xác nhận runtime inactive. Backup từng file/drop-in đúng vị trí nguồn; không ghi đè file backup đã tồn tại.

Với transient unit dưới `/run/systemd/transient` hoặc `enabled=transient`, chỉ stop từng tên, chờ job kết thúc, xác nhận **không còn trong list-units**; không mv file do systemd quản lý, không giả định mask một file /etc sẽ dừng transient đang chạy:

```sh
u=<TEN_TRANSIENT_TU_INVENTORY>
systemctl stop "$u"
systemctl list-units --all --plain --no-legend "$u"      # phải không còn row
systemctl list-timers --all --plain --no-legend | grep -i nailblog   # phải rỗng
```

Nếu transient vẫn loaded/active, DỪNG cho Owner đối soát; không reset-failed để che active lane.

3c. Sau khi dừng và xử lý mọi staging unit/timer, mới dọn cờ failed:

```sh
systemctl reset-failed 'nailblog-staging-*'
systemctl list-units --failed --no-legend '*nailblog*'     # phải rỗng
```

3d. PID agy lạ (PID đổi theo thời gian: KHÔNG dùng số cứng từ plan hay từ lần đọc cũ). Lấy PID hiện tại từ cutover_check, chỉ dừng khi nó không giữ job nào (không có `busy_job` trùng, không có job `dispatched` thiếu receipt) và không nằm trong `processes.lock_holders`.

```sh
python3 $SRC/deploy/nailblog_cutover_check.py | python3 -c 'import json,sys; d=json.load(sys.stdin)["processes"]; print(json.dumps(d,indent=1))'
PID=<PID_TU_OUTPUT_TREN>
test "$(cat /proc/$PID/comm)" = agy && test "$(ps -o user= -p $PID | tr -d ' ')" = startechai \
  && echo "dung tien trinh" || echo "KHONG khop comm/user: DUNG LAI"
kill -TERM $PID                         # chỉ chạy khi dòng trên in "dung tien trinh"; không dùng kill -9 nếu nó còn giữ lock
sleep 30; ps -p $PID || echo gone
python3 $SRC/deploy/nailblog_cutover_check.py | python3 -c 'import json,sys; print(json.load(sys.stdin)["processes"])'
```

Nếu nó giữ job hoặc lock: DỪNG, báo Owner, chờ job xong (tối đa 10 phút).

## Bước 4-pre. Config trung gian TRƯỚC wheel mới

Config Freeze7 chứa key đã gỡ nên wheel mới không load được. `[DỪNG - OWNER]` duyệt config trung gian rồi mới cài; backup `$CUT/nailblog` ở bước 2 phải là **Freeze7 nguyên gốc**. Dừng cả hai writer trước khi thay config. Migration chỉ chuẩn bị file mới, chưa chạy migration dữ liệu nào:

```sh
systemctl stop tg-nailblog tg-nailblog-subscription
PYTHONPATH="$SRC/nailblog/src" python3 "$SRC/deploy/nailblog_config_migrate.py" \
  --in "$CUT/nailblog/nailblog.toml" --out "$CUT/nailblog-intermediate.toml"
diff -u "$CUT/nailblog/nailblog.toml" "$CUT/nailblog-intermediate.toml"
# Owner duyệt diff trên trước dòng install
install -m 0600 -o tgnailblog -g agentfleet "$CUT/nailblog-intermediate.toml" /etc/nailblog/nailblog.toml
```

Script dùng schema/loader thật từ source đã review: bỏ `REMOVED_TABLES`/`REMOVED_KEYS`, providers `agy`/`subscription`, 4 slot, limit 4, bỏ category placeholder và map hai slot; giữ owner/contract/admin credential/profile/subscription/ledger path. Config trung gian **mode=preview, autoflow.enabled=false**, không tự tạo grant `review_bypass`, không ghi DB. Input và output đang tồn tại đều không bị ghi đè; key lạ/secret hay config sai bị từ chối trước khi xuất file. Không tự đổi contract staging/production: nếu Freeze7 contract không phải staging đúng cặp origin thì DỪNG cho Owner.

**Rollback tự động sẽ khôi phục config trung gian**, vì deploy backup sau bước này. Lệnh deploy bên dưới có `--rollback-no-start` để code cũ không tự chạy với schema mới. Nếu deploy thất bại, giữ bot dừng, tự khôi phục `$CUT/nailblog` (Freeze7) theo phần Rollback **trước khi start code cũ**. Không chạy Freeze7 `status`/`startup-gate` trên TOML trung gian.

## Bước 4. Cài wheel

`[DỪNG - OWNER]` trước khi cài wheel và unit: Owner duyệt commit **hoặc** source snapshot SHA256, hash stage, và kết quả bước 3 (`inventory` sau khi dừng lane cũ). Không cần commit/push để chuẩn bị stage của snapshot; agent không tự commit.

Trên máy build (đã có repo ở commit review):

```sh
python deploy/build_wheel.py --service nailblog --commit <COMMIT_DA_REVIEW> --stage <NAILBLOG_STAGE>
```

Với các fix vẫn chưa commit, lấy digest của **đúng worktree** rồi trình Owner duyệt trước khi build (chạy từ root worktree):

```sh
python -c 'from pathlib import Path; from deploy.build_wheel import snapshot_digest; print(snapshot_digest(Path("."), "nailblog"))'
python deploy/build_wheel.py --service nailblog --repo <REVIEWED_WORKTREE> \
  --reviewed-snapshot-sha256 <SHA256_DA_DUYET> --stage <NAILBLOG_STAGE>
```

`build.json` phải giữ `provenance=reviewed_worktree_snapshot`, `commit=null`, `source_revision=snapshot:<sha256>`. Byte source đổi sau review thì builder từ chối. `--commit HEAD` chỉ lấy Git commit, nên không dùng lệnh đó để đóng gói fix chưa commit. Unit/policy/config script từ `$SRC` cũng phải thuộc đúng source đã review; không lẫn file checkout khác.

Trên host, dùng interpreter của venv nailblog:

```sh
python3 $SRC/deploy/nailblog_cutover_check.py > "$CUT/inventory-pre-wheel.json" || exit 1
python3 - <<'PY' || exit 1
import json,os
s=json.load(open(os.environ['CUT']+'/inventory-pre-wheel.json'))['systemd']
if s['legacy_gate']['pass'] is not True or s['report_bridge_present'] or s['query_errors']:
    raise SystemExit('DUNG: lane cu/query runtime chua an toan')
PY
# Chỉ cài khi kiểm trên thành công: deploy sẽ start code mới trước gate ở bước 6.
/opt/agentfleet/nailblog/.venv/bin/python $SRC/deploy/agentfleet_deploy.py --service nailblog --stage <NAILBLOG_STAGE> --rollback-no-start
```

Script chạy full suite trên host, backup code/config/DB, cài wheel, chờ >=30 giây. Startup-gate đưa bot về paused + disarmed. Thêm `--allow-removed <RECORD_PATH>` chỉ cho file bị xoá đã review (xem `deploy/README.md`, mục "Subsequent deployment and recovery").

Cài unit mới (LF):

```sh
install -m 0644 $SRC/systemd/tg-nailblog.service /etc/systemd/system/tg-nailblog.service
install -m 0644 $SRC/nailblog/deploy/tg-nailblog-subscription.service /etc/systemd/system/tg-nailblog-subscription.service
install -m 0644 $SRC/nailblog/deploy/subscription-bot.conf /etc/systemd/system/tg-nailblog.service.d/subscription.conf
systemctl daemon-reload
```

## Bước 5. Config mới, policy text, worker, key Gemini

5a. **Sau deploy wheel**, trên config trung gian preview + autoflow tắt, lấy `category_ids` THẬT từ staging admin. Dạng UUID seed (`a1b2c3d4-...`) có thể là ID thật; không kết luận placeholder chỉ từ regex. Probe mở editor rồi Cancel, không lưu bài:

```sh
sudo -u tgnailblog env NAILBLOG_CONFIG=/etc/nailblog/nailblog.toml \
  PLAYWRIGHT_BROWSERS_PATH=/var/lib/agentfleet/nailblog/browsers \
  /opt/agentfleet/nailblog/.venv/bin/python -m nailblog probe --out /var/lib/agentfleet/nailblog/probe/cutover.json
```

Chép bốn GUID (`hot-trend`, `nail-job`, `nail-salon`, `did-you-know`) từ `categories` của probe vừa chạy, đối chiếu label bằng mắt với admin. Gate sau đó so đúng ID theo label của report xác thực cùng environment/origins, `writes_performed=false`, `editor_roundtrip=true`, file được tạo trong 24 giờ; thiếu/sai/ambiguous/stale report đều FAIL. Không hardcode allowlist ID từ doc hoặc fixture. Report phải do probe tin cậy vừa chạy tạo ra, không sửa report để khớp config.

5b. Sửa `/etc/nailblog/nailblog.toml` theo `config/nailblog.example.toml`: `service.mode=live`, `contract.environment=staging`, 4 slot trong `service.slots`, `admin.daily_publish_limit=4`, `[admin.category_ids]` đủ 4 GUID thật, `[autoflow] enabled=true`, `runs_dir`, giữ nguyên `budget.ledger_db_path` đã kiểm kê (không chuyển/khởi tạo sổ mới), `image.provider="subscription"`, `text_model.provider="agy"`, feed `https://www.nailsmag.com/rss`, và `[subscription]` giống hệt `/etc/nailblog-subscription/worker.toml` (`text_timeout_seconds=600`). Live cần `image.candidates=1`, `max_rounds=1` và grant Owner `review_bypass=true` đã tồn tại; thiếu grant thì DỪNG, không tự cấp.

Kiểm đường ghi: `runs_dir` và mọi thư mục ghi khác trong config (`db_path`, `browser`/`profile_path`, session, `queue`...) phải nằm dưới `/var/lib/agentfleet/nailblog` hoặc `/run/agentfleet` (đúng `ReadWritePaths` của unit; `ProtectSystem=strict`). Đường nằm ngoài sẽ lỗi `Read-only file system` lúc chạy. Queue của worker (`/var/lib/nailblog-subscription/queue`) được drop-in cấp riêng.

```sh
grep -nE '(_dir|_path|session|db)\s*=' /etc/nailblog/nailblog.toml | grep -vE '"(/var/lib/agentfleet/nailblog(/|")|/run/agentfleet(/|")|/var/lib/nailblog-subscription/queue(/|")|/etc/nailblog(/|"))'
# Mọi row còn lại phải được phân loại: đường chỉ đọc (credential/ledger/private_dir) hoặc DỪNG vì đường ghi ngoài sandbox.
chown tgnailblog:agentfleet /etc/nailblog/nailblog.toml && chmod 0600 /etc/nailblog/nailblog.toml
sudo -u tgnailblog env NAILBLOG_CONFIG=/etc/nailblog/nailblog.toml /opt/agentfleet/nailblog/.venv/bin/python -m nailblog status
```

5c. Policy text. Cài đúng byte đã review, cạnh policy ảnh trong thư mục Snap agents:

```sh
A=/home/startechai/snap/antigravity-cli/common/.gemini/config/agents
install -m 0640 -o startechai -g startechai $SRC/nailblog/deploy/nailblog-text-only.md $A/nailblog-text-only.md
sha256sum $SRC/nailblog/deploy/nailblog-text-only.md $A/nailblog-text-only.md     # hai hash phải bằng nhau
sudo -u startechai /snap/bin/agy agent | grep -E 'nailblog-(image|text)-only'
```

Lưu ý: prompt text truyền qua argv của agy nên mọi user cục bộ thấy được qua `ps` trong lúc job chạy; không đưa secret vào prompt (đã ghi trong `nailblog/deploy/subscription-permissions.md`).

5d. Restart worker:

```sh
systemctl restart tg-nailblog-subscription
journalctl -u tg-nailblog-subscription --since '-2 min' --no-pager | tail -30
cat /var/lib/nailblog-subscription/queue/worker-ready.json
```

5e. Hiện diff cho Owner xem TRƯỚC khi gỡ key. Diff env phải che giá trị:

```sh
diff -ru $CUT/nailblog /etc/nailblog
diff -u $CUT/tg-nailblog.service /etc/systemd/system/tg-nailblog.service
diff -u $CUT/tg-nailblog-subscription.service /etc/systemd/system/tg-nailblog-subscription.service
diff <(sed 's/=.*/=<che>/' $CUT/tg-nailblog.env) <(sed 's/=.*/=<che>/' /etc/agentfleet/tg-nailblog.env)
```

`[DỪNG - OWNER]` Owner xem diff config/unit/env và duyệt bằng tin nhắn. Chỉ sau đó mới gỡ key:

```sh
sed -i '/^[[:space:]]*\(export[[:space:]]\+\)\?NAILBLOG_GEMINI_IMAGE_KEY=/d' /etc/agentfleet/tg-nailblog.env
grep -c NAILBLOG_GEMINI_IMAGE_KEY /etc/agentfleet/tg-nailblog.env      # phải in 0
```

## Bước 6. Enable và kiểm unit

Trước `enable`/`restart`, chạy lại kiểm kê và đọc kết quả:

```sh
python3 $SRC/deploy/nailblog_cutover_check.py \
  --admin-probe-report /var/lib/agentfleet/nailblog/probe/cutover.json \
  --admin-listing-sample "$CUT/admin-listing.json" > $CUT/inventory-pre-enable.json || exit 1
python3 - <<'PY' || exit 1
import json, os
d = json.load(open(os.environ['CUT'] + '/inventory-pre-enable.json'))
s = d['systemd']
bad = []
if s['report_bridge_present']: bad.append('report_bridge_present')
if s.get('legacy_gate', {}).get('pass') is not True:
    bad.extend(s.get('legacy_gate', {}).get('reasons', ['runtime_gate_missing']))
if s['failed']: bad.append('failed_units')
if d['database'].get('error'): bad.append('database_inventory_error')
if d['database'].get('legacy_topic_claim_conflict'): bad.append('legacy_topic_claim_conflict')
if d['config']['category_ids'] is None or d['config']['category_ids']['slugs_missing']:
    bad.append('category_ids thieu')
if d['config'].get('category_verification', {}).get('pass') is not True:
    bad.append('category_ids chua khop probe xac thuc moi')
if d.get('admin_listing_sample') is None or d['admin_listing_sample'].get('error'):
    bad.append('listing_sample thieu/khong doc duoc')
if d['env']['gemini_image_key_present'] is not False: bad.append('env con key Gemini')
print('DUNG: ' + '; '.join(bad) if bad else 'OK, duoc phep enable')
raise SystemExit(1 if bad else 0)
PY
```

`[DỪNG - OWNER]` nếu dòng cuối in `DUNG: ...`: không `enable`, không `restart`, báo Owner. Chỉ khi in `OK, duoc phep enable` mới chạy tiếp:

```sh
systemctl enable tg-nailblog tg-nailblog-subscription
systemd-analyze verify /etc/systemd/system/tg-nailblog.service /etc/systemd/system/tg-nailblog-subscription.service
systemctl restart tg-nailblog
systemctl is-enabled tg-nailblog tg-nailblog-subscription     # enabled, enabled
systemctl show tg-nailblog -p TimeoutStopUSec -p ExecStartPre
```

Kỳ vọng: `TimeoutStopUSec=11min` cho bot, `verify` không in cảnh báo. Bot lên ở trạng thái paused + disarmed.

Gate dựa `is-active`/`is-enabled` thật: mọi legacy lane còn file hoặc còn loaded phải **inactive + masked bền** (`is-enabled=masked`, `persistent_masked=true`). `disabled`, `static` và `masked-runtime` đều FAIL vì vẫn có thể được kích hoạt hoặc mất mask sau reboot. Removal chỉ được chấp nhận khi không còn file, không loaded và `is-enabled=not-found`; transient phải biến mất cả file lẫn runtime. Mọi timer NailBlog phải vắng trong `list-timers --all`. File mask riêng lẻ không chứng minh runtime đã dừng. Query active/enabled của **mọi unit**, kể cả bot/worker mới, thiếu/sai/timeout đều FAIL và được giữ trong `systemd.query_errors`; không dùng `--no-systemctl` ở host.

## Bước 7. Owner arm

`[DỪNG - OWNER]` **Hold mọi bài cũ trước resume**, kể cả bài đã được duyệt/admin approval, và bài có autoflow membership cũ. Dùng id từ `inventory-before.json` + `unfinished_posts` mới nhất. Owner gửi từng lệnh có lý do thật: `/nailblog hold <id> cutover-agy-only`, xác nhận bot trả “đã giữ”, rồi kiểm kê lại thấy state `held`. Đây là lớp phòng thủ thứ hai ngoài kiểm membership của `publish_pass`.

`before_publish` chỉ được readback/reconcile, không có transition trực tiếp sang held; `uncertain` phải đối soát admin trước khi hold. Nếu hold bị từ chối, có orphan admin operation, hoặc bài vẫn có thể đăng: **DỪNG**, không SQL UPDATE, không tự POST Publish/Create lại. Owner đối soát theo luồng hợp lệ cho tới terminal `published` có evidence hoặc `held`. Với `approved_unpublished_admin_operations`, xác nhận từng post đã held; approval còn trong audit không được xóa. Không resume khi còn bài cũ pending chưa xử lý.

So `database.admin_binding[].sha256` trước/sau và hash binding config mới (không tạo AdminStore/State, không in giá trị):

```sh
sudo -u tgnailblog /opt/agentfleet/nailblog/.venv/bin/python - <<'PY'
import hashlib,json
from nailblog.config import load_config
from nailblog.publication import binding
value=json.dumps(binding(load_config('/etc/nailblog/nailblog.toml')),sort_keys=True)
print(hashlib.sha256(value.encode()).hexdigest())
PY
```

Hash khác DB ⇒ DỪNG để Owner đối soát môi trường; không sửa `admin_binding` bằng SQL. Hash không thay thế kiểm category/probe đầy đủ.

Sau khi inventory/gate, hold, binding và listing sample đều đạt, **Owner tự gõ** `/nailblog resume` trước 10:00 VN ngày D-1 (preflight, rồi arm). Không ai khác gõ thay. `/nailblog approve` vẫn bị chặn theo plan. Nếu muộn, nghiệm thu dịch D+1.

## Bước 8. Nghiệm thu ngày D

```sh
python3 $SRC/deploy/nailblog_cutover_check.py > $CUT/inventory-after.json
```

Đạt khi đủ tất cả:

1. DB có 4 dòng `autoflow_runs` và 4 `posts` trạng thái `published` của ngày D (`sqlite3 -readonly <db> "select ..."`).
2. Readback có `anonymous_homepage_click` và 4 SHA ảnh.
3. `curl -s -o /dev/null -w '%{http_code}' <URL>` ra 200 cho cả 4 URL.
4. `journalctl -u tg-nailblog -u tg-nailblog-subscription --since today | grep -E 'not_ready|cli_timeout_unknown|Traceback'` không ra dòng nào.
5. `ledger.paid_intents_sha256` trong `inventory-after.json` bằng đúng giá trị ở `inventory-before.json` (`jq -r .ledger.paid_intents_sha256` hai file, `diff`).
6. `systemd.report_bridge_present=false`, `systemd.legacy_gate.pass=true`, `systemd.failed` rỗng, `env.gemini_image_key_present=false`, category IDs khớp probe xác thực ở bước 6 (UUID pattern không quyết định PASS), `systemctl list-timers --all | grep -i nailblog` rỗng.
7. Tuỳ chọn, `[DỪNG - OWNER]` cho quyền reboot: một lần `reboot`, sau đó cả hai service tự lên (`systemctl is-active`), bot ở trạng thái paused cho tới khi Owner `resume`.

## Rollback

Chỉ làm khi Owner duyệt. Không xoá intent hay journal ở bất kỳ nhánh nào.

Nếu bước 4 đã tự rollback code, giữ service stopped và đi thẳng phần khôi phục config/unit/env bên dưới; không chạy `pause-install` bằng Freeze7 trên TOML trung gian. Lệnh pause-install dưới đây dùng khi code Agy-only vẫn đang cài.

```sh
# 1. Dừng bot, không để arm
sudo -u tgnailblog env NAILBLOG_CONFIG=/etc/nailblog/nailblog.toml /opt/agentfleet/nailblog/.venv/bin/python -m nailblog pause-install
systemctl stop tg-nailblog tg-nailblog-subscription
# 2. Cài lại wheel Freeze7 từ backup của agentfleet_deploy.py (giữ nguyên DB hiện tại)
/opt/agentfleet/nailblog/.venv/bin/python $SRC/deploy/agentfleet_deploy.py --service nailblog --stage <NAILBLOG_STAGE> --rollback <NAILBLOG_BACKUP> --no-start
# 3. Khôi phục config, unit, env từ $CUT
diff -r $CUT/nailblog /etc/nailblog || true      # Owner xem diff trước khi ghi đè
diff -r $CUT/nailblog-subscription /etc/nailblog-subscription || true
```

`[DỪNG - OWNER]` xem diff, rồi khôi phục:

```sh
cp -a $CUT/nailblog/. /etc/nailblog/
cp -a $CUT/nailblog-subscription/. /etc/nailblog-subscription/
cp -a $CUT/tg-nailblog.service /etc/systemd/system/
cp -a $CUT/tg-nailblog-subscription.service /etc/systemd/system/
cp -a $CUT/tg-nailblog.service.d/. /etc/systemd/system/tg-nailblog.service.d/
# Khôi phục env KHÔNG mang key API trả phí trở lại; không cat/diff giá trị secret.
grep -vE '^[[:space:]]*(export[[:space:]]+)?NAILBLOG_GEMINI_IMAGE_KEY[[:space:]]*=' "$CUT/tg-nailblog.env" > "$CUT/tg-nailblog-without-paid.env"
install -m 0600 -o tgnailblog -g agentfleet "$CUT/tg-nailblog-without-paid.env" /etc/agentfleet/tg-nailblog.env
# 4. Gỡ policy text đã cài ở bước 5c (policy ảnh giữ nguyên)
rm -f /home/startechai/snap/antigravity-cli/common/.gemini/config/agents/nailblog-text-only.md
# 5. Backup chứa drop-in daily4 (report_bridge): KHÔNG để nó quay lại nếu Owner chưa duyệt
rm -f /etc/systemd/system/tg-nailblog.service.d/zz-daily4-reporting.conf
systemctl daemon-reload
```

Bot/worker vẫn dừng suốt phần restore: `--no-start` chỉ đổi rollback thủ công; mặc định rollback của deploy tool vẫn khởi động lại như cũ. Cutover phải có `--rollback-no-start` ở bước 4 để failure rollback cũng dừng. Config deploy backup là **trung gian**; restore `$CUT/nailblog/.` (Freeze7 ở bước 2) là bắt buộc **trước khi start Freeze7**. Nếu đã tự rollback ở bước 4 thì không cần chạy rollback lần hai, chỉ xác nhận restore code thành công rồi khôi phục config/unit/env như trên. Owner xem diff config/unit + env đã che giá trị sau restore và duyệt trước start. Xác nhận owner/mode vẫn đúng; bỏ key có thể làm tính năng paid của Freeze7 fail closed, không giải quyết bằng tự đưa key lại.

`[DỪNG - OWNER — API trả phí]` Có muốn đưa `NAILBLOG_GEMINI_IMAGE_KEY` trở lại không? Mặc định **KHÔNG**. Tái đưa key trái mặc định quyết định D-033 Agy-only; cần quyết định Owner riêng ngoài rollback, ghi lại lý do trước khi sửa env. Không copy nguyên env Freeze7 chứa key.

`<NAILBLOG_BACKUP>` ở lệnh `--rollback` là thư mục `agentfleet_deploy.py` tạo ở bước 4, dạng `/var/backups/agentfleet/nailblog-<stamp>` (dòng `Backup:`/`deployment.json` trong output bước 4; `ls -dt /var/backups/agentfleet/nailblog-* | head -1` là bản mới nhất). Ghi đường này vào báo cáo cutover ngay sau bước 4. `<NAILBLOG_STAGE>` là stage Freeze7 cũ hoặc stage đã dùng ở bước 4. Chỉ thêm `--restore-db` nếu Owner chọn dữ liệu cũ; mặc định giữ DB đang sống.

Chỉ khi Owner duyệt riêng việc quay lại lane cũ, unmask từng unit (CẢNH BÁO: daily4/makeup cùng chạy với autoflow có thể đăng trùng bài, và Create UNKNOWN 26/10 không được POST lại; mặc định KHÔNG làm):

```sh
for u in nailblog-daily4.timer nailblog-daily4.service $(systemctl list-unit-files --no-legend 'nailblog-makeup-20261026-*' | awk '{print $1}'); do
  systemctl unmask "$u"; done
```

Cuối cùng restart và kiểm lại:

```sh
systemctl restart tg-nailblog-subscription tg-nailblog
systemctl cat tg-nailblog | grep -c report_bridge        # 0 nếu không quay lại lane cũ
python3 $SRC/deploy/nailblog_cutover_check.py > $CUT/inventory-after-rollback.json
```

Đọc `inventory-after-rollback.json`: `systemd.report_bridge_present`, `database.control` (bot phải paused, `armed=false`), `systemd.failed`, `ledger.paid_intents_sha256` bằng giá trị bước 1.

## Các điểm DỪNG (tổng hợp)

| # | Trước bước | Điều kiện |
|---|---|---|
| 1 | 1 | DB/bảng lỗi, claim/conflict chưa đối soát, job dispatched thiếu receipt, quyền/sudo NOPASSWD, hoặc còn phiên Codex ghi |
| 2 | 3 | Owner duyệt dừng lane cũ |
| 3 | 3d | PID agy giữ job hoặc lock |
| 4 | 4-pre / 4 | Owner duyệt config trung gian rồi commit hoặc snapshot SHA256/stage trước khi cài wheel và unit |
| 5 | 5e | Owner xem diff config/unit/env rồi mới gỡ key Gemini |
| 6 | 6 | cutover_check in `DUNG: ...` (runtime lane/timer, category chưa khớp probe, listing, key Gemini) |
| 7 | 7 | Hold/đối soát toàn bộ bài cũ + binding trước Owner resume ngày D-1 trước 10:00 VN |
| 8 | 8.7 | Owner duyệt reboot nghiệm thu |
| 9 | Rollback | Owner duyệt từng lần, khôi phục Freeze7 config trước start; unmask và tái đưa paid key đều là quyết định riêng, mặc định KHÔNG |

## Chưa kiểm (ngoài phạm vi máy build)

Các lệnh trên chưa chạy trên THINKPAD. Tên DB (`/var/lib/agentfleet/nailblog/nailblog.sqlite3`), và đường `/srv/agentfleet-split` lấy từ plan/đọc 27/10, cần xác nhận lại bằng `nailblog_cutover_check.py` trước khi chạy.

## Related Documents

- [Master](../00-master/nailhub-blog-noissue_master_261014_v1.0.0.md)
- [Engineering](nailhub-blog-noissue_engineering_261027_v1.0.0.md)
- [Tasks](../04-tasks/nailhub-blog-noissue_tasks_261027_v2.0.0.md)

## Changelog

| Phiên bản | Ngày / giờ VN | Thay đổi |
|---|---|---|
| v1.0.1 | 27/10/2026 16:11 | G2: config trung gian trước wheel, rollback giữ dừng, inventory runtime/legacy post/binding, quyền và listing sample, lịch D-1 |
| v1.0.0 | 27/10/2026 | Runbook ban đầu |
