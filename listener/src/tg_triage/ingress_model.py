"""Strict route plus canonical draft parser. Candidate IDs are opaque host references."""
import json
import math
from copy import deepcopy
from .classifier import CASE_SCHEMA, parse_case_analysis, strip_thinking

INGRESS_ANALYSIS_SCHEMA=deepcopy(CASE_SCHEMA)
INGRESS_ANALYSIS_SCHEMA['additionalProperties']=False
INGRESS_ANALYSIS_SCHEMA['properties']['details']['additionalProperties']=False
INGRESS_ANALYSIS_SCHEMA['properties']['title']['maxLength']=200
INGRESS_ANALYSIS_SCHEMA['properties']['summary']['maxLength']=500
INGRESS_ANALYSIS_SCHEMA['properties']['reason']['maxLength']=300
INGRESS_ANALYSIS_SCHEMA['properties']['missing_info']['maxItems']=3

INGRESS_SCHEMA={"type":"object","properties":{"groups":{"type":"array","items":{
    "type":"object","properties":{
        "message_ids":{"type":"array","items":{"type":"integer"}},
        "action":{"type":"string","enum":["NEW","CONTINUE","CLARIFY","CHAT"]},
        "candidate_id":{"type":["string","null"]},"confidence":{"type":"number"},
        "question":{"type":"string"},"analysis":INGRESS_ANALYSIS_SCHEMA},
    "required":["message_ids","action","candidate_id","confidence","question","analysis"],
    "additionalProperties":False}}},"required":["groups"],"additionalProperties":False}


def parse_ingress(content):
    try:
        data=json.loads(strip_thinking(content))
        if not isinstance(data,dict) or set(data)!={'groups'} or not isinstance(data['groups'],list) or not 1<=len(data['groups'])<=20:
            return None
        result=[]
        for group in data['groups']:
            if not isinstance(group,dict) or set(group)!={'message_ids','action','candidate_id','confidence','question','analysis'}:
                return None
            ids=group['message_ids']; confidence=group['confidence']
            if (not isinstance(ids,list) or not ids or any(type(i) is not int for i in ids)
                    or len(ids)!=len(set(ids)) or group['action'] not in ('NEW','CONTINUE','CLARIFY','CHAT')
                    or type(confidence) not in (int,float) or not math.isfinite(confidence) or not 0<=confidence<=1
                    or not isinstance(group['question'],str) or len(group['question'])>500
                    or group['candidate_id'] is not None and not isinstance(group['candidate_id'],str)):
                return None
            raw=group['analysis']
            if not isinstance(raw,dict) or set(raw)-set(INGRESS_ANALYSIS_SCHEMA['properties']):
                return None
            details=raw.get('details',{})
            if not isinstance(details,dict) or set(details)-set(INGRESS_ANALYSIS_SCHEMA['properties']['details']['properties']):
                return None
            if (any(not isinstance(raw.get(k,''),str) or len(raw.get(k,''))>cap for k,cap in
                    (('title',200),('summary',500),('reason',300)))
                    or not isinstance(raw.get('missing_info',[]),list) or len(raw.get('missing_info',[]))>3):
                return None
            analysis=parse_case_analysis(json.dumps(raw,ensure_ascii=False))
            if analysis is None:
                return None
            result.append(dict(group,analysis=analysis))
        return result
    except (ValueError,TypeError,KeyError):
        return None
