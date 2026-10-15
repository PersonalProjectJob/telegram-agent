"""Conversational corrections with committed plans and at-most-once GitHub POSTs."""
import json
import logging
import re
from .followup_model import validate_followup, MAX_SPLIT
from .issue_format import Issue, format_issue, redact, sanitize, telegram_link
from .models import Classification
from .group_notices import clean_question, address_question

LOG=logging.getLogger(__name__)
SCOPE_QUESTION='Em chưa xác định chắc mỗi yêu cầu gồm những tin/ảnh nào. Anh/Chị chỉ rõ tin hoặc ảnh thuộc yêu cầu thứ nhất và thứ hai giúp Em nhé?'
TARGET_QUESTION='Em đang theo dõi nhiều ticket cho nội dung này. Bạn muốn bổ sung cho ticket thứ mấy trong danh sách dưới đây?'


def _safe(text):
    return redact(sanitize(text[:4000]))


def _link(repo, number):
    return f'https://github.com/{repo}/issues/{number}'


def _tickets(root):
    return ([root['issue_number']] if type(root['issue_number']) is int and root['issue_number']>0 else []) + json.loads(root['siblings'])


def _edit_retry_notice(pipeline,job,text,retry):
    editor=getattr(pipeline,'notice_editor',None)
    if editor is None or pipeline.config.mode=='dry-run':
        return
    store=pipeline.store
    if not store.reserve_followup_notice_edit(job['id']):
        return
    success=False
    try:
        success=editor(job['chat_id'],retry['notice_message_id'],text[:3500]) is True
    except Exception as error:
        LOG.warning('Followup notice edit unknown exception=%s',type(error).__name__)
    store.finish_followup_notice_edit(job['id'],success)


def _notify(pipeline, job, reply, text):
    if pipeline.config.mode=='dry-run':
        return
    store=pipeline.store
    retry=store.followup_analysis_retry(job['id'])
    if retry is not None:
        _edit_retry_notice(pipeline,job,text,retry)
        return
    if pipeline.notice_sender is None:
        return
    try:
        if not store.reserve_group_notice(reply.chat_id,reply.message_id,'followup'):
            return
        status,notice_id='failed',None
        try:
            sent=pipeline.notice_sender(reply.chat_id,reply.message_id,text[:3500])
            if type(sent) is int and sent>0:
                status,notice_id='sent',sent
        except Exception as error:
            LOG.warning('Followup notice failed exception=%s',type(error).__name__)
        store.finish_group_notice(reply.chat_id,reply.message_id,status,notice_id)
    except Exception as error:
        LOG.warning('Followup notice state failed exception=%s',type(error).__name__)


def _result(pipeline,job,root,reply,status,text,extra=(), *, include_links=True):
    numbers=list(dict.fromkeys(_tickets(root)+list(extra))) if include_links else []
    if numbers:
        text+='\n'+'\n'.join(f'{i+1}. {_link(pipeline.config.gh_repo,n)}' for i,n in enumerate(numbers))
    pipeline.store.finish_followup(job,status,text)
    _notify(pipeline,job,reply,text)


def _clarify(questions):
    questions=[address_question(q) for q in questions[:3]]
    questions=[q for q in questions if q]
    return 'Em cần Anh/Chị xác nhận thêm để xử lý đúng nhé:\n'+'\n'.join('• '+q for q in (questions or [SCOPE_QUESTION]))


def _explicit_label(text):
    text=' '.join((text or '')[:300].lower().split()).rstrip('.!😊 ')
    match=re.fullmatch(r'(?:(?:đây|đó|này) là )?(?:ticket|yêu cầu) ([1-5])(?: nhé)?',text)
    return int(match[1])-1 if match else None


def _explicit_count(text):
    text=' '.join((text or '')[:500].lower().split())
    if re.search(r'(^|\s)(?:không|đừng|chưa)(?:\s|$)',text) or any(ch in text for ch in '"“”\''):
        return None
    counts={int(m[1]) for m in re.finditer(r'(?:cần lên|cần tạo|tạo|lên|thành) ([2-5]) ticket\b',text)}
    return counts.pop() if len(counts)==1 else None


def _apply_context(state, entry, originals):
    """Only an explicit human label on an original reply edge binds a source; never infers tasks."""
    lookup={m.message_id:m for m in originals}
    sender=entry.get('sender_id'); anchor=entry.get('reply_to_id')
    ordinal=_explicit_label(entry.get('text'))
    count=_explicit_count(entry.get('text'))
    owner=state.get('author_id')
    first=originals[0] if originals else None
    if type(sender) is not int or (owner is not None and sender!=owner):
        return False, bool(ordinal is not None or count is not None)
    if count is not None and first is not None and first.sender_id==sender:
        if state.get('desired_count') not in (None,count) or any(v>=count for v in state['bindings'].values()):
            state['conflicts']['count']=entry.get('message_id')
            return False,True
        state['conflicts'].pop('count',None)
        state['desired_count']=count; state['author_id']=sender; state['count_evidence']=entry.get('message_id')
        state['conflicts'].pop('invalid_count',None)
    if ordinal is None:
        return False,False
    source=lookup.get(anchor)
    if source is None or source.sender_id!=sender:
        return False,True
    if state.get('desired_count') is not None and ordinal>=state['desired_count']:
        state['conflicts']['count']=entry.get('message_id')
        return False,True
    key=str(anchor)
    if key in state['bindings'] and state['bindings'][key]!=ordinal:
        state['conflicts'][key]=entry.get('message_id')
        return False,True
    state['conflicts'].pop(key,None)
    state['conflicts'].pop('invalid:'+key,None)
    state['author_id']=sender
    state['bindings'][key]=ordinal
    state['evidence'][key]=entry.get('message_id')
    return True,False


def _context(pipeline,job,root,originals,reply):
    state=json.loads(root.get('context_state') or '{}')
    state.setdefault('bindings',{}); state.setdefault('evidence',{}); state.setdefault('conflicts',{})
    lookup={m.message_id:m for m in originals}
    for key,ordinal in list(state['bindings'].items()):
        source=lookup.get(int(key)) if isinstance(key,str) and key.isdigit() else None
        evidence=pipeline.store.get(root['chat_id'],state['evidence'].get(key))
        valid=(type(ordinal) is int and 0<=ordinal<MAX_SPLIT and source is not None and evidence is not None
               and evidence['decision']=='followup' and evidence['case_key']==root['case_key']
               and evidence['reply_to_id']==source.message_id and evidence['sender_id']==source.sender_id
               and type(source.sender_id) is int and evidence['sender_id']==state.get('author_id')
               and _explicit_label(evidence['text'])==ordinal)
        if not valid:
            state['bindings'].pop(key,None)
            state['conflicts']['invalid:'+key]=True
    if state.get('desired_count') is not None:
        evidence=pipeline.store.get(root['chat_id'],state.get('count_evidence'))
        if (evidence is None or evidence['decision']!='followup' or evidence['case_key']!=root['case_key']
                or evidence['sender_id']!=state.get('author_id') or not originals
                or evidence['sender_id']!=originals[0].sender_id
                or _explicit_count(evidence['text'])!=state['desired_count']):
            state.pop('desired_count',None)
            state['conflicts']['invalid_count']=True
    # Old completed clarification jobs retain their real reply edges, even after the
    # five-entry model history rolls off. Reconstruct at most 100, then persist the result.
    for entry in pipeline.store.followup_binding_history(job):
        if entry['message_id']>state.get('processed_until',0):
            _apply_context(state,entry,originals)
            state['processed_until']=entry['message_id']
    current=dict(message_id=reply.message_id,reply_to_id=reply.reply_to_id,
                 sender_id=reply.sender_id,text=reply.text)
    accepted,conflict=_apply_context(state,current,originals)
    conflict=conflict or bool(state['conflicts'])
    state['processed_until']=max(reply.message_id,state.get('processed_until',0))
    state['unresolved_message_ids']=[m.message_id for m in originals if str(m.message_id) not in state['bindings']]
    pipeline.store.save_followup_context_state(job['root_key'],state)
    root['context_state']=json.dumps(state)
    return state,accepted,conflict


def _describe(item):
    text=clean_question(item.text).replace('“',"'").replace('”',"'")
    if text:
        return '“'+text[:85]+'”'+(' (kèm ảnh)' if item.has_image else '')
    when=item.received_at.strftime('%H:%M')
    sender='“'+clean_question(item.sender_name).replace('“',"'").replace('”',"'")[:40]+'”'
    return f'ảnh không caption của {sender} lúc {when}' if item.has_image else f'tin không có chữ của {sender} lúc {when}'


def _context_ack(state, originals):
    known=[]
    for item in originals:
        ordinal=state['bindings'].get(str(item.message_id))
        if ordinal is not None:
            known.append(f'{_describe(item)} thuộc phạm vi ticket {ordinal+1}')
    return 'Em đã ghi nhận '+ '; '.join(known)+'.' if known else ''


def _context_questions(state, originals):
    remaining=[m for m in originals if str(m.message_id) not in state['bindings']]
    if not state.get('desired_count'):
        return ['Bạn muốn tách thành bao nhiêu ticket tất cả?']
    count=state['desired_count']
    return [f'Tin/ảnh chưa được gán: {_describe(m)} — thuộc ticket nào trong {count} yêu cầu bạn đã xác nhận, hay cần giữ riêng để làm rõ?' for m in remaining[:3]]


def _anchor_split(state, originals):
    """Two or more explicitly confirmed request anchors; retain ALL unassigned content separately."""
    count=state.get('desired_count')
    if not count or any(state.get('author_id')!=m.sender_id for m in originals):
        return None
    groups=[]
    for ordinal in range(count):
        selected=[m for m in originals if state['bindings'].get(str(m.message_id))==ordinal]
        if not selected or not any(m.text.strip() for m in selected):
            return None
        text=next(m.text for m in selected if m.text.strip())
        groups.append(dict(message_ids=[m.message_id for m in selected],title=text[:200],summary=text[:1500],
                           type='task',priority='medium',missing_info=['Nhờ xác nhận chi tiết và kết quả mong muốn của yêu cầu này.']))
    unassigned=[m.message_id for m in originals if str(m.message_id) not in state['bindings']]
    return dict(action='split',confidence=1.0,target_ordinal=None,summary='',questions=[],groups=groups,
                desired_count=count,bindings=[],unresolved_message_ids=unassigned,context_reason='confirmed_anchors')


def _plan(pipeline,job,root,originals,reply,history):
    tickets=_tickets(root)
    if not tickets:
        return dict(action='clarify',questions=['Em chưa xác nhận được ticket gốc. Nhờ bạn hoặc Owner kiểm tra kết quả trên GitHub trước khi tạo lại nhé.']),[]
    state,accepted,conflict=_context(pipeline,job,root,originals,reply)
    ack=_context_ack(state,originals)
    if conflict:
        return dict(action='clarify',ack=ack,context_reason='conflicting_binding',
                    questions=['Em đang giữ các phạm vi đã xác nhận. Bạn xác nhận lại ticket muốn gán cho tin vừa reply, hoặc số ticket cần tách giúp Em nhé?']),[]
    if accepted and len(tickets)>1:
        ordinal=state['bindings'][str(reply.reply_to_id)]
        if ordinal>=len(tickets):
            return dict(action='clarify',ack=ack,questions=[TARGET_QUESTION]),[]
        selected=[m for m in originals if m.message_id==reply.reply_to_id]
        body='### Xác nhận phạm vi từ người gửi\n'+_safe(_describe(selected[0]))+f' thuộc phạm vi ticket {ordinal+1}.'
        if telegram_link(selected[0]):
            body+='\nTin nguồn: '+telegram_link(selected[0])
        images,documents=pipeline.store.followup_media(root,selected,pipeline.config.gh_repo)
        body+=''.join('\nẢnh nguồn: '+url for _,url in images)
        body+=''.join('\nTài liệu nguồn: '+url for _,url in documents)
        return dict(action='update',summary='Xác nhận phạm vi',questions=[],groups=[],context_reason='confirmed_binding'),[('comment',dict(number=tickets[ordinal],body=body))]
    anchor_plan=_anchor_split(state,originals) if (accepted or _explicit_count(reply.text) is not None) else None
    if accepted and anchor_plan is None:
        return dict(action='clarify',ack=ack,context_reason='partial_binding',
                    questions=_context_questions(state,originals)),[]
    unassigned_text=[m for m in originals if m.message_id in (anchor_plan or {}).get('unresolved_message_ids',[]) and m.text.strip()]
    ask=getattr(pipeline.classifier,'analyze_followup',None)
    raw=None
    try:
        # Scope corrections use the original text and media provenance. No pixel re-read:
        # this keeps the configured provider/consent path and never reuploads attachments.
        scopes=json.loads(root['split_plan']).get('groups',[]) if root.get('split_plan') and len(tickets)>1 else []
        mapping=[dict(ordinal=i,issue_number=n,
                      message_ids=scopes[i]['message_ids'] if i<len(scopes) else [m.message_id for m in originals],
                      scope=scopes[i]['summary'] if i<len(scopes) else '') for i,n in enumerate(tickets)]
        if mapping:
            mapping[0]['confirmed_context']=state
        if anchor_plan is not None:
            if not unassigned_text:
                raw=anchor_plan
            else:
                role_ask=getattr(pipeline.classifier,'analyze_source_roles',None)
                anchors=[dict(ordinal=ordinal,message_id=m.message_id,text=m.text)
                         for m in originals for ordinal in [state['bindings'].get(str(m.message_id))]
                         if ordinal is not None and m.text.strip()]
                response=role_ask(anchors,unassigned_text) if role_ask else None
                raw=(dict(action='clarify',confidence=1.0,target_ordinal=None,summary='',questions=[],groups=[],
                          source_roles=response.get('source_roles')) if isinstance(response,dict) else None)
        else:
            raw=ask(originals,reply,mapping,history) if ask else None
        value=validate_followup(raw)
    except Exception as error:
        LOG.warning('Followup classifier failed exception=%s',type(error).__name__)
        value=None
    status=(getattr(pipeline.classifier,'last_followup_outcome','provider_unavailable') if raw is None else 'invalid_output') if value is None else ('uncertain' if value['confidence']<pipeline.config.ollama.confidence_threshold else value['action'])
    LOG.info('Followup analysis job_id=%s status=%s confirmed_bindings=%s',job['id'],status,len(state['bindings']))
    expected_ids={m.message_id for m in originals}
    roles={r['message_id']:r for r in (value or {}).get('source_roles',[])}
    valid_roles=(len(roles)==len((value or {}).get('source_roles',[])) and set(roles)<=expected_ids
                 and (anchor_plan is None or not unassigned_text or set(roles)=={m.message_id for m in unassigned_text}))
    if value is not None and not valid_roles:
        return dict(action='clarify',ack=ack,questions=_context_questions(state,originals) or [SCOPE_QUESTION],
                    context_reason='invalid_source_roles'),[]
    role_clear=valid_roles and all(m.message_id in roles and roles[m.message_id]['role']=='ancillary'
        and roles[m.message_id]['confidence']>=pipeline.config.ollama.confidence_threshold for m in unassigned_text)
    if anchor_plan is not None and any(roles.get(m.message_id,{}).get('role') in ('request','unknown') for m in unassigned_text):
        return dict(action='clarify',ack=ack,questions=_context_questions(state,originals),context_reason='contradictory_source_role'),[]
    if (anchor_plan is not None and len(tickets)==1 and value is not None
            and value['confidence']>=pipeline.config.ollama.confidence_threshold
            and (not unassigned_text or role_clear)
            and not any(roles.get(m.message_id,{}).get('role')=='request' for m in unassigned_text)):
        # Additional text needs a semantic ancillary verdict in this same model request.
        # Never suppress a third request or use sender/adjacency as a role decision.
        value=anchor_plan
        value['source_roles']=list(roles.values())
    elif value is None or value['confidence']<pipeline.config.ollama.confidence_threshold:
        questions=(value.get('questions') if value else None) or _context_questions(state,originals) or [SCOPE_QUESTION]
        if value is None:
            questions=['Em chưa nhận được phân tích hợp lệ lúc này; các xác nhận trước vẫn được giữ.']+questions[:2]
        return dict(action='clarify',ack=ack,questions=questions,context_reason=status),[]
    if anchor_plan is not None and unassigned_text and value.get('context_reason')!='confirmed_anchors':
        model_full=(value['action']=='split' and not value.get('unresolved_message_ids') and
                    {i for g in value['groups'] for i in g['message_ids']}==expected_ids)
        if not model_full:
            return dict(action='clarify',ack=ack,questions=_context_questions(state,originals),context_reason='unresolved_role'),[]
    if value.get('desired_count') is not None and value['desired_count']!=state.get('desired_count'):
        return dict(action='clarify',ack=ack,questions=['Bạn xác nhận tổng số ticket muốn tách giúp Em nhé?'],context_reason='unproven_count'),[]
    for proposed in value.get('bindings',[]):
        if (not accepted or proposed['ordinal']!=_explicit_label(reply.text)
                or proposed['message_ids']!=[reply.reply_to_id]):
            return dict(action='clarify',ack=ack,questions=['Bạn reply trực tiếp vào tin yêu cầu và xác nhận ticket tương ứng giúp Em nhé?'],context_reason='unproven_binding'),[]
    action=value['action']
    if action in ('chat','clarify'):
        value['ack']=ack
        if action=='clarify' and state['bindings']:
            value['questions']=_context_questions(state,originals) or value['questions']
        return value,[]
    if action=='update':
        target=value['target_ordinal']
        if target is None and len(tickets)==1:
            target=0
        if target is None or target>=len(tickets):
            return dict(action='clarify',questions=[TARGET_QUESTION]),[]
        body='### Bổ sung từ hội thoại Telegram\n'+_safe(value['summary'])+'\n\nPhản hồi của người gửi:\n> '+_safe(reply.text).replace('\n','\n> ')
        if telegram_link(reply):
            body+='\n\nNguồn phản hồi: '+telegram_link(reply)
        return value,[('comment',dict(number=tickets[target],body=body))]
    ids=[m.message_id for m in originals]
    assigned=[i for g in value['groups'] for i in g['message_ids']]
    unresolved=value.get('unresolved_message_ids',[])
    accounted=assigned+unresolved
    positions={i:ordinal for ordinal,g in enumerate(value['groups']) for i in g['message_ids']}
    binding_mismatch=any(positions.get(int(i))!=ordinal for i,ordinal in state['bindings'].items())
    count_mismatch=state.get('desired_count') is not None and len(value['groups'])!=state['desired_count']
    unproven_bucket=bool(unresolved) and (not state.get('desired_count') or any(
        not any(state['bindings'].get(str(i))==ordinal for i in group['message_ids'])
        for ordinal,group in enumerate(value['groups'])))
    unresolved_text=[m for m in originals if m.message_id in unresolved and m.text.strip()]
    invalid_bucket_roles=bool(unresolved_text) and (not valid_roles or any(
        m.message_id not in roles or roles[m.message_id]['role']!='ancillary' or
        roles[m.message_id]['confidence']<pipeline.config.ollama.confidence_threshold for m in unresolved_text))
    lookup={m.message_id:m for m in originals}
    unproven_media=any(m.has_image and not m.text.strip() and str(m.message_id) not in state['bindings']
        and m.message_id not in unresolved and not (m.reply_to_id in positions
            and positions.get(m.message_id)==positions[m.reply_to_id]
            and lookup[m.reply_to_id].sender_id==m.sender_id)
        for m in originals) if state['bindings'] else False
    if (not ids or len(accounted)!=len(ids) or set(accounted)!=set(ids)
            or len(set(accounted))!=len(accounted) or binding_mismatch or count_mismatch or unproven_bucket or invalid_bucket_roles or unproven_media or ids[0] not in value['groups'][0]['message_ids']
            or len(value['groups'])>MAX_SPLIT):
        return dict(action='clarify',ack=ack,questions=_context_questions(state,originals) or [SCOPE_QUESTION],context_reason='invalid_accounting'),[]
    if len(tickets)>1 or pipeline.store.followup_split_attempted(job['root_key'],job['id']):
        return dict(action='clarify',questions=['Em đã ghi nhận lần tách trước. Bạn muốn chỉnh phạm vi của ticket thứ mấy? Em cần xác nhận trước khi tạo thêm.']),[]
    operations=[]
    for group in value['groups'][1:]:
        selected=[m for m in originals if m.message_id in group['message_ids']]
        c=Classification(group['type'],value['confidence'],group['title'],group['summary'],group['priority'])
        images,documents=pipeline.store.followup_media(root,selected,pipeline.config.gh_repo)
        issue=format_issue(selected[0],None if value.get('context_reason')=='confirmed_anchors' else c,'model',bool(group['missing_info']),pipeline.config.ollama.model,
                           case_messages=selected,max_quoted=len(selected),images=images,documents=documents,
                           clarify=group['missing_info'],text=(c.title,c.summary),details={})
        body=issue.body+'\n\nTicket nguồn: '+_link(pipeline.config.gh_repo,tickets[0])
        operations.append(('create',dict(title=issue.title,body=body,labels=list(issue.labels))))
    first=value['groups'][0]
    scope='### Điều chỉnh phạm vi sau phản hồi Telegram\nTicket này theo dõi yêu cầu thứ nhất: '+_safe(first['title'])+'\n'+_safe(first['summary'])
    scope+='\nID tin nguồn: '+', '.join(str(i) for i in first['message_ids'])
    if first['missing_info']:
        scope+='\nCần làm rõ:\n'+'\n'.join('• '+_safe(q) for q in first['missing_info'])
    if unresolved:
        pending=[m for m in originals if m.message_id in unresolved]
        scope+='\n\nTin/ảnh chưa được gán, phạm vi chưa xác nhận (không mặc định thuộc ticket này):\n'
        scope+='\n'.join('• '+_safe(_describe(m)) for m in pending)
        pending_images,pending_documents=pipeline.store.followup_media(root,pending,pipeline.config.gh_repo)
        scope+=''.join('\nẢnh chưa được gán: '+url for _,url in pending_images)
        scope+=''.join('\nTài liệu chưa được gán: '+url for _,url in pending_documents)
        value['questions']=_context_questions(state,originals)
    operations.append(('correction',dict(number=tickets[0],body=scope)))
    return value,operations


def _run(pipeline,job):
    store=pipeline.store
    root,originals,reply,history=store.followup_context(job)
    # A stored URL is only proof that the original issue belongs to the CURRENT configured
    # repo. It never selects an API destination. Check before model calls and planned resumes.
    source=store.get(root['chat_id'],root['source_message_id'])
    number=root['issue_number']
    expected=_link(pipeline.config.gh_repo,number)
    source_url=source['issue_url'] if source is not None else None
    if (type(number) is int and number>0
            and (source is None or source['issue_number']!=number or not isinstance(source_url,str)
                 or source_url.casefold()!=expected.casefold())):
        _result(pipeline,job,root,reply,'blocked',
                'Em cần Owner xác nhận lại repo và ticket gốc trước khi xử lý phản hồi này nhé. Em chưa chỉnh hoặc tạo thêm ticket.',
                include_links=False)
        return
    if job['status']=='ambiguous':
        _result(pipeline,job,root,reply,'blocked','Em chưa xác nhận được một thao tác GitHub đã hoàn tất hay chưa. Nhờ Owner kiểm tra trước khi thử lại; Em giữ các ticket đã xác nhận bên dưới nhé.')
        return
    with store._lock:
        earlier=store.connection.execute("SELECT 1 FROM followup_jobs WHERE root_key=? AND id<? AND status IN ('queued','planned') LIMIT 1", (job['root_key'],job['id'])).fetchone()
    if earlier:
        return
    # A previous interrupted/unknown POST blocks further writes for this conversation.
    with store._lock:
        prior=store.connection.execute("""SELECT 1 FROM followup_jobs j JOIN followup_operations o ON o.job_id=j.id
        WHERE j.root_key=? AND j.id<? AND o.status IN ('reserved','ambiguous') LIMIT 1""",(job['root_key'],job['id'])).fetchone()
    if prior:
        plan,ops=dict(action='clarify',questions=['Em cần Owner kiểm tra thao tác GitHub chưa rõ kết quả trước đó trước khi chỉnh tiếp nhé.']),[]
    elif job['plan'] is None:
        plan,ops=_plan(pipeline,job,root,originals,reply,history)
    else:
        plan,ops=json.loads(job['plan']),None
    if job['plan'] is None:
        store.save_followup_plan(job,plan,ops)
    if pipeline.config.mode=='dry-run':
        store.finish_followup(job,'dryrun','Simulated '+plan['action'])
        return
    if plan['action'] in ('clarify','chat'):
        text=_clarify(plan.get('questions',[])) if plan['action']=='clarify' else 'Em đã nhận phản hồi của Anh/Chị rồi nhé 😊'
        if plan.get('ack'):
            text=plan['ack']+'\n'+text
        _result(pipeline,job,root,reply,'done',text)
        return
    completed=[]
    for op in store.followup_operations(job):
        if op['status']=='done':
            if op['kind']=='create':
                completed.append(op['result'])
            continue
        if op['status']!='planned':
            _result(pipeline,job,root,reply,'blocked','Em chưa xác nhận được kết quả thao tác GitHub. Nhờ Owner kiểm tra trước khi thử lại nhé.',completed)
            return
        if not store.reserve_followup_operation(job,op['ordinal'],pipeline.config.max_issues_per_hour):
            _result(pipeline,job,root,reply,'blocked','Em đã chạm giới hạn tạo ticket nên chưa hoàn tất việc tách. Các ticket đã xác nhận được giữ lại bên dưới; nhờ Owner hỗ trợ phần còn lại nhé.',completed)
            return
        payload=json.loads(op['payload'])
        number=None
        try:
            if op['kind']=='create':
                number,_=pipeline.github.create_issue(Issue(payload['title'],payload['body'],tuple(payload['labels'])))
                if type(number) is not int or number<=0:
                    raise ValueError('Invalid created issue number')
                completed.append(number)
                store.finish_followup_operation(job,op,number)
            else:
                body=payload['body']
                if op['kind']=='correction':
                    body+='\n\nCác yêu cầu còn lại được theo dõi riêng:\n'+'\n'.join(_link(pipeline.config.gh_repo,n) for n in completed)
                number=pipeline.github.add_comment(payload['number'],body)
                if type(number) is not int or number<=0:
                    raise ValueError('Invalid comment number')
                store.finish_followup_operation(job,op,number)
        except Exception as error:
            LOG.warning('Followup operation outcome unknown exception=%s',type(error).__name__)
            if type(number) is int and number>0:
                store.remember_followup_result(job,op,number)
            with store._lock, store.connection:
                store.connection.execute("UPDATE followup_operations SET status='ambiguous' WHERE job_id=? AND ordinal=? AND status='reserved'",(job['id'],op['ordinal']))
            _result(pipeline,job,root,reply,'blocked','Em chưa xác nhận được một thao tác GitHub đã hoàn tất hay chưa. Nhờ Owner kiểm tra trước khi thử lại; các ticket đã xác nhận được giữ lại bên dưới nhé.',completed)
            return
    text=('Em đã tách các yêu cầu thành từng ticket riêng rồi nhé 😊' if plan['action']=='split'
          else 'Em đã bổ sung thông tin vào ticket rồi nhé 😊')
    questions=plan.get('questions',[])+[q for g in plan.get('groups',[]) for q in g['missing_info']]
    if questions:
        text+='\n'+_clarify(questions)
    _result(pipeline,job,root,reply,'done',text,completed)


def sweep(pipeline):
    """Called by intake and periodic case sweep, including after restarts with no new messages."""
    # All pipeline instances sharing this Store serialize roots, quota and HTTP reservations.
    with pipeline.store._followup_execution_lock:
        for job in pipeline.store.pending_followups():
            try:
                _run(pipeline,job)
            except Exception as error:
                LOG.error('Followup processing failed job_id=%s exception=%s',job['id'],type(error).__name__)
        for job in pipeline.store.pending_followup_notice_edits():
            try:
                _edit_retry_notice(pipeline,job,job['result'],pipeline.store.followup_analysis_retry(job['id']))
            except Exception as error:
                LOG.warning('Followup pending edit failed exception=%s',type(error).__name__)
