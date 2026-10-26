# Setup nhanh Telegram Triage và NailHub

Cập nhật: 26/10/2026 · Phiên bản: 1.0.3

Chạy trực tiếp bằng CLI, **không cần Console UI hoặc Node.js**. Chỉ cài/chạy worker bạn cần. Hướng dẫn dành cho Windows PowerShell và macOS/Linux Bash hoặc Zsh, dùng tài khoản người dùng thông thường.

## 1. Chuẩn bị và lấy source

Cài **Git**, **Python 3.12** (source yêu cầu tối thiểu 3.11) và [Ollama](https://docs.ollama.com/quickstart). Cần Internet để tải package/model và truy cập Telegram/NailHub. Repository riêng tư cần tài khoản có quyền đọc.

NailHub còn cần hệ điều hành/CPU được [Playwright hỗ trợ](https://playwright.dev/python/docs/intro#system-requirements). Không thể bảo đảm mọi máy hoặc hệ điều hành cũ đều chạy được. Model local cần đủ RAM/dung lượng; máy không đủ tài nguyên cần bên bàn giao cấp cấu hình provider phù hợp.

```sh
git clone --branch codex/cross-platform-cli-setup https://github.com/PersonalProjectJob/telegram-agent.git AgentBuilder
cd AgentBuilder
```

Đã nhận ZIP source thì giải nén và mở terminal trong thư mục có `listener/`, `nailhub/`, `config/`. Không cần clone lại. Sau khi PR được merge, có thể dùng branch `main` thay branch bàn giao.

**Mọi lệnh dưới đây chạy tại root source.** Không copy virtualenv, Telegram session hoặc browser profile từ máy khác; cài và đăng nhập mới trên máy nhận.

Mở Ollama, sau đó tải model đúng với cấu hình mẫu:

```sh
ollama pull qwen3.5:9b
ollama ls
```

Nếu Ollama chưa chạy nền, mở terminal riêng chạy `ollama serve` và giữ terminal đó mở. Không chạy thêm server nếu cổng đã được ứng dụng/dịch vụ Ollama sử dụng. [Tham khảo CLI Ollama](https://docs.ollama.com/cli).

## 2. Cài worker và chuẩn bị terminal

Chọn **một** khối theo hệ điều hành. Bỏ các dòng của worker không sử dụng. Chỉ cài package và copy cấu hình lần đầu; không ghi đè cấu hình đã chỉnh.

### Windows — PowerShell

```powershell
python --version
python -m venv listener/.venv
python -m venv nailhub/.venv
.\listener\.venv\Scripts\python.exe -m pip install ./listener
.\nailhub\.venv\Scripts\python.exe -m pip install ./nailhub
.\nailhub\.venv\Scripts\python.exe -m playwright install chromium

New-Item -ItemType Directory -Force -Path "$HOME/.agentfleet-cli/triage", "$HOME/.agentfleet-cli/nailhub", "$HOME/.agentfleet-cli/model-lock"
Copy-Item config/listener.example.toml "$HOME/.agentfleet-cli/listener.toml"
Copy-Item config/nailhub.example.toml "$HOME/.agentfleet-cli/nailhub.toml"
```

Nếu `python` không được nhận, dùng `py -3.12` thay `python` ở ba dòng đầu. Không cần activate virtualenv hoặc đổi ExecutionPolicy.

Trong **mỗi terminal mới** dùng để chạy worker, đứng tại root source và chạy:

```powershell
function triage { & ./listener/.venv/Scripts/python.exe -m tg_triage @args --config "$HOME/.agentfleet-cli/listener.toml" }
function nailhub { & ./nailhub/.venv/Scripts/python.exe -m nailhub @args --config "$HOME/.agentfleet-cli/nailhub.toml" }
$env:AGENTFLEET_LOCK_DIR = "$HOME/.agentfleet-cli/model-lock"
$env:TG_API_ID = "<telegram-api-id>"
$env:TG_API_HASH = "<telegram-api-hash>"
$env:TG_TRIAGE_MODE = "dry-run"
Write-Output "$HOME/.agentfleet-cli".Replace('\', '/')
```

### macOS / Linux — Bash hoặc Zsh

```sh
python3 --version
python3 -m venv listener/.venv
python3 -m venv nailhub/.venv
./listener/.venv/bin/python -m pip install ./listener
./nailhub/.venv/bin/python -m pip install ./nailhub
./nailhub/.venv/bin/python -m playwright install chromium

umask 077
mkdir -p "$HOME/.agentfleet-cli/triage" "$HOME/.agentfleet-cli/nailhub" "$HOME/.agentfleet-cli/model-lock"
chmod 700 "$HOME/.agentfleet-cli" "$HOME/.agentfleet-cli/triage" "$HOME/.agentfleet-cli/nailhub" "$HOME/.agentfleet-cli/model-lock"
cp -i config/listener.example.toml "$HOME/.agentfleet-cli/listener.toml"
cp -i config/nailhub.example.toml "$HOME/.agentfleet-cli/nailhub.toml"
```

**Linux:** cài thêm thư viện hệ thống cho Chromium bằng lệnh dưới. Bước này có thể yêu cầu quyền quản trị; nhờ quản trị viên thực hiện nếu tài khoản không có quyền. Không chạy worker bằng root.

```sh
./nailhub/.venv/bin/python -m playwright install-deps chromium
```

**Ubuntu/Debian:** nếu tạo venv báo thiếu `ensurepip`/`venv`, cài package `python3-venv` tương ứng phiên bản Python đang dùng rồi tạo lại venv. Trên macOS, dùng bản Python 3.12 đã cài thay cho Python hệ thống quá cũ.

Trong **mỗi terminal mới** dùng để chạy worker, đứng tại root source và chạy:

```sh
triage() { ./listener/.venv/bin/python -m tg_triage "$@" --config "$HOME/.agentfleet-cli/listener.toml"; }
nailhub() { ./nailhub/.venv/bin/python -m nailhub "$@" --config "$HOME/.agentfleet-cli/nailhub.toml"; }
export AGENTFLEET_LOCK_DIR="$HOME/.agentfleet-cli/model-lock"
export TG_API_ID='<telegram-api-id>'
export TG_API_HASH='<telegram-api-hash>'
export TG_TRIAGE_MODE='dry-run'
printf '%s\n' "$HOME/.agentfleet-cli"
```

`triage` và `nailhub` là hai lệnh tắt trong terminal hiện tại; các bước sau dùng chung cho cả ba hệ điều hành. Thay Telegram API ID/hash bằng thông tin được cấp riêng. Worker không tự đọc `.env`. Nếu chỉ chạy NailHub `login`/`probe`/`preview`, chưa cần Telegram credentials.

## 3. Sửa cấu hình một lần

Lệnh cuối ở bước 2 in ra thư mục dữ liệu, ví dụ `C:/Users/An/.agentfleet-cli`, `/Users/an/.agentfleet-cli` hoặc `/home/an/.agentfleet-cli`.

Mở hai TOML trong thư mục đó bằng editor. Trong bảng dưới, **BASE là đường dẫn tuyệt đối vừa in**, không gõ nguyên chữ BASE, `$HOME` hoặc `~` vào TOML. Trên Windows dùng dấu `/`. Lưu file dạng **UTF-8 không BOM**.

Sửa các trường trong section có sẵn, giữ nguyên phần còn lại; không thêm section trùng tên:

| File | Trường cần sửa | Giá trị |
|---|---|---|
| `listener.toml` | `mode` ở đầu file | `"dry-run"` |
| `listener.toml` | `[telegram] session_path` | `"BASE/triage/gateway.session"` |
| `listener.toml` | `[telegram] chat_ids` | Numeric ID của nhóm thử nghiệm, ví dụ `[-1001234567890]`; thay ID mẫu |
| `listener.toml` | `[telegram] listen_all_groups` | `false` |
| `listener.toml` | `[storage] db_path` | `"BASE/triage/triage.sqlite3"` |
| `nailhub.toml` | `[telegram] session` | `"BASE/nailhub/nailhub.session"` |
| `nailhub.toml` | `[worker] mode` | `"preview"` |
| `nailhub.toml` | `[worker] db_path` | `"BASE/nailhub/jobs.sqlite3"` |
| `nailhub.toml` | `[worker] profile_path` | `"BASE/nailhub/profile"` |
| `nailhub.toml` | `[worker] credential_file` | `"BASE/nailhub-credentials.json"` |

Giữ nguyên model `qwen3.5:9b`, endpoint local và `think` của từng worker theo mẫu. NailHub yêu cầu `think = false`. Giữ `contract.verified = false` trong giai đoạn preview.

**Chỉ khi dùng NailHub:** tạo file `nailhub-credentials.json` trong BASE, lưu UTF-8 không BOM:

```json
{
  "email": "<email-dang-nhap-NailHub>",
  "password": "<mat-khau>"
}
```

Thay placeholder bằng thông tin thật. Trên macOS/Linux, bắt buộc đặt quyền file bằng chính tài khoản chạy worker:

```sh
chmod 600 "$HOME/.agentfleet-cli/nailhub-credentials.json"
```

File chứa mật khẩu dạng rõ. Dùng thư mục riêng của tài khoản, không chia sẻ/commit key, session, database hoặc profile; trên Windows giới hạn quyền đọc file mật khẩu cho người vận hành và quản trị viên cần thiết. Cả hai worker trên cùng máy dùng chung `AGENTFLEET_LOCK_DIR` ở bước 2 để tránh tranh tài nguyên model.

## 4. Chạy Telegram Triage

Trong terminal đã chuẩn bị ở bước 2:

```sh
triage check-config
triage login
triage run
```

Chỉ chạy lệnh kế tiếp khi lệnh trước thành công. `login` yêu cầu phone/OTP/2FA lần đầu. `run` giữ terminal mở; dừng bằng `Ctrl+C`.

Thử gửi tin từ tài khoản khác trong nhóm đã cấu hình: listener bỏ qua tin outgoing của chính tài khoản đang đăng nhập. **Dry-run không tạo GitHub issue nhưng vẫn có thể thả reaction Telegram.**

Lần sau: vào root source, chạy lại khối chuẩn bị terminal ở bước 2 rồi `triage run`. Chỉ login lại khi session hết hiệu lực.

## 5. Chạy NailHub

Đăng nhập website và kiểm tra một lượt:

```sh
nailhub login
nailhub probe
nailhub preview
```

Chạy lần lượt; `login` dùng file JSON ở bước 3. `probe` kiểm tra truy cập danh sách; `preview` chạy một lượt rồi kết thúc, không ghi bài lên website nhưng vẫn đọc dữ liệu thật và có thể gọi model.

### Nếu cần chạy liên tục và nhận command Telegram

Nhờ bên bàn giao cấp **numeric Owner ID đã xác minh** để điền `[worker] owner_ids` trong `nailhub.toml`. Source hiện khóa Owner username ở `SotaThao`; không đổi tùy ý sang tài khoản khác. `owner_ids = []` khiến `run` không khởi động, kể cả preview.

Trong terminal đã có Telegram credentials và `AGENTFLEET_LOCK_DIR` ở bước 2:

```sh
nailhub login-telegram
nailhub run
```

Giữ mode preview khi thử nghiệm. Lần sau chỉ cần chuẩn bị terminal và `nailhub run` nếu session còn hợp lệ. Dùng session riêng cho hai worker. Không chạy nhiều tiến trình trên cùng session/database/browser profile; dừng NailHub worker trước khi chạy lại `login`/`probe`/`preview`.

## Khi cần chạy live

- Triage: cấp thêm `GH_TOKEN`, `GH_REPO` trong terminal theo cú pháp biến môi trường của OS, rồi đặt `TG_TRIAGE_MODE` thành `live`. Upload ảnh/tài liệu còn cần GitHub CLI `gh` và quyền truy cập repo đích.
- NailHub: cần Owner binding, contract evidence và cấu hình đã được bên giao review; không chỉ tự bật `verified = true`.
- Cài dịch vụ chạy nền/Linux production: dùng [deploy/README.md](deploy/README.md), đối chiếu host đích trước các bước cutover legacy. Hướng dẫn trên chạy tương tác và không tự khởi động lại khi reboot.

## Lỗi thường gặp

| Hiện tượng | Kiểm tra |
|---|---|
| `triage`/`nailhub` không được nhận | Đang ở root source chưa? Chạy lại khối chuẩn bị terminal của đúng OS |
| Python quá cũ hoặc không tạo được venv | Cài Python 3.12; Ubuntu/Debian kiểm tra package venv tương ứng |
| Thiếu Telegram API ID/hash | Cấp biến trong đúng terminal chạy worker; `.env` không tự được nạp |
| `Data directory ... 0700` | macOS/Linux: chạy lại `chmod 700` ở bước 2; dùng đúng tài khoản sở hữu thư mục |
| Credential file không đọc được | JSON đúng hai trường, UTF-8 không BOM; macOS/Linux cần owner đúng và mode `0600` |
| Lỗi model lock/không có quyền `/run/agentfleet` | Chạy lại dòng `AGENTFLEET_LOCK_DIR` ở bước 2 cho cả hai terminal |
| Model timeout/không kết nối | Ollama đang chạy, model đã tải, đủ tài nguyên và tên model đúng; cấu hình khác cần bên giao xác nhận |
| Không có Chromium/thiếu shared libraries | Cài browser trong NailHub venv; Linux chạy thêm `install-deps chromium` |
| Website login/probe thất bại | Kiểm tra tài khoản, quyền website và selector khớp bản website đang dùng |
| NailHub run báo Owner IDs | Nhận numeric Owner ID đã xác minh; không để allowlist rỗng |

**Giới hạn kiểm chứng:** hướng dẫn được đối chiếu với source và tài liệu dependency. Máy thực thi hiện tại là Windows; chưa xác minh end-to-end trên máy macOS/Linux sạch hoặc đăng nhập tài khoản thật. Các phiên bản dependency chưa có lockfile chung nên cần lưu phiên bản đã cài khi nghiệm thu. Console vẫn có lỗi contract/đóng gói đang được xử lý riêng.

Tham chiếu: [Triage](listener/README.md), [NailHub](nailhub/README.md), [TOML Triage](config/listener.example.toml), [TOML NailHub](config/nailhub.example.toml).
