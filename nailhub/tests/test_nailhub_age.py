"""Owner's listing-Created approval window; synthetic dates and UI contracts."""

import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tests.nailhub_fixtures import FakeBrowser, raw_post, settings
from nailhub.browser import (Browser, LISTING_TIMEZONE, OldPosting, listing_age_days,
                                       listing_date_receipt, posting_from_data)
from nailhub.service import Service
from nailhub.state import State
from nailhub.validation import NeedsHuman


class ListingAgeProofTests(unittest.TestCase):
    def test_visible_calendar_day_binds_partial_listing_to_full_detail(self):
        raw = raw_post(created='2026-08-30T23:50:50.696083')
        for key in ('startDate', 'endDate', 'videoUrls', 'workingHours'):
            raw.pop(key)
        full = raw_post(created='0001-01-01T00:00:00',
                        startDate='0001-01-01T00:00:00',
                        endDate='2027-01-01T00:00:00', videoUrls=['protected'])
        listing_post, detail_post = posting_from_data(raw), posting_from_data(full)
        self.assertNotEqual(listing_post.fingerprint, detail_post.fingerprint)
        receipt = listing_date_receipt(raw, '08/30/2026',
                                       observed_at='2026-10-14T12:00:00+07:00')
        self.assertEqual(listing_age_days(detail_post, receipt, today=date(2026, 10, 14)), 45)
        self.assertEqual(listing_age_days(detail_post, receipt, today=date(2026, 10, 15)), 46)
        self.assertEqual(listing_date_receipt(raw, '8/30/2026')['visible_created'], '8/30/2026')
        changed = posting_from_data({**full, 'title': 'Changed source title'})
        with self.assertRaises(NeedsHuman):
            listing_age_days(changed, receipt, today=date(2026, 10, 14))

    def test_missing_mismatched_future_and_sentinel_dates_fail_closed(self):
        for created, visible in ((None, '10/14/2026'),
                                 ('0001-01-01T00:00:00', '01/01/0001'),
                                 ('2026-10-14T00:00:00', '10/13/2026'),
                                 ('2026-10-14T00:00:00Z', '10/14/2026'),
                                 ('2026-02-30T00:00:00', '02/30/2026')):
            with self.subTest(created=created, visible=visible), self.assertRaises(NeedsHuman):
                listing_date_receipt(raw_post(created=created), visible)
        raw = raw_post(created='2026-10-15T00:00:00')
        receipt = listing_date_receipt(raw, '10/15/2026')
        with self.assertRaises(NeedsHuman):
            listing_age_days(posting_from_data(raw), receipt, today=date(2026, 10, 14))


class VerifiedListingRowsTests(unittest.IsolatedAsyncioTestCase):
    # Literal UI fixtures keep the test independent of the adapter's header constants.
    LAYOUTS = (
        (['Title', 'Business Name', 'Posted By', 'State', 'City', 'Zip Code',
          'Category', 'Status', 'Created', 'Actions'],
         ['Cần Thợ Nails.', 'Twilight Nails', '-', 'IL', 'Downers Grove', '60516',
          'JobPosting', 'Draft', '10/23/2026', 'View']),
        (['Title', 'Business Name', 'Posted By', 'State', 'City', 'Zip Code',
          'Category', 'Schedule', 'Apply By', 'Status', 'Created', 'Actions'],
         ['Cần Thợ Nails.', 'Twilight Nails', '-', 'IL', 'Downers Grove', '60516',
          'JobPosting', '-', '-', 'Draft', '10/23/2026', 'View']),
        (['', 'Title', 'Business Name', 'Posted By', 'State', 'City', 'Zip Code',
          'Category', 'Urgent', 'Status', 'Created', 'Actions'],
         ['', 'Cần Thợ Nails.', 'Twilight Nails', '-', 'IL', 'Downers Grove', '60516',
          'JobPosting', '-', 'Draft', '10/23/2026', 'View']),
    )

    async def verify_rows(self, headers, cells, *, raw_changes=None, row_count=1):
        raw = raw_post(title='Cần Thợ Nails.', businessName='Twilight Nails', state='IL',
                       city='Downers Grove', zipCode='60516', created='2026-10-23T12:00:00')
        raw.update(raw_changes or {})
        row = SimpleNamespace(wait_for=AsyncMock(), locator=Mock(side_effect=lambda selector:
            SimpleNamespace(all_inner_texts=AsyncMock(return_value=cells)) if selector == 'td'
            else self.fail('unexpected row selector: ' + selector)))
        rows = SimpleNamespace(count=AsyncMock(return_value=row_count), nth=Mock(return_value=row))
        def table_locator(selector):
            if selector == 'thead th':
                return SimpleNamespace(all_inner_texts=AsyncMock(return_value=headers))
            self.assertEqual(selector, 'tbody > tr')
            return rows
        table = SimpleNamespace(locator=Mock(side_effect=table_locator),
                                count=AsyncMock(return_value=1), wait_for=AsyncMock(),
                                is_visible=AsyncMock(return_value=True))
        def page_locator(selector):
            self.assertEqual(selector, 'table')
            return table
        with tempfile.TemporaryDirectory() as directory:
            browser = Browser(settings(directory), page=SimpleNamespace(locator=page_locator))
            bound = await browser._verified_listing_rows({'items': [raw]})
        self.assertEqual(len(bound), 1)
        post, actual_row, receipt = bound[0]
        self.assertIs(actual_row, row)
        self.assertEqual(post.fingerprint, posting_from_data(raw).fingerprint)
        self.assertEqual(receipt['id'], raw['id'])
        self.assertEqual(receipt['source_hash'], post.fingerprint)
        self.assertEqual(receipt['visible_created'], '10/23/2026')
        self.assertEqual(receipt['created'], raw['created'])
        self.assertEqual(receipt['shared_source']['status'], 'Draft')

    async def test_all_three_exact_layouts_bind_source_status_and_created(self):
        for headers, cells in self.LAYOUTS:
            with self.subTest(headers=headers):
                await self.verify_rows(headers, cells)

    async def test_unknown_missing_duplicate_and_reordered_headers_fail_closed(self):
        for headers, cells in self.LAYOUTS:
            unknown = [*headers[:-1], 'Unknown']
            duplicate = [*headers[:-1], 'Created']
            reordered = list(headers)
            reordered[1], reordered[2] = reordered[2], reordered[1]
            for invalid in (unknown, headers[:-1], duplicate, reordered):
                with self.subTest(headers=invalid), self.assertRaisesRegex(NeedsHuman, 'columns'):
                    await self.verify_rows(invalid, cells)

    async def test_each_core_field_and_status_must_match_identified_source(self):
        for headers, cells in self.LAYOUTS:
            for field in ('Title', 'Business Name', 'Posted By', 'State', 'City',
                          'Zip Code', 'Category', 'Status'):
                wrong = list(cells)
                wrong[headers.index(field)] = 'Wrong source value'
                with self.subTest(headers=headers, field=field), self.assertRaisesRegex(NeedsHuman, 'visible row'):
                    await self.verify_rows(headers, wrong)

    async def test_created_requires_valid_same_day_source_date(self):
        for headers, cells in self.LAYOUTS:
            for invalid in ('10/22/2026', '', 'not a date'):
                wrong = list(cells)
                wrong[headers.index('Created')] = invalid
                with self.subTest(headers=headers, date=invalid), self.assertRaises(NeedsHuman):
                    await self.verify_rows(headers, wrong)
            with self.subTest(headers=headers, source_date='missing'), self.assertRaises(NeedsHuman):
                await self.verify_rows(headers, cells, raw_changes={'created': None})

    async def test_row_width_and_count_must_match_verified_contract(self):
        for headers, cells in self.LAYOUTS:
            for wrong in (cells[:-1], [*cells, 'extra']):
                with self.subTest(headers=headers, width=len(wrong)), self.assertRaisesRegex(NeedsHuman, 'visible row'):
                    await self.verify_rows(headers, wrong)
            with self.subTest(headers=headers, row_count=2), self.assertRaisesRegex(NeedsHuman, 'table rows'):
                await self.verify_rows(headers, cells, row_count=2)


class AgeStateTests(unittest.TestCase):
    def test_old_existing_detail_cas_is_retained_and_manual_queue_excludes_skip(self):
        with tempfile.TemporaryDirectory() as directory:
            state = State(str(Path(directory) / 'state.db'))
            detail = posting_from_data(raw_post(startDate='0001-01-01T00:00:00'))
            listing = posting_from_data({key:value for key,value in raw_post().items()
                                         if key != 'startDate'})
            self.assertNotEqual(detail.fingerprint, listing.fingerprint)
            state.observe(detail.id, detail.fingerprint, detail.snapshot())
            state.mark(detail.id, 'needs-human', 'prior hold')
            receipt = {'id': listing.id, 'created':'2026-08-29T00:00:00',
                       'visible_created':'08/29/2026', 'source_hash':listing.fingerprint,
                       'observed_at':'2026-10-14T12:00:00+07:00'}
            self.assertTrue(state.skip_old(listing.id, listing.fingerprint,
                                           listing.snapshot(), receipt, 46))
            row = state.get(detail.id)
            self.assertEqual((row['state'], row['hash']), ('skipped-age', detail.fingerprint))
            self.assertEqual(json.loads(row['original']), detail.snapshot())
            self.assertEqual(state.manual_posts(), [])
            self.assertEqual(json.loads(row['details'])['visible_created'], '08/29/2026')

    def test_approved_uncertain_and_inflight_checkpoints_cannot_be_reclassified(self):
        for protected in ('approved','uncertain','before_save','before_approve',
                          'before_publish','category_before_save'):
            with self.subTest(state=protected), tempfile.TemporaryDirectory() as directory:
                state = State(str(Path(directory) / 'state.db'))
                post = posting_from_data(raw_post())
                state.observe(post.id, post.fingerprint, post.snapshot())
                state.mark(post.id, protected, 'keep checkpoint', expected={'saved':'source'})
                before = state.get(post.id)
                receipt = {'id':post.id, 'created':'2026-08-29T00:00:00',
                           'visible_created':'08/29/2026', 'source_hash':post.fingerprint,
                           'observed_at':'2026-10-14T12:00:00+07:00'}
                self.assertFalse(state.skip_old(post.id, post.fingerprint,
                                                post.snapshot(), receipt, 46))
                self.assertEqual(state.get(post.id), before)


class AgeWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_identified_listing_receipt_stops_on_target_without_view_or_write(self):
        with tempfile.TemporaryDirectory() as directory:
            post = posting_from_data(raw_post())
            receipt = {'id':post.id, 'source_hash':post.fingerprint}
            page = SimpleNamespace(expect_response=Mock(side_effect=AssertionError('no next page')))
            browser = Browser(settings(directory), page=page)
            browser.ready = AsyncMock()
            browser._navigate = AsyncMock(return_value={
                'items':[raw_post()], 'pageNumber':1, 'hasNextPage':True})
            browser._verified_listing_rows = AsyncMock(return_value=[(post, None, receipt)])
            self.assertEqual(await browser.listing_receipt(post.id), receipt)
            page.expect_response.assert_not_called()

    async def test_fresh_age_proof_uses_secondary_page_and_strict_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            today = datetime.now(LISTING_TIMEZONE).date()
            secondary = SimpleNamespace(close=AsyncMock())
            primary = SimpleNamespace(context=SimpleNamespace(
                new_page=AsyncMock(return_value=secondary)))
            browser = Browser(settings(directory), page=primary)
            for days in (45, 46):
                with self.subTest(days=days):
                    day = today - timedelta(days=days)
                    raw = raw_post(created=day.isoformat() + 'T23:50:50.696083')
                    post = posting_from_data(raw)
                    receipt = listing_date_receipt(raw, f'{day.month}/{day.day}/{day.year}')
                    async def identified_receipt(reader, post_id):
                        self.assertEqual(post_id, post.id)
                        return receipt
                    with patch.object(Browser, 'listing_receipt', identified_receipt):
                        if days == 45:
                            self.assertEqual(await browser.verify_listing_age(post, fresh=True), receipt)
                        else:
                            with self.assertRaises(OldPosting):
                                await browser.verify_listing_age(post, fresh=True)
            self.assertEqual(primary.context.new_page.await_count, 2)
            self.assertEqual(secondary.close.await_count, 2)

    async def test_old_prior_hold_skips_before_same_hash_and_model(self):
        with tempfile.TemporaryDirectory() as directory:
            post = posting_from_data(raw_post())
            receipt = {'id':post.id, 'created':'2026-08-29T00:00:00',
                       'visible_created':'08/29/2026', 'source_hash':post.fingerprint,
                       'observed_at':'2026-10-14T12:00:00+07:00'}
            class OldBrowser(FakeBrowser):
                async def verify_listing_age(self, source, *, fresh=False):
                    raise OldPosting(receipt, 46)
            browser = OldBrowser(post)
            model = SimpleNamespace(extract=Mock(side_effect=AssertionError('no model call')))
            service = Service(settings(directory), model=model, browser_factory=lambda _:browser)
            service.state.observe(post.id, post.fingerprint, post.snapshot())
            service.state.mark(post.id, 'needs-human', 'prior hold')
            await service.scan()
            self.assertEqual(service.state.get(post.id)['state'], 'skipped-age')
            model.extract.assert_not_called()
            self.assertNotIn('read', browser.calls)

    async def test_old_at_fresh_pre_model_check_never_calls_model(self):
        with tempfile.TemporaryDirectory() as directory:
            post = posting_from_data(raw_post())
            receipt = {'id':post.id, 'created':'2026-08-29T00:00:00',
                       'visible_created':'08/29/2026', 'source_hash':post.fingerprint,
                       'observed_at':'2026-10-14T12:00:00+07:00'}
            class ChangingBrowser(FakeBrowser):
                async def verify_listing_age(self, source, *, fresh=False):
                    if fresh:
                        raise OldPosting(receipt, 46)
                    return receipt
            browser = ChangingBrowser(post)
            model = SimpleNamespace(extract=Mock(side_effect=AssertionError('no model call')))
            service = Service(settings(directory), model=model, browser_factory=lambda _:browser)
            await service.scan()
            self.assertEqual(service.state.get(post.id)['state'], 'skipped-age')
            model.extract.assert_not_called()

    async def test_fresh_preapprove_age_failure_blocks_confirm_click(self):
        with tempfile.TemporaryDirectory() as directory:
            post = posting_from_data(raw_post())
            browser = Browser(settings(directory,mode='live',verified=True,
                                       verification_ref='offline fake'), page=Mock())
            browser.read = AsyncMock(return_value=post)
            browser.action_click = AsyncMock()
            browser.control = AsyncMock(return_value=SimpleNamespace(wait_for=AsyncMock()))
            browser.verify_listing_age = AsyncMock(side_effect=OldPosting({'id':post.id}, 46))
            with self.assertRaises(OldPosting):
                await browser.approve(post)
            browser.action_click.assert_awaited_once_with('approve')

    async def test_old_before_ai_or_save_never_reaches_site_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            post = posting_from_data(raw_post())
            receipt = {'id':post.id}
            page = Mock()
            browser = Browser(settings(directory,mode='live',verified=True,
                                       verification_ref='offline fake'), page=page)
            browser.verify_listing_age = AsyncMock(side_effect=OldPosting(receipt, 46))
            browser._open_edit = AsyncMock()
            browser.action_click = AsyncMock()
            with self.assertRaises(OldPosting):
                await browser.edit_and_rewrite(post, {})
            browser._open_edit.assert_not_awaited()
            browser.action_click.assert_not_awaited()
            with self.assertRaises(OldPosting):
                await browser.save(post, post.snapshot())
            page.route.assert_not_called()
