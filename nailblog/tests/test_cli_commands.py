"""The retired image/one-post CLI subcommands must not come back."""
import argparse
import unittest
from unittest.mock import patch

from nailblog import __main__ as entry

REMOVED = ('images', 'image-compare', 'one-post', 'save-draft')


class RemovedSubcommands(unittest.TestCase):
    def choices(self):
        captured = {}
        original = argparse.ArgumentParser.add_argument

        def spy(parser, *names, **options):
            if names == ('command',):
                captured['choices'] = options.get('choices')
            return original(parser, *names, **options)
        with patch.object(argparse.ArgumentParser, 'add_argument', spy), patch('sys.stdout'):
            with self.assertRaises(SystemExit):
                entry.main(['--help'])
        return captured['choices']

    def test_removed_subcommands_are_not_parser_choices(self):
        choices = self.choices()
        self.assertTrue(choices)
        for name in REMOVED:
            self.assertNotIn(name, choices)

    def test_parsing_a_removed_subcommand_is_rejected(self):
        for name in REMOVED:
            with self.subTest(name), self.assertRaises(SystemExit) as caught, patch('sys.stderr'):
                entry.main([name])
            self.assertEqual(caught.exception.code, 2)


if __name__ == '__main__':
    unittest.main()
