import unittest
from source_mirror.official_category_probe import extract_categories

class CategoryProbeTests(unittest.TestCase):
    def test_extracts_only_named_uuid_categories(self):
        value={'items':[{'id':'b1b2c3d4-e5f6-7890-abcd-ef1234567802','name':'Nail Salon'},
                        {'id':'f1b2c3d4-e5f6-7890-abcd-ef1234567806','name':'Hot Trend'}]}
        self.assertEqual(extract_categories(value),value['items'])
    def test_duplicate_or_unbounded_category_response_held(self):
        item={'id':'b1b2c3d4-e5f6-7890-abcd-ef1234567802','name':'Nail Salon'}
        with self.assertRaises(ValueError):extract_categories([item,item])
        with self.assertRaises(ValueError):extract_categories([{'id':'bad','name':'Nail Salon'}])
        with self.assertRaises(ValueError):extract_categories([None]*1001)
