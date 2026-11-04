import unittest
from source_mirror.telegram_command_contract import parse


class TelegramCommandContractTests(unittest.TestCase):
    def test_control_commands_are_separate_from_existing_nailhub_namespace(self):
        for operation in ('status', 'pause', 'resume'):
            self.assertEqual(parse('/source_mirror ' + operation, frozenset()), {'operation': operation})
        for text in ('/nailhub pause', '/source_mirror pause extra', '/source_mirror resume\n'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse(text, frozenset())

    def test_url_is_request_only_and_requires_exact_allowed_https_host(self):
        hosts = frozenset({'example.com'})
        self.assertEqual(parse('nailblog https://example.com/a', hosts),
                         {'operation': 'crawl_request', 'url': 'https://example.com/a', 'publication_enabled': False})
        for url in ('http://example.com/a', 'https://example.com.evil/a', 'https://u@example.com/a',
                    'https://example.com:444/a', 'https://example.com/a#x', 'https://example.com/a\nnow'):
            with self.subTest(url=url), self.assertRaises(ValueError): parse('nailblog ' + url, hosts)


if __name__ == '__main__': unittest.main()
