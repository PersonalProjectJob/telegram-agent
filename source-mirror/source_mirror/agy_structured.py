"""Experimental strict codec for AGY's schema-backed structured_output channel.

Not wired to the live broker. It normalizes representation only, never repairs
candidate content or approves a review. Preserve the native envelope separately.
All existing source/glossary/factual/MQM/publication checks remain required.
"""
import copy
import json
import math
import re

from .localization import strict_json


def schema(kind):
    """Return an exact task schema; neither schema forces PASS/zero uncertainty."""
    if kind=='official-draft':
        properties={'title_vi':{'type':'string','minLength':1,'maxLength':150000},
                    'text_vi':{'type':'string','minLength':1,'maxLength':150000},
                    'uncertainties':{'type':'array','items':{'type':'string'}}}
    elif kind=='mqm-review':
        issue={'type':'object','properties':{key:{'type':'string'} for key in ('block','category','severity','reason')},
               'required':['block','category','severity','reason'],'additionalProperties':False}
        issue['properties']['severity']['enum']=['critical','major','minor']
        properties={'verdict':{'type':'string','enum':['PASS','HOLD']},
                    **{key:{'type':'integer','minimum':0} for key in ('critical','major','minor')},
                    'issues':{'type':'array','items':issue},'uncertainties':{'type':'array','items':{'type':'string'}},
                    **{key:{'type':'boolean'} for key in ('glossary_ok','accuracy_ok','technical_clarity_ok')}}
    else:
        raise ValueError('unsupported structured schema kind')
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}


def _validate(value,definition):
    kind=definition['type']
    if kind=='object':
        if not isinstance(value,dict) or set(value)!=set(definition['required']): raise ValueError('structured fields missing or unexpected')
        for key,item in value.items(): _validate(item,definition['properties'][key])
    elif kind=='array':
        if not isinstance(value,list): raise ValueError('structured array required')
        for item in value: _validate(item,definition['items'])
    elif kind=='integer':
        if type(value) is not int or value<definition.get('minimum',0): raise ValueError('structured integer required')
    elif kind=='boolean':
        if type(value) is not bool: raise ValueError('structured boolean required')
    elif kind=='string':
        if not isinstance(value,str) or len(value)<definition.get('minLength',0) or len(value)>definition.get('maxLength',1_000_000):
            raise ValueError('structured string invalid')
    else:
        raise ValueError('unsupported structured type')
    if 'enum' in definition and value not in definition['enum']: raise ValueError('structured enum invalid')


def _canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def _text_frames(response):
    """AGY may concatenate a model JSON answer and structured finish answer.

    Parse every complete frame; never pick just the first/last object or discard
    prose, a warning, duplicate keys, or an incomplete markdown fence.
    """
    position=0;count=0;decoder=json.JSONDecoder()
    while position<len(response):
        while position<len(response) and response[position].isspace():position+=1
        if position==len(response):return
        count+=1
        if count>4:raise ValueError('too many native response frames')
        if response.startswith('```',position):
            opening=re.match(r'```(?:json)?[ \t]*\r?\n',response[position:],re.I)
            if not opening:raise ValueError('invalid native JSON fence')
            start=position+opening.end()
            closing=re.search(r'\r?\n```[ \t]*(?:\r?\n|$)',response[start:])
            if not closing:raise ValueError('incomplete native JSON fence')
            yield strict_json(response[start:start+closing.start()])
            position=start+closing.end()
        else:
            try:_,end=decoder.raw_decode(response,position)
            except json.JSONDecodeError as error:raise ValueError('non-JSON native response content') from error
            yield strict_json(response[position:end])
            position=end


def normalize(envelope,kind):
    """Consume native typed output only when any text channel agrees exactly.

    Complete JSON/markdown frames may be representation only, but every frame
    must strictly parse and equal the native structured object. No prose,
    truncated JSON, duplicate keys or contradicting warning may be discarded.
    """
    native_keys={'conversation_id','duration_seconds','json_schema','num_turns','response','status','structured_output','usage'}
    if not isinstance(envelope,dict) or not set(envelope)<=native_keys or envelope.get('status')!='SUCCESS':
        raise ValueError('successful structured envelope required')
    if 'conversation_id' in envelope and (not isinstance(envelope['conversation_id'],str) or not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}',envelope['conversation_id'])):
        raise ValueError('invalid native conversation identifier')
    if 'duration_seconds' in envelope and (type(envelope['duration_seconds']) not in (int,float) or not math.isfinite(envelope['duration_seconds']) or envelope['duration_seconds']<0):
        raise ValueError('invalid native duration')
    if 'num_turns' in envelope and (type(envelope['num_turns']) is not int or envelope['num_turns']<1):
        raise ValueError('invalid native turn count')
    if 'usage' in envelope:
        usage=envelope['usage']
        if (not isinstance(usage,dict) or set(usage)!={'input_tokens','output_tokens','thinking_tokens','cache_read_tokens','total_tokens'}
                or any(type(value) is not int or value<0 for value in usage.values())):
            raise ValueError('unknown or invalid native usage fields')
    # The trusted calling route chooses a fixed kind; the envelope never chooses
    # its own schema. This does not authenticate a caller or authorize publishing.
    expected_schema=schema(kind)
    if _canonical(envelope.get('json_schema'))!=_canonical(expected_schema):
        raise ValueError('native schema binding missing or different')
    structured=envelope.get('structured_output')
    _validate(structured,expected_schema)
    response=envelope.get('response')
    if not isinstance(response,str): raise ValueError('native response channel required')
    if response.strip():
        for other in _text_frames(response):
            if _canonical(other)!=_canonical(structured): raise ValueError('native text and structured channels disagree')
    result=copy.deepcopy(envelope)
    result['original_envelope']=copy.deepcopy(envelope)
    result['provider_response']=response
    result['response']=_canonical(structured)
    result['structured_schema_kind']=kind
    result['publication_enabled']=False
    result['quality_status']='NOT_EVALUATED_BY_CODEC'
    return result
