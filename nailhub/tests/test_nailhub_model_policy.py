"""Exact held-source regressions plus adversarial monetary and identity evidence."""
import copy
import hashlib
import json
import tempfile
import unittest
import unicodedata
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from nailhub.categories import category_change_valid, category_proposal, model_category
from nailhub.browser import posting_from_data
from nailhub.diagnostics import failure_details
from nailhub.model import NailHubModel
from nailhub.validation import (EvidenceError, FACT_KINDS, NeedsHuman, Posting,
                                          candidate_salary, current_address_state_repair,
                                          normalize_extraction, owner_confirmed_pay, proposal, source_active_income_after_filled_manager,
                                          source_salary_policy, source_title_linked_sale_price,
                                          source_store_name_repair, publisher_name_fallback,
                                          publisher_branding_rewrite, require_salon_location_role,
                                          source_worker_pay_clauses,
                                          without_service_prices)
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

    def test_branch_address_digits_do_not_make_a_vague_income_claim_numeric(self):
        body = ('Hiring nail technicians. Salon income is steady year-round.\n'
                'Branch 1: 770 N Glynn St Fayetteville ga 30214\n'
                'Branch 2: 7895 Senoia Rd Fairburn ga 30213')
        post = posting_from_data(raw_post(body, businessName='Vibe Nail Bar',
                                          address='770 N Glynn St Fayetteville GA 30214',
                                          city='Fayetteville', state='GA', zipCode='30214'))
        data = base_data()
        data['salary_candidates'] = [candidate('[]', 'Salon income is steady year-round.', None, 'other')]
        data['location'] = {'city': {'value':'Fayetteville', 'evidence':{'quote':'Fayetteville ga 30214'}},
                            'state': {'value':'GA', 'evidence':{'quote':'Fayetteville ga 30214'}},
                            'zip': {'value':'30214', 'evidence':{'quote':'Fayetteville ga 30214'}}}
        clean = normalize_extraction(post, data)
        self.assertNotIn('salary_candidates', clean)
        with tempfile.TemporaryDirectory() as directory:
            projected = proposal(post, clean, settings(directory))
        self.assertEqual(projected['fields']['salary_type'], 'negotiated')
        self.assertEqual(projected['fields']['address'], post.fields['address'])
        changed_branch = copy.deepcopy(data)
        changed_branch['location']['city'] = {'value':'Fairburn', 'evidence':{'quote':'Fairburn ga 30213'}}
        self.assertIn('city', normalize_extraction(post, changed_branch)['location'])
        for amount in ('Bao 1000', 'Pay 1000', 'Salary 1000', 'Income 1000'):
            with self.subTest(amount=amount):
                with_money = Posting(post.id, post.status, post.category,
                                     post.content + '\n' + amount, post.fields)
                self.assertIn('salary_candidates', normalize_extraction(with_money, data))

        percent_and_contact = posting_from_data(raw_post(
            'Hiring nail technicians. Guaranteed income. Customers are 100% friendly. '
            'Call Phone ☎ for details.', title='Hiring nail technicians',
            businessName='Example Nails', address='3 Circle Rd Troy, MO 63379',
            city='Troy', state='MO', zipCode='63379',
            contactInfo={'name':'Example Nails', 'phone':'6365796895',
                         'mobilePhone':'6366752886', 'email':''}))
        vague = base_data()
        vague['salary_candidates'] = [candidate('INCOME', 'Guaranteed income', None, 'other')]
        clean = normalize_extraction(percent_and_contact, vague)
        self.assertNotIn('salary_candidates', clean)
        with tempfile.TemporaryDirectory() as directory:
            projected = proposal(percent_and_contact, clean, settings(directory))
        self.assertEqual((projected['fields']['salary_type'], projected['fields']['salary_amount']),
                         ('negotiated', ''))
        self.assertEqual((projected['fields']['phone_primary'], projected['fields']['phone_other']),
                         ('6365796895', '6366752886'))

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


class HeldPolicyRegressions(unittest.TestCase):
    def project(self, post, data):
        with tempfile.TemporaryDirectory() as directory:
            return proposal(post, normalize_extraction(post, data), settings(directory))

    def test_confirmed_daily_and_currency_typo_are_exact_source_decisions(self):
        daily = ('Mùa đông thợ cũng làm tối thiểu $300 hoặc $450/ngày. '
                 'Bao lương 7000/ nếu có tay nghề.')
        typo = ('Thợ bột làm everything $2200up - hơn &2800 / 1 tuần. '
                'Tiền tip $1000/1 tháng.')
        self.assertIsNone(owner_confirmed_pay(daily))
        self.assertIsNone(owner_confirmed_pay(typo))
        with patch('nailhub.validation.OWNER_CONFIRMED_DAILY_SOURCE_SHA256',
                   hashlib.sha256(daily.encode('utf-8')).hexdigest()):
            approved = owner_confirmed_pay(daily)
            self.assertEqual(approved['high'], '2700')
            self.assertEqual((approved['owner_decision_ref'], approved['source_sha256']),
                             ('D025', hashlib.sha256(daily.encode('utf-8')).hexdigest()))
            self.assertEqual([(item['amount_text'], item['treatment'])
                              for item in approved['source_amount_audit']],
                             [('$300 hoặc $450/ngày', 'worker'), ('7000', 'excluded')])
            daily_post = posting_from_data(raw_post(daily, businessName='Example Nails'))
            self.assertEqual(self.project(daily_post, base_data())['fields']['salary_amount'], '2700')
            self.assertIsNone(owner_confirmed_pay(daily.replace('7000/', '7100/')))
        extra = daily + ' Extra salary $9000/week.'
        with patch('nailhub.validation.OWNER_CONFIRMED_DAILY_SOURCE_SHA256',
                   hashlib.sha256(extra.encode('utf-8')).hexdigest()):
            self.assertIsNone(owner_confirmed_pay(extra))
        with patch('nailhub.validation.OWNER_CONFIRMED_TYPO_SOURCE_SHA256',
                   hashlib.sha256(typo.encode('utf-8')).hexdigest()):
            approved = owner_confirmed_pay(typo)
            self.assertEqual(approved['high'], '2800')
            self.assertEqual((approved['owner_decision_ref'], approved['source_sha256']),
                             ('D025', hashlib.sha256(typo.encode('utf-8')).hexdigest()))
            self.assertIn('ampersand currency typo',
                          approved['source_amount_audit'][0]['reason'])
            self.assertEqual((approved['source_amount_audit'][1]['amount_text'],
                              approved['source_amount_audit'][1]['treatment']),
                             ('$1000', 'excluded'))
            typo_post = posting_from_data(raw_post(typo, businessName='Example Nails'))
            self.assertEqual(self.project(typo_post, base_data())['fields']['salary_amount'], '2800')
            self.assertIsNone(owner_confirmed_pay(typo.replace('&2800', '&2900')))

    def test_business_sale_scope_price_and_source_named_restaurant(self):
        tea = 'Shop rent $3600. Giá bán yêu thương tình chòm xóm $120K.'
        tea_post = posting_from_data(raw_post(tea, title='Cần bán tiệm trà sữa',
                                              businessName='BaoNail.com'))
        self.assertEqual(category_proposal(tea_post)['target'], 'SalonForSale')
        self.assertEqual(source_title_linked_sale_price(tea_post)['low'], '120000')
        tea_result = self.project(tea_post, base_data())
        self.assertEqual(tea_result['fields']['salon'], 'nailhub.ai')
        self.assertFalse(tea_result['field_corrections']['salon']['actual_business_name_known'])
        self.assertEqual(tea_result['field_corrections']['salon']['source_field'], 'owner_publisher_fallback')
        service = posting_from_data(raw_post('Giá bán dịch vụ $120K.', title='Cần bán tiệm trà sữa',
                                              businessName='Example Tea Shop'))
        self.assertIsNone(source_title_linked_sale_price(service))
        restaurant = ('Nhà hàng SUNRISE Vietnamese Bistro and Teahouse có khách ổn định. '
                      'Giá bán phù hợp.')
        pho = posting_from_data(raw_post(restaurant, title='SANG TIỆM PHỞ',
                                          businessName='BaoNail.com'))
        self.assertEqual(category_proposal(pho)['target'], 'SalonForSale')
        self.assertEqual(source_store_name_repair(pho), {})
        with self.assertRaisesRegex(NeedsHuman, 'explicit source business name'):
            self.project(pho, base_data())
        with patch('nailhub.validation.OWNER_CONFIRMED_RESTAURANT_NAME_SOURCE_SHA256',
                   hashlib.sha256(restaurant.encode('utf-8')).hexdigest()):
            projected = self.project(pho, base_data())
            self.assertEqual((projected['fields']['salon'], projected['fields']['salary_type']),
                             ('SUNRISE Vietnamese Bistro', 'negotiated'))
        repeated = Posting(pho.id, pho.status, pho.category, restaurant + '\n' + restaurant, pho.fields)
        self.assertEqual(source_store_name_repair(repeated), {})
        with self.assertRaisesRegex(NeedsHuman, 'explicit source business name'):
            self.project(repeated, base_data())

    def test_publisher_fallback_preserves_source_names_and_requires_explicit_name_review(self):
        unknown = posting_from_data(raw_post('Cần thợ nail. Lương thương lượng.',
            businessName='BaoNail.com', priceType='Negotiable', price=0))
        result = self.project(unknown, base_data())
        self.assertEqual(result['fields']['salon'], 'nailhub.ai')
        self.assertEqual(result['publisher_branding']['category'], 'JobPosting')
        self.assertEqual(result['publisher_branding']['source_hash'], unknown.fingerprint)
        named = posting_from_data(raw_post('Business name: Orchid Nails. Hiring nail techs.',
            businessName='BaoNail.com', priceType='Negotiable', price=0))
        with self.assertRaisesRegex(NeedsHuman, 'explicit source business name'):
            self.project(named, base_data())
        titled = posting_from_data(raw_post('Hiring nail techs.', title='Orchid Nails hiring',
            businessName='BaoNail.com', priceType='Negotiable', price=0))
        self.assertEqual(self.project(titled, base_data())['fields']['salon'], 'Orchid Nails')
        self.assertEqual(source_store_name_repair(titled)['salon']['source_field'], 'title_business_name')
        generic = posting_from_data(raw_post('Hiring nail techs.', title='Nail Salon for sale',
            businessName='BaoNail.com', priceType='Negotiable', price=0))
        self.assertEqual(source_store_name_repair(generic), {})
        for generic_title in ('Now Hiring Nail Techs', 'Urgently Need Nails', 'Nail Salon For Sale'):
            generic_post = posting_from_data(raw_post('Please call for details.',
                title=generic_title, businessName='BaoNail.com',
                priceType='Negotiable', price=0))
            self.assertEqual(publisher_name_fallback(generic_post)['salon']['value'], 'nailhub.ai')
        for unknown_label in ('Business name: not disclosed. Hiring nail techs.',
                              'Salon name: Unknown. Hiring nail techs.',
                              'Business name: BaoNail.com. Hiring nail techs.',
                              'Tên tiệm: chưa công bố. Cần thợ nail.',
                              '店名：未公开。招聘美甲师。'):
            unlabeled = posting_from_data(raw_post(unknown_label,
                businessName='BaoNail.com', priceType='Negotiable', price=0))
            self.assertEqual(publisher_name_fallback(unlabeled)['salon']['value'], 'nailhub.ai')
            placeholder_value = ('BaoNail.com' if 'BaoNail.com' in unknown_label else
                unknown_label.split(':', 1)[-1].split('：', 1)[-1].split('.', 1)[0].split('。', 1)[0].strip())
            model_placeholder = base_data()
            model_placeholder['location'] = {'salon': {'value': placeholder_value,
                'evidence': {'quote': placeholder_value}}}
            self.assertEqual(self.project(unlabeled, model_placeholder)['fields']['salon'], 'nailhub.ai')
        contradictory = posting_from_data(raw_post(
            'Business name: not disclosed. Orchid Nails is hiring nail techs.',
            businessName='BaoNail.com', priceType='Negotiable', price=0))
        with self.assertRaisesRegex(NeedsHuman, 'explicit source business name'):
            publisher_name_fallback(contradictory)
        for contradictory_labels in (
                'Salon name: Unknown\nBusiness name: Lotus\nHiring nail techs.',
                'Salon name: Unknown; Business name: Lotus. Hiring nail techs.',
                'Tên tiệm: chưa công bố\nTên salon: Lotus\nCần thợ nail.',
                'Tên tiệm: chưa công bố; Tên salon: Lotus. Cần thợ nail.',
                '店名：未公开\n商家名称：Lotus\n招聘美甲师。',
                '店名：未公开；商家名称：Lotus。招聘美甲师。'):
            ambiguous = posting_from_data(raw_post(contradictory_labels,
                businessName='BaoNail.com', priceType='Negotiable', price=0))
            with self.assertRaisesRegex(NeedsHuman, 'explicit source business name'):
                publisher_name_fallback(ambiguous)
        brand_unknown = posting_from_data(raw_post(
            'Business name: Unknown Nails. Hiring nail techs.',
            businessName='BaoNail.com', priceType='Negotiable', price=0))
        real_unknown_name = base_data()
        real_unknown_name['location'] = {'salon': {'value': 'Unknown Nails',
            'evidence': {'quote': 'Unknown Nails'}}}
        self.assertEqual(self.project(brand_unknown, real_unknown_name)['fields']['salon'], 'Unknown Nails')
        conflicting = posting_from_data(raw_post(
            'https://www.baonail.com/index.php?stores=Glaze-Nails&id=one Hiring nail techs.',
            title='Orchid Nails hiring', businessName='BaoNail.com',
            priceType='Negotiable', price=0))
        with self.assertRaisesRegex(NeedsHuman, 'names conflict'):
            self.project(conflicting, base_data())
        source_model = base_data()
        source_model['location'] = {'salon': {'value': 'Orchid Nails',
            'evidence': {'quote': 'Orchid Nails'}}}
        self.assertEqual(self.project(named, source_model)['fields']['salon'], 'Orchid Nails')
        actual = posting_from_data(raw_post('Cần thợ nail.', businessName='Real Nails',
            priceType='Negotiable', price=0))
        self.assertEqual(publisher_name_fallback(actual), {})
        self.assertEqual(self.project(actual, base_data())['fields']['salon'], 'Real Nails')
        empty = posting_from_data(raw_post('Cần thợ nail.', businessName='',
            priceType='Negotiable', price=0))
        self.assertEqual(self.project(empty, base_data())['fields']['salon'], 'nailhub.ai')
        descriptor = posting_from_data(raw_post('Tiệm Nail Sang Vung HARRISONBURG cần thợ.',
            businessName='BaoNail.com', priceType='Negotiable', price=0))
        guessed = base_data()
        guessed['location'] = {'salon': {'value': 'Tiệm Nail Sang Vung HARRISONBURG',
            'evidence': {'quote': 'Tiệm Nail Sang Vung HARRISONBURG'}}}
        self.assertEqual(self.project(descriptor, guessed)['fields']['salon'], 'nailhub.ai')

    def test_existing_unknown_labels_use_fallback_but_real_unknown_nails_is_preserved(self):
        for current in ('Unknown', 'N/A', 'nailhub.ai'):
            with self.subTest(current=current):
                post = posting_from_data(raw_post('Salon name: not disclosed. Hiring nail techs.',
                    businessName=current, priceType='Negotiable', price=0))
                data = base_data()
                data['location'] = {'salon': {'value': 'not disclosed',
                    'evidence': {'quote': 'Salon name: not disclosed'}}}
                result = self.project(post, data)
                self.assertEqual(result['fields']['salon'], 'nailhub.ai')
                self.assertFalse(result['publisher_branding']['actual_business_name_known'])
                named = posting_from_data(raw_post(
                    'Hiring nail techs. https://www.baonail.com/index.php?stores=Glaze-Nails&id=one. Call us.',
                    businessName=current, priceType='Negotiable', price=0))
                real = self.project(named, base_data())
                self.assertEqual(real['fields']['salon'], 'Glaze Nails')
                self.assertIsNone(real['publisher_branding'])
        actual = posting_from_data(raw_post('Unknown Nails is hiring nail techs.',
            businessName='Unknown Nails', priceType='Negotiable', price=0))
        actual_result = self.project(actual, base_data())
        self.assertEqual(actual_result['fields']['salon'], 'Unknown Nails')
        self.assertIsNone(actual_result['publisher_branding'])

    def test_publisher_branding_only_changes_localized_publisher_phrase(self):
        samples = (
            ('Tiệm cần thợ.', 'Tiệm Nailhub.ai cần thợ. Phone 4045550100.', 'JobPosting',
             'Nailhub.ai đăng bài tuyển dụng cần thợ. Phone 4045550100.'),
            ('Bán tiệm.', 'Salon Nailhub.ai sang nhượng. Giá $120K.', 'SalonForSale',
             'Nailhub.ai đăng tin sang nhượng sang nhượng. Giá $120K.'),
            ('招聘美甲师', '店 Nailhub.ai 招聘美甲师。', 'JobPosting',
             'Nailhub.ai 发布招聘信息 招聘美甲师。'),
            ('Hiring nail techs', 'Salon Nailhub.ai is hiring. Call 4045550100.', 'JobPosting',
             'Nailhub.ai posts this job listing is hiring. Call 4045550100.'),
        )
        for original, rewritten, category, expected in samples:
            with self.subTest(original=original):
                self.assertEqual(publisher_branding_rewrite(original, rewritten, category), expected)
        self.assertEqual(publisher_branding_rewrite('Cần thợ', 'Giữ số 4045550100.', 'JobPosting'),
                         'Nailhub.ai đăng bài tuyển dụng\nGiữ số 4045550100.')
        self.assertEqual(publisher_branding_rewrite('Cần thợ',
            'Nailhub.ai đăng bài tuyển dụng. Giữ số 4045550100.', 'JobPosting'),
            'Nailhub.ai đăng bài tuyển dụng. Giữ số 4045550100.')
        bilingual = '诚聘美甲师\nCần thợ nail, gọi 4045550100.'
        self.assertEqual(publisher_branding_rewrite(bilingual,
            'Tiệm Nailhub.ai cần thợ nail. 另有中文说明。', 'JobPosting'),
            'Nailhub.ai đăng bài tuyển dụng cần thợ nail. 另有中文说明。')
        self.assertEqual(publisher_branding_rewrite(bilingual,
            '店 Nailhub.ai 招聘美甲师。 Cần thợ nail.', 'JobPosting'),
            'Nailhub.ai 发布招聘信息 招聘美甲师。 Cần thợ nail.')

    def test_rental_house_location_does_not_become_unrelated_salon_contact(self):
        body = ('Nhà ở Brick Town, NJ có 3 phòng cho thuê, $700-$800/tháng.\n'
                'Tiệm cũng cần thợ nail nữ.')
        post = posting_from_data(raw_post(body, businessName='',
            address='Brick Town, NJ 08724', city='Brick Town', state='NJ', zipCode='08724',
            priceType='Negotiable', price=0))
        with self.assertRaisesRegex(NeedsHuman, 'rental house'):
            require_salon_location_role(post, post.fields, 'JobPosting')
        with self.assertRaisesRegex(NeedsHuman, 'rental house'):
            self.project(post, base_data())
        independently_located = Posting(post.id, post.status, post.category,
            body + '\nTiệm ở Brick Town, NJ cần thợ nail.', post.fields)
        require_salon_location_role(independently_located, independently_located.fields, 'JobPosting')
        other_city = Posting(post.id, post.status, post.category,
            body.replace('Brick Town, NJ', 'Toms River, NJ'), post.fields)
        require_salon_location_role(other_city, other_city.fields, 'JobPosting')

    def test_model_cannot_swap_two_valid_stored_phones_from_redacted_source(self):
        body = 'Bán tiệm nail. Giá $120K. Em bớt còn $80K. Call Phone ☎ or text Phone ☎.'
        post = posting_from_data(raw_post(body, title='Salon for sale',
                                          businessName='Example Nails',
                                          contactInfo={'name':'Example Nails', 'phone':'8586100507',
                                                       'mobilePhone':'9016822003', 'email':''}))
        data = base_data()
        data['location'] = {'phone_primary': {'value':'9016822003', 'evidence':{'quote':'text Phone ☎'}},
                            'phone_other': {'value':'8586100507', 'evidence':{'quote':'Call Phone ☎'}}}
        clean = normalize_extraction(post, data)
        self.assertNotIn('phone_primary', clean['location'])
        self.assertNotIn('phone_other', clean['location'])
        projected = self.project(post, data)
        self.assertEqual((projected['fields']['salary_amount'], projected['fields']['phone_primary'],
                          projected['fields']['phone_other']), ('80000', '8586100507', '9016822003'))
        new_phone = Posting(post.id, post.status, post.category,
                            body + '\nNew contact: 4045550199.', post.fields)
        changed = copy.deepcopy(data)
        changed['location']['phone_primary'] = {'value':'4045550199',
                                               'evidence':{'quote':'New contact: 4045550199'}}
        self.assertIn('phone_primary', normalize_extraction(new_phone, changed)['location'])

    def test_and_up_slash_per_week_is_a_bounded_worker_salary(self):
        pay_line = 'We pay $1,000 and up / per week if hired.'
        source = 'Looking for nail technicians.\n' + pay_line + '\nThe shop is open 7 days a week.'
        post = posting_from_data(raw_post(source, title='Hiring nail technicians',
                                          businessName='Example Nails'))
        data = base_data()
        data['salary_candidates'] = [candidate('$1,000 and up', pay_line, 'week', 'other')]
        self.assertNotIn('salary_candidates', normalize_extraction(post, data))
        projected = self.project(post, data)
        self.assertEqual((projected['category'], projected['fields']['salary_type'],
                          Decimal(projected['fields']['salary_amount'])),
                         ('JobPosting', 'weekly', Decimal('1000')))
        extra = Posting(post.id, post.status, post.category, source + '\n$2,000.', post.fields)
        self.assertIn('salary_candidates', normalize_extraction(extra, data))
        monthly = source.replace('/ per week', '/ per month')
        monthly_pay = source_salary_policy(monthly)
        self.assertEqual(monthly_pay['period'], 'month')
        self.assertEqual(Decimal(monthly_pay['high']), Decimal('1000') * 12 / 52)

    def test_hourly_alternative_commission_without_certain_weekly_hours(self):
        source = ('Position 1: Fridays 9 AM–7:30 PM; Position 2: Tuesday–Friday 9 AM–7:30 PM.\n'
                  'Compensation: 50% commission or $17.50 hourly pay')
        post = posting_from_data(raw_post(source))
        data = base_data()
        data['salary_candidates'] = [candidate('$17.50', '50% commission or $17.50 hourly pay', 'hour')]
        result = self.project(post, data)
        self.assertEqual((result['fields']['salary_type'], result['fields']['salary_amount']),
                         ('negotiated', ''))
        self.assertNotIn('salary_candidates', normalize_extraction(post, data))
        self.assertIsNone(source_salary_policy('Salary $1500/week or $20/hour'))

    def test_explicit_six_day_pay_duration_copies_highest_literal_range(self):
        source = ('Cần thợ tay chân nước, và làm everything. Bao lương $1000-1200 thợ dip, '
                  '$1200-1500/6ngày thợ bột, tùy theo khả năng. Tiệm khu trắng, giá rất cao '
                  '(basic pedi $38)Tip 90%, khách dễ thương.')
        post = posting_from_data(raw_post(source))
        data = base_data()
        data['salary_candidates'] = [candidate('$1000-1200', 'Bao lương $1000-1200 thợ dip', 'week'),
                                     candidate('$1200-1500', '$1200-1500/6ngày thợ bột', 'day'),
                                     candidate('$38', 'basic pedi $38', None, 'service')]
        result = self.project(post, data)
        self.assertEqual((result['fields']['salary_type'], result['fields']['salary_amount']),
                         ('weekly', '1500'))
        self.assertTrue(result['salary']['copy_no_period'])
        with self.assertRaises(NeedsHuman):
            source_salary_policy(source.replace('(basic pedi $38)', '(bonus $38)'))

    def test_explicit_bao_luong_without_period_precedes_model_unit_guess(self):
        source = 'Bao lương $1,600+ HOẶC ăn chia theo tay nghề\nTip cao'
        post = posting_from_data(raw_post(source))
        data = base_data()
        data['salary_candidates'] = [candidate('$1,600', 'Bao lương $1,600+ HOẶC ăn chia theo tay nghề', 'week')]
        result = self.project(post, data)
        self.assertEqual((result['fields']['salary_type'], result['fields']['salary_amount']),
                         ('weekly', '1600'))
        with self.assertRaises(NeedsHuman):
            source_salary_policy('Bao lương $1,600 hoặc $1,800')

    def test_recruiting_body_outweighs_stale_sale_title_without_asking_price(self):
        post = posting_from_data(raw_post('Cần thợ có kinh nghiệm làm bột và gel. Bao lương theo tay nghề.',
                                          title='Bán tiệm nails'))
        self.assertEqual(category_proposal(post)['target'], 'JobPosting')
        sale = posting_from_data(raw_post('Bán tiệm nails giá bán $80K. Cần thợ nail.',
                                          title='Bán tiệm nails'))
        self.assertEqual(category_proposal(sale)['target'], 'SalonForSale')
        for body in ('Hiring nail techs. Price: $60K', 'Giá 60K, cần thợ nail'):
            with self.subTest(body=body):
                mixed = posting_from_data(raw_post(body, title='Salon for sale'))
                self.assertEqual(category_proposal(mixed)['target'], 'SalonForSale')
        headline_price = posting_from_data(raw_post('Hiring nail techs.', title='Salon for sale $60K'))
        self.assertEqual(category_proposal(headline_price)['target'], 'SalonForSale')

    def test_title_linked_asking_discount_stays_in_sale_chain_and_has_no_period(self):
        def asking(body, title='Nail salon for sale'):
            return source_title_linked_sale_price(posting_from_data(raw_post(body, title=title)))
        self.assertEqual(asking('Asking price $120K. Rent $2000. Chủ giảm còn $1000/month.')['low'],
                         '120000')
        self.assertIsNone(asking('Asking price $48K/month.'))
        self.assertEqual(asking('Asking price $48K.')['low'], '48000')
        self.assertEqual(asking('Giá $120K. Em bớt còn $80K. Cần thợ nail.')['low'], '80000')
        self.assertEqual(asking('Giá $120K. Chủ giảm còn $80K/tháng.')['low'], '120000')
        self.assertEqual(asking('Giá $120K. Rent $2000/tháng. Em bớt còn $1000.')['low'], '120000')
        self.assertIsNone(asking('Asking price $48K for manicure.'))

    def test_case_only_city_from_body_keeps_canonical_full_address_city(self):
        source = 'Cần thợ nails. Tiệm ở thành phố BIRMINGHAM , tiểu bang ALABAMA.'
        post = posting_from_data(raw_post(source, address='1030 Inverness Corners Birmingham, AL 35242',
                                          city='Birmingham', state='AL', zipCode='35242'))
        data = base_data()
        data['location'] = {'city': {'value':'BIRMINGHAM', 'evidence':{'quote':'BIRMINGHAM'}},
                            'state': {'value':'ALABAMA', 'evidence':{'quote':'tiểu bang ALABAMA'}}}
        normalized = normalize_extraction(post, data)
        self.assertNotIn('city', normalized['location'])
        self.assertNotIn('state', normalized['location'])
        result = self.project(post, data)
        self.assertEqual(result['fields']['city'], 'Birmingham')
        conflict = posting_from_data(raw_post(source.replace('ALABAMA', 'WV'),
                                              address='1030 Inverness Corners Birmingham, WV 35242',
                                              city='Birmingham', state='AL', zipCode='35242'))
        conflict_data = copy.deepcopy(data)
        conflict_data['location'].pop('state')
        self.assertIn('city', normalize_extraction(conflict, conflict_data)['location'])
        full_name_conflict = posting_from_data(raw_post(source.replace('ALABAMA', 'GEORGIA'),
                                                        address='1030 Inverness Corners Birmingham, AL 35242',
                                                        city='Birmingham', state='AL', zipCode='35242'))
        conflicting_data = copy.deepcopy(data)
        conflicting_data['location']['state']['value'] = 'GEORGIA'
        conflicting_data['location']['state']['evidence'] = {'quote':'tiểu bang GEORGIA'}
        self.assertIn('city', normalize_extraction(full_name_conflict, conflicting_data)['location'])
        self.assertIn('state', normalize_extraction(full_name_conflict, conflicting_data)['location'])
        for city, code, full, zip_code in (('Monroe','LA','LOUISIANA','71291'),
                                            ('Lexington','KY','KENTUCKY','40502')):
            with self.subTest(code=code):
                city_source = f'Cần thợ nails. Tiệm ở thành phố {city.upper()} , tiểu bang {full}.'
                other = posting_from_data(raw_post(city_source,
                    address=f'4920 Cypress St {city}, {code} {zip_code}', city=city,
                    state=code, zipCode=zip_code))
                other_data = base_data()
                other_data['location'] = {'city': {'value':city.upper(), 'evidence':{'quote':city.upper()}},
                    'state': {'value':full, 'evidence':{'quote':f'tiểu bang {full}'}}}
                cleaned = normalize_extraction(other, other_data)
                self.assertNotIn('city', cleaned['location'])
                self.assertNotIn('state', cleaned['location'])

    def test_full_state_alias_metro_phrase_needs_same_stored_city_and_single_branch(self):
        address = '263 E Flournoy Lucas Rd Shreveport, LA 71106'
        def normalized(body, city, state, quote):
            post = posting_from_data(raw_post(body, title='Nail salon for sale',
                                              address=address, city='Shreveport', state='LA', zipCode='71106'))
            data = base_data()
            data['location'] = {'city': {'value': city, 'evidence': {'quote': quote}},
                                'state': {'value': state, 'evidence': {'quote': quote}}}
            return normalize_extraction(post, data)['location']
        body = 'Nail salon for sale ( Shreveport- Bossier, Louisiana).'
        self.assertEqual(normalized(body, 'Shreveport', 'Louisiana', '( Shreveport- Bossier, Louisiana)'), {})
        wrong_state = 'Nail salon for sale ( Shreveport- Bossier, Texas).'
        self.assertIn('state', normalized(wrong_state, 'Shreveport', 'Texas',
                                          '( Shreveport- Bossier, Texas)'))
        different_city = 'Nail salon for sale ( Bossier, Louisiana).'
        result = normalized(different_city, 'Bossier', 'Louisiana', '( Bossier, Louisiana)')
        self.assertIn('city', result)
        self.assertIn('state', result)
        branch = body + '\nSecond branch: 456 Oak Rd Bossier, LA 71111.'
        self.assertIn('state', normalized(branch, 'Shreveport', 'Louisiana',
                                          '( Shreveport- Bossier, Louisiana)'))
        branch_city_pair = body + '\nSecond branch: Bossier, TX 75001.'
        self.assertIn('state', normalized(branch_city_pair, 'Shreveport', 'Louisiana',
                                          '( Shreveport- Bossier, Louisiana)'))

    def test_state_postal_code_case_is_noop_with_complete_current_address(self):
        body = ('Hiring nail technicians.\n'
                'Location 1: 123 Towne Center Dr, Lexington Ky 40511\n'
                'Location 2: 3181 Beaumont Centre Cir, Lexington, Ky 40511')
        post = posting_from_data(raw_post(body, businessName='Club Nails',
                                          address='123 Towne Center Dr Lexington, KY 40511',
                                          city='', state='KY', zipCode=''))
        data = base_data()
        data['location'] = {'city': {'value':'Lexington', 'evidence':{'quote':'Lexington Ky 40511'}},
                            'state': {'value':'Ky', 'evidence':{'quote':'Lexington Ky 40511'}},
                            'zip': {'value':'40511', 'evidence':{'quote':'Lexington Ky 40511'}}}
        clean = normalize_extraction(post, data)
        self.assertNotIn('state', clean['location'])
        with tempfile.TemporaryDirectory() as directory:
            projected = proposal(post, clean, settings(directory))
        self.assertEqual((projected['fields']['city'], projected['fields']['state'], projected['fields']['zip']),
                         ('Lexington', 'KY', '40511'))
        other_state = posting_from_data(raw_post(body, businessName='Club Nails',
                                                 address='123 Towne Center Dr Lexington, KY 40511',
                                                 city='Lexington', state='IL', zipCode='40511'))
        self.assertEqual(current_address_state_repair(other_state)['state']['value'], 'KY')
        changed = copy.deepcopy(data)
        changed['location']['city'] = {'value':'Fairburn', 'evidence':{'quote':'Fairburn'}}
        changed_post = Posting(post.id, post.status, post.category, body + '\nFairburn, GA 30213', post.fields)
        with self.assertRaises(NeedsHuman):
            normalize_extraction(changed_post, changed)
        mixed_state = Posting(post.id, post.status, post.category, body + '\nBranch 3: Atlanta GA 30301', post.fields)
        with self.assertRaises(NeedsHuman):
            normalize_extraction(mixed_state, data)

    def test_full_state_name_is_noop_when_blank_city_zip_repair_from_stored_address(self):
        cases = (
            ('Hiring nail technicians. Salon in Thompson Station - Spring Hill - Tennessee.',
             '2100 Branford Place Thompsons Station, TN 37179', 'TN', 'Tennessee',
             'Salon in Thompson Station - Spring Hill - Tennessee', 'Thompsons Station', '37179',
             'JobPosting'),
            ('Bán tiệm nail. Thành phố Southaven, bang Mississippi – Zip code 38671.',
             '1138 Church Rd W Southaven, MS 38671', 'MS', 'Mississippi',
             'bang Mississippi', 'Southaven', '38671', 'SalonForSale'),
        )
        for body, address, code, full, quote, city, zip_code, category in cases:
            with self.subTest(code=code):
                post = posting_from_data(raw_post(body, title='Bán tiệm nail' if code == 'MS' else 'Hiring nail techs',
                                                  businessName='Example Nails', address=address,
                                                  city='', state=code, zipCode=''))
                data = base_data()
                data['location'] = {'state': {'value':full, 'evidence':{'quote':quote}}}
                clean = normalize_extraction(post, data)
                self.assertNotIn('state', clean['location'])
                with tempfile.TemporaryDirectory() as directory:
                    projected = proposal(post, clean, settings(directory))
                self.assertEqual((projected['category'], projected['fields']['city'],
                                  projected['fields']['state'], projected['fields']['zip']),
                                 (category, city, code, zip_code))
                other_full = 'Alabama' if code == 'TN' else 'Louisiana'
                conflicted = Posting(post.id, post.status, post.category,
                                     body + '\nAlso in ' + other_full + '.', post.fields)
                self.assertIn('state', normalize_extraction(conflicted, data)['location'])
                other_branch = Posting(post.id, post.status, post.category,
                                       body + '\nSecond branch: 456 Oak Rd Atlanta, GA 30301.', post.fields)
                with self.assertRaises(NeedsHuman):
                    normalize_extraction(other_branch, data)
                incomplete = posting_from_data(raw_post(body, title='Hiring nail techs',
                                                       businessName='Example Nails', address='2100 Branford Place',
                                                       city='', state=code, zipCode=''))
                self.assertIn('state', normalize_extraction(incomplete, data)['location'])

    def test_area_only_city_mention_keeps_complete_stored_address(self):
        source = ('Cần thợ Trẻ có kinh nghiệm làm Bột gelx Dip tiệm khu vực Santa Monica\n'
                  'Bao lương theo tay nghề, hơn chia 6/4')
        post = posting_from_data(raw_post(source, title='Bán tiệm nails',
                                          address='11968 Wilshire Boulevard Los Angeles, CA 90025',
                                          city='Los Angeles', state='CA', zipCode='90025'))
        data = base_data()
        data['location'] = {'city': {'value':'Santa Monica', 'evidence':{'quote':'Santa Monica'}}}
        self.assertNotIn('city', normalize_extraction(post, data)['location'])
        result = self.project(post, data)
        self.assertEqual((result['category'], result['fields']['city'], result['fields']['salary_type']),
                         ('JobPosting', 'Los Angeles', 'negotiated'))
        explicit_other_city = posting_from_data(raw_post(source + '\nAddress: Santa Monica, CA 90401',
                                                 title='Bán tiệm nails',
                                                 address='11968 Wilshire Boulevard Los Angeles, CA 90025',
                                                 city='Los Angeles', state='CA', zipCode='90025'))
        conflicting_data = copy.deepcopy(data)
        conflicting_data['location']['city']['evidence'] = {'quote':'Address: Santa Monica, CA 90401'}
        self.assertIn('city', normalize_extraction(explicit_other_city, conflicting_data)['location'])

        region = 'Tiệm nằm ở vùng Rancho San Diego, cần thợ nails.'
        region_post = posting_from_data(raw_post(region, title='Hiring nail technicians',
                                                 businessName='Example Nails',
                                                 address='2451 Jamacha Rd El Cajon, CA 92019',
                                                 city='El Cajon', state='CA', zipCode='92019'))
        region_data = base_data()
        region_data['location'] = {'city':{'value':'Rancho San Diego',
                                          'evidence':{'quote':'vùng Rancho San Diego'}}}
        self.assertNotIn('city', normalize_extraction(region_post, region_data)['location'])
        self.assertEqual(self.project(region_post, region_data)['fields']['city'], 'El Cajon')
        for added in ('\nRancho San Diego, CA 92020',
                      '\nAddress: 111 Main St Rancho San Diego, CA 92020'):
            with self.subTest(added=added):
                conflicting = Posting(region_post.id, region_post.status, region_post.category,
                                      region + added, region_post.fields)
                self.assertIn('city', normalize_extraction(conflicting, region_data)['location'])

    def test_timed_distance_city_reference_keeps_complete_stored_city(self):
        proximity = 'Tiệm cách Chicago 1h30p'
        body = 'Cần thợ nail. Bao lương $3,000/2 tuần hoặc ăn chia.\n' + proximity
        post = posting_from_data(raw_post(body, title='Hiring nail technicians',
                                          businessName='Example Nails',
                                          address='Mishawaka, IN 46545', city='Mishawaka',
                                          state='IN', zipCode='46545'))
        data = base_data()
        data['location'] = {'city': {'value':'Chicago', 'evidence':{'quote':proximity}}}
        self.assertNotIn('city', normalize_extraction(post, data)['location'])
        with tempfile.TemporaryDirectory() as directory:
            projected = proposal(post, normalize_extraction(post, data), settings(directory))
        self.assertEqual((projected['fields']['city'], projected['fields']['state'],
                          Decimal(projected['fields']['salary_amount'])),
                         ('Mishawaka', 'IN', Decimal('1500')))
        explicit_city = Posting(post.id, post.status, post.category,
                                body + '\nTiệm ở Chicago, IL 60601.', post.fields)
        self.assertIn('city', normalize_extraction(explicit_city, data)['location'])
        incomplete = posting_from_data(raw_post(body, title='Hiring nail technicians',
                                                businessName='Example Nails',
                                                address='Mishawaka', city='Mishawaka',
                                                state='IN', zipCode=''))
        self.assertIn('city', normalize_extraction(incomplete, data)['location'])
        minutes = 'Tiệm nail cách Bolsa 25 phút'
        minute_post = posting_from_data(raw_post('Cần thợ nail.\n' + minutes,
                                                 title='Hiring nail technicians',
                                                 businessName='Example Nails',
                                                 address='642 S Beach Blvd La Habra, CA 90631',
                                                 city='La Habra', state='CA', zipCode='90631'))
        minute_data = base_data()
        minute_data['location'] = {'city': {'value':'Bolsa', 'evidence':{'quote':minutes}}}
        self.assertNotIn('city', normalize_extraction(minute_post, minute_data)['location'])

    def test_full_state_name_in_title_is_noop_for_same_complete_stored_address(self):
        title = 'Selling nail salon in Paducah, Kentucky!'
        body = ('Large salon for sale. Business income over one million annually. '
                'Contact us if you wish to buy.')
        post = posting_from_data(raw_post(body, title=title, businessName='Example Nails',
                                          address='2929 James Sanders Blvd Paducah, KY 42001',
                                          city='Paducah', state='KY', zipCode='42001'))
        data = base_data()
        data['category'] = {'target':'JobPosting', 'field':'title',
                            'evidence':{'quote':'Selling nail salon in Paducah, Kentucky!'}}
        data['salary_candidates'] = [candidate('one million annually',
                                               'Business income over one million annually',
                                               'year', 'other')]
        data['location'] = {'state':{'value':'Kentucky',
                                     'evidence':{'quote':'Paducah, Kentucky!'}}}
        clean = normalize_extraction(post, data)
        self.assertNotIn('state', clean['location'])
        projected = self.project(post, data)
        self.assertEqual((projected['category'], projected['fields']['salary_type'],
                          projected['fields']['city'], projected['fields']['state'],
                          projected['fields']['zip']),
                         ('SalonForSale', 'negotiated', 'Paducah', 'KY', '42001'))
        for changed in (
            Posting(post.id, post.status, post.category,
                    body + '\nAnother branch in Mobile, AL 36602.', post.fields),
            Posting(post.id, post.status, post.category,
                    body + '\nAnother branch is in Tennessee.', post.fields),
            posting_from_data(raw_post(body, title=title, businessName='Example Nails',
                                       address='2929 James Sanders Blvd',
                                       city='Paducah', state='KY', zipCode='42001')),
        ):
            with self.subTest(changed=changed.content):
                with self.assertRaises(EvidenceError):
                    normalize_extraction(changed, data)

    def test_hyphenated_city_full_state_in_body_is_noop_only_for_same_place(self):
        place = 'Tiệm ở Paducah-Kentucky cần thợ có bằng Kentucky.'
        body = place + '\nBao lương thợ nails $1500/6 ngày hơn ăn chia.'
        post = posting_from_data(raw_post(body, title='Hiring nail technicians',
                                          businessName='Example Nails',
                                          address='2929 James Sanders Blvd Paducah, KY 42001',
                                          city='Paducah', state='KY', zipCode='42001'))
        data = base_data()
        data['location'] = {'state':{'value':'Kentucky', 'evidence':{'quote':place}}}
        self.assertNotIn('state', normalize_extraction(post, data)['location'])
        projected = self.project(post, data)
        self.assertEqual((projected['category'], projected['fields']['salary_amount'],
                          projected['fields']['city'], projected['fields']['state']),
                         ('JobPosting', '1500', 'Paducah', 'KY'))
        for changed in (
            body + '\nAnother salon is in Tennessee.',
            body + '\nAnother branch: 110 Oak St Mobile, AL 36602.',
        ):
            with self.subTest(changed=changed):
                conflicted = Posting(post.id, post.status, post.category, changed, post.fields)
                self.assertIn('state', normalize_extraction(conflicted, data)['location'])
        unanchored = Posting(post.id, post.status, post.category,
                             body.replace('Tiệm ở Paducah-Kentucky', 'Near Paducah-Kentucky'),
                             post.fields)
        unanchored_data = copy.deepcopy(data)
        unanchored_data['location']['state']['evidence'] = {
            'quote':'Near Paducah-Kentucky cần thợ có bằng Kentucky.'}
        self.assertIn('state', normalize_extraction(unanchored, unanchored_data)['location'])

    def test_salon_nail_city_full_state_alias_uses_complete_stored_locality(self):
        place = 'Cần bán tiệm nail ở Thibodaux - Louisiana'
        body = place + '.\nGiá bán tiệm $400K.'
        post = posting_from_data(raw_post(body, title='Salon for sale',
            businessName='BaoNail.com', address='Thibodaux, LA 70301',
            city='Thibodaux', state='LA', zipCode='70301', priceType='Negotiable', price=0))
        data = base_data()
        data['location'] = {'state': {'value': 'Louisiana', 'evidence': {'quote': place}}}
        self.assertEqual(normalize_extraction(post, data)['location'], {})
        projected = self.project(post, data)
        self.assertEqual((projected['category'], projected['fields']['salary_amount'],
                          projected['fields']['city'], projected['fields']['state'],
                          projected['fields']['salon']),
                         ('SalonForSale', '400000', 'Thibodaux', 'LA', 'nailhub.ai'))
        for changed in (body + '\nSecond branch: New Orleans, TX 70112.',
                        body + '\nAnother salon in Texas.',
                        body.replace('tiệm nail ở Thibodaux - Louisiana', 'tiệm nail ở New Orleans - Louisiana')):
            with self.subTest(changed=changed):
                conflicted = Posting(post.id, post.status, post.category, changed, post.fields)
                changed_data = copy.deepcopy(data)
                changed_data['location']['state']['evidence']['quote'] = (
                    place if place in changed else 'Cần bán tiệm nail ở New Orleans - Louisiana')
                self.assertIn('state', normalize_extraction(conflicted, changed_data)['location'])
        wrong_stored = Posting(post.id, post.status, post.category, body,
                               {**post.fields, 'address': 'Thibodaux, MS 70301', 'state': 'MS'})
        self.assertIn('state', normalize_extraction(wrong_stored, data)['location'])

    def test_state_name_in_prose_cannot_replace_complete_stored_city(self):
        phrase = 'Nhà hàng ở trung tâm Thành phố Mississippi.'
        post = posting_from_data(raw_post(phrase + '\nBusiness for sale.',
                                          title='Restaurant for sale',
                                          businessName='Example Bistro',
                                          address='Diberville, MS 39540',
                                          city='Diberville', state='MS', zipCode='39540'))
        data = base_data()
        data['location'] = {'city':{'value':'Mississippi', 'evidence':{'quote':phrase}}}
        # The wrong-entity recovery is restricted to the observed source.
        self.assertIn('city', normalize_extraction(post, data)['location'])
        source_digest = hashlib.sha256(post.content.encode('utf-8')).hexdigest()
        with patch('nailhub.validation.OBSERVED_STATE_AS_CITY_SOURCE_SHA256',
                   source_digest):
            self.assertNotIn('city', normalize_extraction(post, data)['location'])
            projected = self.project(post, data)
            self.assertEqual((projected['category'], projected['fields']['city'],
                              projected['fields']['state'], projected['fields']['zip']),
                             ('SalonForSale', 'Diberville', 'MS', '39540'))
        for conflicting_body in (phrase + '\nBusiness for sale.\nAddress: 123 Main St Mississippi, MS 39599.',
                                 phrase + '\nBusiness for sale.\nMississippi, MS 39599.'):
            with self.subTest(conflicting_body=conflicting_body):
                conflicting = Posting(post.id, post.status, post.category,
                                      conflicting_body, post.fields)
                with patch('nailhub.validation.OBSERVED_STATE_AS_CITY_SOURCE_SHA256',
                           hashlib.sha256(conflicting_body.encode('utf-8')).hexdigest()):
                    self.assertIn('city', normalize_extraction(conflicting, data)['location'])
        incomplete = posting_from_data(raw_post(phrase + '\nBusiness for sale.',
                                                title='Restaurant for sale',
                                                businessName='Example Bistro',
                                                address='Diberville', city='Diberville',
                                                state='MS', zipCode=''))
        with patch('nailhub.validation.OBSERVED_STATE_AS_CITY_SOURCE_SHA256',
                   source_digest):
            self.assertIn('city', normalize_extraction(incomplete, data)['location'])
        new_york = posting_from_data(raw_post('Business for sale. City of New York.',
                                              title='Business for sale',
                                              businessName='Example Bistro',
                                              address='Albany, NY 12207', city='Albany',
                                              state='NY', zipCode='12207'))
        new_york_data = base_data()
        new_york_data['location'] = {'city': {'value':'New York',
                                             'evidence': {'quote':'City of New York'}}}
        self.assertIn('city', normalize_extraction(new_york, new_york_data)['location'])

    def test_weekly_awards_and_lower_unbound_worker_range(self):
        weekly_a = 'Thợ gel lương $1,200.00 đến $1,500.00 cho 6 ngày/tuần chia 6/4.'
        weekly_b = 'Thợ dip lương $1,000.00 đến $1,200.00 cho 6 ngày/tuần chia 6/4.'
        unbound = 'Thợ manicure làm $800.00 đến $1,000.00 chia 6/4.'
        source = '\n'.join((weekly_a, weekly_b, unbound))
        parsed = source_worker_pay_clauses(source)
        self.assertIsNotNone(parsed)
        self.assertEqual((parsed['period'], parsed['high'], len(parsed['source_clauses'])),
                         ('week', '1500.00', 3))
        self.assertEqual(parsed['literal_unbound_clauses'], [unbound])
        post = posting_from_data(raw_post('Cần thợ nail.\n' + source,
                                          title='Hiring nail technicians',
                                          businessName='Example Nails'))
        data = base_data()
        data['salary_candidates'] = [candidate('$1,000.00 đến $1,200.00', weekly_b,
                                               'week', 'other')]
        projected = self.project(post, data)
        self.assertEqual((projected['category'], projected['fields']['salary_type'],
                          projected['fields']['salary_amount']),
                         ('JobPosting', 'weekly', '1500'))

        for changed in (
            unbound.replace('$1,000.00', '$1,600.00'),
            unbound.replace('chia 6/4', '/day chia 6/4'),
            unbound.replace('chia 6/4', '/hour chia 6/4'),
            unbound.replace('chia 6/4', '/month chia 6/4'),
        ):
            with self.subTest(changed=changed):
                self.assertIsNone(source_worker_pay_clauses('\n'.join((weekly_a, weekly_b, changed))))
        self.assertIsNone(source_worker_pay_clauses(source + '\nExtra worker pay $2,000.00.'))
        five_days = weekly_a.replace('6 ngày', '5 ngày')
        self.assertEqual(source_worker_pay_clauses(five_days)['high'], '1500.00')
        self.assertIsNone(source_worker_pay_clauses(
            five_days + '\nThợ dip lương $2,000.00/month.'))

    def test_parenthetical_worker_income_with_high_tips_keeps_literal_range(self):
        source = 'Tiệm đông khách (income 6k-10k tip cao)cần thợ nail.'
        parsed = source_worker_pay_clauses(source)
        self.assertEqual((parsed['high'], parsed['period'], parsed['copy_no_period']),
                         ('10000', None, True))
        post = posting_from_data(raw_post('Cần thợ nail.\n' + source,
                                          title='Hiring nail technicians',
                                          businessName='Example Nails'))
        data = base_data()
        data['salary_candidates'] = [candidate('6k-10k', 'income 6k-10k tip cao',
                                               'week', 'commission')]
        projected = self.project(post, data)
        self.assertEqual((projected['category'], projected['fields']['salary_amount']),
                         ('JobPosting', '10000'))
        for nonworker in (
            'Tiệm thu nhập 6k-10k/year, cần thợ nail.',
            'Business income 6k-10k/week, hiring nail technicians.',
            'Tiệm đông khách (income 6k-10k tip cao) doanh thu cửa hàng.',
            'Tiệm đông khách (income 6k-10k)cần thợ nail.',
        ):
            with self.subTest(nonworker=nonworker):
                self.assertIsNone(source_worker_pay_clauses(nonworker))
        self.assertIsNone(source_worker_pay_clauses(
            source + '\nTips $2,000/week.'))

    def test_labelled_hand_foot_service_prices_do_not_create_worker_pay(self):
        source = ('Tiệm giá cao, cần thợ nail.\n'
                  'Chân từ $56 lên đến $200\nTay thường từ $46 trở lên')
        masked = without_service_prices(source)
        self.assertEqual(len(masked), len(source))
        self.assertNotIn('$56', masked)
        self.assertNotIn('$200', masked)
        self.assertNotIn('$46', masked)
        post = posting_from_data(raw_post(source, title='Hiring nail technicians',
                                          businessName='Example Nails'))
        data = base_data()
        data['salary_candidates'] = [candidate('$56 lên đến $200',
                                               'Chân từ $56 lên đến $200', None, 'service'),
                                     candidate('$46', 'Tay thường từ $46 trở lên', None, 'service')]
        clean = normalize_extraction(post, data)
        self.assertNotIn('salary_candidates', clean)
        projected = self.project(post, data)
        self.assertEqual((projected['category'], projected['fields']['salary_type']),
                         ('JobPosting', 'negotiated'))
        worker_pay = source + '\nBao lương thợ $1500/week.'
        self.assertIn('$1500/week', without_service_prices(worker_pay))
        with_pay = Posting(post.id, post.status, post.category, worker_pay, post.fields)
        self.assertEqual(source_worker_pay_clauses(worker_pay)['high'], '1500')
        self.assertEqual(Decimal(self.project(with_pay, data)['fields']['salary_amount']),
                         Decimal('1500'))
        self.assertIn('$1500/week', without_service_prices(
            'Tiệm giá cao.\nChân từ $1500/week cho thợ foot specialist.'))
        for duration in ('/2 weeks', '/6 ngày', 'cho 6 ngày/tuần'):
            with self.subTest(duration=duration):
                self.assertIn('$1500', without_service_prices(
                    'Tiệm giá cao.\nChân từ $1500' + duration))
        for tail in ('guaranteed $1,500', 'bao $1,500', '$1,500 guaranteed', '$1,500'):
            with self.subTest(tail=tail):
                mixed = 'Cần thợ nail.\nGiá services\nChân từ $56; ' + tail
                visible = without_service_prices(mixed)
                self.assertNotIn('$56', visible)
                self.assertIn('$1,500', visible)
                if tail != '$1,500':
                    self.assertNotEqual(source_salary_policy(mixed)['mode'], 'negotiated')
        comma_unknown = 'Cần thợ nail.\nGiá services\nChân từ $56, $1,500'
        self.assertIn('$1,500', without_service_prices(comma_unknown))
        unknown_post = Posting(post.id, post.status, post.category, comma_unknown, post.fields)
        self.assertIn('salary_candidates', normalize_extraction(unknown_post, data))

    def test_staff_monthly_income_excludes_salon_revenue_and_tips(self):
        manager = 'starting salary of $100K/year'
        staff = ('Lương $7,000-$10,000 & up mỗi tháng cho thợ làm wax, lash, facial, tcn, gel, dip, '
                 'tùy theo tốc độ và kinh nghiệm (chưa tính tiền tips)')
        revenue = 'Tiệm ở Louisiana thu nhập hơn $2M mỗi năm'
        source = ('1. We are seeking for a manager:\n- ' + manager +
                  '\nUpdate: Tiệm đã mướn được người quản lý.\n2. ~' + staff +
                  '.\n-Trả lương hàng tuần.\n-' + revenue +
                  ' nên không làm 1099 & không trả lương 100% tiền mặt. Chỉ làm 1099 nếu thợ đủ điều kiện.')
        candidates = [candidate('$100K', manager, 'year'),
                      candidate('$7,000-$10,000', staff, 'month'),
                      candidate('$2M', revenue, 'year', 'revenue')]
        result = candidate_salary(source, candidates)
        self.assertEqual((result['mode'], result['high']),
                         ('weekly', str(Decimal(10000) * 12 / 52)))
        post = posting_from_data(raw_post(source, title='$130K/năm or more!!!',
                                          address='4920 Cypress St West Monroe, LA 71291',
                                          city='', state='LA', zipCode=''))
        data = base_data()
        data['category'] = {'target':'JobPosting','field':'content',
                            'evidence':{'quote':'We are seeking for a manager'}}
        data['salary_candidates'] = candidates
        # "St West Monroe" alone does not prove whether West is the street
        # direction or part of the city; the source has no independent city cue.
        with self.assertRaises(NeedsHuman):
            self.project(post, data)
        supported = posting_from_data(raw_post(source + '\nTiệm ở West Monroe, LA.',
            title='$130K/năm or more!!!', address='4920 Cypress St West Monroe, LA 71291',
            city='', state='LA', zipCode=''))
        projected = self.project(supported, data)
        self.assertEqual((projected['fields']['salary_amount'], projected['fields']['city'],
                          projected['fields']['zip']), ('2300', 'West Monroe', '71291'))
        with self.assertRaises(EvidenceError):
            candidate_salary(source, [*candidates[:-1], candidate('$2M', revenue, 'year', 'worker')])
        with self.assertRaises(EvidenceError):
            candidate_salary('Worker income $2M/year', [candidate('$2M', 'Worker income $2M/year',
                                                                 'year', 'revenue')])
        with self.assertRaises(EvidenceError):
            candidate_salary('Salary $1500/week plus tips',
                             [candidate('$1500', 'Salary $1500/week plus tips')])

    def test_filled_manager_source_accounting_recovers_partial_model_candidates(self):
        source = ('We are seeking for a manager:\n- starting salary of $120K/year\n'
                  'Update: Tiệm đã mướn được người quản lý.\n'
                  '2. Lương $6,000-$9,000 & up mỗi tháng cho thợ nails (chưa tính tiền tips).\n'
                  '-Trả lương hàng tuần.\n-Tiệm ở Louisiana thu nhập hơn $3M mỗi năm.')
        post = posting_from_data(raw_post(source))
        model_candidates = [
            [candidate('$120K/year', 'starting salary of $120K/year', 'year', 'other')],
            [candidate('$6,000-$9,000 & up mỗi tháng',
                       '$6,000-$9,000 & up mỗi tháng cho thợ nails (chưa tính tiền tips)', 'month')],
        ]
        expected = source_active_income_after_filled_manager(source)
        self.assertEqual((expected['period'], expected['high']),
                         ('month', str(Decimal(9000) * 12 / 52)))
        self.assertEqual([(entry['treatment'], entry['reason']) for entry in expected['source_amount_audit']],
                         [('excluded', 'manager position explicitly filled'),
                          ('worker', 'active worker income with local month period'),
                          ('excluded', 'salon-subject business revenue')])
        for candidates in model_candidates:
            with self.subTest(candidates=candidates):
                data = base_data()
                data['salary_candidates'] = candidates
                self.assertNotIn('salary_candidates', normalize_extraction(post, data))
                result = self.project(post, data)
                self.assertEqual((result['fields']['salary_type'], result['fields']['salary_amount']),
                                 ('weekly', '2000'))

    def test_filled_manager_recovery_requires_complete_unambiguous_source(self):
        manager = 'We are seeking for a manager:\n- starting salary of $120K/year\n'
        filled = 'Update: Tiệm đã mướn được người quản lý.\n'
        worker = 'Lương $6,000-$9,000 & up mỗi tháng cho thợ nails (chưa tính tiền tips).\n'
        revenue = '-Tiệm ở Louisiana thu nhập hơn $3M mỗi năm.'
        valid = manager + filled + worker + revenue
        self.assertIsNotNone(source_active_income_after_filled_manager(valid))
        for equivalent in (unicodedata.normalize('NFD', valid),
                           valid.replace('- starting', '- 💰 starting').replace('Lương', '💵 Lương')):
            with self.subTest(unicode_variant=equivalent):
                parsed = source_active_income_after_filled_manager(equivalent)
                self.assertIsNotNone(parsed)
                self.assertEqual(parsed['high'], str(Decimal(9000) * 12 / 52))
        variants = (
            manager + worker + revenue,  # Manager is still an active higher-paid role.
            manager + filled + worker + revenue + '\nExtra income $800/week.',
            manager + filled + worker + '-Thợ ở Louisiana thu nhập hơn $3M mỗi năm.',
            manager + filled + worker + '-Tiệm ở Louisiana thu nhập hơn $3M mỗi năm cho thợ.',
            manager + filled + worker.replace('chưa tính tiền tips', 'bao gồm tiền tips') + revenue,
            manager + filled + worker.replace('Lương', 'Potential income') + revenue,
            manager.replace('salary', 'salon revenue') + filled + worker + revenue,
        )
        for source in variants:
            with self.subTest(source=source):
                self.assertIsNone(source_active_income_after_filled_manager(source))
                post = posting_from_data(raw_post(source))
                data = base_data()
                data['salary_candidates'] = [candidate('$6,000-$9,000',
                                                       '$6,000-$9,000 & up mỗi tháng', 'month')]
                self.assertIn('salary_candidates', normalize_extraction(post, data))
