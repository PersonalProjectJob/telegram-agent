"""paste_html focus/selection/wait regression tests. Fake page only; no browser or network."""
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from nailblog.admin import Admin
from nailblog.publication import AdminError
from tests.test_admin_phase2 import config

OLD = '<h2>Cũ</h2><p>Nội dung cũ.</p><figure class="blog-image-figure"><img src="https://x.test/a.webp" alt="a"></figure>'
NEW = '<h2>Mới</h2><p>Nội dung mới.</p><figure><img src="https://x.test/b.webp" alt="b"><figcaption>c</figcaption></figure>'


def build(directory, *, blocks=1, in_editor=True, html=(NEW,)):
    admin = Admin(config(directory))
    editor, text = MagicMock(), MagicMock()
    editor.locator.return_value = text
    text.count = AsyncMock(return_value=blocks)
    text.first.click = AsyncMock()
    editor.click, editor.press = AsyncMock(), AsyncMock()
    editor.inner_html = AsyncMock(side_effect=list(html))
    async def evaluate(script, *args):
        return in_editor if 'getSelection' in script else None
    admin.page = MagicMock()
    admin.page.evaluate = AsyncMock(side_effect=evaluate)
    admin.page.get_by_role.return_value.locator.return_value = editor
    return admin, editor, text


class PasteFocusTests(unittest.IsolatedAsyncioTestCase):
    async def test_clicks_text_block_outside_figure_not_editor_centre(self):
        with tempfile.TemporaryDirectory() as d:
            admin, editor, text = build(d)
            self.assertEqual(await admin.paste_html(NEW), NEW)
            self.assertIn('not(ancestor::figure)', editor.locator.call_args.args[0])
            text.first.click.assert_awaited_once()
            editor.click.assert_not_awaited()
            self.assertEqual([c.args[0] for c in editor.press.await_args_list], ['ControlOrMeta+a', 'ControlOrMeta+v'])

    async def test_empty_editor_falls_back_to_editor_click(self):
        with tempfile.TemporaryDirectory() as d:
            admin, editor, text = build(d, blocks=0)
            self.assertEqual(await admin.paste_html(NEW), NEW)
            editor.click.assert_awaited_once()
            text.first.click.assert_not_awaited()

    async def test_selection_outside_editor_fails_closed_before_any_key(self):
        with tempfile.TemporaryDirectory() as d:
            admin, editor, _ = build(d, in_editor=False)
            with self.assertRaisesRegex(AdminError, '^editor_focus_lost$'):
                await admin.paste_html(NEW)
            editor.press.assert_not_awaited()
            editor.inner_html.assert_not_awaited()

    async def test_second_paste_waits_until_html_changes(self):
        with tempfile.TemporaryDirectory() as d:
            admin, editor, _ = build(d, html=(OLD, OLD, NEW))
            with patch('nailblog.admin.asyncio.sleep', new=AsyncMock()) as sleep:
                self.assertEqual(await admin.paste_html(NEW), NEW)
            self.assertEqual(editor.inner_html.await_count, 3)
            self.assertEqual(sleep.await_count, 2)

    async def test_never_matching_html_is_roundtrip_loss_and_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            admin, editor, _ = build(d, html=[OLD] * 1000)
            with patch('nailblog.admin.asyncio.sleep', new=AsyncMock()):
                with self.assertRaisesRegex(AdminError, '^editor_roundtrip_loss$'):
                    await admin.paste_html(NEW)
            self.assertLess(editor.inner_html.await_count, 1000)


if __name__ == '__main__':
    unittest.main()
