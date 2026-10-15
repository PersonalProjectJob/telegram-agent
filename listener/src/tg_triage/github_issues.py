"""Create an issue once, with fixed labels and safe error messages."""

import json
import logging
import re
from http import HTTPStatus
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .models import LABELS

LOG = logging.getLogger(__name__)


class GitHubError(RuntimeError):
    """GitHub failure that never contains response bodies or credentials."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GitHubIssues:
    def __init__(self, repo: str, token: str, *, opener=None):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise ValueError("GH_REPO: expected owner/repo")
        if not token.strip() or any(ord(ch) < 32 or ord(ch) == 127 for ch in token):
            raise ValueError("GH_TOKEN: expected non-empty token without control characters")
        self.repo = repo
        self._token = token
        self.opener = build_opener(_NoRedirect()).open if opener is None else opener

    def create_issue(self, issue) -> tuple[int, str | None]:
        if not issue.labels or not set(issue.labels) <= LABELS:
            raise ValueError("Issue labels must come from the fixed label set")
        body = {"title": issue.title, "body": issue.body, "labels": list(issue.labels)}
        request = Request(
            f"https://api.github.com/repos/{self.repo}/issues",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
            headers={"Authorization": f"Bearer {self._token}",
                     "Accept": "application/vnd.github+json",
                     "Content-Type": "application/json",
                     "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with self.opener(request, timeout=30) as response:
                data = json.loads(response.read())
            if not isinstance(data, dict):
                raise ValueError
            number = data["number"]
            url = data.get("html_url")
            if type(number) is not int or number <= 0:
                raise ValueError
            expected = f"https://github.com/{self.repo}/issues/{number}"
            if not isinstance(url, str) or url.casefold() != expected.casefold():
                LOG.warning("GitHub URL mismatch issue_number=%s", number)
            # A usable number proves creation even if optional URL metadata is bad.
            # Never echo the returned URL in diagnostics.
            if not isinstance(url, str):
                url = None
            return number, url
        except HTTPError as error:
            code = error.code
            try:
                phrase = HTTPStatus(code).phrase
            except ValueError:
                phrase = "HTTP failure"
            error.close()
            raise GitHubError(f"GitHub HTTP {code}: {phrase}") from None
        except (URLError, OSError):
            raise GitHubError("GitHub connection failed") from None
        except (ValueError, TypeError, KeyError):
            raise GitHubError("GitHub returned an invalid response") from None

    def add_comment(self, issue_number, body):
        """Append once; the caller durably reserves this POST and never retries unknown outcomes."""
        if type(issue_number) is not int or issue_number <= 0 or not isinstance(body, str) or not body.strip():
            raise ValueError("Invalid issue comment")
        request = Request(
            f"https://api.github.com/repos/{self.repo}/issues/{issue_number}/comments",
            data=json.dumps({"body": body}, ensure_ascii=False).encode("utf-8"), method="POST",
            headers={"Authorization": f"Bearer {self._token}",
                     "Accept": "application/vnd.github+json", "Content-Type": "application/json",
                     "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with self.opener(request, timeout=30) as response:
                data = json.loads(response.read(1024 * 1024 + 1))
            comment_id = data.get("id") if isinstance(data, dict) else None
            if type(comment_id) is not int or comment_id <= 0:
                raise ValueError
            return comment_id
        except HTTPError as error:
            error.close()
            raise GitHubError("GitHub comment HTTP failure") from None
        except (URLError, OSError, ValueError, TypeError):
            raise GitHubError("GitHub comment result is unknown") from None
