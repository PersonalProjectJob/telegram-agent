---
type: doc
title: "NailBlog Agy-only cutover runbook"
owner: AlexChu
created: 2026-10-27
status: draft
---

# Runbook cutover NailBlog Agy-only (THINKPAD)

Phiên bản v1.0.0, 27/10/2026. Nguồn: plan `Docs/04-tasks/nailhub-blog-noissue_tasks_261027_v2.0.0.md`, mục "Deploy + nghiệm thu", "Rủi ro GATE 0b", "Rollback".

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

- Repo ở commit đã review; `python -m unittest deploy.test_nailblog_deploy deploy.test_deploy` và `cd nailblog; PYTHONPATH=src python -m unittest discover -s tests -t .` đều xanh trên máy build.
- `.gitattributes` đã có trong commit (nailblog/** và `systemd/*.service` là LF), wheel build từ byte LF.
- Owner đã trả lời bốn câu hỏi trong plan (mô hình duyệt, bài trượt gate, nhóm chủ đề, quyền cutover và reboot).

## Bước 1. Kiểm kê (chỉ đọc)

```sh
python3 $SRC/deploy/nailblog_cutover_check.py > $CUT/inventory-before.json
python3 -m json.tool $CUT/inventory-before.json | less
```

Đọc và ghi lại ở báo cáo cutover:

- `systemd.report_bridge_present` (phải là true trước khi gỡ, false sau khi gỡ), `systemd.failed`;
- `database.control` (kỳ vọng `paused=1`), `database.onepost_missing_claims` (phải rỗng, nếu không rỗng thì DỪNG);
- `queue.jobs`: mỗi job có `marker` và `receipt` hay chưa; `queue.worker_ready.busy_job`;
- `ledger.paid_intents_sha256` và `paid_intents_rows`: chép lại, đây là số đối chiếu sau cutover;
- `processes.agy` và `processes.lock_holders`;
- `config.category_ids` (có phải GUID placeholder), `env.gemini_image_key_present`.

Script chỉ mở DB bằng `mode=ro` và đọc sổ qua bản copy; không ghi file nào. Nếu cần chứng minh, chạy `find /etc/nailblog* /var/lib/agentfleet/nailblog /var/lib/nailblog-subscription -type f -exec sha256sum {} + | sort > $CUT/sha-before.txt` trước và sau, rồi `diff`.

`[DỪNG - OWNER]` nếu `onepost_missing_claims` không rỗng, hoặc có job `dispatched` không có `receipt`, hoặc thấy phiên Codex còn ghi vào host.

## Bước 2. Backup

```sh
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

3b. Disable và mask daily4/makeup (giữ nguyên state root của chúng, plan: Create UNKNOWN 26/10):

```sh
systemctl disable --now nailblog-daily4.timer nailblog-daily4.service
systemctl mask nailblog-daily4.timer nailblog-daily4.service
for u in $(systemctl list-unit-files --no-legend 'nailblog-makeup-20261026-*' | awk '{print $1}'); do
  systemctl disable --now "$u"; systemctl mask "$u"; done
systemctl list-timers --all | grep -i nailblog            # không còn timer chạy
```

3c. Dọn trạng thái failed của staging (chỉ xoá cờ failed, không đụng file):

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

## Bước 4. Cài wheel

`[DỪNG - OWNER]` trước khi cài wheel và unit: Owner duyệt commit đã review, hash stage, và kết quả bước 3 (`inventory` sau khi dừng lane cũ).

Trên máy build (đã có repo ở commit review):

```sh
python deploy/build_wheel.py --service nailblog --commit <COMMIT_DA_REVIEW> --stage <NAILBLOG_STAGE>
```

Trên host, dùng interpreter của venv nailblog:

```sh
/opt/agentfleet/nailblog/.venv/bin/python $SRC/deploy/agentfleet_deploy.py --service nailblog --stage <NAILBLOG_STAGE>
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

5a. Lấy `category_ids` THẬT từ staging admin (không dùng GUID placeholder dạng `a1b2c3d4-e5f6-7890-abcd-ef12345678xx`):

```sh
sudo -u tgnailblog env NAILBLOG_CONFIG=/etc/nailblog/nailblog.toml \
  /opt/agentfleet/nailblog/.venv/bin/python -m nailblog probe --out /var/lib/agentfleet/nailblog/probe/cutover.json
```

Chép bốn GUID (`hot-trend`, `nail-job`, `nail-salon`, `did-you-know`) từ kết quả probe, đối chiếu bằng mắt với admin.

5b. Sửa `/etc/nailblog/nailblog.toml` theo `config/nailblog.example.toml`: `service.mode=live`, `contract.environment=staging`, 4 slot trong `service.slots`, `admin.daily_publish_limit=4`, `[admin.category_ids]` đủ 4 GUID thật, `[autoflow] enabled=true`, `runs_dir`, `budget.ledger_db_path=/var/lib/agentfleet/nailblog/ledger/nailblog-ledger.sqlite3` (code chỉ đọc; đường này nằm dưới `/var/lib/agentfleet/nailblog` nên vốn đã có quyền ghi), `image.provider="subscription"`, `text_model.provider="agy"`, feed `https://www.nailsmag.com/rss`, và `[subscription]` giống hệt `/etc/nailblog-subscription/worker.toml` (`text_timeout_seconds=600`).

Kiểm đường ghi: `runs_dir` và mọi thư mục ghi khác trong config (`db_path`, `browser`/`profile_path`, session, `queue`...) phải nằm dưới `/var/lib/agentfleet/nailblog` hoặc `/run/agentfleet` (đúng `ReadWritePaths` của unit; `ProtectSystem=strict`). Đường nằm ngoài sẽ lỗi `Read-only file system` lúc chạy. Queue của worker (`/var/lib/nailblog-subscription/queue`) được drop-in cấp riêng.

```sh
grep -nE '(_dir|_path|session|db)\s*=' /etc/nailblog/nailblog.toml | grep -vE '"(/var/lib/agentfleet/nailblog|/run/agentfleet|/var/lib/nailblog-subscription/queue|/etc/nailblog)' || echo "moi duong ghi hop le"
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
python3 $SRC/deploy/nailblog_cutover_check.py > $CUT/inventory-pre-enable.json
python3 - <<'PY'
import json, os
d = json.load(open(os.environ['CUT'] + '/inventory-pre-enable.json'))
s = d['systemd']
bad = []
if s['report_bridge_present']: bad.append('report_bridge_present')
live = [t['name'] for t in s['timers'] + s['units'] if ('daily4' in t['name'] or 'makeup' in t['name']) and not t['masked']]
if live: bad.append('chua mask: ' + ', '.join(live))
if d['config']['category_ids'] is None or d['config']['category_ids']['placeholder'] or d['config']['category_ids']['slugs_missing']:
    bad.append('category_ids placeholder/thieu')
if d['env']['gemini_image_key_present'] is not False: bad.append('env con key Gemini')
print('DUNG: ' + '; '.join(bad) if bad else 'OK, duoc phep enable')
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

## Bước 7. Owner arm

`[DỪNG - OWNER]` Owner tự gõ trong Telegram: `/nailblog resume` (preflight, rồi arm). Không ai khác gõ thay. `/nailblog approve` vẫn bị chặn theo plan.

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
6. `systemd.report_bridge_present=false`, `systemd.failed` rỗng, `env.gemini_image_key_present=false`, `config.category_ids.placeholder` rỗng, `systemctl list-timers | grep -i nailblog` rỗng.
7. Tuỳ chọn, `[DỪNG - OWNER]` cho quyền reboot: một lần `reboot`, sau đó cả hai service tự lên (`systemctl is-active`), bot ở trạng thái paused cho tới khi Owner `resume`.

## Rollback

Chỉ làm khi Owner duyệt. Không xoá intent hay journal ở bất kỳ nhánh nào.

```sh
# 1. Dừng bot, không để arm
/opt/agentfleet/nailblog/.venv/bin/python -m nailblog pause-install
systemctl stop tg-nailblog tg-nailblog-subscription
# 2. Cài lại wheel Freeze7 từ backup của agentfleet_deploy.py (giữ nguyên DB hiện tại)
/opt/agentfleet/nailblog/.venv/bin/python $SRC/deploy/agentfleet_deploy.py --service nailblog --stage <NAILBLOG_STAGE> --rollback <NAILBLOG_BACKUP>
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
cp -a $CUT/tg-nailblog.env /etc/agentfleet/
# 4. Gỡ policy text đã cài ở bước 5c (policy ảnh giữ nguyên)
rm -f /home/startechai/snap/antigravity-cli/common/.gemini/config/agents/nailblog-text-only.md
# 5. Backup chứa drop-in daily4 (report_bridge): KHÔNG để nó quay lại nếu Owner chưa duyệt
rm -f /etc/systemd/system/tg-nailblog.service.d/zz-daily4-reporting.conf
systemctl daemon-reload
```

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
| 1 | 1 | `onepost_missing_claims` khác rỗng, job `dispatched` thiếu receipt, hoặc còn phiên Codex ghi |
| 2 | 3 | Owner duyệt dừng lane cũ |
| 3 | 3d | PID agy giữ job hoặc lock |
| 4 | 4 | Owner duyệt commit/stage trước khi cài wheel và unit |
| 5 | 5e | Owner xem diff config/unit/env rồi mới gỡ key Gemini |
| 6 | 6 | cutover_check in `DUNG: ...` (report_bridge, chưa mask, placeholder, key Gemini) |
| 7 | 7 | Owner tự gõ `/nailblog resume` |
| 8 | 8.7 | Owner duyệt reboot nghiệm thu |
| 9 | Rollback | Owner duyệt từng lần, và duyệt riêng việc unmask lane cũ |

## Chưa kiểm (ngoài phạm vi máy build)

Các lệnh trên chưa chạy trên THINKPAD. Tên DB (`/var/lib/agentfleet/nailblog/nailblog.sqlite3`), và đường `/srv/agentfleet-split` lấy từ plan/đọc 27/10, cần xác nhận lại bằng `nailblog_cutover_check.py` trước khi chạy.
