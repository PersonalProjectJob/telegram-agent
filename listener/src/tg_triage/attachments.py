"""Image attachments for case issues (Assistant definition 12.8).

Images are downloaded through an injected `downloader`, validated, uploaded to a
per-case GitHub release through an injected `uploader`, and embedded by code as
Markdown. Nothing here imports Telethon. Log lines carry counts and exception class
names only: never file names, URLs, message text or tokens.
"""

import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

LOG = logging.getLogger(__name__)

ALLOWED_MIME = frozenset(("image/jpeg", "image/png", "image/webp"))
ALT_TEXT = "Ảnh"
_SAFE_URL = re.compile(r"https://[A-Za-z0-9._~/%+-]+")


class AttachmentError(RuntimeError):
    """Attachment failure whose message never carries remote output or credentials."""


class UploadError(AttachmentError):
    pass


class DownloaderUnavailable(AttachmentError):
    pass


class UploaderUnavailable(AttachmentError):
    pass


class AttachmentUploader(Protocol):
    """Anything with these two methods can upload case images (a fake in tests)."""

    def ensure_release(self, tag: str) -> None:
        """Create the release for `tag` when it does not exist yet."""

    def upload(self, tag: str, path: Path, asset_name: str) -> str:
        """Upload `path` as `asset_name` and return its public asset URL."""


def release_tag(case_key: str) -> str:
    """`attachments-<case_key>`; ':' is not valid in a git tag, so it becomes '_'."""
    return "attachments-" + re.sub(r"[^A-Za-z0-9._-]", "_", case_key)


def asset_name(message_id: int, ordinal: int, extension: str) -> str:
    return f"{int(message_id)}-{int(ordinal)}.{extension}"


def sniff_extension(path) -> str | None:
    """Detect JPG/PNG/WEBP from magic bytes, never from names or Telegram metadata."""
    try:
        with open(path, "rb") as stream:
            head = stream.read(16)
    except OSError:
        return None
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


class GhCliUploader:
    """Upload through the `gh` CLI. argv is a list (no shell); the token goes via env only."""

    def __init__(self, repo, token, *, runner=subprocess.run, executable=None, timeout=120):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise ValueError("GH_REPO: expected owner/repo")
        if not token.strip() or any(ord(ch) < 32 or ord(ch) == 127 for ch in token):
            raise ValueError("GH_TOKEN: expected non-empty token without control characters")
        self.repo = repo
        self._token = token
        self._runner = runner
        self._executable = executable
        self._timeout = timeout

    def _run(self, *arguments):
        executable = self._executable or shutil.which("gh")
        if executable is None:
            raise UploadError("gh executable not found")
        env = {**os.environ, "GH_TOKEN": self._token, "GH_PROMPT_DISABLED": "1", "NO_COLOR": "1"}
        try:
            completed = self._runner(
                [executable, *arguments], env=env, capture_output=True, timeout=self._timeout,
                check=False, shell=False, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            # Do not chain: the original exception text could echo argv or output.
            raise UploadError("gh could not be run") from None
        return completed.returncode

    def ensure_release(self, tag):
        if self._run("release", "view", tag, "--repo", self.repo) == 0:
            return
        code = self._run("release", "create", tag, "--repo", self.repo,
                         "--title", tag, "--notes", "Ảnh đính kèm tự động từ tg-triage",
                         "--latest=false")
        if code != 0:
            raise UploadError("gh release create failed")

    def upload(self, tag, path, asset):
        if Path(path).name != asset:
            raise UploadError("asset name must equal the file name")
        if self._run("release", "upload", tag, str(path), "--repo", self.repo, "--clobber") != 0:
            raise UploadError("gh release upload failed")
        return f"https://github.com/{self.repo}/releases/download/{tag}/{asset}"


@dataclass
class AttachmentResult:
    images: list = field(default_factory=list)   # [(alt text, url)] for the issue body
    notes: list = field(default_factory=list)    # fixed, code-written Vietnamese notes
    dry_run_count: int = 0                       # images that passed validation in dry-run
    attempted: int = 0                           # images found on candidate messages


class AttachmentProcessor:
    def __init__(self, settings, store, *, repo="", downloader=None, uploader=None):
        self.settings = settings
        self.store = store
        self.repo = repo
        self.downloader = downloader
        self.uploader = uploader

    def process(self, case_key, messages, *, live) -> AttachmentResult:
        result = AttachmentResult()
        candidates = [m for m in messages if m.has_image]
        if not candidates or self.settings.max_per_case == 0:
            return result
        result.attempted = len(candidates)
        directory = Path(tempfile.mkdtemp(prefix="tg-triage-"))
        try:
            os.chmod(directory, 0o700)
            accepted = self._download(case_key, candidates, directory, result)
            if not live:
                result.dry_run_count = len(accepted)
                return result
            if accepted:
                self._upload(case_key, accepted, result)
        finally:
            # Temporary files never outlive the case, whether or not upload worked.
            shutil.rmtree(directory, ignore_errors=True)
        return result

    def _download(self, case_key, candidates, directory, result):
        """Validate each image; every skipped image is counted by reason, never named."""
        limits = self.settings
        skipped = {"vượt giới hạn số ảnh": 0, "quá dung lượng cho phép": 0,
                   "sai định dạng (chỉ nhận JPG, PNG, WEBP)": 0, "không tải được": 0}
        accepted, failure = [], None
        for message in candidates:
            if len(accepted) >= limits.max_per_case:
                skipped["vượt giới hạn số ảnh"] += 1
                continue
            if message.image_size is not None and message.image_size > limits.max_bytes:
                skipped["quá dung lượng cho phép"] += 1
                continue
            if message.image_mime is not None and message.image_mime not in ALLOWED_MIME:
                skipped["sai định dạng (chỉ nhận JPG, PNG, WEBP)"] += 1
                continue
            try:
                if self.downloader is None:
                    raise DownloaderUnavailable("no downloader configured")
                workdir = directory / str(message.message_id)
                workdir.mkdir(mode=0o700, exist_ok=True)
                downloaded = self.downloader(message.chat_id, message.message_id, workdir)
                if downloaded is None:
                    skipped["không tải được"] += 1
                    continue
                downloaded = Path(downloaded)
                if downloaded.stat().st_size > limits.max_bytes:
                    skipped["quá dung lượng cho phép"] += 1
                    continue
                extension = sniff_extension(downloaded)
                if extension is None:
                    skipped["sai định dạng (chỉ nhận JPG, PNG, WEBP)"] += 1
                    continue
                final = directory / asset_name(message.message_id, len(accepted) + 1, extension)
                os.replace(downloaded, final)
            except Exception as error:
                failure = failure or type(error).__name__
                LOG.warning("Attachment download failed message_id=%s exception=%s",
                            message.message_id, type(error).__name__)
                skipped["không tải được"] += 1
                continue
            accepted.append((message, final))
        if failure:
            result.notes.append(f"Ảnh đính kèm không tải được: {failure}")
        for reason, count in skipped.items():
            if count and not (reason == "không tải được" and failure):
                result.notes.append(f"Ảnh bị bỏ qua ({count} ảnh): {reason}")
        return accepted

    def _upload(self, case_key, accepted, result):
        tag = release_tag(case_key)
        try:
            if self.uploader is None:
                raise UploaderUnavailable("no uploader configured")
            ensured = False
            for index, (message, path) in enumerate(accepted):
                call_id = self.store.reserve_attachment_call(
                    case_key, message.message_id, path.name, self.settings.max_per_hour)
                if call_id is None:
                    result.notes.append(
                        f"Ảnh bị bỏ qua ({len(accepted) - index} ảnh): đạt giới hạn ảnh mỗi giờ")
                    break
                try:
                    if not ensured:
                        self.uploader.ensure_release(tag)
                        ensured = True
                    url = self.uploader.upload(tag, path, path.name)
                    if not isinstance(url, str) or not _SAFE_URL.fullmatch(url):
                        raise UploadError("invalid asset url")
                except Exception:
                    self.store.finish_attachment(call_id, "error")
                    raise
                self.store.finish_attachment(call_id, "uploaded")
                result.images.append((f"{ALT_TEXT} {index + 1}", url))
        except Exception as error:
            # One attempt per image; the first failure stops the batch (no retry loop).
            LOG.warning("Attachment upload failed images=%s exception=%s",
                        len(accepted), type(error).__name__)
            result.notes.append(f"Ảnh đính kèm không tải được: {type(error).__name__}")
