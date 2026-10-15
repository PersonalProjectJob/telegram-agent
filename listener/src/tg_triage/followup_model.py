"""Semantic follow-up boundary, parsed independently of all transport and durable state."""
import json
from .models import Classification, TICKET_TYPES, utf8_valid

MAX_SPLIT=5
FOLLOWUP_SCHEMA={"type":"object","properties":{
    "action":{"type":"string","enum":["update","split","clarify","chat"]},
    "confidence":{"type":"number"}, "target_ordinal":{"type":["integer","null"]},
    "summary":{"type":"string"}, "questions":{"type":"array","items":{"type":"string"}},
    "groups":{"type":"array","maxItems":MAX_SPLIT,"items":{"type":"object","properties":{
        "message_ids":{"type":"array","items":{"type":"integer"}},
        "title":{"type":"string"},"summary":{"type":"string"},
        "type":{"type":"string","enum":list(TICKET_TYPES)},
        "priority":{"type":"string","enum":["low","medium","high"]},
        "missing_info":{"type":"array","items":{"type":"string"}}},
        "required":["message_ids","title","summary","type","priority","missing_info"]}}},
    "required":["action","confidence","target_ordinal","summary","questions","groups"]}


def validate_followup(value):
    if not isinstance(value,dict) or value.get('action') not in ('update','split','clarify','chat'):
        return None
    confidence=value.get('confidence')
    if type(confidence) not in (int,float) or not 0 <= confidence <= 1:
        return None
    target=value.get('target_ordinal')
    if target is not None and (type(target) is not int or not 0 <= target < MAX_SPLIT):
        return None
    summary=value.get('summary','')
    questions=value.get('questions',[])
    groups=value.get('groups',[])
    if (not utf8_valid(summary) or not isinstance(questions,list)
            or any(not utf8_valid(q) for q in questions) or not isinstance(groups,list) or len(groups)>MAX_SPLIT):
        return None
    result=dict(action=value['action'],confidence=confidence,target_ordinal=target,
                summary=summary[:2000],questions=[q[:200] for q in questions[:3]],groups=[])
    for group in groups:
        if not isinstance(group,dict):
            return None
        ids=group.get('message_ids'); missing=group.get('missing_info',[])
        c=Classification(group.get('type'),confidence,group.get('title'),group.get('summary'),group.get('priority'))
        if (not isinstance(ids,list) or not ids or any(type(i) is not int for i in ids)
                or not c.is_valid() or c.type not in TICKET_TYPES or not isinstance(missing,list)
                or any(not utf8_valid(q) for q in missing)):
            return None
        result['groups'].append(dict(message_ids=ids,title=c.title[:300],summary=c.summary[:2000],
                                     type=c.type,priority=c.priority,missing_info=[q[:200] for q in missing[:3]]))
    if result['action']=='split' and len(groups)<2:
        return None
    if result['action']=='update' and not summary.strip():
        return None
    return result


def parse_followup(content):
    from .classifier import strip_thinking
    try:
        return validate_followup(json.loads(strip_thinking(content)))
    except (ValueError,TypeError):
        return None
