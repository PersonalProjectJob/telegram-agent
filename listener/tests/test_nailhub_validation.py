import tempfile
import unittest
import json
from unittest.mock import patch
from tg_triage.nailhub.browser import posting_from_data
from tg_triage.nailhub.validation import (FACT_KINDS, NeedsHuman, amounts, dedup_phones, evidence,
                                        invariant_facts, invariant_title, normalize_extraction, number, proposal, salary)
from tests.nailhub_fixtures import extracted, raw_post, settings

class ValidationTests(unittest.TestCase):
    def test_owner_default_start_date_only_becomes_processing_utc_date(self):
        with tempfile.TemporaryDirectory() as directory, patch('tg_triage.nailhub.validation.processing_start_date',return_value='2026-10-12T00:00:00'):
            for start in ('0001-01-01T00:00:00',None,'2020-01-01T00:00:00','2030-01-01T00:00:00'):
                with self.subTest(start=start):
                    post=posting_from_data(raw_post(startDate=start))
                    intended=proposal(post,extracted(post.content),settings(directory))
                    actual=json.loads(intended['fields']['_preserved'])['startDate']
                    self.assertEqual(actual,'2026-10-12T00:00:00' if start=='0001-01-01T00:00:00' else start)
                    self.assertEqual(bool(intended['metadata_changes']),start=='0001-01-01T00:00:00')
                    self.assertNotIn('startDate',intended['changes'])
            for end in ('2020-01-01T00:00:00','2026-10-12T00:00:00','invalid'):
                post=posting_from_data(raw_post(startDate='0001-01-01T00:00:00',endDate=end))
                with self.subTest(end=end),self.assertRaises(NeedsHuman):
                    proposal(post,extracted(post.content),settings(directory))
            post=posting_from_data(raw_post(startDate='0001-01-01T00:00:00',endDate='2030-01-01T00:00:00Z'))
            intended=proposal(post,extracted(post.content),settings(directory))
            self.assertEqual(json.loads(intended['fields']['_preserved'])['endDate'],'2030-01-01T00:00:00Z')

    def test_k_ranges_decimal_and_vietnamese_separators(self):
        for raw,low,high in [('6-10K','6000','10000'),('6K–10K','6000','10000'),
                            ('6.5K-10.2K','6500.0','10200.0'),('6,5K-10,2K','6500.0','10200.0'),
                            ('$6.000–$10.000','6000','10000'),('$6,000.50-$10,000.75','6000.50','10000.75'),
                            ('$6.000,50–$10.000,75','6000.50','10000.75')]:
            with self.subTest(raw=raw):
                a,b,r=amounts(raw);self.assertEqual(str(a),low);self.assertEqual(str(b),high);self.assertTrue(r)
        for invalid in ('1,2345','NaN','1.234.56','10K-6K','-12','0'):
            with self.assertRaises(NeedsHuman): amounts(invalid)

    def test_exact_week_month_year_biweekly_conversion(self):
        for source,raw,period,wanted in [('Bao $1200/week','$1200','week','1200'),
                ('Bao $6000/month','$6000','month',str(number('6000')*12/52)),
                ('Bao $52000/year','$52000','year','1000'),('Bao $2400 every 2 weeks','$2400','biweek','1200')]:
            result=salary(source,extracted(source,raw,period)['salary']);self.assertEqual(result['low'],wanted)

    def test_owner_exact_income_range_max_floor_100_and_original_preserved(self):
        source='Income $6K-$10K/tháng tùy theo khả năng';post=posting_from_data(raw_post(source))
        with tempfile.TemporaryDirectory() as directory:
            result=proposal(post,extracted(source,'$6K-$10K','month','potential'),settings(directory))
        self.assertEqual(result['changes']['salary_amount'],'2300');self.assertEqual(result['salary']['kind'],'potential')
        self.assertEqual(result['salary']['low'],str(number('6000')*12/52))
        self.assertEqual(result['salary']['high'],str(number('10000')*12/52))
        self.assertIn('original range',result['salary']['display_policy']);self.assertEqual(post.content,source)

    def test_single_pay_keeps_cent_precision_and_range_hold_policy_available(self):
        with tempfile.TemporaryDirectory() as directory:
            source='Bao $6000/month';post=posting_from_data(raw_post(source))
            self.assertEqual(proposal(post,extracted(source,'$6000','month'),settings(directory))['changes']['salary_amount'],'1384.61')
            source='Bao $6K-$10K/month';post=posting_from_data(raw_post(source))
            with self.assertRaises(NeedsHuman): proposal(post,extracted(source,'$6K-$10K','month'),settings(directory,range_policy='hold'))

    def test_hallucinated_amount_period_cherrypicked_range_and_conflicts_fail(self):
        cases=[('Bao $6000/month','$7000','month'),('Bao $6000/month','$6000','week'),
               ('Bao $6K-$10K/month','$6K','month'),('Bao $1200/week and $3000/week','$1200','week'),
               ('Bao $1200/week or per month','$1200','week')]
        for source,raw,period in cases:
            with self.subTest(source=source):
                with self.assertRaises(NeedsHuman): salary(source,extracted(source,raw,period)['salary'])

    def test_explicit_schedule_day_and_hour_only(self):
        for source,raw,period,expected in [('Bao $200/day; 5 days/week','$200','day','1000'),
                                          ('Bao $20/hour; 5 days/week; 8 hours/day','$20','hour','800')]:
            value=extracted(source,raw,period)['salary'];value['evidence']={'quote':source.split(';')[0]}
            value['schedule']={'days_per_week':'5','hours_per_day':'8','hours_per_week':None,
                               'evidence':{'quote':source.split(';',1)[1].strip()}}
            self.assertEqual(salary(source,value)['low'],expected)
            value['schedule']['days_per_week']='6'
            with self.assertRaises(NeedsHuman): salary(source,value)
        source='Bao $20/hour'
        with self.assertRaises(NeedsHuman): salary(source,extracted(source,'$20','hour')['salary'])

    def test_hidden_and_negotiated_need_source_evidence(self):
        source='Hiring technician. Call us.';value=extracted(source,None,None,'none','hidden')['salary']
        self.assertEqual(salary(source,value)['mode'],'negotiated')
        for source in ('6000/tháng',):
            with self.assertRaises(NeedsHuman): salary(source,extracted(source,None,None,'none','hidden')['salary'])
        for source in ('salary unknown','Earn $1000','tips available'):
            self.assertEqual(salary(source,extracted(source,None,None,'none','hidden')['salary'])['mode'],'negotiated')
        source='Lương thương lượng';value=extracted(source,None,None,'none','negotiated')['salary']
        self.assertEqual(salary(source,value)['mode'],'negotiated');value['evidence']={'quote':'Lương'}
        self.assertEqual(salary(source,value)['mode'],'negotiated')

    def test_single_potential_and_commission_tips_never_assumed_guarantee(self):
        for source in ('Income $6000/month','Bao $6000/month plus tips'):
            with self.assertRaises(NeedsHuman): salary(source,extracted(source,'$6000','month')['salary'])
        source='Commission $6000/month'
        self.assertEqual(salary(source,extracted(source,'$6000','month')['salary'])['mode'],'negotiated')

    def test_bare_conflicting_pay_is_not_hidden_by_selected_evidence(self):
        for source,raw,period in [('Salary 1200/week or 2400/week','1200','week'),
                ('Lương 1200/tuần hoặc 2400/tuần','1200','week'),
                ('Salary 1200/week. Another offer 6000/month.','1200','week')]:
            for quote in (source,raw+'/'+('tuần' if 'tuần' in source else 'week')):
                value=extracted(source,raw,period)['salary'];value['evidence']={'quote':quote}
                with self.subTest(source=source,quote=quote):
                    with self.assertRaises(NeedsHuman):salary(source,value)
        for source in ('Negotiable salary or 1200/week','Lương thương lượng hoặc 6000/tháng'):
            value=extracted(source,None,None,'none','negotiated')['salary']
            with self.assertRaises(NeedsHuman):salary(source,value)
        source='Salary 1200/week. Address 123 Main St. Phone 404-555-0100. 5 days/week.'
        value=extracted(source,'1200','week')['salary'];value['evidence']={'quote':'Salary 1200/week'}
        self.assertEqual(salary(source,value)['low'],'1200')
        source='Salary 1200-2450/week'
        value=extracted(source,'2450','week')['salary'];value['evidence']={'quote':'2450/week'}
        with self.assertRaises(NeedsHuman):salary(source,value)
        with tempfile.TemporaryDirectory() as directory:
            result=proposal(posting_from_data(raw_post(source)),extracted(source,'1200-2450','week'),settings(directory))
        self.assertEqual(result['changes']['salary_amount'],'2400')

    def test_original_qualification_cannot_be_cherry_picked_out(self):
        for source in ('Potential income up to $1200/week','Thu nhập có thể lên đến $1200/tuần',
                       'Salary $1200/week depending on skills',
                       ('Hiring nail technicians with experience and flexible availability '*10)
                       +'potential income up to $1200/week'):
            quote='$1200/tuần' if 'tuần' in source else '$1200/week'
            value=extracted(source,'$1200','week')['salary'];value['evidence']={'quote':quote}
            with self.assertRaises(NeedsHuman):salary(source,value)
        source='Income $6K-$10K/tháng tùy theo khả năng'
        value=extracted(source,'$6K-$10K','month')['salary'];value['evidence']={'quote':'$6K-$10K/tháng'}
        result=salary(source,value);self.assertEqual(result['kind'],'potential')
        self.assertEqual(result['qualification_evidence'],source)
        source='Salary $1200/week or per month'
        value=extracted(source,'$1200','week')['salary'];value['evidence']={'quote':'$1200/week'}
        with self.assertRaises(NeedsHuman):salary(source,value)

    def test_actual_salary_schedule_line_and_equivalent_amount_formatting(self):
        source=('Tiệm ở thành phố Sylvester ,GA . Cần thợ nail nữ , biết làm everything .\n'
                'Income $6K-$10K /tháng tuỳ theo khả năng ( tiệm mở cửa 6 ngày / tuần , đóng cửa chủ Nhật)\n'
                'Tiệm nhỏ , chủ trẻ nên thoải mái , làm không khí gia đình . Có chỗ cho thợ ở xa .\n'
                'Xin liên hệ :\nJim Phone ☎ ( xin để lại tin nhắn nếu k nghe máy )')
        for raw in ('$6K-$10K','$6K - $10K','$6,000–$10,000','6000-10000',
                    '$6K-$10K /tháng','$6,000-$10,000 per month'):
            data=extracted(source,raw,'month','potential');data['salary']['evidence']={'quote':source.splitlines()[1]}
            with tempfile.TemporaryDirectory() as directory:
                result=proposal(posting_from_data(raw_post(source)),data,settings(directory))
            self.assertEqual(result['changes']['salary_amount'],'2300')
            self.assertEqual(result['salary']['source_amount'],'$6K-$10K')
        for raw in ('$6K','$10K','$6K-$11K','$6K-$10K /week','$6K-$10K /tháng bonus $500',
                    '$6K-$10K /tháng or /week'):
            value=extracted(source,raw,'month','potential')['salary'];value['evidence']={'quote':source.splitlines()[1]}
            with self.assertRaises(NeedsHuman):salary(source,value)

    def test_actual_empty_location_and_amount_with_period_keep_existing_zip(self):
        source='Income $6K-$10K /tháng tuỳ theo khả năng'
        post=posting_from_data(raw_post(source,zipCode='31791'))
        data=extracted(source,'$6K-$10K /tháng','month','potential')
        data['location']={'zip':{'value':'','evidence':{'quote':'Tiệm ở thành phố Sylvester ,GA'}},
                          'email':{'value':'','evidence':{'quote':'not source'}}}
        data['salary']['schedule']={'days_per_week':'unused invalid model schedule'}
        with tempfile.TemporaryDirectory() as directory:
            normalized=normalize_extraction(post,data);result=proposal(post,normalized,settings(directory))
        self.assertEqual(normalized['location'],{});self.assertEqual(result['fields']['zip'],'31791')
        self.assertNotIn('zip',result['changes']);self.assertEqual(result['changes']['salary_amount'],'2300')
        self.assertEqual(result['salary']['source_amount'],'$6K-$10K')

    def test_actual_amount_period_qualification_excerpt_uses_exact_original_range(self):
        source='Income $6K-$10K /tháng tuỳ theo khả năng ( tiệm mở cửa 6 ngày / tuần , đóng cửa chủ Nhật)'
        value=extracted(source,'$6K-$10K /tháng tuỳ theo khả năng','month','potential')['salary']
        value['schedule']={'days_per_week':'}}} }','hours_per_day':None,'hours_per_week':None,
                           'evidence':{'quote':source}}
        result=salary(source,value)
        self.assertEqual(result['source_amount'],'$6K-$10K');self.assertTrue(result['range'])
        self.assertEqual(result['kind'],'potential');self.assertEqual(result['period'],'month')
        self.assertEqual(result['qualification_evidence'],source)
        with tempfile.TemporaryDirectory() as directory:
            data=extracted(source);data['salary']=value
            intended=proposal(posting_from_data(raw_post(source)),data,settings(directory))
        self.assertEqual(intended['changes']['salary_amount'],'2300')
        for bad in ('$6K-$11K /tháng tuỳ theo khả năng',
                    '$6K-$10K /tháng guaranteed', '$10K /tháng tuỳ theo khả năng',
                    '$6K-$10K /week tuỳ theo khả năng'):
            with self.subTest(amount=bad):
                with self.assertRaises(NeedsHuman):salary(source,{**value,'amount_text':bad})
        for conflicting in (source+'\nBonus $500/week',
                            source.replace('/tháng tuỳ','/tháng or /week tuỳ')):
            with self.subTest(source=conflicting):
                bad={**value,'evidence':{'quote':conflicting}}
                with self.assertRaises(NeedsHuman):salary(conflicting,bad)

    def test_location_sentence_reduces_to_all_proven_entities_before_ai(self):
        source='Tiệm ở thành phố Sylvester ,GA .\nIncome $6K-$10K /tháng tuỳ theo khả năng'
        post=posting_from_data(raw_post(source,city='Sylvester',state='GA',zipCode='31791'))
        data=extracted(source,'$6K-$10K /tháng','month','potential')
        data['salary']['evidence']={'quote':source.splitlines()[1]}
        data['facts']['location']=[{'quote':source.splitlines()[0]}]
        with tempfile.TemporaryDirectory() as directory:
            result=proposal(post,data,settings(directory))
        self.assertEqual(set(result['facts']['location']),{'Sylvester','GA'})
        rewritten='Cần thợ tại Sylvester, GA. Thu nhập $6,000-$10,000/tháng tùy theo khả năng.'
        self.assertTrue(invariant_facts(source,rewritten,result['facts'],post.fields))
        for bad in (rewritten.replace('Sylvester','Atlanta'),rewritten.replace('GA','NC')):
            self.assertFalse(invariant_facts(source,bad,result['facts'],post.fields))
        data['facts']['location']=[{'quote':'Income'}]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(NeedsHuman):proposal(post,data,settings(directory))

    def test_required_contact_missing_or_invalid_holds_and_original_fill_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=settings(directory);post=posting_from_data(raw_post())
            for key in ('salon','address','city','state','zip','phone_primary'):
                fields={**post.fields,key:''}
                with self.subTest(missing=key):
                    with self.assertRaises(NeedsHuman):proposal(type(post)(post.id,post.status,post.category,post.content,fields),
                                                               extracted(post.content),cfg)
            for key,value in (('zip','123'),('zip','ABCDE'),('state','Georgia'),
                              ('phone_primary','111-111-1111'),('phone_primary','4045550100 or 7705550100')):
                fields={**post.fields,key:value}
                with self.subTest(key=key,value=value):
                    with self.assertRaises(NeedsHuman):proposal(type(post)(post.id,post.status,post.category,post.content,fields),
                                                               extracted(post.content),cfg)
            source='Bao $1200/week. Address 123 Main St. ZIP 30301.'
            post=posting_from_data(raw_post(source,address='',zipCode=''))
            data=extracted(source);data['salary']['evidence']={'quote':'Bao $1200/week'}
            data['location']={'address':{'value':'123 Main St','evidence':{'quote':'123 Main St'}},
                              'zip':{'value':'30301','evidence':{'quote':'30301'}}}
            result=proposal(post,data,cfg)
            self.assertEqual(result['fields']['address'],'123 Main St');self.assertEqual(result['fields']['zip'],'30301')

    def test_only_exact_unchanged_location_suggestions_are_ignored(self):
        source='Salary $1200/week. Zip 30302.';post=posting_from_data(raw_post(source))
        data=extracted(source);data['salary']['evidence']={'quote':'Salary $1200/week'}
        data['location']={'zip':{'value':'30301','evidence':{'quote':'30301'}},
                          'email':{'value':'','evidence':{'quote':'Jim Phone'}}}
        normalized=normalize_extraction(post,data)
        self.assertEqual(normalized['location'],{});self.assertEqual(len(data['location']),2)
        data['location']['zip']={'value':'30302','evidence':{'quote':'30302'}}
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(proposal(post,normalize_extraction(post,data),settings(directory))['changes']['zip'], '30302')

    def test_short_title_can_use_body_facts_without_repeating_body(self):
        post=posting_from_data(raw_post('Income $6K-$10K /tháng tuỳ theo khả năng (6 ngày / tuần).',
                                       title='Cần thợ gấp6k-10k'))
        self.assertTrue(invariant_title(post,'Cần thợ nail: thu nhập $6,000-$10,000/tháng'))
        for title in ('Income $6K-$10K/tuần','Income $6K-$11K/tháng','Guaranteed $6K-$10K/tháng',
                      'Salary $2300/week','Call 770-555-0101'):
            self.assertFalse(invariant_title(post,title))

    def test_quote_and_offset_evidence_exact_bounded_unique(self):
        self.assertEqual(evidence('abc',{'start':0,'end':3}),'abc');self.assertEqual(evidence('abc',{'quote':'bc'}),'bc')
        for span in ({'start':True,'end':2},{'start':0,'end':4},{'quote':'wrong'}, {'quote':'a'}):
            with self.assertRaises(NeedsHuman): evidence('aa' if span=={'quote':'a'} else 'abc',span)

    def test_address_only_original_exact_missing_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            source='Bao $1200/week. Address 123 Main St.';post=posting_from_data(raw_post(source,address=''))
            data=extracted(source);data['salary']['evidence']={'quote':'Bao $1200/week'}
            data['location']={'address':{'value':'123 Main St','evidence':{'quote':'123 Main St'}}}
            self.assertEqual(proposal(post,data,settings(directory))['changes']['address'],'123 Main St')
            data['location']['address']['value']='999 Fake St'
            with self.assertRaises(NeedsHuman): proposal(post,data,settings(directory))

    def test_phone_dedup_preserves_distinct_numbers_and_extensions(self):
        value=dedup_phones('(404) 555-0100','+1 404 555 0100; 404-555-0100 x2; 404-555-0100 x3; 770-555-0100')
        self.assertEqual(value,'404-555-0100 x2; 404-555-0100 x3; 770-555-0100')
        self.assertEqual(dedup_phones('4045550100 ext 2','+1(404)555-0100 ext.2;4045550100'),'4045550100')
        with self.assertRaises(NeedsHuman): dedup_phones('4045550100','call manager later')

    def test_faithful_paraphrase_passes_invariants_changed_facts_fail(self):
        original='Income $6K-$10K/month. Free housing. Call 404-555-0100.'
        rewritten='Thu nhập $6K-$10K/month. Có nhà ở miễn phí. Call 404-555-0100.'
        facts={k:[] for k in FACT_KINDS};facts['benefits']=['Free housing.']
        self.assertTrue(invariant_facts(original,rewritten,facts,{}))
        for bad in (rewritten.replace('10K','11K'),rewritten.replace('/month','/week'),rewritten+' guaranteed',rewritten+' email fake@example.com'):
            self.assertFalse(invariant_facts(original,bad,facts,{}))
        self.assertTrue(invariant_facts('We offer paid vacation and flexible hours.',
            'Paid vacation and flexible hours are offered.',{'benefits':['We offer paid vacation and flexible hours.']},{}))

    def test_canonical_money_phone_and_known_structured_additions(self):
        original='Income $6K-$10K/tháng. Call (404) 555-0100 x2. Work 5 days/week.'
        rewritten='Thu nhập $6,000-$10,000/tháng. Call +1 404.555.0100 ext.2. Work 5 days/week.'
        self.assertTrue(invariant_facts(original,rewritten,{},{}))
        for changed in (rewritten.replace('10,000','11,000'),rewritten.replace('ext.2','ext.3'),
                        rewritten.replace('5 days','6 days'),rewritten.replace('/tháng','/tuần')):
            self.assertFalse(invariant_facts(original,changed,{},{}))
        fields={'phone_primary':'404-555-0100','phone_other':'770-555-0100 x3',
                'email':'salon@example.invalid','address':'123 Main St','zip':'30301','city':'Atlanta'}
        source='Salary $1200/week.'
        good=source+' Call +1 (404) 555-0100 or 7705550100 ext3, salon@example.invalid. 123 Main St, Atlanta 30301.'
        self.assertTrue(invariant_facts(source,good,{},fields))
        for bad in (good.replace('30301','30302'),good.replace('123 Main','124 Main'),
                    good.replace('7705550100','7705550101'),good.replace('salon@','other@'),good+' Bonus $500.'):
            self.assertFalse(invariant_facts(source,bad,{},fields))
        self.assertFalse(invariant_facts(source,source+' Bonus $30301.',{},fields))
        self.assertFalse(invariant_facts(source,source+' Bonus $ 30301.',{},fields))
