"""Conservative editorial selection; this module does not create or publish posts."""
from datetime import date
from html.parser import HTMLParser
import json
import re
import unicodedata
from .store import verify_snapshot, sha


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts=[]
    def handle_data(self, data):
        self.parts.append(data)


_TAX=re.compile(r'\b(?:tax(?:es|ation|able)?|irs|1099(?:[ -]k|[ -]nec)?|w[ -]?[249]|(?:form[ -]?)?(?:941|1040)|payroll|withholding|deductions?|bookkeeping|accounting|sales[ -]tax|tip[ -]+reporting|report(?:ing)?[ -]+tips|qualified[ -]+tips|employee[ -]+(?:vs\.?|versus|or)[ -]+independent[ -]+contractor|thuế|thue|khai[ -]+thuế|kê[ -]+khai[ -]+thuế|khấu[ -]+trừ|tiền[ -]+thuế)\b',re.I)
_BUSINESS=re.compile(r'\b(?:salon (?:business|owner|management|marketing|growth|startup|retention)|(?:open|opening|manage|managing|grow|growing|build|building|running|finance|financing) (?:a |your )?(?:nail )?salon|business plan|client retention|pricing strategy)\b',re.I)
_LUXURY=re.compile(r'\b(?:luxury|luxurious|premium|elegan(?:t|ce)|bridal|classic|minimalist|marble|chrome|metallic|gold|pearl|quiet luxury)\b',re.I)
DEFAULT_SLOTS={'08:00':'business','12:00':'other','16:00':'business','20:00':'business'}


def policy(config):
    value=config.get('topics',{})
    if not isinstance(value,dict) or set(value)-{'halloween_max_per_7_days','priority_slots','us_tax_requires_human_review'}:
        raise ValueError('invalid topics configuration')
    cap=value.get('halloween_max_per_7_days',1); slots=value.get('priority_slots',DEFAULT_SLOTS)
    if type(cap) is not int or cap not in (0,1) or type(value.get('us_tax_requires_human_review',True)) is not bool:
        raise ValueError('unsafe topics policy')
    if not isinstance(slots,dict) or set(slots)!=set(DEFAULT_SLOTS) or any(x not in ('business','luxury','other') for x in slots.values()):
        raise ValueError('invalid topic slot priorities')
    return cap,slots


def classify(text):
    """Do not treat the nail-art word 'tip' as tax advice."""
    text=unicodedata.normalize('NFKC',text)
    normalized=''.join((' ' if i and ch.isupper() and text[i-1].islower() else '')+ch for i,ch in enumerate(text)).replace('_',' ')
    normalized=re.sub(r'\baccounting for regional differences in expenses\b', 'considering regional differences in expenses', normalized, flags=re.I)
    if _TAX.search(normalized): return 'tax_review'
    if _BUSINESS.search(text): return 'business'
    if _LUXURY.search(text): return 'luxury'
    return 'other'


def classify_article(store, article):
    folder=verify_snapshot(store.root,article['id'])
    if sha(folder/'snapshot-manifest.json')!=article['manifest_sha']:
        raise ValueError('snapshot manifest changed')
    blocks=json.loads((folder/'content.json').read_text(encoding='utf-8'))
    parts=[article['title'],article['source_url']]
    media=json.loads((folder/'media-manifest.json').read_text(encoding='utf-8'))
    parts.extend(item.get('alt','') for item in media if isinstance(item,dict) and isinstance(item.get('alt',''),str))
    for item in blocks:
        if item.get('type')=='html':
            parser=_Text();parser.feed(item['html']);parser.close()
            parts.extend(parser.parts)
    return classify(' '.join(parts))


def payload_contains_tax(payload):
    """Conservatively recheck generated fields before any publish reservation."""
    parts=[]
    for locale in payload['locales'].values():
        for key in ('title','summary','meta_title','meta_description'):
            value=locale[key]
            if not isinstance(value,str):raise ValueError('invalid localized text')
            parts.append(value)
        for item in locale['blocks']:
            if item.get('type')=='html':
                parser=_Text();parser.feed(item['html']);parser.close();parts.extend(parser.parts)
    parts.extend(payload['hashtags'])
    return classify(' '.join(parts)) == 'tax_review'


def matches_slot(store,article,slot):
    if slot is None:return True
    from .store import verify_snapshot as verify_slot_snapshot
    from .localization import source_text
    from .slot_policy import allows
    folder=verify_slot_snapshot(store.root,article['id'])
    metadata=json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
    blocks=json.loads((folder/'content.json').read_text(encoding='utf-8'))
    return allows(slot,metadata.get('title'),source_text(blocks))


def official_business_source(store, article):
    """Require an authorized primary source, independently of topic keywords."""
    from .official_sources import SOURCES
    from .original_business import CATALOG, verify_original
    folder=verify_snapshot(store.root,article['id'])
    metadata=json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
    url=metadata.get('source_url')
    if metadata.get('content_origin')=='ai_original':
        blocks=json.loads((folder/'content.json').read_text(encoding='utf-8'))
        verify_original(metadata,blocks,fresh=False)
        return url in {item['url'] for item in CATALOG}
    return url in {item['url'] for item in SOURCES}


def candidates(store, day, slot, limit=8, config=None, only_article_id=None):
    """Return eligible candidates before any localization or publish reservation.

    Legacy halloween_max_per_7_days is accepted for config compatibility but
    does not select, suppress, prioritize, or limit any article.
    """
    _,priority_slots=policy(config or {})
    date.fromisoformat(day)
    # Scan the whole eligible queue, then apply the bounded inference budget.
    rows=store.ready_candidates(limit=None)
    if only_article_id is not None:rows=[row for row in rows if row['id']==only_article_id]
    valid=[]
    for article in rows:
        try: category=classify_article(store,article)
        except Exception:
            store.quality_hold(article['id'],'topic: snapshot validation failed')
            continue
        valid.append(('business' if category=='tax_review' else category,article))
    if slot is not None:
        eligible=[]
        for pair in valid:
            try:
                if matches_slot(store,pair[1],slot):eligible.append(pair)
            except (ValueError,OSError,TypeError,KeyError):
                store.quality_hold(pair[1]['id'],'topic: snapshot validation failed')
        valid=eligible
        from .slot_policy import BUSINESS_SLOTS, priority_for_article
        if slot in BUSINESS_SLOTS:
            ranked=[]
            for pair in valid:
                try: ranked.append((priority_for_article(store,pair[1],slot),pair))
                except (ValueError,OSError,TypeError,KeyError):
                    store.quality_hold(pair[1]['id'],'topic: snapshot validation failed')
            ranked.sort(key=lambda pair: pair[0])
            valid=[pair for _,pair in ranked]
    # Source trust follows the content route, not presence of tax words.
    from .slot_policy import BUSINESS_SLOTS, title_kind
    source_checked=[]
    for pair in valid:
        try:
            needs_official=slot in BUSINESS_SLOTS
            if slot is None:
                folder=verify_snapshot(store.root,pair[1]['id'])
                meta=json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
                from .localization import source_text
                blocks=json.loads((folder/'content.json').read_text(encoding='utf-8'))
                needs_official=title_kind(meta.get('title'),source_text(blocks))!='nail'
            if not needs_official or official_business_source(store,pair[1]):source_checked.append(pair)
        except (ValueError,OSError,TypeError,KeyError):
            store.quality_hold(pair[1]['id'],'topic: source validation failed')
    valid=source_checked
    if slot is None:
        preferred=priority_slots.get(slot,'luxury')
        valid.sort(key=lambda pair: 0 if pair[0]==preferred else (1 if pair[0] in ('business','luxury') else 2))
    return [item for _,item in valid[:limit]]
