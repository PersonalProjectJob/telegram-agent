# Nailhub Source Mirror

This repository includes a snapshot of the deployed Source Mirror runtime: 96 Python modules under `source_mirror/`, 21 service/socket/timer definitions in this directory, and two scripts under `standalone/` invoked by separate services. Additional modules and a service definition are preserved from earlier work and are not necessarily deployed. The snapshot is not an automated installer or backup. Service definitions require adaptation and validation for each host.

## Publishing workflow

The deployed scheduler uses the `Asia/Ho_Chi_Minh` time zone and four daily slots. The intended topic policy is business and operations at **08:00, 16:00 and 20:00**, and nail designs at **12:00**. A scheduled article must pass the active content, source, duplicate, quota and time checks. A slot can remain unfilled; a schedule is not evidence that an article was published. Some dated assignments exist in operational state and are not included in this source snapshot.

For eligible AI-original articles, the system attempts 3–5 contextual body illustrations based on the title, H2 headings and section content. The body-image stage is optional: failures or time limits can leave fewer or no illustrations. The current budget is at most 360 seconds total and 65 seconds per generation call, subject to remaining time in the slot. It preserves time for upload and publishing. Source attribution is retained, and generation provenance is internal. Real-world successful publication and public readback with these illustrations have not yet been established by the canary.

An AI-original factual review may return a valid HOLD with an empty source quote for a statement it cannot support. That HOLD can trigger the single permitted correction; every nonempty quote must still match the reference exactly. A corrected article must pass a fresh factual review and independent adjudication, and a PASS never permits missing or fabricated quotes. On 2026-11-04, a real fraud-prevention draft entered correction through this path but remained held: its next review reported PASS with two quotes that did not occur verbatim in the source. The strict PASS gate rejected it; no article was staged or scheduled from that attempt.

## Observed runtime dependencies

The observed host used Python 3.14.4 for both environments. Its worker virtual environment contained Pillow 13.0.0 and Playwright 1.63.0; system Python contained Pillow 12.1.1 and cryptography 46.0.5. `requirements.txt` contains the existing unpinned worker baseline, including its Windows-only timezone dependency; `requirements-producer.txt` lists the producer libraries observed from imports. They are references, **not a lockfile** or a claim of a complete install. Playwright browser binaries, OS services and identities, OpenSSL, and the separate AGY CLI/subscription environment must also be provisioned and verified.

## Configuration and recovery limits

The worker checks configuration fields including `data_root`, `timezone`, `slots`, `source_url`, `localization`, and `topics`; optional capabilities read additional settings and policy paths. These names are derived from source and are not a complete validated schema. Configuration and authorization must be designed and provisioned for the target environment; do not substitute defaults or reuse test policies as production authorization.

This snapshot intentionally excludes live configuration values, private keys, authorization grants, credentials, browser profiles and sessions, databases, article snapshots, operational queues, and actual scheduled assignments. Restoring it alone **cannot recover or safely restart the service**. It also cannot establish that an article was sent or published. Review unit paths and external services before deployment; never commit real keys, profiles, databases or credentials.

## Verification scope

All 96 exported package modules compiled in memory on the source host, and selected worker and producer entrypoints imported in their respective environments. The two standalone scripts also compiled. A conditional review and isolated canary exercised one real generated illustration through upload and both locale renderings, stopping before Create. The tests do not establish complete deployment reproducibility or a successful production publish with inline images.

## Test-suite compatibility at sync

On 2026-11-04 the deployed runtime snapshot was synced into this worktree while preserving the existing test suite from earlier work. On Windows, `python -m unittest discover -s source-mirror/tests -p test_*.py -q` ran 395 tests: 13 failures, 43 errors and 18 skipped. The older tests and the deployed runtime differ in fixtures and in some behavior; these results have not been fully triaged, and neither the tests nor the production behavior should be assumed correct from this snapshot. The previous workspace state was preserved in the preceding commit. Candidate-only tests for the newer inline-image, duplicate and schedule changes were run separately and are not part of this committed suite.
