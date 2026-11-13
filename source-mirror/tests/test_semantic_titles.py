import json
import unittest

from source_mirror.semantic_titles import assess, assess_with_agy, TitleReviewUnavailable


def record(ident, title, status='Published'):
    return {'id': ident, 'status': status, 'titles': {'VI': title}}


def fake(scores, uncertain=()):
    def infer(prompt):
        batch = json.loads(prompt.split('\nPUBLISHED=', 1)[1])
        rows = [{'key': item['key'], 'score': scores[int(item['key'])],
                 'uncertain': int(item['key']) in uncertain, 'reason': 'specific comparison'}
                for item in batch]
        return {'status': 'SUCCESS', 'response': json.dumps({'results': rows})}
    return infer


class TitleMeaningTests(unittest.TestCase):
    def test_seventy_is_duplicate_and_sixty_nine_is_distinct(self):
        rows = [record('1', 'Một tiêu đề đã đăng')]
        for score, expected in ((69, 'distinct'), (70, 'duplicate'), (100, 'duplicate')):
            with self.subTest(score=score):
                result = assess({'VI': 'Một bài mới'}, rows, inference=fake([score]))
                self.assertEqual(expected, result['decision'])
                self.assertEqual(1, result['compared'])

    def test_only_published_is_compared(self):
        rows = [record('a', 'Cùng tiêu đề', 'Draft'), record('b', 'Khác', 'Archived')]
        result = assess({'VI': 'Cùng tiêu đề'}, rows, inference=lambda _: self.fail('called model'))
        self.assertEqual('distinct', result['decision'])
        self.assertEqual(0, result['compared'])

    def test_exact_title_blocks_without_model(self):
        result = assess({'VI': ' TÊN  TIỆM NAIL '}, [record('a', 'tên tiệm nail')],
                        inference=lambda _: self.fail('called model'))
        self.assertEqual(('duplicate', 'exact_title'), (result['decision'], result['basis']))

    def test_uncertain_is_not_distinct(self):
        result = assess({'VI': 'Một bài mới'}, [record('a', 'Bài cũ')],
                        inference=fake([35], uncertain=(0,)))
        self.assertEqual('review', result['decision'])

    def test_duplicate_precedes_uncertainty(self):
        result = assess({'VI': 'Bài mới'}, [record('a', 'Bài cũ'), record('b', 'Bài khác')],
                        inference=fake([70, 1], uncertain=(1,)))
        self.assertEqual('duplicate', result['decision'])

    def test_all_batches_covered_without_silent_truncation(self):
        rows = [record(str(i), 'Bài đã đăng '+str(i)) for i in range(81)]
        result = assess({'VI': 'Bài mới'}, rows, inference=fake([0]*80+[70]))
        self.assertEqual('duplicate', result['decision'])
        self.assertEqual('80', result['matches'][0]['id'])
        self.assertEqual(81, result['compared'])

    def test_thousand_records_checked_and_over_limit_fails(self):
        rows = [record(str(i), 'Bài đã đăng '+str(i)) for i in range(1000)]
        result = assess({'VI': 'Bài mới'}, rows, inference=fake([0]*999+[70]))
        self.assertEqual('duplicate', result['decision'])
        self.assertEqual('999', result['matches'][0]['id'])
        with self.assertRaises(TitleReviewUnavailable):
            assess({'VI': 'Bài mới'}, rows+[record('extra','Thêm')], inference=fake([0]*1001))

    def test_missing_or_duplicate_or_extra_results_rejected(self):
        rows = [record('a', 'Bài cũ')]
        for results in ([], [{'key':'0','score':1,'uncertain':False,'reason':'x'}]*2,
                        [{'key':'1','score':1,'uncertain':False,'reason':'x'}]):
            def infer(_): return {'status':'SUCCESS', 'response':json.dumps({'results':results})}
            with self.subTest(results=results), self.assertRaises(TitleReviewUnavailable):
                assess({'VI':'Bài mới'}, rows, inference=infer)

    def test_invalid_score_and_broker_failure_are_not_approval(self):
        rows = [record('a', 'Bài cũ')]
        for score in (-1, 101, True, 70.0):
            def infer(_):
                return {'status':'SUCCESS', 'response':json.dumps({'results':[
                    {'key':'0','score':score,'uncertain':False,'reason':'x'}]})}
            with self.subTest(score=score), self.assertRaises(TitleReviewUnavailable):
                assess({'VI':'Bài mới'}, rows, inference=infer)
        def fail(_): raise TimeoutError('timeout')
        with self.assertRaises(TitleReviewUnavailable): assess({'VI':'Bài mới'}, rows, inference=fail)

    def test_duplicate_ids_or_unknown_status_rejected(self):
        for rows in ([record('a','A'), record('a','B')], [record('a','A','Unknown')]):
            with self.subTest(rows=rows), self.assertRaises(TitleReviewUnavailable):
                assess({'VI':'Bài mới'}, rows, inference=fake([0]))

    def test_fixed_deadline_blocks_before_call_and_after_elapsed_call(self):
        rows = [record('a', 'Bài cũ')]
        with self.assertRaises(TitleReviewUnavailable):
            assess({'VI':'Bài mới'}, rows, inference=lambda _: self.fail('called model'),
                   deadline_monotonic=224, clock=lambda: 0)
        values = iter((0, 226))
        with self.assertRaises(TitleReviewUnavailable):
            assess({'VI':'Bài mới'}, rows, inference=fake([0]),
                   deadline_monotonic=225, clock=lambda: next(values))

    def test_real_broker_adapter_requires_deadline(self):
        with self.assertRaises(TitleReviewUnavailable):
            assess_with_agy({'VI':'Bài mới'}, [], {}, deadline_monotonic=None)


if __name__ == '__main__':
    unittest.main()
