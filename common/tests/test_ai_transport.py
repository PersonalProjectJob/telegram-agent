"""Pure transport contracts ported from listener; no service dependencies."""
import unittest
from agentfleet_common import ai_transport
from agentfleet_common.ai_transport import ProviderError, complete, usage_of

import json

CASE = json.dumps({"verdict": "ticket", "type": "bug", "title": "Lỗi đăng nhập", "summary": "Không đăng nhập được.",
                   "priority": "high", "confidence": 0.9, "key_message_ids": [1]}, ensure_ascii=False)


DEEPSEEK_USAGE = {"prompt_tokens": 3024, "completion_tokens": 1300, "prompt_cache_hit_tokens": 1000,
                  "prompt_cache_miss_tokens": 2024}


OLLAMA_ENVELOPE = {"message": {"content": CASE}, "prompt_eval_count": 410, "eval_count": 77}


def deepseek_envelope(content=CASE, usage=None):
    return {"choices": [{"message": {"content": content}}], "usage": usage or DEEPSEEK_USAGE}


class Upstream:
    """Fake hosted API: records every payload, answers from a script (dict, or an exception to raise)."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, url, payload, headers, timeout, provider):
        self.calls.append((url, payload, headers, provider))
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


class HostedImageTests(unittest.TestCase):
    def test_complete_validates_images_and_raises_the_input_limit_only_for_them(self):
        parts = [{"type": "text", "text": "t"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]
        ok = Upstream(deepseek_envelope("{}"))
        big = [{"role": "user", "content": [parts[0], {"type": "image_url", "image_url": {
            "url": "data:image/png;base64," + "A" * (1024 * 1024)}}]}]
        self.assertEqual(complete("deepseek", "https://api.deepseek.com", "m", big, {}, key="k", transport=ok), {})
        with self.assertRaises(ProviderError) as error:
            complete("deepseek", "https://api.deepseek.com", "m", [{"role": "user", "content": "x" * (129 * 1024)}],
                     {}, key="k", transport=ok)
        self.assertEqual(error.exception.code, "input_too_large")
        huge = [{"role": "user", "content": [parts[0], {"type": "image_url", "image_url": {
            "url": "data:image/png;base64," + "A" * (21 * 1024 * 1024)}}]}]
        with self.assertRaises(ProviderError) as error:
            complete("deepseek", "https://api.deepseek.com", "m", huge, {}, key="k", transport=ok)
        self.assertEqual(error.exception.code, "input_too_large")
        for provider, base, code in (("anthropic", "https://api.anthropic.com", "images_unsupported"),
                                     ("deepseek", "https://api.deepseek.com", "invalid_image")):
            bad = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "https://evil.example/x.png"}}]}]
            if provider == "anthropic":
                bad = [{"role": "user", "content": parts}]
            with self.subTest(provider=provider), self.assertRaises(ProviderError) as error:
                complete(provider, base, "m", bad, {}, key="k", transport=ok)
            self.assertEqual(error.exception.code, code)
        self.assertEqual(len(ok.calls), 1)


class RedactTests(unittest.TestCase):
    def test_timestamps_and_bracketed_ids_survive_real_contact_data_does_not(self):
        keep = ("[416] LEO Nguyen · 2026-10-15 08:51 [kèm ảnh]: nên thêm ký tự",
                "[123456789] Lan · 2026-10-15 08:51:30: ok",
                "[123456789] [trả lời 123456780] Lan · 2026-10-15 08:51: ok",
                "họp lúc 20:30 ngày 15.10.2026 hoặc 2026-10-15",
                "TOÀN BỘ CASE, các ID bắt buộc: 12345678, 12345679. Đọc tất cả")
        for text in keep:
            with self.subTest(text=text):
                self.assertEqual(ai_transport.redact(text), text)
        for phone in ("0912 345 678", "+84 912 345 678", "0912.345.678", "(028) 3822 1234", "0912345678", "0912-345-678"):
            with self.subTest(phone=phone):
                self.assertEqual(ai_transport.redact(f"gọi {phone} nhé"), "gọi [phone] nhé")
        self.assertEqual(ai_transport.redact("mail owner@example.com"), "mail [email]")
        # a phone typed right before a time keeps the time; a phone in brackets inside the text is still hidden
        self.assertEqual(ai_transport.redact("0912345678 08:51"), "[phone] 08:51")
        self.assertEqual(ai_transport.redact("x [0912345678] y"), "x [[phone]] y")


    def test_a_full_transcript_line_with_time_long_id_and_phone(self):
        line = ("[123456789] [trả lời 123456780] Lan · 2026-10-15 08:51 [kèm ảnh]: "
                "gọi 0912 345 678 hoặc owner@example.com lúc 20:30")
        self.assertEqual(ai_transport.redact(line), "[123456789] [trả lời 123456780] Lan · 2026-10-15 08:51 [kèm ảnh]: "
                                                    "gọi [phone] hoặc [email] lúc 20:30")


class UsageSinkTests(unittest.TestCase):
    def test_sink_receives_deepseek_tokens(self):
        seen = []
        complete("deepseek", "https://api.deepseek.com", "m", [{"role": "user", "content": "x"}], {}, key="k",
                 transport=Upstream(deepseek_envelope("{}")), usage_sink=seen.append)
        self.assertEqual(seen, [{"prompt_tokens": 3024, "completion_tokens": 1300, "cache_hit_tokens": 1000}])


    def test_sink_receives_ollama_tokens(self):
        seen = []
        complete("ollama", "http://127.0.0.1:11434", "m", [{"role": "user", "content": "x"}], {},
                 transport=Upstream(OLLAMA_ENVELOPE), usage_sink=seen.append)
        self.assertEqual(seen, [{"prompt_tokens": 410, "completion_tokens": 77, "cache_hit_tokens": 0}])


    def test_sink_is_optional_called_even_for_unparsable_content_and_cannot_break_the_call(self):
        seen = []
        with self.assertRaises(ProviderError):
            complete("deepseek", "https://api.deepseek.com", "m", [], {}, key="k",
                     transport=Upstream(deepseek_envelope("nope")), usage_sink=seen.append)
        self.assertEqual(seen[0]["prompt_tokens"], 3024)
        def broken(_):
            raise RuntimeError("sink")
        self.assertEqual(complete("deepseek", "https://api.deepseek.com", "m", [], {}, key="k",
                                  transport=Upstream(deepseek_envelope("{}")), usage_sink=broken), {})
        self.assertEqual(complete("deepseek", "https://api.deepseek.com", "m", [], {}, key="k",
                                  transport=Upstream(deepseek_envelope("{}"))), {})


    def test_odd_usage_shapes_give_zeros(self):
        for envelope in ({}, {"usage": []}, {"usage": {"prompt_tokens": "9", "completion_tokens": -1}}, []):
            with self.subTest(envelope=envelope):
                self.assertEqual(usage_of("deepseek", envelope),
                                 {"prompt_tokens": 0, "completion_tokens": 0, "cache_hit_tokens": 0})


