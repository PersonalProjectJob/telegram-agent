"""Bounded data-only schema v1. Descriptors are trusted source, requests are not."""
import copy
import json
import math
import re

from .errors import APIError

WORKER_ID = re.compile(r'[a-z][a-z0-9_-]{0,31}\Z')
ID = re.compile(r'[a-z][a-zA-Z0-9_-]{0,63}\Z')
FIELD_KEYS = {'id','type','label','default','minimum','maximum','maxLength','group','section','help','widget','options','visibleWhen','disabledWhen','placeholder','wide','step','itemType','maxItems'}
PUBLIC_KEYS = {'id','displayName','description','schemaVersion','contractRevision','capabilities','groups','providers','fields'}
TYPES = {'string','integer','number','boolean','enum','list'}
WIDGETS = {'text','number','toggle','select','textarea','provider'}

def _bad():
    raise ValueError('Invalid worker descriptor.')

def _text(value, limit=8000, empty=True):
    return isinstance(value,str) and (empty or bool(value.strip())) and len(value)<=limit and not any(ord(c)<32 and c not in '\n\r\t' for c in value)

def _number(value):
    return type(value) in (int,float) and -1e15<=value<=1e15 and math.isfinite(value)

def validate_condition(condition, fields, depth=0):
    if depth>8 or not isinstance(condition,dict) or len(condition)!=1:_bad()
    op, expression=next(iter(condition.items()))
    if op=='all':
        if not isinstance(expression,list) or not 1<=len(expression)<=128:_bad()
        for item in expression:validate_condition(item,fields,depth+1)
        return
    expected={'field','value'} if op=='equals' else {'field','values'}
    if op not in ('equals','in') or not isinstance(expression,dict) or set(expression)!=expected or expression['field'] not in fields:_bad()
    values=[expression['value']] if op=='equals' else expression['values']
    if not isinstance(values,list) or not 1<=len(values)<=256:_bad()
    for value in values:
        try:validate_value(fields[expression['field']],value)
        except APIError:_bad()

def validate_value(field, value):
    kind=field['type'];valid=False
    if kind=='string':valid=_text(value,field.get('maxLength',8000))
    elif kind=='boolean':valid=type(value) is bool
    elif kind=='integer':valid=type(value) is int and _number(value)
    elif kind=='number':valid=_number(value)
    elif kind=='enum':valid=type(value) is str and value in [item['value'] for item in field['options']]
    elif kind=='list':
        valid=isinstance(value,list) and len(value)<=field.get('maxItems',256)
        if valid:
            item_field={'id':field['id'],'type':field['itemType'],'maxLength':field.get('maxLength',8000)}
            for item in value:validate_value(item_field,item)
    if valid and kind in ('integer','number'):
        valid=field.get('minimum',-1e15)<=value<=field.get('maximum',1e15)
    if not valid:raise APIError('invalid_field','Value does not match the supported field contract.',field=field['id'])

def validate_descriptor(raw):
    if not isinstance(raw,dict):_bad()
    # Explicit projection: private TOML mappings never cross the HTTP boundary.
    try:public={key:copy.deepcopy(raw[key]) for key in PUBLIC_KEYS}
    except KeyError:_bad()
    try:encoded=json.dumps(raw,ensure_ascii=False,allow_nan=False).encode()
    except (ValueError,TypeError,RecursionError):_bad()
    if len(encoded)>256*1024:_bad()
    if not isinstance(public['id'],str) or not WORKER_ID.fullmatch(public['id']):_bad()
    if type(public['schemaVersion']) is not int or public['schemaVersion']!=1:_bad()
    if not all(_text(public[key],500,False) for key in ('displayName','description','contractRevision')):_bad()
    caps=public['capabilities']
    if not isinstance(caps,dict) or set(caps)!={'settings','probe','usage'} or any(type(v) is not bool for v in caps.values()):_bad()
    groups=public['groups'];sections=set();group_ids=set()
    if not isinstance(groups,list) or not 1<=len(groups)<=32:_bad()
    for group in groups:
        if not isinstance(group,dict) or set(group)!={'id','label','sections'} or not isinstance(group['id'],str) or not ID.fullmatch(group['id']) or group['id'] in group_ids or not _text(group['label'],200,False):_bad()
        group_ids.add(group['id'])
        if not isinstance(group['sections'],list) or len(group['sections'])>32:_bad()
        for section in group['sections']:
            if not isinstance(section,dict) or not {'id','title','description'}<=set(section) or set(section)-{'id','title','description','disabledWhen','disabledHelp'} or not isinstance(section['id'],str) or not ID.fullmatch(section['id']) or (group['id'],section['id']) in sections or not _text(section['title'],200,False) or not _text(section['description'],2000):_bad()
            if 'disabledHelp' in section and not _text(section['disabledHelp'],2000):_bad()
            sections.add((group['id'],section['id']))
    fields=public['fields'];by_id={}
    if not isinstance(fields,list) or not 1<=len(fields)<=128:_bad()
    for field in fields:
        required={'id','type','label','default','group','section','help','widget'}
        if not isinstance(field,dict) or not required<=set(field) or set(field)-FIELD_KEYS:_bad()
        if not isinstance(field['id'],str) or not ID.fullmatch(field['id']) or field['id'] in by_id or field['id'] in ('apiKey','key_file'):_bad()
        if field['type'] not in TYPES or field['widget'] not in WIDGETS or (field['group'],field['section']) not in sections:_bad()
        if not _text(field['label'],200,False) or not _text(field['help'],2000):_bad()
        for key in ('minimum','maximum','step'):
            if key in field and not _number(field[key]):_bad()
        if field.get('minimum',-1e15)>field.get('maximum',1e15) or field.get('step',1)<=0:_bad()
        for key,cap in (('maxLength',8000),('maxItems',256)):
            if key in field and (type(field[key]) is not int or not 1<=field[key]<=cap):_bad()
        if 'wide' in field and type(field['wide']) is not bool:_bad()
        if 'placeholder' in field and not _text(field['placeholder'],500):_bad()
        if field['type']=='list' and field.get('itemType') not in ('string','integer','number','boolean'):_bad()
        if field['type']=='enum':
            options=field.get('options');seen=set()
            if not isinstance(options,list) or not 1<=len(options)<=256:_bad()
            for option in options:
                if not isinstance(option,dict) or set(option)!={'value','label'} or not _text(option['value'],200,False) or not _text(option['label'],200,False) or option['value'] in seen:_bad()
                seen.add(option['value'])
        by_id[field['id']]=field
        try:validate_value(field,field['default'])
        except APIError:_bad()
    for field in fields:
        for key in ('visibleWhen','disabledWhen'):
            if key in field:validate_condition(field[key],by_id)
    for group in groups:
        for section in group['sections']:
            if 'disabledWhen' in section:validate_condition(section['disabledWhen'],by_id)
    providers=public['providers'];seen=set()
    if not isinstance(providers,list) or len(providers)>32:_bad()
    for provider in providers:
        if not isinstance(provider,dict) or set(provider)!={'id','label','note','defaults','requiresKey'} or not isinstance(provider['id'],str) or not ID.fullmatch(provider['id']) or provider['id'] in seen:_bad()
        if not _text(provider['label'],200,False) or not _text(provider['note'],200) or type(provider['requiresKey']) is not bool or not isinstance(provider['defaults'],dict) or set(provider['defaults'])!={'baseUrl','model'} or any(not _text(v,2000) for v in provider['defaults'].values()):_bad()
        seen.add(provider['id'])
    if providers and ('provider' not in by_id or by_id['provider']['type']!='enum' or seen!={v['value'] for v in by_id['provider']['options']}):_bad()
    mapping=raw.get('mapping')
    if not isinstance(mapping,dict) or set(mapping)!=set(by_id):_bad()
    for entry in mapping.values():
        if not isinstance(entry,dict) or set(entry)!={'section','key','codec'} or not _text(entry['section'],200) or not _text(entry['key'],200,False) or entry['codec'] not in ('identity','negative_ids','lines','mib_bytes'):_bad()
    return public

def validate_settings(descriptor, settings):
    if not isinstance(settings,dict) or set(settings)!={f['id'] for f in descriptor['fields']}:raise APIError('invalid_settings','Expected complete service settings.')
    for field in descriptor['fields']:validate_value(field,settings[field['id']])
    return copy.deepcopy(settings)
