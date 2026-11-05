"""Deterministic review payload, not a verified native Create request."""
import base64
import hashlib
import html
import json
import re
from collections import Counter
from datetime import datetime
from .localization import strict_json, model_response, validate_review
from .official_draft import LABELS, GLOSSARY, validate_retained_legal_labels, validate_vietnamese_surface
from .official_sources import SOURCES, extract, source_title, verify_source_date, MAX_BYTES
from .agy_broker import MODEL, EFFORT
from .topics import classify
from .source_disclosures import CDC_DISCLOSURE, IRS_DISCLOSURE, SBA_DISCLOSURE, SBA_TAX_DISCLOSURE
from .official_tax import source_is_tax
from .official_stock_policy import is_candidate as is_stock_candidate
from .official_generated_media import is_generated,validate_descriptor
from .official_source_links import marked_source, render_marked, validate_translation
from . import official_link_contract as link_contract


def build(draft_bytes, media, editorial, allow_official_tax=False):
    """Build exactly reproducible prospective content; never publishes."""
    if not isinstance(draft_bytes,bytes) or not draft_bytes: raise ValueError('draft missing')
    draft=strict_json(draft_bytes.decode('utf-8'))
    expected={'version','status','owner_approval','specialist_review','source','candidate','automated_mqm','generation_envelope','review_envelope','model','effort','glossary','generation_prompt_sha256','review_prompt_sha256'}
    if (not isinstance(draft,dict) or set(draft)!=expected or draft['version']!='official-review-v1' or draft['status']!='HOLD'
            or draft['owner_approval'] is not None or draft['specialist_review'] is not None
            or draft['model']!=MODEL or draft['effort']!=EFFORT or draft['glossary']!=GLOSSARY):
        raise ValueError('invalid held draft')
    source=draft['source']
    if not isinstance(source,dict) or set(source)!={'metadata','reference_text','source_label','source_html_base64'} or not isinstance(source['metadata'],dict):
        raise ValueError('invalid source schema')
    meta=source['metadata']; found=[x for x in SOURCES if x['id']==meta.get('source_id')]
    if len(found)!=1: raise ValueError('unknown source')
    item=found[0]; encoded=source['source_html_base64']
    if not isinstance(encoded,str) or len(encoded)>((MAX_BYTES+2)//3)*4: raise ValueError('invalid source HTML')
    try: raw=base64.b64decode(encoded,validate=True)
    except (ValueError,TypeError): raise ValueError('invalid source HTML') from None
    if extract(raw)!=source['reference_text']:
        raise ValueError('legacy source extraction requires a separate versioned snapshot')
    verify_source_date(item,raw)
    if (len(raw)>MAX_BYTES or base64.b64encode(raw).decode('ascii')!=encoded or hashlib.sha256(raw).hexdigest()!=meta.get('source_sha256')
            or extract(raw)!=source['reference_text'] or source_title(raw)!=meta.get('title')
            or hashlib.sha256(source['reference_text'].encode('utf-8')).hexdigest()!=meta.get('reference_text_sha256')
            or meta.get('source_url')!=item['url'] or meta.get('geographic_scope')!=item['scope']
            or meta.get('category')!=item['category'] or source['source_label']!=LABELS[item['id']]
            or meta.get('status')!='expert_review_required' or meta.get('version')!=1):
        raise ValueError('source binding mismatch')
    candidate=draft['candidate']; review=draft['automated_mqm']
    for field,kind in (('generation_envelope','official-draft'),('review_envelope','mqm-review')):
        envelope=draft[field]
        if isinstance(envelope,dict) and set(envelope)!={'status','response'}:
            from .agy_structured import normalize
            if normalize(envelope.get('original_envelope'),kind)!=envelope:
                raise ValueError('structured draft evidence mismatch')
    if (not isinstance(candidate,dict) or set(candidate)!={'title_vi','text_vi','uncertainties'} or candidate['uncertainties']!=[]
            or any(not isinstance(candidate[k],str) or not candidate[k].strip() for k in ('title_vi','text_vi'))
            or model_response(draft['generation_envelope'])!=candidate or model_response(draft['review_envelope'])!=review):
        raise ValueError('draft generation evidence mismatch')
    validate_review(review)
    validate_retained_legal_labels(source,candidate)
    if meta['source_id']=='cdc-ncezid-nail-hygiene-2024':
        validate_translation(raw,candidate['text_vi'])
    generic_links=[] if meta['source_id']=='cdc-ncezid-nail-hygiene-2024' else link_contract.links(raw,item['url'])
    if generic_links: link_contract.validate_translation(raw,item['url'],candidate['text_vi'])
    validate_vietnamese_surface(source,candidate,generic_links)
    if re.search(r'\bCAPT\b',source['reference_text']) and not re.search(r'\bCAPT\b',candidate['text_vi']):
        raise ValueError('source rank abbreviation changed')
    for field, original in (('title_vi',meta['title']),('text_vi',source['reference_text'])):
        translated=candidate[field]
        expected_numbers=Counter(re.findall(r'\d+(?:[.,]\d+)*',original))
        if meta['source_id']=='cdc-niosh-2015-bulletin' and field=='title_vi' and '2015' not in expected_numbers:
            expected_numbers['2015']=1
        if meta['source_id']=='cdc-ncezid-nail-hygiene-2024' and field=='title_vi' and '2024' not in expected_numbers:
            expected_numbers['2024']=1
        if (len(translated)>150000 or not re.search(r'[ăâđêôơưĂÂĐÊÔƠƯ\u1ea0-\u1ef9]',translated) or translated.strip()==original.strip()
                or expected_numbers != Counter(re.findall(r'\d+(?:[.,]\d+)*',translated))
                or (re.search(r'\b(?:no|not|never|without|cannot|don.t|mustn.t|shouldn.t)\b',original,re.I) and not re.search(r'\b(?:không|chưa|đừng|chẳng|khỏi)\b',translated,re.I))):
            raise ValueError('translation invariant failed')
    tax_source=source_is_tax(found[0],meta['title'],source['reference_text'])
    if not isinstance(media,list) or not media: raise ValueError('media required')
    if not isinstance(editorial,dict) or set(editorial)!={'locales','hashtags','category','status','is_public','is_featured','video_urls','published_at'}:
        raise ValueError('explicit editorial fields required')
    if not isinstance(editorial['locales'],dict) or set(editorial['locales'])!={'vi','en'}:
        raise ValueError('both editorial locales required')
    for fields in editorial['locales'].values():
        if not isinstance(fields,dict) or set(fields)!={'summary','meta_title','meta_description'}:
            raise ValueError('explicit locale fields required')
        for key,maximum in (('summary',500),('meta_title',70),('meta_description',160)):
            value=fields[key]
            if not isinstance(value,str) or not value.strip() or value!=value.strip() or len(value)>maximum:
                raise ValueError('invalid editorial field')
            if meta['source_id']=='cdc-niosh-2015-bulletin' and not re.search(r'\b2015\b',value):
                raise ValueError('historical date required in summary and metadata')
            if meta['source_id']=='cdc-ncezid-nail-hygiene-2024' and not re.search(r'\b2024\b',value):
                raise ValueError('source review year required in summary and metadata')
    tags=editorial['hashtags']
    if (not isinstance(tags,list) or not 3<=len(tags)<=5 or any(not isinstance(tag,str) or not 2<=len(tag)<=50 or tag!=tag.strip() or tag.startswith('#') for tag in tags) or len(set(tags))!=len(tags)):
        raise ValueError('invalid explicit hashtags')
    if (not isinstance(editorial['category'],str) or not editorial['category'].strip() or editorial['status']!='Published' or editorial['is_public'] is not True):
        raise ValueError('invalid publication fields')
    if type(editorial['is_featured']) is not bool or editorial['video_urls']!=[]:
        raise ValueError('featured flag and empty video list required')
    editorial_text=' '.join([editorial['category'],*tags,*[value for fields in editorial['locales'].values() for value in fields.values()]])
    try: published=datetime.fromisoformat(editorial['published_at'].replace('Z','+00:00'))
    except (AttributeError,ValueError): raise ValueError('invalid publication time') from None
    if published.tzinfo is None: raise ValueError('publication time requires timezone')
    references=[]
    for item in media:
        if not isinstance(item,dict) or (not is_generated(item) and set(item)!={'id','sha256','provenance_url','credit','rights_basis','rights_evidence_sha256','alt'}):
            raise ValueError('invalid media descriptor')
        if is_generated(item): validate_descriptor(item)
        if any(not isinstance(item[k],str) or not item[k].strip() for k in item): raise ValueError('invalid media descriptor')
        reference={'id':item['id'],'sha256':item['sha256'],'alt':item['alt'],'credit':item['credit']}
        if is_generated(item): reference.update({'kind':'generated','rights_evidence_sha256':item['rights_evidence_sha256']})
        references.append(reference)
    if len({item['id'] for item in references})!=len(references): raise ValueError('duplicate media')
    def body(text,language):
        if meta['source_id']=='cdc-ncezid-nail-hygiene-2024':
            content=render_marked(raw,text if language=='vi' else marked_source(raw),source_language=language=='en')
        elif generic_links:
            content=link_contract.render(raw,meta['source_url'],text if language=='vi' else link_contract.marked_source(raw,meta['source_url']),source_language=language=='en')
        else:
            paragraphs=text.strip().split('\n\n')
            content=''.join('<p>'+html.escape(p,quote=True).replace('\n','<br>')+'</p>' for p in paragraphs)
        credit_label='Ảnh: ' if language=='vi' else 'Image: '
        content+=''.join('<p>'+html.escape(credit_label+item['credit'],quote=True)+'</p>' for item in references if item.get('kind')!='generated')
        if meta['source_id']=='cdc-niosh-2015-bulletin':
            notice=('Nguồn xuất bản ngày 13/05/2015; số liệu và hướng dẫn phản ánh thời điểm đó.'
                    if language=='vi' else 'Source published May 13, 2015; figures and guidance reflect that time.')
            content='<p>'+html.escape(notice,quote=True)+'</p>'+content
        for image in media:
            if is_stock_candidate(image):
                label=('Ảnh stock minh họa chung; tác giả: ' if language=='vi' else 'General stock illustration; credit: ')
                content+='<p>'+html.escape(label+image['credit'],quote=True)+'</p>'
        if meta['source_id']=='cdc-ncezid-nail-hygiene-2024':
            notice=('Nguồn CDC được rà soát ngày 16/04/2024; cần kiểm tra quy định hiện hành tại địa phương.'
                    if language=='vi' else 'CDC source reviewed April 16, 2024; check current local requirements.')
            content='<p>'+html.escape(notice,quote=True)+'</p>'+content
        if meta['source_id'].startswith('cdc-'):
            source_link='<a href="'+html.escape(meta['source_url'],quote=True)+'" target="_blank" rel="noopener noreferrer">'+html.escape(source['source_label'],quote=True)+'</a>'
            return content+'<p>Source: '+source_link+'</p><p>'+html.escape(CDC_DISCLOSURE[language],quote=True)+'</p>'
        if meta['category']=='us-tax':
            content+='<p>'+html.escape(IRS_DISCLOSURE[language],quote=True)+'</p>'
        elif tax_source and meta['source_id']=='sba-business-plan':
            content+='<p>'+html.escape(SBA_TAX_DISCLOSURE[language],quote=True)+'</p>'
        if meta['source_id']=='sba-business-plan':
            content+='<p>'+html.escape(SBA_DISCLOSURE[language],quote=True)+'</p>'
        return content+'<p>Source: <a href="'+html.escape(meta['source_url'],quote=True)+'">'+html.escape(source['source_label'],quote=True)+'</a></p>'
    payload={'version':'official-payload-v1','source':{'id':meta['source_id'],'url':meta['source_url'],'label':source['source_label'],'scope':meta['geographic_scope']},
             'locales':{'vi':{'title':candidate['title_vi'],'body_html':body(candidate['text_vi'],'vi'),**editorial['locales']['vi']},
                        'en':{'title':meta['title']+(' (2015)' if meta['source_id']=='cdc-niosh-2015-bulletin' and '2015' not in meta['title'] else '')+(' (2024)' if meta['source_id']=='cdc-ncezid-nail-hygiene-2024' and '2024' not in meta['title'] else ''),'body_html':body(source['reference_text'],'en'),**editorial['locales']['en']}},'media':references,
             'hashtags':tags,'category':editorial['category'],'status':editorial['status'],'is_public':editorial['is_public'],
             'is_featured':editorial['is_featured'],'video_urls':editorial['video_urls'],'published_at':editorial['published_at']}
    return (json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode('utf-8')
