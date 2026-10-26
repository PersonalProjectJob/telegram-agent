---
type: doc
title: "Master Plan: Console FE/BE và Worker Registry"
status: current
area: platform
created: 2026-10-15
owner: AlexChu
plan_status: complete
approved_on: 2026-10-15
approved_by: "Owner (chat): duyệt plan, triển khai đợt 1–3"
---

# Master Plan: Console FE/BE và Worker Registry

## Metadata

- Version: v1.0.9; created: 2026-10-15 15:53; updated: 2026-10-15 18:45 Asia/Saigon. Tên file giữ revision khởi tạo để link review ổn định.
- Status: Approved; đợt 1 S0-S1 hoàn tất, chưa commit/push. Owner yêu cầu tiếp tục đợt 2 trong chat ngày 2026-10-15; đợt 2 S2-S4 hoàn tất và kiểm chứng local; chưa commit/push, đợt 3 S5-S6 hoàn tất và kiểm chứng local, không tác động THINKPAD.
- Owner: AlexChu; executor khảo sát và lập plan: Codex.
- Branch: `codex/console-fe-be-plan`; baseline: `6fdb7db7da22247052782e2450f861375e79668f`.
- Ticket: chưa có issue riêng; `noissue` không đại diện cho một GitHub ticket đã tồn tại.
- User yêu cầu: tách FE/BE, cập nhật Console tương ứng khi worker cập nhật hoặc thêm worker; không thay đổi hai worker vì đang vận hành.
- Review skill: `plan-eng-review`. Bản này là đầu vào review, không phải kết luận review đã PASS.

## 1. Mục tiêu và tiêu chí thành công

Owner có một Console quản trị chung. Developer bổ sung worker hoặc trường cấu hình bằng hợp đồng được kiểm tra, thay vì sửa các nhánh `triage/nailhub` trong nhiều màn hình và routes.

| AC | Kết quả cần chứng minh |
|---|---|
| AC1 | Source FE/BE Console nằm trong thư mục riêng; worker giữ nguyên vị trí và nội dung. |
| AC2 | Hai worker hiện có hiển thị bằng registry; một worker fixture thứ ba được thêm bằng đăng ký + adapter/descriptor, không sửa router hoặc form renderer lõi. |
| AC3 | Schema mô tả field chuẩn sinh form và validation BE; FE không có bảng mapping cấu hình song song. |
| AC4 | Cấu hình triage hỗ trợ DeepSeek chính, Ollama fallback, model Qwen và fallback thinking theo loader hiện có. Không áp tính năng fallback của triage cho NailHub khi NailHub chưa hỗ trợ. |
| AC5 | Thay đổi hợp đồng worker có kiểm tra tương thích và drift; khác phiên bản chưa được kiểm chứng phải hiện unknown/unsupported, không đoán support. |
| AC6 | Launcher cũ và ZIP portable vẫn chạy khi giải nén ở đường dẫn có khoảng trắng; không yêu cầu npm/Git để mở Console. |
| AC7 | Không đổi source, package metadata, prompt, config mẫu, deploy script hoặc systemd của hai worker. Review/refactor không tự deploy hoặc chạy jobs trên production. |
| AC8 | Save/Apply/Restart chỉ tác động đúng worker trên host chạy backend Console, khi Owner chủ động thao tác và guard phù hợp. Bootstrap/status không tự save/apply/restart. Test refactor dùng fixtures và subprocess giả lập. |

## 2. Phạm vi và ranh giới vận hành

### Được thay đổi khi plan được duyệt

- `console-ui/**`: FE/BE, registry, adapter, hợp đồng, tests, đóng gói, compatibility launcher.
- Launcher Console tại root và phần hướng dẫn Console trong `README.md` khi cần.
- Tài liệu và CI dành riêng cho Console/kiểm tra tương thích read-only.
- Fixtures và dữ liệu test nằm trong Console, dùng thư mục tạm; không sửa test của worker.

### NOT in scope

- `listener/**`, `nailhub/**`, `config/**`, `deploy/**`, `systemd/**`, `.env.example`: chỉ đọc, không sửa/move/copy đè.
- Tách thư viện AI/model lock khỏi hai worker: sẽ làm thay đổi package/deploy và vi phạm ràng buộc hiện tại.
- Refactor không tự đổi model/provider/think/fallback đang chạy, mode, Owner binding, selectors hay business rule. Owner vẫn có thể thay setting được hỗ trợ bằng Console chạy trên đúng host theo D3; không bỏ guard bảo vệ dữ liệu.
- Cài Console lên THINKPAD hoặc kích hoạt quản trị từ xa: cần một việc triển khai riêng được Owner yêu cầu.
- Framework FE/BE mới, Node build bắt buộc, container, message broker, microservices: chưa cần cho mục tiêu hiện tại.
- Auto-discovery bằng chạy/import plugin tải từ mạng hoặc bằng code tùy ý trong TOML: không thuộc hợp đồng v1.
- Tự nhận diện mọi thay đổi trong worker mà không cập nhật adapter/contract: không thể đảm bảo khi worker chưa xuất schema.

Owner chốt D3 ngày 2026-10-15: cho phép Save/Apply/Restart khi sử dụng Console trực tiếp trên máy/source tương ứng, ví dụ chạy trên THINKPAD hoặc khởi chạy Console qua SSH THINKPAD. Backend phải chạy trên host có worker; browser có thể truy cập qua SSH port-forward. Không xây connector SSH trong web backend và không nhận host/path/command tùy ý qua HTTP.

Local profile chỉ chỉnh cấu hình trong state directory riêng, không điều khiển service THINKPAD. Installed profile giữ Save/Apply (Apply restart đúng service) khi đúng binding/capability/quyền OS và Owner chủ động thao tác. Không cần thêm chế độ chỉ xem thay thế installed. Khi quyền/capability thiếu, disable action và giải thích lý do, không nâng quyền hoặc đổi sang local âm thầm. Giữ guard revision/fingerprint, credential scope, receipt/PID/mode và allowlist unit hiện tại. Không thêm nút restart độc lập bỏ qua kiểm tra Apply.

Phân biệt quyền sản phẩm với thao tác trong lượt review: lập plan không kết nối/mutate production. Trong refactor, kiểm thử Save/Apply bằng temp configs, fake systemctl và fixtures; không migrate state tại chỗ. Việc chạy thật sau này tuân theo điều kiện host/source Owner vừa chốt, không được hiểu là miễn kiểm tra compatibility hoặc tự sửa source worker.

## 3. What already exists

| Thành phần | Bằng chứng baseline | Cách tái sử dụng |
|---|---|---|
| API boundary FE | `console-ui/api.js`: fetch, CSRF, timeout, lỗi | Giữ behavior và facade; tách khỏi views/state. |
| HTTP server | `console-ui/run.py`: loopback, Host/Origin/CSRF, asset allowlist | Giữ một origin và thư viện chuẩn Python; tách routes/static serving/bootstrap. |
| Configuration transaction | `backend.py`: patch_toml, lock, fingerprint, revision, recovery | Di chuyển nguyên behavior trước; không viết lại transaction cùng lúc đổi hợp đồng. |
| Runtime validation | `backend.py:408-412`: gọi loader worker trên candidate TOML | Đặt trong legacy adapters, chỉ dùng candidate/temp; không sửa loader. |
| Status acknowledgement | `backend.py:275-298,423-445`: receipt + PID + revision | Phân biệt config saved, runtime acknowledged, capability verified. Không dùng receipt thay build identity. |
| Usage summary | `backend.py:306-327`: đọc JSON giới hạn kích thước, không mở DB | Adapter triage tái sử dụng read-only; không giả định mọi worker có usage. |
| Fallback triage | `listener/src/tg_triage/config.py:91-98` và `provider.py` | Descriptor Console phản ánh contract hiện có; không đổi defaults/runtime. |
| Worker độc lập | Hai pyproject và hai systemd units | Giữ nguyên toàn bộ. |
| Portable packaging | `console-ui/package.py` và `test_package.py` | Update allowlist Console và thử ZIP thật; giữ bytes worker nguyên baseline. |

Giới hạn phát hiện phiên bản: hai package đang dùng version 1.0.0, nên chỉ so version là chưa đủ. Readiness receipt chứng minh lần chạy/config; không tự chứng minh build hỗ trợ một field mới.

## 4. Cấu trúc đích đề xuất

```text
AgentBuilder/
  console-ui/
    frontend/
      index.html
      assets/                    # tokens.css, styles.css
      src/
        app.js                   # boot + composition
        api.js                   # same-origin client
        state.js                 # draft/saved/revision, không secrets persistent
        components/              # field renderer, tabs, status, dialogs
        pages/                   # overview, worker, usage, history
    backend/
      agentfleet_console/
        server.py                # HTTP security boundary + trusted dispatch
        registry.py              # explicit allowlist của built-in worker adapters
        schema.py                # validate descriptor và public field values
        settings.py              # config transaction facade
        storage.py               # TOML patch, lock, atomic write, recovery
        credentials.py           # key scope/rotation; không public secret
        status.py                # saved/acknowledged/unknown
        usage.py                 # bounded read-only summary
        policy.py                # local vs installed; host binding + action guards
        workers/
          descriptors/           # triage.json, nailhub.json; schema v1
          triage.py              # mapping/validator/status adapter hiện có
          nailhub.py             # mapping/validator/status adapter hiện có
    tests/                       # unit, HTTP integration, contract, browser, ZIP
    scripts/                     # packaging/check-contracts (Console only)
    run.py                       # launcher tương thích lệnh hiện tại
    backend.py                   # chỉ tạm ở đợt A; bỏ ở đợt B theo D5-A
    package.py                   # facade lệnh đóng ZIP hiện tại
  listener/                      # unchanged
  nailhub/                       # unchanged
  config/ deploy/ systemd/       # unchanged
```

Đây là cấu trúc đích, không phải yêu cầu tạo đủ file rỗng. Chỉ tách module khi có trách nhiệm thực tế và test tương ứng. Giữ JS thuần, Python stdlib, không thêm framework. FE/BE là ranh giới source, không bắt buộc chạy hai server.

Owner chốt D5-A: giữ `run.py`, `package.py` và root launcher làm entry points tương thích. `backend.py` cũ chỉ re-export trong đợt A, không chứa bản sao implementation; bỏ ở đợt B khi tests/imports nội bộ đã trỏ package mới. Không hứa giữ API import backend.py cho script bên ngoài. Test patch phải trỏ module chứa function đang chạy, không patch facade không còn được dùng. Vị trí test được migrate cùng source; public CLI/API parity có integration tests riêng.

## 5. Hợp đồng và hướng phụ thuộc

```text
Trusted Console descriptors + adapters (server-owned)
                  |
                  v
           Registry + policy
                  |
     GET bootstrap / public descriptor
                  |
                  v
 FE navigation --> generic form --> API request
                                      |
                             schema + adapter validation
                                      |
                      local: config transaction, temp state
                      installed: correct-host binding + guarded Save/Apply

Read-only legacy adapters --> worker config loaders / receipt / usage
No dependency from a worker back to Console
```

Descriptor gồm id, display name, schema version, adapter compatibility revision, field id/type/default/bounds/options/visibility/help/group và required capabilities. Backend giữ mapping field -> TOML trong descriptor tin cậy; public projection loại bỏ file paths, systemd unit, secrets và implementation details. Không gửi rồi tin lại các thông tin nội bộ từ browser.

Kiểu v1: string, integer, finite number, boolean, enum, list; conditional visibility dùng biểu thức dữ liệu giới hạn (equals/in/all), không eval JS/Python. Validation BE là authoritative; FE validation giúp nhập liệu. Hidden field không tự bị xóa; phân biệt inactive và disabled. Custom screen cần module riêng được đăng ký trong source.

Registry là allowlist cài cùng Console. Request chỉ truyền worker id/field values/revision; không truyền executable, import path, config path hoặc unit name. Worker id lạ trả 404; descriptor lỗi làm worker đó unavailable, không làm mất hai worker hợp lệ.

Giữ các route hiện có `/api/bootstrap`, `/api/usage`, `/api/services/{id}/test|save|apply`; bootstrap bổ sung contractVersion + descriptors + capabilities, route dùng registry thay regex cố định hai id. Chỉ có capability usage ở triage thì UI mới mở phần tương ứng. Unknown schema version hiển thị lỗi tương thích và không cho mutate.

Legacy adapters có thể gọi validator/transport hiện có bằng import read-only; đặt cơ chế resolve path tại một chỗ phía BE, không rải sys.path trong domain modules. Không trích thư viện chung khỏi worker trong task này. Future worker phải đăng ký adapter + descriptor + fixtures tương thích; không đòi hai worker hiện tại implement interface mới.

## 6. Đồng bộ update và bảo vệ tương thích

Vì worker chưa xuất schema, Console descriptor là contract quản trị, còn loader worker vẫn là nguồn quyết định runtime chấp nhận gì. Hai nguồn này được nối bằng contract tests, không giả vờ đã có một nguồn tự sinh hoàn toàn.

Mỗi worker update: developer xem diff config/receipt/usage/provider -> cập nhật Console descriptor/adapter nếu cần -> chạy check-contracts -> test form/API/ZIP -> phát hành Console tương thích. Update chỉ logic nội bộ worker có thể không cần UI mới. Gate phát hiện thay đổi file contract chưa được rà và yêu cầu adapter review, không tự merge schema đoán từ AST.

Mỗi worker mới: thêm descriptor + trusted adapter + fixtures/tests + registration. Navigation/form renderer/routes lõi không thêm if/else cho worker mới. Cài worker là việc triển khai riêng; đăng ký UI không tự tạo user/venv/unit hoặc bật worker.

Owner chốt D4-A: compatibility phải được kiểm tự động bằng hash; installed source không khớp hoặc chưa xác minh thì báo lý do và chặn Save/Apply. Không có nút tự xác nhận để bỏ qua. Status vẫn đọc được khi có quyền; chưa xác minh không được diễn đạt thành đã biết một capability cụ thể không được hỗ trợ.

Console tạo manifest tương thích trong artifact của chính Console, từ bytes canonical của wheel/git archive đã test (không dùng raw hash checkout Windows có thể CRLF). Manifest có worker id, contract revision, danh sách path tương đối/hashes cho Python modules và package data liên quan. Expected manifest là allowlist trusted, không lấy hash tự khai của worker làm bằng chứng duy nhất. Installed check đọc đúng package từ binding/venv của service, không import/chạy worker để dò. Không chỉ so version 1.0.0 hoặc receipt; thiếu/thừa source module, bytes khác hoặc không đủ quyền đọc đều thành unverified/mismatch. Bỏ qua pyc/cache và RECORD do pip ghi lại; kiểm nội dung file thực tế chứ không tin hash RECORD đơn độc. Không đọc credential/DB/profile/session để kiểm build.

Registry trả capability + lý do sanitized; action gate kiểm lại trước Save/Apply, không tin cached FE flag. Hash success không thay thế quyền OS, revision/fingerprint hay receipt/PID/mode. Installed check không sửa source/metadata worker. Triển khai worker đồng thời với Save/Apply chưa có lock chung với deploy script: không hứa atomic với thao tác root; hướng dẫn không thao tác Console khi deploy và recheck để phát hiện thay đổi. Không sửa deploy script trong scope này.

Console ZIP phải bao gồm toàn bộ dependency transitive cần cho imports. Baseline packager có danh sách module cố định; gate cần bắt module bị thiếu (ví dụ import UsageRecorder từ CLI nhưng usage.py chưa trong allowlist), kiểm chứng bằng extracted ZIP thực tế. Không sửa file worker để né lỗi đóng gói.

## 7. Kế hoạch triển khai theo lát cắt

Owner đã chốt D2-A trong chat ngày 2026-10-15: review đầy đủ mục tiêu và triển khai theo ba lát cắt. Master đã được Owner duyệt; actual phase1–3 execution và giới hạn kiểm chứng ở §9 và phase3 handoff. Tổng dự kiến 20-28 giờ người, 4-7 giờ agent gồm QA, không phải cam kết ETA.

| Bước | Nội dung | Phụ thuộc | Gate |
|---|---|---|---|
| S0 | Hash/snapshot worker và protected trees; baseline Console tests/ZIP; xác định import graph | — | Baseline và safe fixture workspace |
| S1 | Tách FE/BE vật lý, giữ launcher/facades/API/transaction; không thêm UI field | S0 | HTTP, persistence, security, ZIP relocation parity |
| S2 | Registry/descriptors hai legacy workers; public projection; trusted adapter routing; policy boundary | S1 | Contract/API parity; unknown worker/schema/version cases |
| S3 | Generic navigation/form, state modules, defaults và visibility từ contract | S2 | Two-worker UI parity; browser real interaction |
| S4 | Thêm field fallback triage đã được loader hỗ trợ; không thêm vào NailHub | S3 | Approved visual evidence + config round-trip |
| S5 | Worker fixture thứ ba, drift/compatibility gate, package dependency closure | S2-S4 | Không sửa renderer/router để thêm fixture; extracted ZIP |
| S6 | Kiểm tra immutable worker diff/hash, full Console regression, docs/handoff | S5 | Review complete; production untouched |

Lát A: S0-S1; lát B: S2-S4; lát C: S5-S6. Mỗi lát có điểm rollback riêng. Không ghép move file, đổi transaction và thay business behavior vào một diff không kiểm chứng được.

### Worktree parallelization strategy

| Workstream | Modules touched | Depends on |
|---|---|---|
| A: move + compatibility | Console FE/BE, launchers, Console tests | S0 |
| B1: generic frontend | `console-ui/frontend/` | A + contract frozen S2 |
| B2: adapter/hash enforcement | `console-ui/backend/` | A + contract frozen S2 |
| C: cross-layer tests, ZIP/drift | Console tests/scripts, README, Console CI | B1 + B2 |

Đề xuất thực thi tuần tự A -> B1/B2 -> C; chưa spawn agent. B1/B2 có thể song song ở worktree riêng sau khi contract cố định, nhưng cùng sửa tests thì phải để integration lane C sở hữu test integration. Descriptor changes, server contract và package allowlist không được sửa đồng thời bởi hai lane. 4 workstreams, 2 có thể song song; phương án mặc định vẫn tuần tự để giảm xung đột. Không chọn model/executor ngoài thay Owner.

## 8. Test strategy và failure modes (đề xuất, chưa chạy)

```text
READ CONTRACT / BOOTSTRAP
  valid known version --> two workers + fixture navigation [NEW contract + browser]
  invalid / duplicate / unknown version --> bounded error [NEW unit + HTTP]
  unknown worker id --> 404, no adapter call [NEW HTTP]
EDIT / SAVE LOCAL
  valid --> adapter validator --> atomic transaction [EXISTING + regression]
  stale revision / concurrent save --> 409 [EXISTING + regression]
  unknown TOML / protected fields --> preserve / reject [EXISTING + regression]
  hidden fallback fields --> retain values, validate in BE [NEW round-trip + browser]
INSTALLED (TEMP FIXTURES / FAKE SYSTEMCTL IN REFACTOR TESTS)
  status / usage --> bounded sanitized read, no auto-action [EXISTING + regression]
  explicit save/apply + valid host/guards --> exact target only [EXISTING + regression]
  wrong binding / missing permission / capability --> reject before side effect [NEW]
BUILD / EXTRACT
  allowlist --> checksum --> extract path with spaces --> launch [EXISTING + regression]
  source imported from missing packaged module --> fail gate [NEW import smoke]
```

Existing coverage references: `test_backend.py` covers revision/concurrency, unknown TOML, credential scope/redaction, recovery, HTTP boundary, receipt/PID mismatch, usage bounds. `test_package.py` covers relocation/extraction/launch and package safety. These tests are inspected, not newly executed during planning. No coverage percentage claimed.

| Failure | Expected handling / user result | Planned verification |
|---|---|---|
| Worker upgraded without descriptor review | Drift gate requires review; runtime build unknown disables new mutations | contract fingerprints, fixture build mismatch |
| Schema advertises unsupported fallback on NailHub | Reject descriptor/config in adapter; no enabled field | loader contract fixtures, endpoint invalid-input tests |
| Stale FE submits against new descriptor | schema/revision conflict; reload preserving draft where safe | real HTTP + browser stale tab |
| Invalid descriptor prevents boot | Per-worker unavailable with sanitized error; healthy workers remain visible | malformed/duplicate/schema limit tests |
| Hidden field reset by provider switch | Preserve last saved value; no implicit clearing | browser DeepSeek/Ollama round-trip |
| Refactor weakens CSRF/path boundary | Request rejected before registry/store | existing HTTP security tests + traversal/id tests |
| Status read accidentally starts/imports worker execution | Adapter never invokes CLI/browser/network for discovery | subprocess/network spies; no-main-import checks |
| Test/probe holds model lock | Preserve timeout/cancellation and priority; no automatic paid probes | lock regression; explicit action + correct binding |
| Portability import graph incomplete | Packaging verification/import smoke fails before release | extracted ZIP actual imports and launch |
| Production write via policy bypass | Require explicit action + target binding + existing guards before side effect | wrong-target fixtures, exact-unit assertion, before/after other-worker hashes |

### Test review: planned coverage và assertions

Python `unittest` là framework hiện tại; real HTTP/temp files đã có trong hai suite Console. Browser regression cần runner đã có hoặc discovery theo evidence rules; không thêm dependency vào worker và không thay browser interaction bằng page.evaluate inject state. Diagram dưới đây là coverage REQUIREMENTS của plan, không phải kết quả test code chưa được viết.

```text
ENTRY / USER FLOW                    EXPECTED BRANCH / ERROR             TEST PLAN
bootstrap + navigation               known / third / invalid worker      contract + HTTP + browser
schema form                          types / bounds / conditional        unit + browser keyboard/save
provider change                      active / hidden / stale probe       browser round-trip
fallback triage                      ollama / none / invalid model       adapter + form tests
NailHub descriptor                   no triage-only capability           negative contract test
save local                           valid / stale / external edit       existing + regression
save installed                       verified / unknown / mismatch       hash fixtures + HTTP
apply installed                      correct unit / bad receipt / deny   fake systemctl + real HTTP
source changes after bootstrap       recheck at action entry             negative integration
credential handling                  scope rotate / empty / bad endpoint existing + regression
schema changes while editing         matching / stale contract           HTTP conflict + browser retry
two tabs / rapid save                first wins / second conflict        concurrency + browser
reload / API offline                 persisted / actionable error        real HTTP + browser
usage                               valid / stale / invalid / oversized existing bounded-read tests
packaged import closure              included / missing module           extracted-process smoke
ZIP relocate + launcher              spaces / no site-packages           existing + regression
protected source                     identical / modified path           hash + diff + status gate
```

| Test module proposed under `console-ui/tests/` | Specific assertion |
|---|---|
| `test_registry.py` | Third fixture registered without changing core router/renderer; unknown id 404; one bad descriptor isolates that worker. |
| `test_contracts.py` | JSON types/finite numbers/options/defaults/conditions round-trip; field mappings accepted by unchanged loaders on temp configs; NailHub does not gain triage fallback. |
| `test_compatibility.py` | Canonical source hash matches installed fixture; CRLF checkout is not identity input; missing/changed/additional module, symlink, unreadable file or same version/different build blocks mutation. |
| `test_actions.py` | Real HTTP -> schema/host/hash/revision gates -> fake exact-unit restart -> receipt PID/revision/mode check. Denial makes zero config/key/subprocess writes; other worker hashes unchanged. |
| `test_storage.py` | Preserve existing unknown multiline TOML, fractional MiB, secret generation/scope, crash recovery and cross-process stale revision behavior. |
| `test_http.py` | Preserve Host/Origin/CSRF/content-type/body/asset boundaries; schema ids cannot become paths/imports/units; stale schema rejected before mutation. |
| `test_package.py` | Real ZIP extract and launch with `-E -S`, paths containing spaces; all Console lazy import paths resolve from ZIP; no credential/state/test/cache content leaks. |
| Browser suite | Actual third-worker navigation, tab/keyboard/focus behavior, provider/fallback visibility, save/reload, double-submit and stale-tab errors; sanitized key state. |

CRITICAL regression requirements (mandatory, no scope reduction): CLI/asset path moves must not break extracted ZIP launch; facade removal must not leave old mock/import targets; dynamic routing must not weaken allowlists; hidden fallback fields must survive provider switching; hash denial must occur before write/restart. These are new checks for changed codepaths, not claims that baseline already fails those flows.

Packaging audit also found a pre-existing closure risk: `TRIAGE_MODULES` omits `usage`, while bundled `tg_triage/__main__.py:18` imports `UsageRecorder`. Existing ZIP test exercises Console boot/save, not the worker CLI import. Add a Console-side packaging dependency check and include unchanged `usage.py` if the artifact continues to include the CLI module; do not execute the worker CLI, install Telethon or start a worker for this test. Do not mislabel this as a demonstrated Console startup failure.

No LLM eval required: no prompts, parsing or worker business behavior changes. Provider probes stay synthetic and fake upstream in tests. All planned feature paths now have an explicit test requirement; actual test execution remains a gate for implementation. No numeric coverage score is claimed.

### Performance review

No new unresolved performance issue after bounding the proposal. Proposed v1 implementation limits: at most 16 registered workers, 128 fields/worker, 256 enum options/field, 256 KiB/descriptor, 1 MiB public descriptors/bootstrap, condition depth 8. These are selected validation caps, not measured latency or a platform standard; oversized schema fails explicitly. Local read/validation must not perform provider requests. Retain usage limit 512 KiB and existing bounded history.

Reuse validated immutable descriptors across requests. UI navigation reuses bootstrap state and does not refetch/probe every card. Polling, if added later, must not overlap; not required by this refactor. Avoid N+1 file reads by reading a worker config once for one request snapshot. Display-only hash observations may be cached, but Save/Apply re-read and compare content; cache never authorizes a write. Stream artifact hashing in bounded chunks, reject unbounded file inventory, and use the trusted manifest for expected files. Concrete manifest size caps derive from baseline inventory at S0, with overflow rejection and tests; do not silently truncate verification.

No worker DB queries, browser startup or AI calls during bootstrap/status/compatibility. Keep current cancellation/timeout/model-lock behavior for explicit probes. Performance tests verify bounds/no-side-effects; no production benchmark is claimed.

Console verification after implementation: existing backend and package unittest suites; new contract/policy tests; browser settings save/reload/provider visibility/stale revision; ZIP extract/launch from path with spaces. Worker suites may run read-only with temp fixtures as regression evidence, but no edits to make them pass.

Immutable gate: compare `git diff BASE -- listener nailhub config deploy systemd .env.example` (must be empty), tracked file hashes before/after, and worker paths in ZIP. Refactor tests use temp/state copies, never installed worker venv. Clean Git diff proves source invariance, not absence of operational effects: track commands as well, with no production mutation during planning/refactor tests. Owner-authorized future Save/Apply from correct host is operational configuration management, not permission to modify worker source.

## 9. Review decisions và trạng thái công việc

- [x] Xác nhận mục tiêu FE/BE + Console cập nhật theo worker.
- [x] Ghi ràng buộc hai worker bất biến và phạm vi Console.
- [x] Khảo sát code/API/loader/packaging/test hiện tại và lưu draft.
- [x] Step 0: Owner chọn D2-A, giữ đầy đủ registry/form/compatibility/ZIP theo ba lát cắt; không thu hẹp còn move thư mục.
- [x] D3: Owner cho phép Save/Apply/Restart từ Console backend chạy trên đúng host/source, trực tiếp hoặc qua SSH; giữ các guard, không đổi worker source.
- [x] D4: Owner chọn kiểm hash tự động; chặn Save/Apply khi mismatch/unverified, không sửa worker.
- [x] Architecture: Console-owned registry/descriptors + legacy adapters, host binding và hash gate đã được chốt; không thêm service/framework hoặc sửa worker.
- [x] Code quality: D5-A giữ launchers, bỏ backend.py facade sau đợt B; registry thay hardcode, không đổi transaction cùng lúc move.
- [x] Tests: requirements/diagram/assertions cho all planned paths; regression requirements bắt buộc; execution và Visual Gate cho fallback fields ở implementation.
- [x] Performance: bounded descriptors, no automatic probes/DB queries, stream hashes; action recheck không dùng cache để authorize.
- [x] Outside voice: skipped optional review; không chạy external executor/model, không giả nhận independent review.
- [x] Final review report và implementation tasks trong Master; child docs chỉ tạo sau Master sign-off.
- [x] Owner duyệt plan và yêu cầu bắt đầu đợt 1, giữ nguyên hai worker, không tác động THINKPAD.

### Đợt 1: execution state

- Team: Owner chọn Phối hợp; executor/reviewer/verifier high, model kế thừa chat; không gọi CLI/model ngoài.
- Scope: S0-S1 / T1, tách vật lý FE/BE; giữ nguyên toàn bộ nội dung UI, API, state, transaction và installed behavior. Registry/hash gate/fallback form ở đợt 2, chưa triển khai.
- Integration branch: `codex/console-fe-be-plan`; FE/BE có managed worktree riêng, fence file/runtime trong `.context/console-fe-be-phase1/CONTRACT.md`.
- FE sở hữu move 5 assets; BE sở hữu package Python + launcher/facade; integration packaging/tests chạy sau hai stream. Không source writer nào sửa chồng file.
- Baseline Console suite: `Ran 51 tests in 123.873s`, `OK (skipped=1)`. Log `.context/console-fe-be-phase1/baseline-tests.log`.
- Snapshot: 109 tracked files trong protected trees; 5 frontend asset raw hashes. Git autocrlf trên máy làm checkout CRLF khác canonical Git LF: verify move theo raw checkout trước/sau và canonical identity riêng; không đổi UI content để xử lý newline.
- UI trước/sau kiểm bằng actual browser trên temp state, không dùng .agentfleet thật. No SSH, provider network probes hoặc systemctl production trong lượt này.
- [x] S0 baseline tests + protected/UI hash snapshot.
- [x] S1 FE/BE worktrees: hai stream rời nhau -> scoped patches -> integration tuần tự -> packaging/test stream.
- [x] Portable package/HTTP/security/persistence tests sau integration: main checkout `Ran 53 tests in 157.427s`, `OK (skipped=1)`; log `.context/console-fe-be-phase1/final-tests.log`. Integration trước đó 53 tests/205.739s; reviewer chạy 6 test độc lập, không thay controller output.
- [x] Browser parity: original commit TEMP snapshot với cùng finalized runner so với main, 4 viewport flows PASS; controller đối chiếu 96 PNG + 96 ARIA byte-identical, differences=[]; metrics/forms matched trong evidence report.
- [x] Independent source review: reviewer khác FE/BE/integration writers, no actionable findings; store/HTTP AST parity, allowlist và source boundaries giữ nguyên.
- [x] Protected hash gate: main raw 109/109; protected Git diff/status empty; 5/5 FE raw bytes giữ nguyên; Python AST10 và JS syntax2 PASS.
- [x] Handoff đợt 1; dừng trước commit/push và trước đợt 2. Không SSH hoặc tác động THINKPAD.

Kiểm chứng có giới hạn được ghi rõ: Windows skip test symlink creation vì thiếu quyền, junction rejection PASS. Google Fonts requests bị test harness chủ động chặn (cùng policy trước/sau), không gọi cloud provider; unexpected runtime/local HTTP errors=0. Một số ảnh baseline đầu khác trạng thái drawer/readiness do harness; đã giữ nguyên các failed attempts và 14 sai lệch ban đầu, chạy đối chứng lại trên source Git6fdb7db trong TEMP với runner đã sửa; không sửa product để làm xanh. Final strict parity report: `.context/console-fe-be-phase1/root-ui-parity-check.json`.

Cấu trúc đợt 1 thực tế: FE nằm dưới `console-ui/frontend/{index.html,src,assets}`; BE dưới `console-ui/backend/agentfleet_console/{paths,store,server,cli}.py`. `run.py`/`package.py` giữ CLI, `backend.py` là alias tạm duy nhất tới store (không copy logic), test routes public vẫn là URL cũ. Store monolith được giữ behavior để đợt 2 chia trách nhiệm tiếp, không tự áp registry/hash/fallback mới trong đợt 1.

### Đợt 2: execution state — complete locally, uncommitted

- Owner yêu cầu tiếp tục registry/form theo plan đã duyệt; Owner duyệt fallback visual proposal trong chat trước khi mount controls mới. Hai worker bất biến; không SSH/THINKPAD/provider/service thật.
- Scope S2-S4/T2-T5: registry/descriptors/schema, trusted legacy adapters, generic FE navigation/form/state; Triage fallback Ollama/none, Qwen model và fallback thinking độc lập. NailHub không có fallback. backend.py facade đã bỏ, tests dùng package thực.
- Team giữ lựa chọn Owner: Phối hợp, model kế thừa chat, các vai high; BE/FE/integration worktrees riêng. Có policy và Visual Gate nên áp các gate dispatch Route C: reviewer và verifier độc lập, không tự miễn theo làn nhanh. Frozen contracts/runtime fences `.context/console-fe-be-phase2/CONTRACT.md`.
- [x] Baseline53 tests OK(skipped1), protected109 SHA256 snapshot và phase1 rollback patch.
- [x] Registry/schema/public projection/adapters, unknown descriptor/capability/schema handling.
- [x] Generic navigation/form/state và third-worker fixture đăng ký adapter+descriptor: actual HTTP save/reload không sửa router/renderer lõi.
- [x] Owner duyệt UI fallback; inactive fields giữ giá trị, NailHub negative test.
- [x] Canonical manifest từ Git6fdb7db: root và verifier tái dựng23 Triage files281184bytes (20py+3prompts),21 NailHub py312153bytes; không dùng raw CRLF checkout.
- [x] Installed hash/effective binding guards, fresh rechecks trước Save/Apply/recovery; public unverified disable actions và sanitized reason. Status không replay journal hoặc ghi installed config/meta/config-adjacent locks. Private Console state ACL provisioning lúc startup là setup được giữ riêng.
- [x] Full main Python suite `Ran 86 tests in 265.346s`, `OK (skipped=1)` sau BE fix cuối. Sau đó chỉ sửa app.js async feedback: root permanent Node regression8/8 PASS, Node syntax PASS, extracted ZIP2tests OK6.487s. Không giả nhận86tests chạy lại sau JS-only fix; BE/Python bytes giữ nguyên.
- [x] Independent source reviewer APPROVED, sáu findings đã sửa. Independent verifier PASS:33 focused Python100.175s, HTTP1test8.706s, package2tests11.709s trước JS fix; sau fix Node8/8, tái hiện counterexample bản cũ thất bại, package2tests8.102s. Chi tiết `.context/console-fe-be-phase2/verifier-report.md`.
- [x] Browser main trước async fix:4viewport,116screenshots,52assertiongroups,20UI actual HTTP saves và8error/concurrency scenarios PASS; source23hashes stable. Sau fix:4viewport smoke,20screenshots,8groups,8actual UI saves,4delayed synthetic Test/Save race scenarios và positive-owner feedback PASS. Các synthetic Test requests bị intercept local, không gửi provider; actual Save smoke đi qua backend TEMP thật.
- [x] Root mở ảnh desktop/mobile sau fix; fallback đúng layout/content đã duyệt. Font metrics IBM Plex Sans/sans fallback; Google Fonts bị harness chặn, FontFaceSet0, không claim remote fonts loaded.
- [x] Root/verifier protected109/109 raw hashes exact, protected Git diff/status empty; source main/integration normalized equal trước JS fix, final23FE/BE hashes root đối chiếu exact.14 raw EOL differences ở worktree checkout không phải worker edits; main raw bytes giữ nguyên.
- [x] README và report handoff, không commit/push hoặc bắt đầu đợt3. Report `console-ui/PHASE2-VERIFICATION.md`; evidence `C:/Users/AD/.gstack/projects/PersonalProjectJob-telegram-agent/console-phase2-evidence/`.

Giới hạn thực tế: Windows bỏ qua1file-symlink test do quyền tạo symlink, junction rejection PASS. Hai systemd units baseline dùng EnvironmentFile; Console không đọc file có thể chứa secrets để đoán source/config effective, nên installed compatibility là unverified và Save/Apply bị khóa ngay cả khi package hash khớp. Đây là fail-closed D4-A, không phải chứng nhận host sẵn sàng. Không thay unit/env/worker để bỏ guard. Chỉ Linux fixtures/fake runner được test, không native installed host/THINKPAD.

Harness runner đã thử thật exit2: `Không thấy package.json ... — harness-verify hiện chỉ hỗ trợ project Node.` Không thêm package.json để né, không tạo formal harness-gate PASS. Python/Node/HTTP/ZIP/browser script gates thực tế cùng independent review/verifier là bằng chứng của đợt2; task manifest giữ handoff thay vì done giả. State/progress trong `.agent-tasks/T-261015-console-phase2-registry-form-and-compati.md` và `.context/console-fe-be-phase2/progress.log`.

Tại handoff đợt2, S5-S6/T6-T7 còn đợt3: final contract drift/update gate, đầy đủ worker CLI dependency closure/ZIP release và CI Linux/Windows fixtures. Third-worker proof và phase2 portable regression đã làm để verify registry, không tự coi đợt3 hoàn tất.

### Architecture findings

1. [P1, confidence 10/10, D3 resolved] Draft chỉ xem có thể làm mất Apply hiện có. Evidence: `console-ui/backend.py:433` gọi `result=self.runner(['systemctl','--no-ask-password','restart',unit],capture_output=True,timeout=165)`. Owner chốt giữ thao tác từ đúng host/source như §2; chưa thực thi thao tác thật.
2. [P1, confidence 9/10, D4 resolved] Cùng host chưa chứng minh Console source khớp worker wheel. Cả hai `pyproject.toml:7` đều ghi `version = "1.0.0"`; `backend.py:279` chỉ kiểm có `receipt.py` và systemd. Receipt `ready()` ghi `{'revision': revision, 'pid': os.getpid(), 'readyAt': time.time(), 'mode': config.mode}`; không có commit/build identity. Owner chọn hash gate read-only; enforcement được ghi ở §6.

### Code-quality finding D5 resolved

[P2, confidence 9/10] Baseline `test_backend.py:18-19` import `backend`/`run`, dòng 163 patch `backend.time`. Owner chọn D5-A: launcher compatibility lâu dài; backend facade chỉ ở đợt A, bỏ sau migrate internal tests ở đợt B; một implementation BE. Review còn đánh giá DRY/mapping, errors, module responsibilities và duplicate worker helpers: descriptors xử lý hardcode, guards/transactions giữ nguyên, shared worker helper extraction bị loại khỏi scope để giữ worker bất biến. Không có thêm quyết định code-quality chưa chốt.

### Completion summary

- Scope: full scope accepted, phased A/B/C; không cắt registry/form/compatibility/ZIP.
- Architecture: 2 findings, D3/D4 đã được Owner chốt và ghi enforcement trong plan.
- Code quality: 1 finding, D5 đã được Owner chốt; không còn dual implementation/facade lifetime ambiguity.
- Tests: diagram + assertion matrix; 5 critical regression requirements, 1 packaging import-closure risk được đưa vào gate. Không test hoặc implementation PASS giả.
- Performance: đã đánh giá memory/reads/cache/slow paths; 0 unresolved issues; v1 schema caps nêu rõ là proposed limits.
- NOT in scope và What already exists: có; no worker/source/production rewrite.
- Failure modes: 0 silent critical gaps left in the PLAN; error handling và assertions bắt buộc trước khi ship code.
- TODOS.md: 0 new follow-up proposals; mọi việc cần cho mục tiêu nằm trong plan, phần ngoài scope không tự tạo backlog.
- Outside voice: skipped optional; không có independent/cross-model verdict.
- Parallelization: 4 workstreams, 2 có thể song song sau contract freeze; mặc định tuần tự.
- Coverage-oriented choices: full scope selected; không dùng điểm số cho D3-D5 vì khác policy/compatibility, không phải số lượng coverage.
- Current state: Master và fallback Visual Gate đã được Owner duyệt; phase1–3 complete locally/uncommitted; Windows fixtures verified, Linux CI configured chưa chạy.

## Implementation Tasks

Đây là danh sách hành động trong Master plan, không phải Tasks Doc đã được tự duyệt. Estimate để chia việc, không phải ETA cam kết. Chưa assign external executor/model.

- [x] **T1 (P2, human ~3h / agent ~45min)** — Tách FE/BE vật lý, giữ CLI/API/transaction parity. Source: Console mixed concerns + D5. Files: Console frontend/backend/tests, launchers; backend.py facade chỉ tạm. Verified phase 1: main53 tests OK(skipped1), extracted launcher parity, browser96PNG+96ARIA equal, protected raw109 unchanged. Estimate ban đầu, không phải effort thực đo.
- [x] **T2 (P1, human ~4h / agent ~60min)** — Tạo registry/descriptors/trusted adapters và public projection. Source: hardcode ids trong app.js/api.js/run.py/backend.py. Files: Console registry/schema/workers/server. Verify: valid/invalid/duplicate/unknown registry + unchanged loader contract tests.
- [x] **T3 (P2, human ~3h / agent ~45min)** — Dựng navigation/form/state từ descriptor, chuyển tests sang package thực và bỏ facade backend.py ở đợt B. Source: hardcode FE + D5. Files: Console FE/state/components/pages/tests. Verify: browser two-worker parity + third fixture + mock target regression.
- [x] **T4 (P1, human ~4h / agent ~60min)** — Enforce correct-host binding + canonical hash gate cho installed Save/Apply; giữ receipt/PID/revision và exact unit guards. Source: D3/D4. Files: Console policy/compatibility/status/actions/packaging metadata. Verify: fake installed matching/mismatch/unreadable, action recheck, zero-side-effect denial, real HTTP -> fake systemctl.
- [x] **T5 (P2, human ~2h / agent ~30min)** — Hiển thị triage fallback/Qwen/thinking từ supported contract; giữ NailHub descriptor riêng. Source: worker/UI field drift hiện có. Files: Console descriptors/FE/tests only. Verify: Visual Gate được duyệt, provider-switch round-trip, negative NailHub fallback test.
- [x] **T6 (P1, human ~3h / agent ~45min)** — Hoàn thành cross-layer/security/browser regressions và immutable worker gate. Source: test review requirements. Files: Console tests, evidence scripts, CI if added. Verify: registry -> render -> save -> reload; stale/concurrent/error flows; hash/diff protected trees empty.
- [x] **T7 (P1, human ~3h / agent ~45min)** — Kiểm packaging dependency closure, drift gate và ZIP portable; cập nhật README. Source: packaging module list + usage import closure + update synchronization goal. Files: Console package/scripts/tests, README, Console CI. Verify: clean extraction with -E -S, no secrets/state, third fixture registration, schema drift failure, Windows/Linux fixture CI.

Tổng ~22h người / ~5h agent, chưa tính chờ Owner/Visual Gate; chia các task <=6h. Không có task sửa hai worker. `_No new tasks from Performance beyond bounds already included in T2/T4._`

Kiểm chứng sau khi code: `python -m unittest discover -s console-ui/tests -p 'test_*.py'` sau migrate; trong đợt A giữ lệnh suite cũ tương ứng. Browser runner command được ghi thật sau discovery, không bịa runner chưa tồn tại. Linux CI dùng fixtures/fake commands cho Apply, không dùng credentials hoặc THINKPAD thật. ZIP verification dùng CLI tương thích `python console-ui/package.py --verify <artifact>` và actual extraction/launch.

Inline diagrams nên nằm ở registry/public projection, canonical hash gate trước action và transaction recovery, để developer thấy luồng trust/side effect tại chỗ.

## 10. Related documents và changelog

- Existing Console Master: [agentfleet-settings-1_master_261014_v1.0.2.md](agentfleet-settings-1_master_261014_v1.0.2.md).
- Existing Console docs: `Docs/01-product/`, `Docs/02-engineering/`, `Docs/03-qa/`, `Docs/04-tasks/` (reference baseline, không tự coi là approval cho refactor này).
- Đợt 1 không thay UI/UX/business logic: triển khai theo scope/technical/test plan trong Master đã duyệt, đối chiếu bộ Console docs hiện có. Không tự tạo hoặc coi child docs cho đợt 2/3 đã được duyệt thiết kế.
- GitHub issue/PR: chưa tạo. Không commit/push/merge trong lượt lập plan.

| Version | Date | Time | Author | Change | Status |
|---|---|---|---|---|---|
| v1.0.0 | 2026-10-15 | 15:53 | AlexChu / Codex executor | Draft FE/BE, Console-owned contracts/adapters, immutable workers, phased migration và test strategy | Review pending |
| v1.0.1 | 2026-10-15 | 15:58 | AlexChu / Codex executor | Owner chọn D2-A đầy đủ theo ba lát cắt; bắt đầu architecture review, phát hiện policy installed mâu thuẫn giữ behavior | D3 pending |
| v1.0.2 | 2026-10-15 | 16:04 | AlexChu / Codex executor | D3 resolved: giữ Save/Apply trên đúng host/source, SSH chỉ là cách truy cập; bỏ policy installed-observe; nêu D4 build compatibility | D4 pending |
| v1.0.3 | 2026-10-15 | 16:07 | AlexChu / Codex executor | D4-A accepted: canonical hash manifest, fail-closed Save/Apply; architecture chốt; code-quality review nêu D5 facade lifecycle | D5 pending |
| v1.0.4 | 2026-10-15 | 16:16 | AlexChu / Codex executor | D5-A accepted; hoàn tất code-quality/test/performance review, assertion matrix, task plan, QA artifact, review-log/read và compatibility/immutable gates | Plan review complete; sign-off pending |
| v1.0.5 | 2026-10-15 | 16:26 | AlexChu / Codex executor | Owner duyệt Master và bắt đầu S0-S1; chọn phối hợp FE/BE high; ghi baseline suite, hash snapshot và fences | Approved; phase 1 active |
| v1.0.6 | 2026-10-15 | 16:49 | AlexChu / Codex controller; isolated team executors/reviewer | Hoàn tất S0-S1/T1; main53tests, protected109raw, exactUI5, controlled browser96PNG+96ARIA và independent review; giữ nguyên worker, no THINKPAD | Phase 1 complete; uncommitted |
| v1.0.7 | 2026-10-15 | 17:08 | AlexChu / Codex controller | Owner yêu cầu đợt 2; baseline53 tests, protected109, frozen contract và BE/FE worktrees; fallback visual preview pending | Phase 2 active |
| v1.0.8 | 2026-10-15 | 18:06 | AlexChu / Codex controller; isolated executors/reviewer/verifier | Hoàn tất đợt2 registry/form/fallback/hash guards; root86Python +8Node, portable post-fix, browser matrix/race smoke; independent APPROVED/PASS, immutable109 | Phase2 complete locally; uncommitted |

### Review artifacts và verification thực tế của lượt lập plan

- `gstack-review-log` và `gstack-review-read` đã chạy: `status=clean`, `scope=plan_only`, `unresolved=0`, `critical_gaps=0`, `issues_found=4`, `commit=6fdb7db`, `implementation_tests=not_run`. Log nằm ở `C:/Users/AD/.gstack/projects/PersonalProjectJob-telegram-agent/codexconsole-fe-be-plan-reviews.jsonl`.
- QA test-plan artifact: `C:/Users/AD/.gstack/projects/PersonalProjectJob-telegram-agent/AlexChu-codexconsole-fe-be-plan-eng-review-test-plan-20261015-1611.md`.
- `git diff --exit-code 6fdb7db -- listener nailhub config deploy systemd .env.example`: exit 0, no output. `git status --short --untracked-files=all` cho các protected paths: no output. Repo status chỉ có Master plan mới; `git diff --check`: exit 0 (plan chưa tracked, markdown plan được kiểm riêng).
- Không chạy browser/tests/build hoặc SSH production trong lượt review này. Test diagram là requirements, không phải measured coverage; không có ảnh evidence hay verdict production PASS.
- `jq` không có trên PATH: task JSONL cho autoplan được skip theo fallback của skill; checklist Markdown đầy đủ trong §Implementation Tasks. Không cài dependency chỉ để tạo artifact phụ.
- `gstack-decision-log` đã thử nhưng lỗi `Cannot find module '../lib/gstack-decision'`; không coi là ghi thành công. Source of truth của các quyết định D2-D5 vẫn là Master này, có owner AlexChu và changelog. Không sửa gstack/harness canonical để khắc phục trong scope này.
- Bản skill cài trên máy không có `sections/review-sections.md`; đã dùng toàn bộ Architecture/Code quality/Test/Performance instructions được nhúng trực tiếp trong SKILL.md, không giả báo đã đọc file không tồn tại.

## GSTACK REVIEW REPORT

| Review | Runs | Status | Findings |
|---|---|---|---|
| Scope challenge | 1 completed | D2-A accepted | Full scope, ba lát cắt; giữ nguyên worker/protected paths |
| Architecture | 1 completed | Decisions resolved | 2 findings P1 folded: host-bound actions + canonical hash compatibility gate |
| Code quality | 1 completed | Decisions resolved | 1 finding P2 folded: launchers preserved, backend facade retired after B |
| Tests | 1 plan audit | Requirements complete | Diagram + test matrix; 5 mandatory regression requirements, packaging closure gate; execution NOT RUN |
| Performance | 1 plan audit | Requirements complete | Bounded schemas/reads/hashes; no automatic AI or DB calls; benchmark NOT RUN |
| Outside voice | 0 | Skipped optional | Không có independent/cross-model verdict |
| Phase 1 source review | 1 independent | APPROVED | Physical split only, behavior/security/package parity; no actionable findings |
| Phase 1 verification | Main + isolated review | PASS with documented skip | Main53 tests OK(skipped1); immutable109raw; UI5raw; controlled96PNG+96ARIA equal; no production test |
| Phase 2 source review | Independent, iterative | APPROVED | Six findings closed; registry/form/policy/async races reviewed |
| Phase 2 verification | Root + independent verifier/browser | PASS with documented limits | Root86Python OK(skipped1), post-JS8Node+2ZIP PASS; browser4viewport/races PASS; immutable109raw; formal Node-only harness gate unsupported |

VERDICT: ENG REVIEW CLEAR (PLAN); PHASES 1–3 IMPLEMENTED AND VERIFIED locally with documented Windows symlink skip and installed EnvironmentFile fail-closed limitation. Phase3 independent source review APPROVED and verifier PASS; root107Python OK, FE8/8, final ZIP actual smoke PASS. Linux CI configured, not executed. Formal Node-only harness gate unsupported; no production change or commit/push.

NO UNRESOLVED DECISIONS; phase3 complete locally, human-controlled commit/push/deployment not performed.

Tracking limitation at final handoff: task manifest version7 lease AlexChu expired before heartbeat renewal; tool rejected heartbeat and read-only next proposed changing owner to codex. No takeover executed because Owner requires AlexChu identity. No manual frontmatter/CAS bypass or false done. Complete implementation/verification/handoff remains in this Master, report and .context/HANDOFF.md; formal harness Node-only gate also unsupported.

### Phase3 execution — Owner approved; complete locally

Owner yêu cầu đợt3 gate tương thích/drift và ZIP, giữ nguyên worker, không THINKPAD và chưa commit/push. Frozen contract ở `.context/console-fe-be-phase3/CONTRACT.md`, progress/snapshots/logs cùng thư mục. A/B isolated worktrees; independent reviewer. Baseline Python86 OK(skipped1), Node8 PASS, protected109raw exact. Source runtime policy/UI giữ nguyên.

Readonly fetch phát hiện origin/main `ed2998a` (PR42) mới hơn approved baseline `6fdb7db`, sửa Triage group_notices. Không merge/pull bytes worker trong phase này. Gate vẫn pin baseline đã review; bytes PR42 cần review lại trước khi được chấp nhận. CI Windows/Ubuntu được cấu hình nhưng chưa chạy remote; local Docker/WSL chưa có Python Linux usable, không claim Linux PASS.

### Phase3 final handoff — 2026-10-15 18:45 Asia/Saigon

- T6/T7 complete locally: stdlib read-only contracts CLI and explicit review record, current source/Git pin/archive drift validation, exact packaging registration/import closure, missing usage.py inclusion, offline extended third-worker proof, ZIP release and Windows/Ubuntu Python3.11/3.12 CI definition. No worker/API/UI/policy modification.
- Root final: `python -m unittest discover -s console-ui/tests -p 'test_*.py' -v` — **Ran107 tests in204.027s, OK(skipped1)**. Node custom FE regression **8/8 PASS**. Root source CLI and final ZIP verify PASS.
- Independent source review **APPROVED**, zero unresolved findings. Reviewer found import-from-package and initializer self-justification gaps; executor fixed, regression reproduced old defects then rejected own/repeated/cyclic imports while allowing legitimate exports. Independent verifier **PASS**: final package18 tests49.247s OK(skipped1), actual NEW archive noGit `-E -S` checker/packager, both worker CLI help, Console bootstrap/save both/stop/relaunch/reload, no real jobs/probes/apply.
- Final artifact: `dist/agentfleet-console-phase3-final-261015.zip`,269367bytes,79entries including78source+manifest, SHA256 `a62ee84aea3425bcd3e0cdbf8cf223ebf2181a3dbd333d83f927eaaac62897b5`. Earlier ZIP and106test run are historical superseded evidence, not final artifact claims.
- Root/verifier protected109 and original FE/BE runtime23 rawSHA256 unchanged; final verifier frozen37 exact; protected Git diff/status and staged content empty. HEAD remains approved6fdb7db. Actual PR42 origin/main ed2998a worker blob substituted only in TEMP/in-memory fixture is rejected as drift. No merge of newer worker changes.
- CI is configured, not executed. Windows Python3.12 locally verified;1file-symlink test skipped by OS privilege, junction rejection passes. Docker daemon unavailable; only DockerDesktop WSL without Python/Node discovered, no Linux PASS claim. Formal Node-only harness gate remains unsupported; no false formal gate/status owner takeover. EnvironmentFiles continue to block installed Save/Apply when unverified; no change to running THINKPAD.
- Durable report: `console-ui/PHASE3-VERIFICATION.md`; source review/verifier/progress/snapshots/commands and raw Git status in `.context/console-fe-be-phase3/`. No commit/push/merge/deployment; isolated worktrees retained for review without creating archive snapshot commits.

| Version | Date | Time | Author | Change | Status |
|---|---|---|---|---|---|
| v1.0.9 | 2026-10-15 | 18:45 | AlexChu / Codex controller; isolated executors/reviewer/verifier | Phase3 drift/closure/ZIP/CI; root107Python+8Node, independentAPPROVED/PASS, finalarchive actual smoke, protected109/runtime23 unchanged | Phases1–3 complete locally; uncommitted; Linux CI not run |
