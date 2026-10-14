"""Offline policy regressions; UI contracts are mocked, no live writes or Telegram client."""
import json
import tempfile
import unittest
import unicodedata
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from tg_triage.nailhub.browser import Browser, posting_from_data
from tg_triage.nailhub.diagnostics import failure_details
from tg_triage.nailhub.model import NailHubModel
from tg_triage.nailhub.notices import VERSION, manual_notice, units
from tg_triage.nailhub.state import State
from tg_triage.nailhub.validation import (NeedsHuman, EvidenceError, normalize_extraction,
                                        proposal, require_contact, salary, source_salary_policy, source_state_repair,
                                        current_address_repair)
from tests.nailhub_fixtures import extracted, raw_post, settings, OWNER


class FieldPolicyTests(unittest.TestCase):
    def test_owner_salon_sale_asking_price_precedes_revenue_and_worker_salary(self):
        source = ('Cần tuyển thợ chân tay nước và thợ làm bột ở Louisiana, không cần kinh nghiệm, Lương Cao.\n'
            'Vui lòng nhắn tin Chinchin Phone ☎\n'
            '***Looking for a worker for pedicure or acrylic. Willing to train. Good price. Located in Louisiana. '
            'If you want more details please contact chin chin by text Phone ☎ (English)\n'
            'Bán tiệm với giá 60k. Do có 2 tiệm nên chúng tôi không có đủ thời gian để quản lý cả hai. cần bán gấp. '
            'Tiệm có thu nhập ổn định 30k-35k/tháng. Tiệm mở cửa 7 ngày/tuần nhưng bạn có thể thay đổi. '
            'chúng tôi có đánh giá rất cao và đã nhiều lần giành giải tiệm được đánh giá cao nhất. '
            'Tiệm mới được cải tạo gần đây và có 7 ghế cho khách. Tất cả khách hàng đều trả giá cao '
            'và dịch vụ chăm sóc móng chân và trọn bộ bắt đầu với mức giá cao. Khách hàng sẵn sàng trả giá cao. '
            'Thu hồi vốn nhanh và rất phù hợp cho các gia đình làm việc. liên hệ Chin Chin: '
            '(Vui lòng chỉ nói tiếng Anh) Phone ☎')
        post = posting_from_data(raw_post(source, businessName='Regal Nails', city='Shreveport', state='LA',
            zipCode='71118', address='9550 Mansfield Rd Shreveport LA 71118', priceType='Negotiable', price=0,
            contactInfo={'name': 'Regal Nails', 'phone': '3186788524', 'mobilePhone': '3182667276', 'email': ''}))
        data = extracted(source, 'bad model salary')
        data['salary']['evidence'] = {'quote': 'invented'}
        with tempfile.TemporaryDirectory() as directory:
            result = proposal(post, normalize_extraction(post, data), settings(directory))
        self.assertEqual(result['fields']['salary_amount'], '60000')
        self.assertEqual(result['fields']['salary_type'], 'fixed')
        self.assertEqual(result['category'], 'SalonForSale')
        self.assertTrue(result['salary']['sale_asking_price'])
        self.assertEqual(post.category, 'JobPosting')
        self.assertIn('no conversion or rounding', result['salary']['display_policy'])
        self.assertEqual(source_salary_policy(source+'\nSalary $1500/week. Giá gội đầu: $70, $110, $170')['low'], '60000')

    def test_sale_price_lowest_literal_and_positive_business_association(self):
        for source, expected in [
                ('Bán tiệm giá60K. Sang tiệm giá55k.', '55000'),
                ('Sang nhượng tiệm: $60K–$75K. Thu nhập35k/tháng.', '60000'),
                ('Salon for sale. Asking price: $60,125.50. Sale price: $58,999.75.', '58999.75'),
                ('Nail salon for sale for $60K. Income $35K/month.', '60000'),
                ('Chuyển nhượng tiệm giá 60125.50', '60125.50'),
                ('Bán tiệm 60–70K. Giá bán55K. Giá sang nhượng54K.', '54000')]:
            with self.subTest(source=source):
                self.assertEqual(source_salary_policy(source)['low'], expected)
        for source in ('Salon not for sale. Salary $1200/week',
                       'Không bán tiệm giá60k. Salary $1200/week',
                       'Product for sale $60K. Salary $1200/week',
                       'Salon bán polish giá60K. Salary $1200/week',
                       'Salon for sale. Revenue $35K/month. Asking price uncertain.',
                       'Salon for sale. Product asking price: $60K.',
                       'Salon sale price $20 per manicure. Hiring staff Salary $1200/week',
                       'Salon for sale $60000 USD per month',
                       'Salon for sale $60000. Not for sale anymore',
                       'Not for sale. Salon asking price $60000',
                       'Salon sale price $20 for manicure. Salary $1200/week',
                       'Salon sale price $20 manicure. Salary $1200/week',
                       'Salon for sale $60000 USD /2 weeks',
                       'Bán tiệm 60k. Tiệm không bán.'):
            with self.subTest(source=source):
                policy = source_salary_policy(source)
                self.assertFalse(policy and policy.get('sale_asking_price'))

    def test_service_price_list_negotiated_despite_bad_salary_model(self):
        for source in ('Cần thợ gội đầu. Giá gội đầu: $70, $110, $170.',
                       'Tuyển thợ. Giá gội đầu dưỡng sinh: $70, $110, $170\nCall Teddy.',
                       'Hiring. Price pedicure: $70, $110, $170'):
            post = posting_from_data(raw_post(source))
            data = extracted(source, '$70')
            data['salary']['evidence'] = {'quote': 'model invented salary'}
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                result = proposal(post, normalize_extraction(post, data), settings(directory))
            self.assertEqual(result['fields']['salary_type'], 'negotiated')
            self.assertEqual(result['fields']['salary_amount'], '')

    def test_service_price_exclusion_preserves_real_pay_and_conflict_guards(self):
        for pay, raw, period, expected in [('Bao lương $7000', '$7000', None, '7000'),
                ('Salary $1200/week', '$1200', 'week', '1200.00'),
                ('Bao $6000–$10000/month', '$6000–$10000', 'month', '2300')]:
            source = 'Giá gội đầu: $70, $110, $170\n' + pay
            data = extracted(source, raw, period)
            data['salary']['evidence'] = {'quote': pay}
            with tempfile.TemporaryDirectory() as directory:
                result = proposal(posting_from_data(raw_post(source)), data, settings(directory))
            self.assertEqual(result['fields']['salary_amount'], expected)
        # A generic price word is not sufficient authority to discard money.
        for source in ('Giá $70, $110, $170. Salary $1200/week',
                       'Giá gội đầu: $70, $110, $170\nSalary $1200/week or $1400'):
            with self.subTest(source=source), self.assertRaises(NeedsHuman):
                salary(source, extracted(source, '$1200')['salary'])
        source = 'Giá gội đầu: $70, $110, $170\nSalary $1200/week'
        source = unicodedata.normalize('NFD', source)
        pay = extracted(source, '$1200')['salary']
        pay['evidence'] = {'quote': 'Salary $1200/week'}
        self.assertEqual(salary(source, pay)['low'], '1200')
        data = extracted(source, '$70', 'week')
        data['salary']['evidence'] = {'quote': source.splitlines()[0]}
        with self.assertRaises(NeedsHuman):
            salary(source, data['salary'])
        for extra in ('rent is $5000/week', 'bonus of $5000/week', 'tips can reach $5000/week',
                      'commission income $5000/week', 'bonus income $5000/week'):
            source = 'Salary $1200/week, ' + extra
            with self.subTest(extra=extra), self.assertRaises(NeedsHuman):
                salary(source, extracted(source, '$1200')['salary'])

    def test_owner_actual_service_menu_and_highest_worker_income(self):
        source = ('(lương thật trong hình): 1 tháng, 3 tháng\n'
            'Calla Nails Beauty tuyển thợ ưu tiên nữ, CTN có khả năng gội đầu dưỡng sinh. '
            'Giá gội đầu: $70, $110, $170\nVui lòng liên lạc Teddy: Phone ☎\n'
            'CTN gội đầu Income $2500+/2 tuần làm 12 ngày\neverything: $2900+/2 tuần\n'
            'callanailsbeautyindy.com\n❤️Tiệm ở Fishers Indiana. Tiệm lớn có 3 phòng wax, lashes, '
            'phòng ăn rộng rãi sạch sẽ.\n❤️Em xin nhắn tìm đồng đội về làm chung với tiệm em. '
            'Anh chị làm CTN dip/ Bột designs. Manager trẻ trung, yêu đời thoải mái cho cô chú anh chị em. '
            'Có duyên mình ở, không thì gặp gỡ nhau tạo mối quan hệ.\nVui lòng liên lạc Teddy: Phone ☎')
        weekly = ('Thợ làm đủ thứ bao lương $1000/1500 tuần hơn chia tùy theo tay nghề, '
            'thợ làm manicure, pedicure $700-$900/tuần hơn chia tùy theo tay nghề, '
            'bảo đảm thu nhập ổn định cho anh chị em mình, khách dễ chịu, tiền tip rất cao.')
        monthly = ('Tiền típ cao (thường 20% hoặc hơn).\n'
            'Lương ổn định cho thợ bột từ $6,000 - $8,000/tháng.\nCó phòng cho thợ ở xa.')
        for body, expected in ((source, '1400'), (weekly, '1500'), (monthly, '1800'),
                               ('Salary $1000/week. Salary $1500/week. Salary $700–$900/week', '1500')):
            post = posting_from_data(raw_post(body))
            data = extracted(body, 'bad model amount')
            data['salary']['evidence'] = {'quote': 'invented'}
            with self.subTest(body=body), tempfile.TemporaryDirectory() as directory:
                result = proposal(post, normalize_extraction(post, data), settings(directory))
            self.assertEqual(result['fields']['salary_type'], 'weekly')
            self.assertEqual(result['fields']['salary_amount'], expected)
        decomposed = unicodedata.normalize('NFD', 'ố'*40 + '\nBao $6000-$8000/month')
        audit = source_salary_policy(decomposed)
        self.assertEqual(audit['source_amount'], '$6000-$8000')
        self.assertEqual(audit['qualification_evidence'], 'Bao $6000-$8000/month')
        same_line = unicodedata.normalize('NFD', 'Giá gội đầu: $70, $110, $170 Bao $6000-$8000/month')
        audit = source_salary_policy(same_line)
        self.assertEqual(audit['source_amount'], '$6000-$8000')
        self.assertEqual(audit['qualification_evidence'], same_line)

    def test_actual_lakewood_missing_city_zip_from_existing_address_not_body(self):
        body = ('Tiệm cần nhiều thợ nails,ưu tiên biết làm Bột.\nKhông cần mua sắm dụng cụ, tiệm bao tất cả '
                'và không trừ tiền supply.\nTiệm khu mỹ trắng,tip cao.\nCó phòng riêng,có xe đưa đón.\n'
                'Thật lòng xin liên lạc:\nTEXT ONLY (Jimmy) Phone ☎ or Phone ☎ ( Kim).\nThank you.')
        address = '279 E Fairmount Ave Lakewood, NY 14750'
        post = posting_from_data(raw_post(body, businessName='Luxe Spa', city='', state='NY', zipCode='',
            address=address, priceType='Negotiable', price=0,
            contactInfo={'name': 'Luxe Spa', 'phone': '7164505256', 'mobilePhone': '7169487979', 'email': ''}))
        data = extracted(body, 'bad amount')
        data['salary']['evidence'] = {'quote': 'invented'}
        data['location'] = {'city': {'value': 'New York', 'evidence': {'quote': 'invented'}},
                            'zip': {'value': '00000', 'evidence': {'quote': 'invented'}}}
        with tempfile.TemporaryDirectory() as directory:
            result = proposal(post, normalize_extraction(post, data), settings(directory))
        self.assertEqual(result['fields']['city'], 'Lakewood')
        self.assertEqual(result['fields']['zip'], '14750')
        self.assertEqual(result['fields']['state'], 'NY')
        self.assertEqual(result['fields']['salary_type'], 'negotiated')
        for key in ('salon', 'address', 'phone_primary', 'phone_other'):
            self.assertEqual(result['fields'][key], post.fields[key])
        for key in ('city', 'zip'):
            audit = result['field_corrections'][key]
            self.assertEqual(audit['source_field'], 'address')
            self.assertEqual(audit['evidence'], address)
            self.assertEqual(audit['current'], '')
        self.assertNotIn(address, post.content)
        self.assertEqual(data['location']['city']['value'], 'New York')

    def test_address_repair_multiword_city_no_guess_and_conflicts(self):
        for address, state, city, zip_code in [
                ('123 Main St New York, NY 10001', 'NY', 'New York', '10001'),
                ('123 Main Street St. Louis, MO 63101', 'MO', 'St. Louis', '63101'),
                ('91 Saratoga Dr #d Charles Town, WV 25414', 'WV', 'Charles Town', '25414')]:
            post = posting_from_data(raw_post('Hiring', address=address, state=state, city='', zipCode=''))
            repair = current_address_repair(post)
            self.assertEqual(repair['city']['value'], city)
            self.assertEqual(repair['zip']['value'], zip_code)
        for address in ('279 E Fairmount Ave Lakewood, NY', 'NY 14750',
                        '279 E Fairmount Ave NY 14750', '123 N St John St Lakewood, NY 14750'):
            post = posting_from_data(raw_post('Hiring', address=address, state='NY', city='', zipCode=''))
            self.assertEqual(current_address_repair(post), {})
        for body, state, city, zip_code, field in [
                ('Hiring', 'CA', '', '', 'state'), ('Hiring', 'NY', 'Brooklyn', '', 'city'),
                ('Hiring', 'NY', '', '10001', 'zip'),
                ('Salon in Chicago, IL 60601', 'NY', '', '', 'address'),
                ('Address: 123 Main St Chicago, IL 60601', 'NY', '', '', 'address')]:
            post = posting_from_data(raw_post(body, address='279 E Fairmount Ave Lakewood, NY 14750',
                state=state, city=city, zipCode=zip_code))
            with self.subTest(body=body, field=field), self.assertRaises(NeedsHuman) as raised:
                normalize_extraction(post, extracted(body))
            self.assertEqual(failure_details(raised.exception, post)['field'], field)
            self.assertEqual(failure_details(raised.exception, post)['kind'], 'data')
        body = 'Address: 279 E Fairmount Ave Lakewood, NY 14750. Lakewood, NY 14750. Email shop@example.com.'
        post = posting_from_data(raw_post(body, address='279 E Fairmount Ave Lakewood, NY 14750',
            state='NY', city='', zipCode=''))
        data = extracted(body)
        data['location']['email'] = {'value': 'shop@example.com', 'evidence': {'quote': 'shop@example.com'}}
        with tempfile.TemporaryDirectory() as directory:
            result = proposal(post, normalize_extraction(post, data), settings(directory))
        self.assertEqual(result['fields']['city'], 'Lakewood')
        self.assertEqual(result['fields']['email'], 'shop@example.com')

    def test_owner_unclear_salary_negotiated_actual4148_ignores_bad_model_and_minutes(self):
        actual = ('Cần tìm thợ nữ biết làm everything. Bao lương trên ăn chia.\n'
            'Môi trường làm việc vui vẻ, không giành giật, không trừ tiền supply, đủ đồ nghề design.\n'
            'Chủ nhật đóng cửa\nTiệm cách BJ red lion 20 phút lái xe\n'
            'Xin gọi qua phone hoặc nhắn tin nếu không kịp nghe máy. Phone ☎')
        for source in (actual, 'Cần thợ. Bao ăn ở.', 'Salary unknown; call for details.',
                       'Lương hấp dẫn. Ăn chia 6/4.', 'Commission $6000/month', 'Tips $500/week',
                       'Cho thuê phòng facial $500/month. Cần thợ. Address 123 Main St 30301.'):
            raw = raw_post(source)
            if source == actual:
                raw.update(businessName='BaoNail.com', state='PA', city='Feasterville Trevose', zipCode='19053',
                    address='(bên cạnh nhà hàng và tiệm làm tóc, bãi đậu xe lớn) Feasterville Trevose, PA 19053',
                    priceType='Negotiable', price=0,
                    contactInfo={'name': 'BaoNail.com', 'phone': '2675758870', 'mobilePhone': '2675758870', 'email': ''})
            post = posting_from_data(raw)
            data = extracted(source, 'unrecognized amount', 'month')
            data['salary']['evidence'] = {'quote': 'invented'}
            if source == actual:
                # This old fixture has a publisher placeholder in the salon field.
                self.assertEqual(source_salary_policy(source)['mode'], 'negotiated')
                with tempfile.TemporaryDirectory() as directory:
                    result = proposal(post, normalize_extraction(post, data), settings(directory))
                self.assertEqual((result['fields']['salon'], result['fields']['salary_type']),
                                 ('nailhub.ai', 'negotiated'))
                self.assertFalse(result['field_corrections']['salon']['actual_business_name_known'])
                continue
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                result = proposal(post, normalize_extraction(post, data), settings(directory))
            self.assertEqual(result['changes']['salary_type'], 'negotiated')
            self.assertEqual(result['changes']['salary_amount'], '')
            for key in ('salon', 'address', 'city', 'state', 'zip', 'phone_primary'):
                self.assertEqual(result['fields'][key], post.fields[key])

    def test_bare_numeric_period_stays_numeric_and_conflicts_hold(self):
        for source, raw in [('1200/week', '1200'), ('1200 USD per week', '1200')]:
            self.assertIsNone(source_salary_policy(source))
            self.assertEqual(salary(source, extracted(source, raw)['salary'])['low'], '1200')
        for source in ('Bao $7000 hoặc $8000', 'Salary 1200/week or2400/week'):
            with self.assertRaises(NeedsHuman):
                salary(source, extracted(source, '7000', None)['salary'])

    def test_explicit_address_state_overrides_model_format_before_evidence(self):
        source = ('Pure Nails Spa. Charles Town, WV.\nĐịa chỉ tiệm: '
                  '91 Saratoga Dr #d Charles Town, WV 25414. Cảm ơn.\nCharles Town, WV. Bao lương $7000.')
        post = posting_from_data(raw_post(source, state='CA', city='Charles Town', zipCode='25414',
            address='91 Saratoga Dr #d Charles Town, WV 25414'))
        for state_item in ({'value': 'West Virginia', 'evidence': {'quote': 'invented'}},
                           {'value': 'WV', 'evidence': {'quote': 'WV'}}, None):
            data = extracted(source)
            data['location'] = {'state': state_item,
                'salon': {'value': 'Pure Nails Spa', 'evidence': {'quote': 'Pure Nails Spa'}}}
            normalized = normalize_extraction(post, data)
            with tempfile.TemporaryDirectory() as directory:
                result = proposal(post, normalized, settings(directory))
            self.assertEqual(result['changes']['state'], 'WV')
            self.assertEqual(result['changes']['salon'], 'Pure Nails Spa')
            self.assertEqual(result['changes']['salary_amount'], '7000')
            self.assertEqual(data['location']['state'], state_item)  # Input remains unchanged.
        correct = posting_from_data(raw_post(source, state='WV', city='Charles Town', zipCode='25414'))
        data = extracted(source)
        data['location']['state'] = {'value': 'California', 'evidence': {'quote': 'invented'}}
        self.assertNotIn('state', normalize_extraction(correct, data)['location'])

    def test_state_repair_requires_address_context_and_unambiguous_all_pairs(self):
        for source in ('Charles Town, WV 25414. Bao $7000',
                       'Địa chỉ: NotCharles Town, WV 25414. Bao $7000',
                       'Địa chỉ: Charles Town, wv 25414. Bao $7000',
                       'Địa chỉ: Charles Town, WV. Bao $7000'):
            post = posting_from_data(raw_post(source, state='CA', city='Charles Town'))
            self.assertIsNone(source_state_repair(post))
        for other in ('Charles Town, CA', 'Charles Town, WV 25415'):
            source = 'Địa chỉ: 91 Saratoga Dr Charles Town, WV 25414. ' + other
            post = posting_from_data(raw_post(source, state='CA', city='Charles Town'))
            with self.assertRaises(NeedsHuman) as raised:
                normalize_extraction(post, extracted(source))
            detail = failure_details(raised.exception, post)
            self.assertEqual(detail['kind'], 'data')
            self.assertEqual(detail['field'], 'state')
            self.assertEqual(detail['current'], 'CA')
            self.assertTrue(detail['source']); self.assertTrue(detail['action'])
        source = ('Địa chỉ: Charles Town, WV 25414.\nĐịa chỉ: Charles Town, WV 25414. Bao $7000')
        self.assertEqual(source_state_repair(posting_from_data(raw_post(source, city='Charles Town')))['value'], 'WV')
        for zip_code in ('25414-1234', '254141234'):
            source = f'Địa chỉ: Charles Town, WV {zip_code}.'
            self.assertEqual(source_state_repair(posting_from_data(raw_post(source, city='Charles Town')))['value'], 'WV')

    def test_no_pay_overrides_broken_model_salary_and_benefit_bao(self):
        for source in ('Cần thợ và cho thuê phòng facial. Gần Costco.',
                       'Cần thợ nail. Bao ăn ở. Liên hệ 4045550100.',
                       'Có bao chỗ ở. Làm 6 ngày / tuần. Địa chỉ 123 Main St 30301.'):
            post = posting_from_data(raw_post(source))
            data = extracted(source)
            data['salary'] = {'mode': 'weekly', 'evidence': {'start': 99999, 'end': -1}}
            with tempfile.TemporaryDirectory() as directory:
                result = proposal(post, normalize_extraction(post, data), settings(directory))
            self.assertEqual(result['changes']['salary_type'], 'negotiated')
            self.assertEqual(result['changes']['salary_amount'], '')
            self.assertEqual(result['changes']['phone_other'], '')

    def test_no_pay_entire_model_failure_fallback_and_explicit_correction_exclusion(self):
        for failure in ('model returned invalid JSON', 'model request timed out', 'model request or JSON validation failed'):
            with tempfile.TemporaryDirectory() as directory:
                model = NailHubModel(settings(directory))
                model.ask = Mock(side_effect=NeedsHuman(failure))
                post = posting_from_data(raw_post('Cần thợ. Cho thuê phòng facial. Gần Costco.'))
                data = model.extract(post)
                self.assertEqual(proposal(post, data, model.config)['salary']['mode'], 'negotiated')
                model.ask.assert_called_once()
                for source in ('Address 91 Saratoga Dr, Charles Town, WV 25414.',
                               'Pure Nails Spa cần thợ.', 'Luxury Nail cần thợ.', 'Liên hệ 7705550101.', 'Cần thợ tại Charles Town WV'):
                    with self.subTest(source=source), self.assertRaises(NeedsHuman):
                        model.extract(posting_from_data(raw_post(source)))

    def test_actual_no_unit_repeated_7000_qualifiers_and_no_conversion(self):
        sources = ['Bao 7000', 'Lương từ 7000', 'Thu nhập 7000. Address 700 Main St.',
            'Wages 7000 USD', 'Compensation 7000 dollars',
            'Cần thợ nails gấp!!!! Bao lương $7000\nPure Nails Spa, Charles Town, WV.\n'
            'Thu nhập: Bao lương $7000 trở lên (tùy theo tay nghề) hoặc ăn chia hấp dẫn.']
        for source in sources:
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                post = posting_from_data(raw_post(source))
                data = extracted(source, '$99999', 'month', 'potential')
                data['salary']['evidence'] = {'quote': 'invented'}
                result = proposal(post, normalize_extraction(post, data), settings(directory))
                self.assertEqual(result['changes']['salary_amount'], '7000')
                self.assertIsNone(result['salary']['period'])
                self.assertTrue(result['salary']['copy_no_period'])

    def test_no_unit_conflicts_rent_bonus_phone_zip_not_salary(self):
        for source in ('Bao lương $7000. Bonus $500.', 'Bao lương $7000. Rent $7000.',
                       'Bao lương $7000 hoặc $8000.',
                       'Salary $7000 plus commission'):
            with self.subTest(source=source), self.assertRaises(NeedsHuman):
                source_salary_policy(source)
        for source in ('Thu nhập. Phone 9512373355. ZIP25414. Address 91 Saratoga Dr.', 'Làm ăn chia 6/4',
                       'Commission $7000', 'Tips $7000', 'Rent $7000/month'):
            self.assertEqual(source_salary_policy(source)['mode'], 'negotiated')

    def test_no_period_ranges_keep_endpoints_upper_floor100_no_conversion(self):
        for source, low, high, wanted in [('Bao lương6K–10K', '6000', '10000', '10000'),
                ('bao1300–2380', '1300', '2380', '2300'),
                ('Bao lương $6K–$10K\nBao lương $6K–$10K', '6000', '10000', '10000')]:
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                post = posting_from_data(raw_post(source))
                result = proposal(post, normalize_extraction(post, extracted(source)), settings(directory))
                self.assertEqual(result['changes']['salary_amount'], wanted)
                self.assertEqual((result['salary']['low'], result['salary']['high']), (low, high))
                self.assertIsNone(result['salary']['period'])
                self.assertIn('without conversion', result['salary']['display_policy'])
                self.assertEqual(post.content, source)
        for source in ('Bao lương $6K–$10K. Bao lương $7K–$10K',
                       'Bao lương $6K–$10K. Bonus $500', 'Bao 7000. Salary $8000'):
            with self.subTest(source=source), self.assertRaises(NeedsHuman):
                source_salary_policy(source)

    def test_populated_source_corrections_unique_context_and_grounded_pair(self):
        source = ('Pure Nails Spa tuyển thợ. Charles Town, WV.\n'
                  'Địa chỉ: 91 Saratoga Dr #d Charles Town, WV 25414.\n'
                  'Charles Town, WV. Bao lương $7000 trở lên hoặc ăn chia.')
        post = posting_from_data(raw_post(source, state='CA', city='Charles Town', businessName='Pure Nail Spa'))
        data = extracted(source)
        context = '91 Saratoga Dr #d Charles Town, WV 25414'
        data['location'] = {key: {'value': value, 'evidence': {'quote': quote}} for key, value, quote in
            [('salon', 'Pure Nails Spa', 'Pure Nails Spa tuyển thợ'), ('state', 'WV', context),
             ('city', 'Charles Town', context), ('address', context, context), ('zip', '25414', context)]}
        with tempfile.TemporaryDirectory() as directory:
            result = proposal(post, normalize_extraction(post, data), settings(directory))
            self.assertEqual(result['fields']['state'], 'WV')
            self.assertEqual(result['fields']['salon'], 'Pure Nails Spa')
            self.assertEqual(result['field_corrections']['state']['current'], 'CA')
            data['location']['state'] = {'value': 'NY', 'evidence': {'quote': 'NY'}}
            with self.assertRaises(NeedsHuman):
                proposal(post, data, settings(directory))
        arbitrary = posting_from_data(raw_post('CA. Charles Town. WV. Bao $7000', state='CA', city='Charles Town'))
        data = extracted(arbitrary.content)
        data['location'] = {'state': {'value': 'WV', 'evidence': {'quote': 'WV'}}}
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(NeedsHuman):
            proposal(arbitrary, data, settings(directory))
        prefixed = posting_from_data(raw_post('NotCharles Town WV. Bao $7000', state='CA', city='Charles Town'))
        data = extracted(prefixed.content)
        data['location'] = {'state': {'value': 'WV', 'evidence': {'quote': 'WV'}}}
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(NeedsHuman):
            proposal(prefixed, data, settings(directory))

    def test_contact_corrections_are_source_exact_then_deduped(self):
        source = 'Phone 770-555-0101. Email shop@example.com. Bao $7000.'
        post = posting_from_data(raw_post(source))
        data = extracted(source)
        data['location'] = {key: {'value': value, 'evidence': {'quote': value}} for key,value in
                            [('phone_primary', '770-555-0101'), ('email', 'shop@example.com')]}
        with tempfile.TemporaryDirectory() as directory:
            result = proposal(post, data, settings(directory))
        self.assertEqual(result['fields']['phone_primary'], '7705550101')
        self.assertEqual(result['fields']['email'], 'shop@example.com')
        self.assertNotEqual(result['fields']['phone_other'], '')  # Distinct original number survives.

    def test_exact_invalid_contact_field_and_technical_evidence_diagnostics(self):
        post = posting_from_data(raw_post())
        for key, value in [('address', ''), ('zip', 'oops'), ('phone_primary', '')]:
            fields = {**post.fields, key: value}
            with self.assertRaises(NeedsHuman) as raised:
                require_contact(fields)
            detail = failure_details(raised.exception, post)
            self.assertEqual(detail['field'], key)
            self.assertEqual(detail['kind'], 'data')
        detail = failure_details(EvidenceError('invalid evidence span', path='location.state.evidence'), post)
        self.assertEqual(detail['field'], 'state'); self.assertEqual(detail['kind'], 'automation')
        self.assertIn('Chưa', detail['source'])

    def test_source_period_commission_ambiguity_is_data_not_model_failure(self):
        for source in ('Bao $1200/week or per month', 'Bao $1200/week plus tips',
                       'Bao $1200/week plus commission'):
            post = posting_from_data(raw_post(source))
            with self.subTest(source=source), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(NeedsHuman) as raised:
                    data = normalize_extraction(post, extracted(source))
                    proposal(post, data, settings(directory))
                detail = failure_details(raised.exception, post)
                self.assertEqual(detail['kind'], 'data')
                self.assertEqual(detail['field'], 'salary_amount')
                self.assertEqual(detail['current'], post.fields['salary_amount'])
                self.assertTrue(detail['evidence'])
                self.assertIn('$', detail['source'])
                self.assertTrue(detail['action'])
        post = posting_from_data(raw_post('Bao $1200/week'))
        malformed = extracted(post.content, 'not a number')
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(NeedsHuman) as raised:
            proposal(post, malformed, settings(directory))
        self.assertEqual(failure_details(raised.exception, post)['kind'], 'automation')
        self.assertEqual(failure_details(EvidenceError('invalid evidence span', path='salary.evidence'), post)['kind'], 'automation')
        long_source = 'Bao $1200/week ' + 'x' * 600
        post = posting_from_data(raw_post(long_source))
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(NeedsHuman) as raised:
            data = extracted(long_source)
            data['salary']['evidence'] = {'quote': 'Bao $1200/week'}
            proposal(post, data, settings(directory))
        detail = failure_details(raised.exception, post)
        self.assertEqual(detail['field'], 'salary_amount')
        self.assertTrue(detail['evidence'])
        self.assertLessEqual(len(detail['evidence']), 220)

    def test_diagnostic_context_persist_dedup_restart_and_safe_dm(self):
        with tempfile.TemporaryDirectory() as directory:
            state = State(settings(directory).db_path)
            post = posting_from_data(raw_post())
            state.observe(post.id, post.fingerprint, post.snapshot())
            detail = {'kind': 'automation', 'field': 'state', 'current': 'CA', 'source': 'WV',
                      'evidence': 'Charles Town, WV', 'action': 'Chọn West Virginia và Charles Town.'}
            state.mark(post.id, 'uncertain', 'save outcome unverified', details=detail)
            row = state.get(post.id); key = state.notice_key(row, VERSION, OWNER)
            text = manual_notice(row)
            for wanted in ('Tiểu bang', 'CA', 'WV', 'Charles Town', 'chưa xác định'):
                self.assertIn(wanted, text)
            self.assertLess(units(text), 3500)
            self.assertEqual(State(settings(directory).db_path).get(post.id)['details'], row['details'])
            state.mark(post.id, 'uncertain', 'save outcome unverified', details={**detail, 'source': 'NY'})
            self.assertNotEqual(key, state.notice_key(state.get(post.id), VERSION, OWNER))
            state.mark(post.id, 'uncertain', 'SECRET raw exception', details={**detail, 'evidence': 'password=secret-value'})
            text = manual_notice(state.get(post.id))
            self.assertNotIn('secret-value', text); self.assertNotIn('SECRET raw', text)


class WidgetPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_visible_single_label_excludes_aria_and_requires_exact_unique_value(self):
        with tempfile.TemporaryDirectory() as directory:
            browser = Browser(settings(directory), page=SimpleNamespace())
            label = SimpleNamespace(wait_for=AsyncMock(), count=AsyncMock(return_value=1),
                                    inner_text=AsyncMock(return_value='Charles Town'), filter=Mock())
            label.filter.return_value = label
            widget = SimpleNamespace(locator=Mock(return_value=label),
                get_by_text=Mock(side_effect=AssertionError('hidden ARIA announcement must not be queried')),
                inner_text=AsyncMock(return_value='option Charles Town, selected. Wrong City'))
            browser._field = AsyncMock(return_value=widget)
            await browser.selected_location('city', 'Charles Town')
            widget.locator.assert_called_with('.react-select__single-value')
            pattern = label.filter.call_args.kwargs['has_text']
            self.assertIsNotNone(pattern.fullmatch('Charles Town'))
            self.assertIsNone(pattern.search('option Charles Town, selected.'))
            widget.get_by_text.assert_not_called(); widget.inner_text.assert_not_awaited()
            for visible, count in [('NotCharles Town', 1), ('Charles Town Extra', 1), ('Charles Town', 2)]:
                label.inner_text.return_value = visible; label.count.return_value = count
                with self.subTest(visible=visible, count=count), self.assertRaises(NeedsHuman):
                    await browser.selected_location('city', 'Charles Town')
            label.count.return_value = 1; label.inner_text.return_value = 'West Virginia - WV'
            await browser.selected_location('state', 'WV')
            pattern = label.filter.call_args.kwargs['has_text']
            self.assertIsNotNone(pattern.fullmatch('West Virginia - WV'))
            self.assertIsNone(pattern.search('option West Virginia - WV, selected.'))
            label.inner_text.return_value = 'California - CA'
            with self.assertRaises(NeedsHuman):
                await browser.selected_location('state', 'WV')

    async def test_state_change_reselects_same_original_city_and_checks_retention(self):
        with tempfile.TemporaryDirectory() as directory:
            post = posting_from_data(raw_post(state='CA', city='Charles Town'))
            browser = Browser(settings(directory), page=SimpleNamespace(get_by_role=Mock()))
            widgets = {key: SimpleNamespace(get_by_role=Mock(return_value=SimpleNamespace(fill=AsyncMock())),
                get_by_text=Mock(return_value=SimpleNamespace(wait_for=AsyncMock())),
                inner_text=AsyncMock(return_value=value)) for key,value in
                [('state', 'West Virginia - WV'), ('city', 'Charles Town')]}
            for key, widget in widgets.items():
                label = SimpleNamespace(wait_for=AsyncMock(), count=AsyncMock(return_value=1),
                    inner_text=AsyncMock(return_value='West Virginia - WV' if key == 'state' else 'Charles Town'), filter=Mock())
                label.filter.return_value = label
                widget.locator = Mock(return_value=label)
            browser._field = AsyncMock(side_effect=lambda key: widgets[key])
            option = SimpleNamespace(wait_for=AsyncMock(), count=AsyncMock(return_value=1), click=AsyncMock())
            browser.page.get_by_role.return_value = option
            await browser.select_location(post, {'state': 'WV'})
            self.assertEqual(option.click.await_count, 2)
            widgets['city'].get_by_role.return_value.fill.assert_awaited_once_with('Charles Town')
            self.assertEqual(browser.page.get_by_role.call_args_list[1].kwargs['name'], 'Charles Town')
            option.count.return_value = 2
            with self.assertRaises(NeedsHuman):
                await browser.select_location(post, {'state': 'WV'})
