---
type: doc
title: "NailBlog changelog — S8 dọn code Agy-only"
owner: AlexChu
created: 2026-10-27
status: draft
---

# Changelog: NailBlog S8 (dọn code, tài liệu, finding LOW)

Version: v1.0.0 · 2026-10-27 · Căn cứ: [Plan v2.0](../04-tasks/nailhub-blog-noissue_tasks_261027_v2.0.0.md) Stream S8 · [Engineering](../02-engineering/nailhub-blog-noissue_engineering_261027_v1.0.0.md) · [QA](../03-qa/nailhub-blog-noissue_qa_261027_v1.0.0.md).

## Đã gỡ

- **File:** `ai_transport.py` (provider transport anthropic/openai/deepseek/ollama/compatible, đọc khóa) → thay bằng `net.py` chỉ có `resolve` và `PinnedHTTPS`. `tests/test_no_paid_text.py` gộp vào `tests/test_no_paid_paths.py`.
- **Config (dataclass):** `BudgetConfig.daily_usd` và các bảng giá (`text_prices`, `image_prices`, `image_output_prices`, `image_unit_prices`); `ImageConfig` model/khóa/trần/kích thước/`key_source`; `TextModelConfig.url/think/key_file/num_predict/section_thinking`; `AutoflowConfig.run_usd`; `CloudflareConfig` và field `cloudflare`; hằng `KEY_SOURCES`, `UNIT_PRICE_SIZES`. Key cũ trong TOML vẫn bị từ chối bằng `removed_key:<path>`.
- **Service:** `Service.model_call` (đường gọi model có khoá/ghi sổ cũ), nhánh `provider=None` giữ `MODEL_LOCK`; tham số `call` và `**_ignored` của `TextModel`; kwarg `key_reader` của `Runtime`.
- **Usage:** `text_cost`, `image_cost`, `UnpricedModel`, `UsageRecorder.record_text/record_image`; `staging_immediate` không còn so `account_id` Cloudflare; `autoflow` không còn đọc `run_usd` (cột DB giữ, ghi 0).
- **AGENTS.md:** mô tả Gemini key, `image-compare`, bằng chứng Cloudflare, lệnh `one-post`, trần 2 USD/ngày.

## Đã sửa (finding verify S4/S4b)

- **F1** thêm test mức `OnePost.approve`: Draft đổi sau override bị từ chối dù còn row approved cũ.
- **F3** Create UNKNOWN, đúng 1 Draft nhưng read-back lệch → `create_requires_manual_reconciliation` + DM, không retry.
- **F4** bỏ `**_ignored`; seam đổi tên `_test_transport`, production luôn truyền `None` (có test).
- **F5** xoá `model_call` (không còn ai gọi).
- **F6** `send` của outbox bọc `asyncio.wait_for(…, 30)`: treo = lỗi gửi, vào backoff, scheduler vẫn chạy `publish_pass`.
- **F8** một hằng `NEVER_OVERRIDABLE` ở `override_policy.py`, `writer.py` và `approvals.py` import từ đó.

## Thêm

- `net.py`, `override_policy.py`, `tests/test_no_paid_paths.py`, `tests/test_s8_findings.py`; docs engineering/QA/changelog này.
