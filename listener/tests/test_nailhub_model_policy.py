"""Exact held-source regressions plus adversarial monetary and identity evidence."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from tg_triage.nailhub.categories import category_change_valid, category_proposal, model_category
from tg_triage.nailhub.browser import posting_from_data
from tg_triage.nailhub.diagnostics import failure_details
from tg_triage.nailhub.model import NailHubModel
from tg_triage.nailhub.validation import (EvidenceError, FACT_KINDS, NeedsHuman, Posting,
                                          candidate_salary, current_address_state_repair,
                                          normalize_extraction, proposal,
                                          source_store_name_repair)
from tests.nailhub_fixtures import raw_post, settings

SOURCES = Path(__file__).resolve().parents[2] / '.context/nailhub-evidence/nailhub-model-policy-before.json'


def source_posts():
    rows = json.loads(SOURCES.read_text(encoding='utf-8'))['rows']
    return {row['id']: Posting(**json.loads(row['original'])) for row in rows}


def base_data():
    return {'salary': {'mode':'negotiated', 'kind':'none', 'amount_text':None,
                       'period':None, 'evidence':None, 'schedule':None, 'rationale':'source'},
            'location':{}, 'facts':{key:[] for key in FACT_KINDS}, 'rationale':'Source facts'}


def candidate(amount, quote, period='week', kind='worker'):
    return {'kind':kind, 'amount_text':amount, 'period':period,
            'evidence':{'quote':quote}, 'role':'', 'schedule':None}


def three_data(posts):
    hiring, multilingual, descriptor = (posts[key] for key in (
        'a01e7a02-0d95-4a9d-385c-08df28db55a0',
        'df176e7a-a9c0-4cde-bb0f-3dbd147ffbe1',
        'f94c24d1-f671-4c6b-b230-97bd143639ee'))
    a = base_data()
    a.update(category={'target':'JobPosting', 'field':'content',
                       'evidence':{'quote':'NOW HIRING NAIL TECHS!'}},
             salary_candidates=[candidate('$900–$1,500+',
                 'Earn $900–$1,500+ per week — depending on your skills & performance!')])
    b = base_data()
    b.update(category={'target':'JobPosting', 'field':'content',
                       'evidence':{'quote':'Cần thợ 💕💕💕'}},
             salary_candidates=[candidate('1500 tới 2700', 'income cao1500 tới 2700 / tuần'),
                 candidate('$1300 – $2700', '收入：每周 $1300 – $2700（按技术水平而定）')])
    c = base_data()
    c.update(category={'target':'JobPosting', 'field':'content',
                       'evidence':{'quote':'cần gấp nhiều thợ nail'}}, salary_candidates=[],
             location={'salon':{'value':'Tiệm Nail Sang Vung HARRISONBURG',
                                'evidence':{'quote':'Tiệm Nail Sang Vung HARRISONBURG'}}})
    return ((hiring,a),(multilingual,b),(descriptor,c))


def portable_posts():
    """Sanitized source-equivalent fixtures that run in any checkout."""
    hiring = posting_from_data(raw_post(
        'NOW HIRING NAIL TECHS!\nEarn $900–$1,500+ per week — depending on your skills & performance!\n'
        'We’re looking for talented and motivated Nail Technicians to join our team!\n'
        'https://www.baonail.com/index.php?stores=Glaze-Nails&id=A66c8dca27\n'
        'Ghi chú: Nguồn URL xác nhận tên tiệm.',
        id='a01e7a02-0d95-4a9d-385c-08df28db55a0', title='Now Hiring Nail Techs!',
        businessName='BaoNail.com'))
    multilingual = posting_from_data(raw_post(
        'Cần thợ 💕💕💕\nincome cao1500 tới 2700 / tuần\ntùy thuộc vào tay nghề\n'
        '诚聘有经验美甲师\n收入：每周 $1300 – $2700（按技术水平而定）',
        id='df176e7a-a9c0-4cde-bb0f-3dbd147ffbe1', title='CẦN THỢ NAILS !!!',
        businessName='Happy Nails And Spa', address='1701 Deming Way Middleton, WI 53562',
        city='Middleton', state='IL', zipCode='53562'))
    descriptor = posting_from_data(raw_post(
        'Tiệm Nail Sang Vung HARRISONBURG, VA cần gấp nhiều thợ nail có kinh nghiệm.\n'
        'Nếu cần chủ sẽ bao luơng tuỳ theo tay nghề (quanh năm).',
        id='f94c24d1-f671-4c6b-b230-97bd143639ee', title='CẦN GẤP NHIỀU THỢ NAILS !',
        businessName='Allure Nail Lounge', address='2035 E Market St Harrisonburg, VA 22801',
        city='Harrisonburg', state='VA', zipCode='22801', priceType='Negotiable', price=0))
    return {post.id:post for post in (hiring,multilingual,descriptor)}


@unittest.skipUnless(SOURCES.is_file(), 'exact held source snapshot unavailable outside local evidence worktree')
class ExactHeldSourceTests(unittest.TestCase):
    def test_actual_hiring_title_survives_joined_model_citation(self):
        post = source_posts()['a01e7a02-0d95-4a9d-385c-08df28db55a0']
        joined = {'target':'JobPosting', 'field':'content', 'evidence':{'quote':
            'NOW HIRING NAIL TECHS! We’re looking for talented and motivated Nail Technicians to join our team!'}}
        with self.assertRaises(EvidenceError):
            model_category(post, joined)
        sourced = category_proposal(post, joined)
        self.assertEqual(sourced['target'], 'JobPosting')
        self.assertEqual(sourced['evidence'][0]['field'], 'title')
        self.assertEqual(sourced['evidence'][0]['quote'], 'Hiring Nail Techs')

    def test_three_full_original_snapshots_project_expected_fields(self):
        posts = source_posts()
        with tempfile.TemporaryDirectory() as directory:
            results = [(post, proposal(post, normalize_extraction(post, data), settings(directory)))
                       for post, data in three_data(posts)]
        first, second, third = (result for _, result in results)
        self.assertEqual((first['category'], first['fields']['salon'], first['fields']['salary_amount']),
                         ('JobPosting', 'Glaze Nails', '1500'))
        self.assertEqual(first['field_corrections']['salon']['source_field'], 'content_url')
        self.assertEqual((second['fields']['state'], second['fields']['salary_amount'],
                          second['fields']['phone_other']), ('WI', '2700', ''))
        self.assertEqual(second['field_corrections']['state']['source_field'], 'address')
        self.assertEqual((third['fields']['salon'], third['fields']['salary_type'],
                          third['fields']['salary_amount']), ('Allure Nail Lounge', 'negotiated', ''))
        for post, result in results:
            expected = {**post.snapshot(), 'category':result['category'], 'fields':result['fields'],
                        'audit':{'category_change':result['category_change']}}
            self.assertTrue(category_change_valid(post, expected))

    def test_all_income_clauses_required_and_one_bounded_retry(self):
        posts = source_posts()
        post, valid = three_data(posts)[1]
        source_repeat = copy.deepcopy(valid)
        source_repeat['salary_candidates'].pop()
        reverse_repeat = copy.deepcopy(valid)
        reverse_repeat['salary_candidates'].pop(0)
        missing = copy.deepcopy(valid)
        missing['salary_candidates'].clear()
        with tempfile.TemporaryDirectory() as directory:
            repeated = proposal(post, normalize_extraction(post, source_repeat), settings(directory))
            reversed_result = proposal(post, normalize_extraction(post, reverse_repeat), settings(directory))
            self.assertEqual((repeated['fields']['salary_amount'],
                              repeated['salary']['source_repeats'][0]['high']), ('2700', '2700'))
            self.assertEqual((reversed_result['fields']['salary_amount'],
                              reversed_result['salary']['source_repeats'][0]['high']), ('2700', '2700'))
            self.assertEqual(reversed_result['salary']['source_repeats'][0]['amount_text'],
                             '1500 tới 2700')
            with self.assertRaises(EvidenceError) as caught:
                proposal(post, normalize_extraction(post, missing), settings(directory))
            self.assertEqual(caught.exception.path, 'salary_candidates')
            model = NailHubModel(settings(directory))
            replies = iter((missing, valid))
            model.ask = lambda *_: next(replies)
            self.assertEqual(model.extract(post), valid)

    def test_url_slug_is_literal_bounded_and_owner_spaced(self):
        post = source_posts()['a01e7a02-0d95-4a9d-385c-08df28db55a0']
        self.assertEqual(source_store_name_repair(post)['salon']['value'], 'Glaze Nails')
        for changed in (post.content.replace('www.baonail.com', 'other.example'),
                        post.content.replace('stores=Glaze-Nails', 'store=Glaze-Nails')):
            self.assertEqual(source_store_name_repair(Posting(post.id,post.status,post.category,changed,post.fields)), {})

    def test_stored_address_state_repair_rejects_conflicting_source_pair(self):
        post = source_posts()['df176e7a-a9c0-4cde-bb0f-3dbd147ffbe1']
        self.assertEqual(current_address_state_repair(post)['state']['value'], 'WI')
        conflicting = Posting(post.id, post.status, post.category,
                              post.content + '\nMiddleton, IL 53562', post.fields)
        with self.assertRaises(NeedsHuman):
            current_address_state_repair(conflicting)


class CandidateGuardTests(unittest.TestCase):
    def test_live_range_separator_recovers_one_literal_full_range_only(self):
        source = 'income cao1500 tới 2700 / tuần'
        exact = candidate_salary(source, [candidate('1500 tới 2700', source)])
        represented = candidate_salary(source, [candidate('1500-2700', source)])
        self.assertEqual(represented, exact)
        for wrong in ('1500-2800', '1500'):
            with self.subTest(wrong=wrong), self.assertRaises(EvidenceError):
                candidate_salary(source, [candidate(wrong, source)])
        repeated = 'Income 1500 tới 2700 and 1500 tới 2700 / tuần'
        with self.assertRaises(EvidenceError):
            candidate_salary(repeated, [candidate('1500-2700', repeated)])

    def test_glued_bare_income_range_accepts_only_same_literal_endpoints(self):
        vietnamese = 'income cao1500 tới 2700 / tuần\ntùy thuộc vào tay nghề'
        represented = candidate_salary(vietnamese, [candidate('$1500 tới $2700', vietnamese)])
        self.assertEqual((represented['low'], represented['high']), ('1500', '2700'))
        for wrong in ('$1500 tới $2800', '$1500', '$1500-$2700/week'):
            with self.subTest(wrong=wrong), self.assertRaises(EvidenceError):
                candidate_salary(vietnamese, [candidate(wrong, vietnamese)])
        rent = 'Rent 1500 tới 2700 / week'
        with self.assertRaises(EvidenceError):
            candidate_salary(rent, [candidate('$1500 tới $2700', rent, 'week', 'rent')])
        repeated = 'income cao1500 tới 2700 and 1500 tới 2700 / tuần'
        with self.assertRaises(EvidenceError):
            candidate_salary(repeated, [candidate('$1500 tới $2700', repeated)])
        chinese = '收入：每周 $1300 – $2700（按技术水平而定）'
        translated = candidate_salary(vietnamese + '\n' + chinese,
                                      [candidate('$1500 tới $2700', vietnamese)])
        self.assertEqual((translated['low'], translated['high']), ('1300', '2700'))
        self.assertEqual(translated['source_repeats'], [{
            'quote':chinese, 'amount_text':'$1300 – $2700', 'period':'week',
            'low':'1300', 'high':'2700', 'source_field':'content',
            'amount_span': {'start': (vietnamese + '\n' + chinese).index('$1300'),
                            'end': (vietnamese + '\n' + chinese).index('$1300') + len('$1300 – $2700')}}])
        for invalid in ('收入：每周 $1300 – $2800（按技术水平而定）',
                        '收入：每月 $1300 – $2700（按技术水平而定）',
                        '收入：每周 $1300 – $2700（佣金）',
                        '收入：每周 $1300 – $2700（commission）',
                        '每周 $1300 – $2700（按技术水平而定）'):
            with self.subTest(invalid=invalid), self.assertRaises(EvidenceError) as omitted:
                candidate_salary(vietnamese + '\n' + invalid,
                                 [candidate('$1500 tới $2700', vietnamese)])
            self.assertEqual(omitted.exception.path, 'salary_candidates')
        with self.assertRaises(EvidenceError):
            candidate_salary(vietnamese + '\n' + chinese + '\n' + chinese,
                             [candidate('$1500 tới $2700', vietnamese)])
        scalar = 'Income $2700/week'
        with self.assertRaises(EvidenceError):
            candidate_salary(scalar + '\n' + chinese, [candidate('$2700', scalar)])

    def test_chinese_only_model_candidate_audits_exact_vietnamese_weekly_repeat(self):
        vietnamese = 'income cao1500 tới 2700 / tuần'
        chinese = '收入：每周 $1300 – $2700（按技术水平而定）'
        source = vietnamese + '\n' + chinese
        result = candidate_salary(source, [candidate('$1300 – $2700',
                                                    '每周 $1300 – $2700（按技术水平而定）')])
        self.assertEqual((result['low'], result['high']), ('1300', '2700'))
        self.assertEqual(result['source_repeats'], [{
            'quote': vietnamese, 'amount_text': '1500 tới 2700', 'period': 'week',
            'low': '1500', 'high': '2700', 'source_field': 'content',
            'amount_span': {'start': source.index('1500'), 'end': source.index('1500') + len('1500 tới 2700')},
        }])
        for invalid in ('income cao1500 tới 2800 / tuần',
                        'income cao1500 tới 2700 / tháng',
                        'income cao1500 tới 2700 / tuần commission',
                        'income 1500 tới 2700 / tuần',
                        'rent cao1500 tới 2700 / tuần',
                        'income cao1500 tới 2700 / tuần\nincome cao1500 tới 2700 / tuần'):
            with self.subTest(invalid=invalid), self.assertRaises(EvidenceError) as omitted:
                candidate_salary(invalid + '\n' + chinese,
                                 [candidate('$1300 – $2700',
                                            '每周 $1300 – $2700（按技术水平而定）')])
            self.assertEqual(omitted.exception.path, 'salary_candidates')
        with self.assertRaises(EvidenceError):
            candidate_salary(vietnamese + '\nIncome $2700/week',
                             [candidate('$2700', 'Income $2700/week')])

    def test_model_amount_with_literal_terminal_week_unit_binds_same_source_range(self):
        vietnamese = 'income cao1500 tới 2700 / tuần'
        chinese = '收入：每周 $1300 – $2700（按技术水平而定）'
        source = vietnamese + '\n' + chinese
        accepted = candidate_salary(source, [candidate('$1500 tới 2700 / tuần', vietnamese)])
        self.assertEqual((accepted['low'], accepted['high']), ('1300', '2700'))
        self.assertEqual(accepted['source_repeats'][0]['amount_text'], '$1300 – $2700')
        for wrong in ('$1500 tới 2800 / tuần', '$1500 tới 2700 / tháng',
                      '$1500 tới 2700 / week', '$1500 tới 2700 / tuần extra',
                      '$1500 tới 2700 / tháng / tuần'):
            with self.subTest(wrong=wrong), self.assertRaises(EvidenceError):
                candidate_salary(source, [candidate(wrong, vietnamese)])
        misplaced = 'income cao1500 tới 2700; work schedule / tuần'
        with self.assertRaises(EvidenceError):
            candidate_salary(misplaced, [candidate('$1500 tới 2700 / tuần', misplaced)])

    def test_both_captured_one_clause_models_project_full_multilingual_post(self):
        post = portable_posts()['df176e7a-a9c0-4cde-bb0f-3dbd147ffbe1']
        all_data = next(data for source, data in three_data(portable_posts()) if source.id == post.id)
        vietnamese_only = copy.deepcopy(all_data)
        vietnamese_only['salary_candidates'] = [candidate(
            '$1500 tới $2700', 'income cao1500 tới 2700 / tuần\ntùy thuộc vào tay nghề')]
        chinese_only = copy.deepcopy(all_data)
        chinese_only['salary_candidates'] = [candidate(
            '$1300 – $2700', '每周 $1300 – $2700（按技术水平而定）')]
        with tempfile.TemporaryDirectory() as directory:
            for data in (vietnamese_only, chinese_only):
                with self.subTest(amount=data['salary_candidates'][0]['amount_text']):
                    resolved = proposal(post, normalize_extraction(post, data), settings(directory))
                    self.assertEqual((resolved['fields']['state'], resolved['fields']['salary_amount'],
                                      resolved['fields']['phone_other']), ('WI', '2700', ''))
                    self.assertEqual(len(resolved['salary']['source_repeats']), 1)

    def test_captured_unit_suffixed_model_candidate_projects_full_multilingual_post(self):
        post = portable_posts()['df176e7a-a9c0-4cde-bb0f-3dbd147ffbe1']
        data = next(data for source, data in three_data(portable_posts()) if source.id == post.id)
        data['salary_candidates'] = [candidate('$1500 tới 2700 / tuần',
                                                'income cao1500 tới 2700 / tuần')]
        with tempfile.TemporaryDirectory() as directory:
            resolved = proposal(post, normalize_extraction(post, data), settings(directory))
        self.assertEqual((resolved['fields']['state'], resolved['fields']['salary_amount']), ('WI', '2700'))
        self.assertEqual(resolved['salary']['source_repeats'][0]['amount_text'], '$1300 – $2700')

    def test_category_case_only_citation_maps_to_literal_selected_field(self):
        post = portable_posts()['a01e7a02-0d95-4a9d-385c-08df28db55a0']
        suggestion = {'target':'JobPosting', 'field':'title',
                      'evidence':{'quote':'NOW HIRING NAIL TECHS!'}}
        self.assertEqual(model_category(post, suggestion),
                         ('JobPosting', 'title', 'Now Hiring Nail Techs!'))
        modeled_post = Posting('x','Draft','JobPosting','Apply now', {'title':'Nail Techs Needed'})
        category = category_proposal(modeled_post, {'target':'JobPosting', 'field':'title',
            'evidence':{'quote':'NAIL TECHS NEEDED'}})
        self.assertEqual(category['model_evidence']['field'], 'title')
        self.assertEqual(category['model_evidence']['evidence']['quote'], 'Nail Techs Needed')
        other_field = {'target':'JobPosting', 'field':'title',
                       'evidence':{'quote':'We’re looking for talented and motivated Nail Technicians'}}
        self.assertEqual(model_category(post, other_field)[1], 'content')
        ambiguous = Posting('x','Draft','JobPosting','NOW HIRING NAIL TECHS!',
                            {'title':'Now Hiring Nail Techs! Now Hiring Nail Techs!'})
        with self.assertRaises(EvidenceError):
            model_category(ambiguous, suggestion)
        with self.assertRaises(EvidenceError):
            model_category(post, {**suggestion, 'evidence':{'quote':'NOW HIRING NAIL ARTISTS!'}})

    def test_hiring_nail_techs_is_explicit_without_accepting_noncontiguous_quote(self):
        post = portable_posts()['a01e7a02-0d95-4a9d-385c-08df28db55a0']
        joined = {'target':'JobPosting', 'field':'content', 'evidence':{'quote':
            'NOW HIRING NAIL TECHS! We’re looking for talented and motivated Nail Technicians to join our team!'}}
        with self.assertRaises(EvidenceError):
            model_category(post, joined)
        sourced = category_proposal(post, joined)
        self.assertEqual(sourced['target'], 'JobPosting')
        self.assertEqual(sourced['evidence'][0]['field'], 'title')
        self.assertIn('Hiring Nail Techs', sourced['evidence'][0]['quote'])
        self.assertNotIn('model_evidence', sourced)
        for negative in ('Not hiring nail techs.', 'No longer hiring nail techs.'):
            with self.subTest(negative=negative):
                blocked = Posting('x', 'Draft', 'JobPosting', negative,
                                  {'title':negative})
                with self.assertRaises(NeedsHuman):
                    category_proposal(blocked)
                with self.assertRaises(EvidenceError):
                    model_category(blocked, {'target':'JobPosting','field':'title',
                                             'evidence':{'quote':negative}})

    def test_no_money_source_discards_only_hallucinated_salary_candidates(self):
        posts = portable_posts()
        post, data = three_data(posts)[2]
        hallucinated = copy.deepcopy(data)
        hallucinated['salary_candidates'] = [candidate('$900', 'Tiệm Nail Sang Vung HARRISONBURG')]
        clean = normalize_extraction(post, hallucinated)
        self.assertNotIn('salary_candidates', clean)
        with tempfile.TemporaryDirectory() as directory:
            projected = proposal(post, clean, settings(directory))
        self.assertEqual((projected['fields']['salon'], projected['fields']['salary_type']),
                         ('Allure Nail Lounge', 'negotiated'))
        with_phones = Posting(post.id, post.status, post.category,
                              post.content + '\nXin gọi 571-315-0414 hoặc 540-300-6666.', post.fields)
        self.assertNotIn('salary_candidates', normalize_extraction(with_phones, hallucinated))
        paid, paid_data = three_data(posts)[1]
        self.assertEqual(len(normalize_extraction(paid, paid_data)['salary_candidates']), 2)
        for mention in ('Earn 1500 per week', 'Weekly 1500', '3K', '月薪1500'):
            with self.subTest(mention=mention):
                with_money = Posting(post.id, post.status, post.category,
                                     post.content + '\n' + mention, post.fields)
                self.assertIn('salary_candidates', normalize_extraction(with_money, hallucinated))
        for address_line in ('1701 Salary $1500 Way Middleton, WI 53562',
                             '1701 Income 1500 Way Middleton, WI 53562'):
            with self.subTest(address_line=address_line):
                with_money = Posting(post.id, post.status, post.category,
                                     post.content + '\n' + address_line, post.fields)
                self.assertIn('salary_candidates', normalize_extraction(with_money, hallucinated))

    def test_city_case_only_source_pair_preserves_stored_form_label(self):
        post, data = three_data(portable_posts())[2]
        data = copy.deepcopy(data)
        data['location']['city'] = {'value':'HARRISONBURG',
                                    'evidence':{'quote':'HARRISONBURG, VA'}}
        normalized = normalize_extraction(post, data)
        self.assertNotIn('city', normalized['location'])
        with tempfile.TemporaryDirectory() as directory:
            result = proposal(post, normalized, settings(directory))
        self.assertEqual(result['fields']['city'], 'Harrisonburg')
        conflict = Posting(post.id, post.status, post.category,
                           post.content.replace('HARRISONBURG, VA', 'HARRISONBURG, WV'), post.fields)
        data['location']['city']['evidence']['quote'] = 'HARRISONBURG, WV'
        self.assertIn('city', normalize_extraction(conflict, data)['location'])

    def test_portable_three_case_projection(self):
        with tempfile.TemporaryDirectory() as directory:
            results = [proposal(post, normalize_extraction(post, data), settings(directory))
                       for post, data in three_data(portable_posts())]
        self.assertEqual((results[0]['fields']['salon'], results[0]['fields']['salary_amount']),
                         ('Glaze Nails', '1500'))
        self.assertEqual((results[1]['fields']['state'], results[1]['fields']['salary_amount']),
                         ('WI', '2700'))
        self.assertEqual((results[2]['fields']['salon'], results[2]['fields']['salary_type']),
                         ('Allure Nail Lounge', 'negotiated'))

    def test_full_range_and_local_period_cannot_be_cherry_picked(self):
        source='Income $900–$1,500+ per week depending on skill.'
        with self.assertRaises(EvidenceError):
            candidate_salary(source, [candidate('$1,500', source)])
        with self.assertRaises(EvidenceError):
            candidate_salary('Salary $1500\nWork 5 days/week',
                             [candidate('$1500', 'Salary $1500\nWork 5 days/week')])
        with self.assertRaises(EvidenceError):
            candidate_salary('Income 1500 tới 2700 / tuần', [])
        with self.assertRaises(EvidenceError):
            candidate_salary('Manicurist income $7000; Pedicurist income $1200/month.', [
                candidate('$7000', 'Manicurist income $7000; Pedicurist income $1200/month.', 'month'),
                candidate('$1200', 'Pedicurist income $1200/month.', 'month')])
        with self.assertRaises(EvidenceError):
            candidate_salary('Income $900-$1533/week', [
                candidate('$900', '$900', None, 'other'),
                candidate('$1533', '$1533/week', 'week')])
        with self.assertRaises(EvidenceError):
            candidate_salary('Rent $7000/month.', [candidate('$7000', '$7000/month.', 'month')])
        with self.assertRaises(EvidenceError):
            candidate_salary('Prices manicure: $70/week', [candidate('$70', '$70/week', 'week')])
        with self.assertRaises(EvidenceError):
            candidate_salary('Business revenue $7000/month.', [candidate('$7000', '$7000/month.', 'month')])

    def test_service_exclusion_no_unit_copy_and_repeated_scalar(self):
        self.assertEqual(candidate_salary('Prices manicure: $30',
            [candidate('$30', 'Prices manicure: $30', None, 'service')])['mode'], 'negotiated')
        no_unit = candidate_salary('Salary $1533', [candidate('$1533', 'Salary $1533', None)])
        self.assertTrue(no_unit['copy_no_period'])
        self.assertEqual(no_unit['low'], '1533')
        repeated = candidate_salary('Salary $1533/week\nSalary $1533/week', [
            {'kind':'worker', 'amount_text':'$1533', 'period':'week',
             'evidence':{'start':0,'end':17}, 'role':'', 'schedule':None},
            {'kind':'worker', 'amount_text':'$1533', 'period':'week',
             'evidence':{'start':18,'end':35}, 'role':'', 'schedule':None}])
        self.assertFalse(repeated['range'])
        no_unit_repeated = candidate_salary('Bao luong 7000. Income 7000.', [
            candidate('7000', 'Bao luong 7000.', None),
            candidate('7000', 'Income 7000.', None)])
        self.assertTrue(no_unit_repeated['copy_no_period'])
        self.assertEqual(no_unit_repeated['low'], '7000')
        repeated_range = candidate_salary('Bao luong 6000-10000. Income 6000-10000.', [
            candidate('6000-10000', 'Bao luong 6000-10000.', None),
            candidate('6000-10000', 'Income 6000-10000.', None)])
        self.assertTrue(repeated_range['copy_no_period'])
        self.assertEqual((repeated_range['low'], repeated_range['high']), ('6000', '10000'))
        alternative = candidate_salary('Guaranteed $1533/week or commission.', [
            candidate('$1533', 'Guaranteed $1533/week or commission.')])
        self.assertEqual(alternative['low'], '1533')

    def test_explicit_short_salon_name_survives_normalization(self):
        post=posting_from_data(raw_post('Name of salon: Envy. Hiring nail technician.',
                                       businessName='Allure Nail Lounge'))
        data=base_data()
        data['location']={'salon':{'value':'Envy','evidence':{'quote':'Envy'}}}
        with tempfile.TemporaryDirectory() as directory:
            result=proposal(post, normalize_extraction(post,data), settings(directory))
        self.assertEqual(result['fields']['salon'], 'Envy')

    def test_invalid_model_state_diagnostic_retains_stored_current(self):
        post=posting_from_data(raw_post('Hiring nail technician. Richmond, Virginia.',
                                       city='Richmond', state='VA'))
        data=base_data()
        data['location']={'state':{'value':'Virginia','evidence':{'quote':'Virginia'}}}
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(EvidenceError) as caught:
                proposal(post, normalize_extraction(post,data), settings(directory))
            details=failure_details(caught.exception, post)
            self.assertEqual((details['kind'], details['current'], details['source']),
                             ('automation','VA','Virginia'))
            model=NailHubModel(settings(directory))
            calls=[]
            def ask(*_):
                calls.append(1)
                return data
            model.ask=ask
            with self.assertRaises(EvidenceError):
                model.extract(post)
            self.assertEqual(len(calls), 2)

    def test_explicit_negative_sale_blocks_model_override(self):
        post=Posting('x','Draft','JobPosting','Not for sale. Hiring nail techs.',{'title':'Neutral'})
        self.assertEqual(category_proposal(post, {'target':'SalonForSale','field':'content',
                         'evidence':{'quote':'Not for sale. Hiring nail techs.'}})['target'], 'JobPosting')
        with self.assertRaises(EvidenceError):
            model_category(post, {'target':'SalonForSale','field':'content',
                                  'evidence':{'quote':'Not for sale. Hiring nail techs.'}})
        post=Posting('x','Draft','JobPosting','We are not hiring nail techs today.',{'title':'Neutral'})
        with self.assertRaises(NeedsHuman):
            category_proposal(post, {'target':'JobPosting','field':'content',
                                     'evidence':{'quote':'We are not hiring nail techs today.'}})
