# AgentFleet console

Portable settings console for Telegram Triage and NailHub Worker. Requires Python **3.11 or newer**. The local console uses only Python's standard library; Git, npm and service dependencies are unnecessary to open the UI, save settings or test a provider.

## Run from source or an extracted ZIP

Open a terminal in this folder:

```sh
python console-ui/run.py
```

Windows can also run `start-console.cmd`; Linux/macOS can run `sh start-console.sh`. The scripts locate their source relative to themselves, so paths with spaces work. On Linux/macOS the launcher uses `python3`. Use an unprivileged account.

The terminal prints the loopback URL and opens your browser. `--no-open` disables browser launch; `--port 0` selects a free port. Use the exact printed URL. Stop with Ctrl+C.

Default settings, revision history and protected credential files persist in `.agentfleet/` beside this README. They belong to the extracted copy; reopening that copy retains them. `--state-dir PATH` selects a dedicated private directory. Windows protects that directory's ACL and Unix uses mode0700: do not point it at a shared folder containing other data. Back up state privately, separately from distributable ZIPs.

Ollama defaults to the local host. Cloud probes are real synthetic JSON calls and may incur provider charges; task data reaches a cloud model only after explicit consent. API keys are protected by OS permissions, not encrypted at rest. Each service keeps its own provider/endpoint credential scope.

The default **local** profile writes local configuration only; it does not start or restart installed Telegram/NailHub services. The ZIP is a console source distribution, not a service installer. Running the workers still requires their separate environments, account/session/profile setup and verified business contract. Installed Linux apply additionally requires upgraded runtimes, OS provisioning and matching readiness receipts; see [console usage](console-ui/README.md) and the full repository deployment documentation. The source preserves NailHub BR-13 age checks and BR-14 publisher branding; packaging does not authorize external mutations.

## Build and verify a portable ZIP

```sh
python console-ui/package.py --output dist/agentfleet-console.zip
python console-ui/package.py --verify dist/agentfleet-console.zip
```

Packaging reads current source bytes, including reviewed edits that have not been committed. It uses an exact allowlist of UI assets, runtime Python source, prompts and example TOML. Actual config, keys, `.env`, `.agentfleet`, sessions, profiles, tests, caches, private evidence and Git metadata are excluded. Symlinks/reparse points are refused. Existing output is never overwritten: choose a new filename for the next build. Inside this source folder, outputs must be under `dist/`.

Extract the ZIP into a new folder, open its `agentfleet-console` subfolder and run the command above. No Git checkout, virtualenv or dependency install is needed for the console. `package-manifest.json` records every bundled source file's SHA256 and byte length; verification detects changes but is not a signed authenticity guarantee. ZIP paths use forward slashes and launchers work after relocation. The packager prints the archive SHA256 for transfer verification.

## Telegram triage documentation

[Canonical business rules TG-01..TG-33](Docs/01-product/telegram-triage-noissue_business_261015_v1.0.0.md) describe the reviewed deployed behavior. [THINKING research](Docs/02-engineering/telegram-triage-noissue_thinking-research_261015_v1.0.0.md) is a proposal; [listener operations](listener/README.md) covers runtime commands.

Documentation update v1.0.0 — 2026-10-15 21:12 (Asia/Saigon), owner AlexChu: add canonical triage entry; no runtime changes.
