import json
from pathlib import Path
import tempfile
import unittest
import io
from PIL import Image

from source_mirror.snapshot import save
from source_mirror.store import Store
from source_mirror.topics import candidates
from source_mirror.publisher import verify_source_at_create, VerificationIncomplete
from source_mirror.publication_gate import require_supported_source


class PublicationGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.store = Store(self.root); self.addCleanup(self.store.close)
        self.store.set_paused(False)

    def add(self, url, body='Nail design'):
        meta = {'source_url':url,'title':'Nail design'}
        aid = save(self.root,meta,[{'type':'html','html':'<p>'+body+'</p>'}],'<p>evidence</p>',[])
        self.store.add_ready(url,meta['title'])
        return aid,meta

    def test_official_reservation_cannot_be_bypassed_by_fake_approval(self):
        for url in ('https://www.cdc.gov/niosh/a','https://www.sba.gov/a','https://www.irs.gov/a'):
            aid,_ = self.add(url)
            with self.assertRaisesRegex(ValueError,'official source'):
                self.store.reserve('2026-10-29','08:00',aid)
            self.assertEqual(self.store.db.execute('SELECT count(*) FROM attempts').fetchone()[0],0)
            self.assertEqual(self.store.db.execute('SELECT count(*) FROM slots').fetchone()[0],0)
        with self.assertRaises(ValueError): require_supported_source('https://www.cdc.gov./a')
        require_supported_source('https://www.scratchmagazine.co.uk/a')

    def test_official_hold_does_not_displace_regular_candidate(self):
        official,_ = self.add('https://www.cdc.gov/niosh/a')
        regular,_ = self.add('https://www.scratchmagazine.co.uk/a')
        selected = candidates(self.store,'2026-10-29','08:00')
        self.assertEqual([a['id'] for a in selected],[regular])
        row=self.store.db.execute('SELECT state,reason FROM articles WHERE id=?',(official,)).fetchone()
        self.assertEqual(row['state'],'held'); self.assertTrue(row['reason'].startswith('topic:'))
        self.store.add_ready('https://www.cdc.gov/niosh/a','Nail design')
        self.assertEqual(self.store.db.execute('SELECT state FROM articles WHERE id=?',(official,)).fetchone()[0],'held')

    def test_tax_source_cannot_reserve_directly(self):
        aid,_=self.add('https://www.scratchmagazine.co.uk/a','Tax reporting')
        with self.assertRaisesRegex(ValueError,'tax topics'): self.store.reserve('2026-10-29','08:00',aid)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM attempts').fetchone()[0],0)

    def test_create_boundary_detects_official_or_changed_snapshot(self):
        aid,meta=self.add('https://www.scratchmagazine.co.uk/a')
        article=dict(self.store.db.execute('SELECT * FROM articles WHERE id=?',(aid,)).fetchone())
        verify_source_at_create(self.store,article,meta)
        with self.assertRaises(VerificationIncomplete): verify_source_at_create(self.store,article,dict(meta,title='changed'))
        article['source_url']='https://www.cdc.gov/a'
        with self.assertRaisesRegex(ValueError,'official source'): verify_source_at_create(self.store,article,meta)
        article['source_url']=meta['source_url']
        (self.root/'articles'/aid/'content.html').write_text('changed')
        with self.assertRaises(ValueError): verify_source_at_create(self.store,article,meta)

    def test_create_boundary_rejects_media_tamper(self):
        output=io.BytesIO(); Image.new('RGB',(1,1),'white').save(output,format='PNG')
        meta={'source_url':'https://www.scratchmagazine.co.uk/media','title':'Nail design'}
        aid=save(self.root,meta,[{'type':'image','media':0},{'type':'html','html':'<p>Nail design</p>'}],'<p>Nail design</p>',[{'bytes':output.getvalue()}])
        self.store.add_ready(meta['source_url'],meta['title'])
        article=dict(self.store.db.execute('SELECT * FROM articles WHERE id=?',(aid,)).fetchone())
        verify_source_at_create(self.store,article,meta)
        (self.root/'articles'/aid/'media'/'0000.png').write_bytes(b'changed')
        with self.assertRaises(ValueError): verify_source_at_create(self.store,article,meta)


if __name__=='__main__': unittest.main()
