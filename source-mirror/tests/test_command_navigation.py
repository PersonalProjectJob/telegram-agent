import socket
import unittest
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch
from source_mirror.crawler import check_command_request, command_route, crawl_url
from source_mirror.store import Store, identity


class CommandNavigationTests(unittest.TestCase):
    def test_redirect_and_private_dns_abort_before_continue(self):
        good='https://www.scratchmagazine.co.uk/nail_art/article/'
        public=[(socket.AF_INET,socket.SOCK_STREAM,0,'',('8.8.8.8',443))]
        private=[(socket.AF_INET,socket.SOCK_STREAM,0,'',('127.0.0.1',443))]
        with patch('source_mirror.crawler.socket.getaddrinfo', return_value=public):
            check_command_request(good, True)
            for url in ('https://example.com/', 'https://www.scratchmagazine.co.uk/redirect', 'http://www.scratchmagazine.co.uk/nail_art/x'):
                route=SimpleNamespace(request=SimpleNamespace(url=url,is_navigation_request=lambda:True),abort=Mock(),continue_=Mock())
                command_route(route); route.abort.assert_called_once(); route.continue_.assert_not_called()
        with patch('source_mirror.crawler.socket.getaddrinfo', return_value=private):
            with self.assertRaises(ValueError): check_command_request(good, True)

    def test_known_ready_is_targeted_and_held_does_not_publish(self):
        url='https://www.scratchmagazine.co.uk/nail_art/article/'
        with tempfile.TemporaryDirectory() as root:
            store=Store(root)
            try:
                aid=identity(url)
                store.db.execute("INSERT INTO articles VALUES (?,?,?,NULL,'ready',NULL,?,?)",(aid,url,'title','time','hash'))
                self.assertEqual(crawl_url({},store,url),{'state':'captured','article_id':aid})
                store.db.execute("UPDATE articles SET state='held' WHERE id=?",(aid,))
                self.assertEqual(crawl_url({},store,url),{'state':'held'})
            finally: store.close()

    def test_allowed_response_uses_checked_ip_and_tls_hostname(self):
        url='https://www.scratchmagazine.co.uk/nail_art/article/'
        route=SimpleNamespace(request=SimpleNamespace(url=url,method='GET',is_navigation_request=lambda:True),abort=Mock(),fulfill=Mock())
        public=[(socket.AF_INET,socket.SOCK_STREAM,0,'',('8.8.8.8',443))]
        raw=Mock(); tls=Mock(); response=Mock(status=200)
        response.read.return_value=b'content';response.getheader.return_value='text/html'
        with patch('source_mirror.crawler.socket.getaddrinfo',return_value=public), \
             patch('source_mirror.crawler.socket.create_connection',return_value=raw) as connect, \
             patch('source_mirror.crawler.ssl.create_default_context',return_value=tls), \
             patch('source_mirror.crawler.http.client.HTTPSConnection.request',new=lambda conn,*a,**k:conn.connect()), \
             patch('source_mirror.crawler.http.client.HTTPSConnection.getresponse',return_value=response):
            command_route(route)
        connect.assert_called_once_with(('8.8.8.8',443),timeout=12)
        tls.wrap_socket.assert_called_once_with(raw,server_hostname='www.scratchmagazine.co.uk')
        route.fulfill.assert_called_once();route.abort.assert_not_called()
