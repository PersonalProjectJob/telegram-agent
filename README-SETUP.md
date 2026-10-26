# Chạy Telegram Triage và NailHub từ source

Cập nhật: 26/10/2026 · Phiên bản: 1.0.1

Console UI hiện chưa khả dụng. Hướng dẫn này chạy trực tiếp bằng CLI trên **Windows / PowerShell**. Chỉ làm phần của worker cần dùng; muốn chạy cả hai thì mở hai terminal riêng.

## 1. Chuẩn bị

- Cài **Python 3.11+** và nhận source đầy đủ từ bên bàn giao.
- Mở PowerShell tại thư mục gốc source, nơi có `listener/`, `nailhub/`, `config/`.
- Chuẩn bị Telegram API ID/hash và tài khoản Telegram được phép sử dụng.
- Có Ollama đang chạy tại `http://127.0.0.1:11434`, đã tải model `qwen3.5:9b` theo cấu hình mẫu. Nếu bên giao dùng model/provider khác, nhận cấu hình tương ứng từ bên giao.
- Với NailHub: cần thêm tài khoản đăng nhập website.

Kiểm tra Python:

```powershell
python --version
```

Các lệnh bên dưới đều chạy từ root source. Không cần Node.js hoặc Console UI.

## 2. Cài package — chỉ làm lần đầu

**Telegram Triage:**

```powershell
python -m venv listener/.venv
.\listener\.venv\Scripts\python.exe -m pip install ./listener
```

**NailHub:**

```powershell
python -m venv nailhub/.venv
.\nailhub\.venv\Scripts\python.exe -m pip install ./nailhub
.\nailhub\.venv\Scripts\python.exe -m playwright install chromium
```

Không cần activate virtualenv. Hai worker dùng hai môi trường riêng.

## 3. Tạo cấu hình — chỉ làm lần đầu

```powershell
New-Item -ItemType Directory -Force -Path C:/AgentFleetConfig
New-Item -ItemType Directory -Force -Path C:/AgentFleetData/triage
New-Item -ItemType Directory -Force -Path C:/AgentFleetData/nailhub
Copy-Item config/listener.example.toml C:/AgentFleetConfig/listener.toml
Copy-Item config/nailhub.example.toml C:/AgentFleetConfig/nailhub.toml
```

Không copy lại đè lên cấu hình đã chỉnh. Giữ thư mục cấu hình và dữ liệu riêng tư, không đưa key, session hoặc browser profile vào source bàn giao.

### Cấu hình Telegram Triage

Mở `C:/AgentFleetConfig/listener.toml`, sửa các giá trị sau **trong section sẵn có**, giữ nguyên phần còn lại:

```toml
mode = "dry-run"

[telegram]
session_path = "C:/AgentFleetData/triage/gateway.session"
chat_ids = [-1001234567890] # Thay bằng ID nhóm thử nghiệm thực tế
listen_all_groups = false

[storage]
db_path = "C:/AgentFleetData/triage/triage.sqlite3"
```

Các đoạn TOML trong hướng dẫn chỉ thể hiện trường cần sửa; không thay toàn bộ file mẫu hoặc thêm section trùng tên.

### Cấu hình NailHub

Mở `C:/AgentFleetConfig/nailhub.toml`, sửa trong section sẵn có:

```toml
[telegram]
session = "C:/AgentFleetData/nailhub/nailhub.session"

[worker]
mode = "preview"
db_path = "C:/AgentFleetData/nailhub/jobs.sqlite3"
profile_path = "C:/AgentFleetData/nailhub/profile"
credential_file = "C:/AgentFleetConfig/nailhub-credentials.json"
```

Tạo file riêng `C:/AgentFleetConfig/nailhub-credentials.json`, chỉ gồm hai trường:

```json
{
  "email": "<email-dang-nhap>",
  "password": "<mat-khau>"
}
```

Thay placeholder bằng tài khoản website được cấp. File chứa mật khẩu dạng rõ: chỉ cấp quyền đọc cho tài khoản vận hành và quản trị viên cần thiết, không commit hoặc gửi kèm source.

Giữ `model.think = false` và `contract.verified = false` theo mẫu. Bước `probe`/`preview` bên dưới chưa cần chạy Telegram worker NailHub.

## 4. Chạy Telegram Triage

Trong terminal chạy Triage, cấp biến môi trường:

```powershell
$env:TG_API_ID = "<telegram-api-id>"
$env:TG_API_HASH = "<telegram-api-hash>"
$env:TG_TRIAGE_MODE = "dry-run"
```

Thay placeholder bằng giá trị được cấp riêng. Worker không tự đọc file `.env`.

Kiểm tra cấu hình và đăng nhập Telegram lần đầu:

```powershell
.\listener\.venv\Scripts\python.exe -m tg_triage check-config --config C:/AgentFleetConfig/listener.toml
.\listener\.venv\Scripts\python.exe -m tg_triage login --config C:/AgentFleetConfig/listener.toml
```

Nhập số điện thoại, OTP và 2FA khi được hỏi. Sau đó chạy:

```powershell
.\listener\.venv\Scripts\python.exe -m tg_triage run --config C:/AgentFleetConfig/listener.toml
```

Giữ terminal mở. Gửi tin trong nhóm thử nghiệm đã cấu hình để kiểm tra. **Dry-run không tạo GitHub issue nhưng vẫn có thể thả reaction Telegram.**

Dừng bằng `Ctrl+C`. Lần sau chỉ cần cấp lại biến môi trường trong terminal mới và chạy lệnh `run`; không cần login lại nếu session còn hợp lệ.

## 5. Chạy thử NailHub

Đăng nhập website lần đầu bằng thông tin trong file JSON:

```powershell
.\nailhub\.venv\Scripts\python.exe -m nailhub login --config C:/AgentFleetConfig/nailhub.toml
```

Kiểm tra truy cập, rồi chạy một lượt preview:

```powershell
.\nailhub\.venv\Scripts\python.exe -m nailhub probe --config C:/AgentFleetConfig/nailhub.toml
.\nailhub\.venv\Scripts\python.exe -m nailhub preview --config C:/AgentFleetConfig/nailhub.toml
```

`probe` đọc danh sách để kiểm tra truy cập. `preview` chạy một lượt rồi kết thúc, không ghi bài lên website; vẫn đọc dữ liệu thật và có thể gọi model. Nếu session website còn hợp lệ, các lần sau có thể chạy thẳng `preview`.

### Nếu cần NailHub chạy liên tục và nhận lệnh Telegram

Cần bên bàn giao xác nhận **numeric Owner ID** để điền `worker.owner_ids`. Source hiện khóa `owner_username` ở `SotaThao`, không hỗ trợ đổi tùy ý sang username khác. `owner_ids = []` khiến lệnh `run` không khởi động được, kể cả mode preview.

Sau khi nhận đúng Owner binding, mở terminal riêng tại root source:

```powershell
$env:TG_API_ID = "<telegram-api-id>"
$env:TG_API_HASH = "<telegram-api-hash>"

.\nailhub\.venv\Scripts\python.exe -m nailhub login-telegram --config C:/AgentFleetConfig/nailhub.toml
.\nailhub\.venv\Scripts\python.exe -m nailhub run --config C:/AgentFleetConfig/nailhub.toml
```

`login-telegram` chỉ cần lần đầu hoặc khi session hết hiệu lực. Giữ mode preview khi thử nghiệm. Dừng bằng `Ctrl+C`. Dừng worker trước khi chạy lại `login`, `probe` hoặc `preview`; không chạy hai tiến trình cùng dùng một profile/session/database.

## 6. Khi chạy thật

- **Triage:** cần `GH_TOKEN`, `GH_REPO` và mode `live`; biến `TG_TRIAGE_MODE` ghi đè TOML. Upload ảnh/tài liệu còn cần GitHub CLI `gh`. Xác nhận repo đích và quyền trước khi bật.
- **NailHub:** mode `live` cần Owner binding và contract evidence đã xác minh. Nhận cấu hình đã được bên giao review, không chỉ tự đổi `verified = true`.
- Linux production: theo [hướng dẫn deploy/rollback](deploy/README.md), đối chiếu host đích trước khi chạy các bước cutover legacy.

## Lỗi thường gặp

| Lỗi | Cách xử lý |
|---|---|
| Không tìm thấy `python` | Kiểm tra Python/PATH; thử `py -3.12` thay `python` nếu máy dùng Python launcher |
| Không import được package | Dùng đúng đường dẫn Python trong virtualenv của worker |
| Thiếu Telegram API ID/hash | Cấp biến môi trường trong đúng terminal đang chạy worker |
| Model timeout/không kết nối | Kiểm tra Ollama đang chạy, model đã tải và tên model khớp TOML |
| Không tìm thấy Chromium | Chạy lệnh cài Chromium ở bước 2 bằng Python NailHub |
| NailHub login lỗi | Kiểm tra `credential_file`, JSON đúng hai trường và quyền truy cập website |
| NailHub run báo Owner IDs | Nhận numeric Owner ID được xác minh từ bên bàn giao; không để allowlist rỗng |

## Tham chiếu

- [Telegram Triage README](listener/README.md)
- [NailHub README](nailhub/README.md)
- [Cấu hình mẫu Triage](config/listener.example.toml)
- [Cấu hình mẫu NailHub](config/nailhub.example.toml)

Hướng dẫn được đối chiếu với source; chưa thực hiện đăng nhập tài khoản hoặc chạy worker thật trong lượt cập nhật tài liệu này.
