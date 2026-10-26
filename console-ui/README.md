# AgentFleet Settings Console

UI/API đi kèm source, quản lý Telegram Triage và NailHub Worker riêng biệt. Python 3.11+, không cần npm build hoặc website riêng.

```sh
python console-ui/run.py
```

Mở browser tại `http://127.0.0.1:8765`. `Ctrl+C` dừng console. `--no-open` bỏ tự mở browser; `--port 0` chọn cổng trống. Dùng đúng URL terminal in ra (Host/Origin được kiểm tra nghiêm ngặt).

## Source layout — phase 2

```text
console-ui/
  frontend/
    index.html
    src/{app,api,state,schema-form}.js
    assets/{styles,tokens}.css
  backend/agentfleet_console/
    paths.py, cli.py, server.py, store.py, errors.py
    registry.py, schema.py, worker_sources.py
    compatibility.py, policy.py
    workers/
      adapters.py
      descriptors/{triage,nailhub}.json
      compatibility.json
  tests/
    test_backend.py, test_registry.py, test_schema.py
    test_compatibility.py, test_integration.py, test_package.py
  run.py, package.py
```

Lệnh chạy giữ nguyên. Server phục vụ `/`, `/index.html`, `/app.js`, `/api.js`, `/state.js`, `/schema-form.js`, `/styles.css`, `/tokens.css`; đường dẫn source nội bộ không được public. `.agentfleet/` mặc định nằm ở root source hoặc root ZIP đã giải nén, kể cả khi chạy từ thư mục khác. `run.py` và `package.py` giữ vai trò entry point; facade import `backend.py` đã bỏ. Imports Python nội bộ dùng package `agentfleet_console`.

Registry trong source đăng ký rõ từng descriptor và adapter tin cậy. Descriptor sở hữu tên, nhóm, trường, defaults, giới hạn và điều kiện hiển thị; FE tạo navigation/form từ public contract, không nhận mapping TOML hay đường dẫn host. Khi thêm worker, bổ sung descriptor/adapter/registration và fixtures kiểm chứng; không tải code từ JSON hoặc từ HTTP. `tests/test_integration.py` có worker fixture thứ ba với name/enabled/list và conditional visibility, chỉ dùng local TEMP.

`GET /api/bootstrap` bổ sung `contractVersion: 1`, `descriptors`, trạng thái available/canSave và compatibility cho từng service. Descriptor lỗi được cô lập. FE gửi `contractRevision` khi test/save/apply; hợp đồng cũ trả 409 trước tác động. Client cũ có thể bỏ trường này để giữ API tương thích, nhưng nên reload và gửi revision hiện tại. Các guard CSRF, Host/Origin, revision cấu hình và giới hạn request vẫn áp dụng.

## Local

Mặc định `--host-profile local` tạo hai TOML và metadata dưới `.agentfleet/` ở repo, đã được Git ignore. `--state-dir PATH` chọn thư mục riêng khác bằng CLI. Unix yêu cầu console sở hữu thư mục và đặt mode `0700`; Windows áp ACL chỉ tài khoản hiện tại, SYSTEM và Administrators. Không chọn thư mục dùng chung cho dữ liệu khác: console bảo vệ ACL của cả thư mục state. Lưu cấu hình và API key hoạt động thật, giữ sau khi đóng/mở console. Profile local không restart các service cài trên máy khác.

- Mỗi service chọn riêng Ollama, Claude, OpenAI, DeepSeek hoặc HTTPS API tương thích OpenAI; nhập model ID thủ công theo quyền tài khoản.
- Test gọi endpoint thật bằng mẫu JSON tổng hợp, không gửi tin Telegram/bài NailHub và không tự lưu. Cloud provider có thể tính phí. Các bài test tự động dùng upstream giả, không dùng key thật.
- Lưu tạo revision mới. Áp dụng là thao tác riêng. Hai tab/cửa sổ cùng sửa nhận lỗi conflict thay vì ghi đè âm thầm.
- Key không được trả về browser, localStorage, diff hoặc lịch sử. Bỏ trống giữ key của đúng provider + endpoint; đổi endpoint cần key mới. Key được lưu trong file riêng, **không phải mã hoá tại rest**; quyền OS bảo vệ file.
- Quyền gửi nội dung tác vụ đến cloud mặc định tắt. Đổi provider/endpoint trên UI tắt lại quyền này. Triage che e-mail/điện thoại và giữ lại tác vụ có ảnh khi dùng cloud. NailHub cần nội dung gốc để đối chiếu bằng code; các gate Owner/VERIFIED/evidence vẫn giữ nguyên.
- Thinking của model chính chỉ điều chỉnh với Ollama của Triage. NailHub giữ thinking tắt theo policy hiện hành. Model không hỗ trợ JSON cần chọn lại sau khi test báo lỗi.
- Triage có fallback Ollama hoặc tắt fallback, model fallback (mặc định `qwen3.5:9b`) và fallback thinking. Giá trị được giữ khi chuyển provider hoặc khi điều khiển tạm bị disable; BE vẫn kiểm tra toàn bộ setting. NailHub không có và không chấp nhận các trường fallback này. Console chỉ cấu hình khả năng worker đã có.

## Installed Linux host

Chạy source console bằng tài khoản chuyên dụng **không phải root**, trên host đã cài runtime được nâng cấp từ cùng source:

```sh
python console-ui/run.py --host-profile installed --state-dir /var/lib/agentfleet-console
```

Binding cố định: `/etc/tg-triage/listener.toml`, `/etc/nailhub/nailhub.toml`; chỉ restart `tg-triage.service` hoặc `tg-nailhub.service`. Request HTTP không nhận đường dẫn file, unit hoặc shell command.

Quản trị viên cần cấp quyền một lần trước khi dùng profile này. Console không tự cấp quyền, cài wheel hay sửa policy OS:

1. Tạo group riêng nếu chưa có: `getent group tgnailhub || groupadd --system tgnailhub` (quản trị viên chạy một lần). Baseline tạo user `tgnailhub` với primary group `agentfleet`, chưa tạo group riêng này. Cài hai wheel riêng vào đúng venv `/opt/agentfleet/listener/.venv` và `/opt/agentfleet/nailhub/.venv` theo [deploy/README.md](https://github.com/PersonalProjectJob/telegram-agent/blob/main/deploy/README.md). Runtime mới có `receipt.py`; dùng unit NailHub mới với `SupplementaryGroups=tgnailhub` sau khi group tồn tại. Giữ EnvironmentFile/session/contract đã xác minh. Không gộp hai venv.
2. Tài khoản console sở hữu `/var/lib/agentfleet-console` mode `0700`; cấu hình và khoá thao tác nằm ngoài thư mục source để không phụ thuộc checkout. Chỉ tài khoản này ghi metadata.
3. Cho tài khoản console thay file trong **hai thư mục cấu hình chuyên dụng**. File TOML được console ghi mode `0640`, group `tglistener` hoặc `tgnailhub` tương ứng. Runtime cần đọc được TOML; console phải thuộc hai group chuyên dụng để đặt group file (ví dụ `usermod -aG tglistener,tgnailhub agentfleet-console`, sau đó mở phiên mới; thay đúng tên tài khoản đã chọn). Không dùng group chung `agentfleet` để lưu key.
4. Tạo `/etc/tg-triage/private` và `/etc/nailhub/private`, owner tài khoản console, group tương ứng `tglistener`/`tgnailhub`, mode `0750`. Key generations được ghi `0640`; chỉ console và runtime tương ứng đọc được. Không đặt dưới home: unit dùng `ProtectHome=yes`.
5. Cấp console ACL **search-only** trên các DB parent thực tế để đọc riêng `agentfleet-ready.json` (file receipt mode `0640`, service group). Không cấp quyền đọc/ghi DB, Telegram session hoặc browser profile. Ví dụ, với layout mặc định: `setfacl -m u:agentfleet-console:--x /var/lib/agentfleet/tg /var/lib/agentfleet/nailhub`; thay tên tài khoản đã chọn khi provisioning.
6. Cấp ACL có phạm vi cho giao thức model lock tại `/run/agentfleet`: console cần tìm/đọc marker, tạo/xoá marker của mình và đọc/ghi `ollama.lock`. Đảm bảo ACL tồn tại lại sau reboot bằng provisioning/tmpfiles thích hợp. Không thay owner/quyền DB hoặc thêm console vào group rộng chỉ để đọc lock. Probe NailHub chờ background, Triage có ưu tiên foreground.
7. Cấp quyền Polkit chỉ `org.freedesktop.systemd1.manage-units`, subject đúng tài khoản console, `unit` nằm trong hai unit nêu trên và `verb == restart`. Không cấp sudo/systemctl tuỳ ý. `systemctl --no-ask-password restart ...` phải chạy được với tài khoản này. Console giữ nguyên quyền `TimeoutStopSec=150` của các unit.

Áp dụng chỉ ghi nhận revision khi systemd active, MainPID khớp và runtime phát receipt mới chứa revision/mode đã nạp sau startup có xác thực. Mode bị EnvironmentFile ghi đè sẽ không được báo áp dụng thành công. Khi restart/readiness lỗi, bản đã lưu vẫn còn; UI không restore DB hoặc đảo tác động bên ngoài. PID/revision trên UI là lần xác nhận cuối, không phải health monitoring liên tục.

Trước Save/Apply ở installed profile, Console kiểm tra package/data worker bằng SHA256 manifest canonical từ Git objects của baseline `6fdb7db7da22247052782e2450f861375e79668f`; không dùng hash checkout CRLF hoặc hash tự khai của runtime. Thiếu, thừa, thay bytes, không đọc được hoặc đường dẫn bị redirect sẽ chặn action với trạng thái mismatch/unverified. Manifest thuộc artifact Console, không sửa worker. Bootstrap/status không tự replay pending transaction hoặc ghi cấu hình installed.

Hash khớp chỉ là điều kiện cần. Mỗi action kiểm tra lại trong lock và đọc effective systemd binding: unit/user/group, executable/module/config, working directory và environment phải khớp binding trong source. Drop-in hoặc EnvironmentFile chưa xác minh khiến action bị chặn; Console không đọc file environment có thể chứa credential để cố mở quyền. Vì vậy hai unit baseline có EnvironmentFile có thể vẫn bị chặn dù package hash khớp. `canSave`/`canApply` và compatibility trên UI phản ánh binding chưa xác minh: Save/Apply bị disable và hiển thị lý do. Quyền OS vẫn được kiểm tra khi thao tác; trạng thái cho phép không hứa mọi lần ghi/restart sẽ thành công. Không thao tác Console trong lúc deploy worker bằng công cụ khác.

Console vẫn bind loopback. Browser phải truy cập cùng host/URL; bản này chưa có connector điều khiển Linux từ Windows, LAN/public listener hay luồng đăng nhập remote. Cài đặt host và kiểm với API key thật chưa được thực hiện trong lượt phát triển local.

`deploy/build_wheel.py` dùng source đã commit. Người dùng cần review/commit thay đổi runtime trước khi tạo wheel triển khai; kiểm wheel có `ai_transport.py` và `receipt.py`. Lệnh console local chạy trực tiếp source nên không cần commit để dùng thử. Không có commit/push được thực hiện tự động.

## Chi phí API

Mục **Chi phí API** (`#usage`, `GET /api/usage`) hiển thị số lần gọi, token, chi phí, lỗi và số lần fallback sang local của Telegram Triage theo Hôm nay / 7 ngày / 30 ngày, kèm bảng theo provider/model và biểu đồ cột 30 ngày. Console **không** mở database của runtime: nó chỉ đọc một tệp tổng hợp JSON `agentfleet-usage.json` mà runtime Triage ghi cạnh `storage.db_path` (cùng thư mục với file receipt `agentfleet-ready.json`).

- Tệp tối đa 512 KiB, được kiểm schema chặt (số không âm, chuỗi/mảng có giới hạn, `schema` phải là 1). Trả về browser chỉ gồm các trường đã kiểm; không bao giờ có đường dẫn tệp hay lỗi hệ điều hành.
- Trạng thái: `ok`; `missing` (runtime chưa ghi, hoặc không đọc được cấu hình Triage); `stale` (`generatedAt` cũ hơn 24 giờ, vẫn hiển thị số liệu cuối); `invalid` (sai schema, quá lớn, không phải JSON hoặc không đọc được).
- Chi phí lấy nguyên từ tệp runtime ghi; console không tự tính hay đối chiếu với hoá đơn provider. Trình duyệt chỉ lưu lựa chọn khoảng thời gian trong `localStorage`.
- Route `/api/services/{id}/usage` kiểm tra capability của worker; `/api/usage` giữ làm alias cho worker usage mặc định trong registry.

## Verification

Các lệnh kiểm thử bên dưới dành cho checkout source đầy đủ. ZIP portable không chứa tests hoặc báo cáo evidence.

```sh
python -m unittest discover -s console-ui/tests -p "test_*.py" -v
python -m unittest discover -s listener/tests -t listener
python -m unittest discover -s nailhub/tests -t nailhub
node --check console-ui/frontend/src/app.js
node --check console-ui/frontend/src/api.js
node --check console-ui/frontend/src/state.js
node --check console-ui/frontend/src/schema-form.js
node console-ui/tests/test_frontend.cjs
```

Node chỉ cần cho contributor chạy syntax/regression FE; Console và ZIP portable vẫn chỉ cần Python, không yêu cầu Node khi vận hành.

Hai suite runtime cần source package trên `PYTHONPATH`: `listener/src` và `nailhub/src` (PowerShell dùng dấu `;`, Linux dùng `:`). `BACKEND-VERIFICATION.md` (chỉ có trong checkout feature đầy đủ, không có trong ZIP) ghi lệnh/output và giới hạn kiểm chứng. `VERIFICATION.md` (chỉ có trong checkout feature đầy đủ, không có trong ZIP) lưu evidence thiết kế trước integration.

[PHASE1-VERIFICATION.md](PHASE1-VERIFICATION.md) ghi kết quả kiểm thử tách FE/BE. [PHASE2-VERIFICATION.md](PHASE2-VERIFICATION.md) ghi kết quả registry/form và installed guards. Các báo cáo này không nằm trong ZIP.

Giao diện Hallmark Workbench/Cobalt theo bản Owner đã duyệt; không dùng Pencil. Font Google Fonts có local fallback, UI dùng được khi font mạng không tải được.

## Portable ZIP

Xem [README gốc](../README.md) để tạo ZIP từ source hiện tại, kiểm SHA256 manifest và chạy bản giải nén. ZIP không phải installer và không chứa tài liệu/evidence triển khai riêng tư; các liên kết deployment trỏ tới repository đầy đủ.

## Cập nhật worker và contract Console

Khi worker thay đổi, developer review diff cấu hình/receipt/usage/provider và dependency imports trước. Nếu hợp đồng quản trị thay đổi, cập nhật descriptor, trusted adapter, registration và contract revision tương ứng; cập nhật metadata `workers/contract-review.json` sau review có chủ đích. Không tự suy schema, tự chấp nhận bytes hiện tại hoặc sửa hai worker để làm Console migration đi qua. Nếu thêm worker, package phải có từng dependency/file được allowlist rõ ràng và fixture chứng minh worker đó hoạt động mà không sửa router/form lõi.

`workers/compatibility.json` giữ pin Git canonical đã kiểm thử. `contracts.py` sở hữu `PINNED_COMMIT` và `PINNED_MANIFEST_SHA256`; `workers/contract-review.json` ghi `reviewedModules` (hash text các module chung) và hash semantic JSON của descriptors. Khi chấp nhận một worker update mới, developer phải review thay đổi trước, cập nhật có chủ đích code pin, canonical compatibility manifest, descriptor/adapter/contract revision cần thiết và review record đồng bộ, rồi chạy lại toàn bộ gate. Không tự tái tạo hoặc chấp nhận hashes từ source/installed files để vượt gate. Source gate kiểm bytes hiện tại cùng provenance của pin Git, còn archive gate kiểm đúng bytes đã đóng gói và review metadata đi kèm. SHA256 và metadata giúp phát hiện drift/nhất quán; chúng không phải chữ ký hoặc xác thực nhà phát hành. Một update runtime có thể chỉ đổi logic nội bộ, nhưng developer vẫn phải review trước khi quyết định có cần contract/UI mới.

Từ root checkout đầy đủ (Git phải có lịch sử chứa pin tương thích):

```sh
python console-ui/scripts/check-contracts.py
python -m unittest discover -s console-ui/tests -p "test_*.py" -v
node console-ui/tests/test_frontend.cjs
python console-ui/package.py --output dist/agentfleet-console.zip
python console-ui/package.py --verify dist/agentfleet-console.zip
```

Packager từ chối output đã tồn tại: chọn tên ZIP mới khi build lại. Khi đăng ký thêm worker/package local, cập nhật rõ `PACKAGE_REGISTRATIONS`, `FILES` và `LOCAL_PACKAGES` trong `package.py` cùng dependency/file cần phát hành; không dùng wildcard tự ship mọi file. Fixture phải chứng minh thiếu dependency bị chặn và bản đầy đủ chạy được. ZIP không cần Git/npm khi vận hành. Kiểm extracted ZIP có imports/worker CLI help và Console HTTP save/relaunch/reload trong suite bằng TEMP, không chạy jobs/probes/apply.

[Console CI](../.github/workflows/console.yml) cấu hình Windows/Ubuntu với Python3.11/3.12, checkout full history, Python fixture tests, Node dev regression và ZIP build/verify. Workflow mới chưa có kết quả GitHub Actions trong lượt local này; không coi cấu hình CI là Linux PASS. [PHASE3-VERIFICATION.md](PHASE3-VERIFICATION.md) ghi kết quả thực tế và giới hạn sau khi chạy. Hệ thống đang chạy/guard EnvironmentFiles không bị thay đổi bởi các gate này.
