import unittest
from source_mirror.telegram_bot import incoming


class BotTests(unittest.TestCase):
    def test_only_owner_private_unforwarded_message_is_accepted(self):
        message={'message_id':17,'from':{'id':42,'is_bot':False},'chat':{'id':42,'type':'private'},'text':'/mirror pause'}
        self.assertEqual(incoming({'message':message},42),(17,'/source_mirror pause'))
        for changed in (dict(message,forward_origin={}),dict(message,chat={'id':42,'type':'group'}),
                        dict(message,**{'from':{'id':99,'is_bot':False}}),dict(message,**{'from':{'id':42,'is_bot':True}})):
            with self.subTest(changed=changed): self.assertIsNone(incoming({'message':changed},42))
    def test_url_text_and_nailblog_slash_are_not_confused(self):
        message={'message_id':18,'from':{'id':42,'is_bot':False},'chat':{'id':42,'type':'private'},'text':'nailblog https://example.com/a'}
        self.assertEqual(incoming({'message':message},42),(18,message['text']))
        message['text']='/nailblog https://example.com/a'
        self.assertIsNone(incoming({'message':message},42))
