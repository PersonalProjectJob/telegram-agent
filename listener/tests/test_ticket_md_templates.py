"""Keep Docs/templates/github-ticket/*.md in step with the renderer the daemon uses.

The Markdown templates are what people and agents copy when they write a ticket by hand; the
code in issue_format/followups/group_notices is the source. These checks fail when either
side changes headings, field labels, standard questions, labels or fixed notice wording alone.
"""

import json
import re
import unittest
from pathlib import Path

from tg_triage import issue_format
from tg_triage.classifier import parse_case_analysis
from tg_triage.issue_format import format_issue
from tests.helpers import message
from tests.test_issue_templates import (BUG_DETAILS, DOCUMENT, FEEDBACK_DETAILS, IMAGE, TASK_DETAILS,
                                        case_payload)

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "Docs" / "templates" / "github-ticket"
SOURCE = ROOT / "listener" / "src" / "tg_triage"
FULL_FEEDBACK = dict(FEEDBACK_DETAILS, proposal="Thêm bộ lọc", alternatives="Xuất CSV", context="Đối soát cuối ngày")
FULL_TASK = dict(TASK_DETAILS, reason_context="Kế toán cần", scope_in="Màn giao dịch", scope_out="Báo cáo",
                 due="Thứ sáu", links=["https://example.invalid/spec"])


def template(name):
    return (TEMPLATES / name).read_text(encoding="utf-8")


def headings(text):
    return [line for line in text.splitlines() if line.startswith("### ")]


def header_labels(text, prefix="labels:"):
    line = next(line for line in text.splitlines() if line.strip().startswith(prefix))
    return {label.strip() for label in line.split(":", 1)[1].split(",")}


def render(category, details):
    analysis = parse_case_analysis(json.dumps(case_payload(category, details), ensure_ascii=False))
    return format_issue(message(), analysis.classification, "model", False, details=analysis.details,
                        images=(IMAGE,), documents=(DOCUMENT,), clarify=("Câu hỏi?",))


def fenced(text, language):
    return re.findall(r"```" + language + r"\n(.*?)\n```", text, re.S)


class TicketTemplateTests(unittest.TestCase):
    def test_type_templates_match_rendered_sections_and_labels(self):
        for name, category, details in (("bug.md", "bug", BUG_DETAILS), ("task.md", "task", FULL_TASK),
                                        ("feedback.md", "feedback", FULL_FEEDBACK)):
            with self.subTest(name=name):
                text, issue = template(name), render(category, details)
                self.assertEqual(headings(text), headings(issue.body))
                labels = {"feedback" if label == "feature" else label for label in issue.labels}
                self.assertEqual(header_labels(text), labels)
                self.assertIn("<!-- tg:{{chat_id}}:", text)
                self.assertIn("tạo tự động bởi tg-triage", text)
                self.assertIn(issue_format.ORIGINAL_ICON + " " + issue_format.ORIGINAL_TITLE, text)

    def test_field_labels_and_fixed_lines_come_from_the_renderer(self):
        known = {value for key, value in vars(issue_format).items() if key.startswith("LABEL_")}
        for name in ("bug.md", "task.md", "feedback.md"):
            with self.subTest(name=name):
                used = set(re.findall(r"^\*\*([^*{]+):\*\*", template(name), re.M))
                self.assertTrue(used <= known, used - known)
        self.assertIn(issue_format.DOD_EVIDENCE_LINE, template("task.md"))
        for name in ("bug.md", "task.md", "feedback.md"):
            self.assertIn(issue_format.NO_DETAIL_LINE, template(name))

    def test_standard_questions_are_quoted_verbatim(self):
        for question in issue_format.BUG_QUESTIONS:
            self.assertIn(question, template("bug.md"))
        for question in issue_format.FEEDBACK_QUESTIONS:
            self.assertIn(question, template("feedback.md"))
        self.assertIn(issue_format.TASK_WORK_QUESTION, template("task.md"))

    def test_needs_human_matches_untyped_rendering(self):
        text = template("needs-human.md")
        issue = format_issue(message(), None, "model", True, text=("Chưa rõ", "Chưa rõ"),
                             clarify=("Nút nào?",), details={})
        self.assertEqual(headings(text), headings(issue.body))
        self.assertEqual(header_labels(text), set(issue.labels))
        self.assertTrue(issue.title.startswith("[cần phân loại] "))
        self.assertIn("[cần phân loại]", text)

    def test_comment_and_notice_wording_exists_in_code(self):
        code = "\n".join(path.read_text(encoding="utf-8") for path in SOURCE.glob("*.py"))
        code = re.sub(r"([\"'])\s*\n\s*\1", "", code)  # join implicitly concatenated literals
        for block in fenced(template("comments.md"), "markdown"):
            for line in block.splitlines():
                if line.startswith("### ") or line.split("{{")[0].strip().endswith(":"):
                    fixed = line.split("{{")[0].strip()
                    with self.subTest(line=fixed):
                        self.assertTrue(fixed in code, fixed)
        for block in fenced(template("group-replies.md"), "text"):
            for line in block.splitlines():
                for part in re.split(r"\{\{[^}]*\}\}", line):
                    part = part.strip(" •")
                    if len(part) > 8:
                        with self.subTest(part=part):
                            self.assertTrue(part in code, part)

    def test_readme_lists_every_template(self):
        readme = template("README.md")
        for path in TEMPLATES.glob("*.md"):
            if path.name != "README.md":
                self.assertIn(f"]({path.name})", readme)


if __name__ == "__main__":
    unittest.main()
