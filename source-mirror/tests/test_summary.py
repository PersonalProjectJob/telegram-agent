import unittest
from source_mirror.publisher import source_summary, VerificationIncomplete

class SourceSummaryTests(unittest.TestCase):
    def test_extracts_plain_source_text_and_never_media(self):
        blocks=[{'type':'image','media':0},{'type':'html','html':'<h3>A <em>nail artist</em> shares an <a href="https://example.com">easy design</a>.</h3>'}]
        summary=source_summary(blocks)
        self.assertEqual(summary,'A nail artist shares an easy design.')
        self.assertNotIn('<',summary)
        self.assertNotIn('https://',summary)
    def test_length_is_bounded_and_word_boundary_preserved(self):
        source=' '.join(['nails']*80)
        result=source_summary([{'type':'html','html':'<p>'+source+'</p>'}])
        self.assertLessEqual(len(result),180)
        self.assertTrue(result.endswith('…'))
        self.assertTrue(source.startswith(result[:-1]))
    def test_step_label_skipped_in_favor_of_description(self):
        blocks=[{'type':'html','html':'<h3>Step 1</h3>'},{'type':'html','html':'<p>Prepare the nail carefully.</p>'}]
        self.assertEqual(source_summary(blocks),'Prepare the nail carefully.')
    def test_missing_text_holds(self):
        with self.assertRaises(VerificationIncomplete): source_summary([{'type':'image','media':0}])

if __name__=='__main__': unittest.main()
