"""Shared Playwright file-chooser interaction; caller retains all safety gates."""
from urllib.parse import urlsplit


def scratch_upload_url(response):
    """Validate the same quick-upload response accepted by Scratch."""
    return scratch_upload_payload(response.json())


def scratch_upload_payload(payload):
    url=payload.get('imageUrl') if isinstance(payload,dict) else None
    if not isinstance(url,str) or urlsplit(url).scheme!='https' or urlsplit(url).hostname!='vlink-storages-prod.sgp1.cdn.digitaloceanspaces.com':
        raise ValueError('unexpected upload response')
    return url


def upload_with_chooser(page, dialog, button, files, response_matches, *, timeout=30000):
    if button not in ('Upload Cover Image','Upload Images'):
        raise ValueError('unsupported image upload control')
    if not callable(response_matches) or type(timeout) is not int or not 0 < timeout <= 900000:
        raise ValueError('invalid upload expectation')
    with page.expect_file_chooser() as chooser:
        dialog.get_by_role('button',name=button,exact=True).click()
    with page.expect_response(response_matches,timeout=timeout) as response:
        chooser.value.set_files(files)
    return response.value
