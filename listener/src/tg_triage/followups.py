"""Conversational corrections with committed plans and at-most-once GitHub POSTs."""
import json
import logging
from .followup_model import validate_followup, MAX_SPLIT
from .issue_format import Issue, format_issue, redact, sanitize, telegram_link
from .models import Classification
from .group_notices import clean_question

LOG=logging.getLogger(__name__)
SCOPE_QUESTION='Em chưa xác định chắc mỗi yêu cầu gồm những tin/ảnh nào. Bạn chỉ rõ tin hoặc ảnh thuộc yêu cầu thứ nhất và thứ hai giúp Em nhé?'
TARGET_QUESTION='Em đang theo dõi nhiều ticket cho nội dung này. Bạn muốn bổ sung cho ticket thứ mấy trong danh sách dưới đây?'


def _safe(text):
    return redact(sanitize(text[:4000]))


def _link(repo, number):
    return f'https://github.com/{repo}/issues/{number}'


def _tickets(root):
    return ([root['issue_number']] if type(root['issue_number']) is int and root['issue_number']>0 else []) + json.loads(root['siblings'])


def _notify(pipeline, job, reply, text):
    if pipeline.config.mode=='dry-run' or pipeline.notice_sender is None:
        return
    store=pipeline.store
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
    questions=[clean_question(q) for q in questions[:3]]
    questions=[q for q in questions if q]
    return 'Em cần bạn xác nhận thêm để xử lý đúng nhé:\n'+'\n'.join('• '+q for q in (questions or [SCOPE_QUESTION]))


def _plan(pipeline,job,root,originals,reply,history):
    tickets=_tickets(root)
    if not tickets:
        return dict(action='clarify',questions=['Em chưa xác nhận được ticket gốc. Nhờ bạn hoặc Owner kiểm tra kết quả trên GitHub trước khi tạo lại nhé.']),[]
    ask=getattr(pipeline.classifier,'analyze_followup',None)
    try:
        # Scope corrections use the original text and media provenance. No pixel re-read:
        # this keeps the configured provider/consent path and never reuploads attachments.
        scopes=json.loads(root['split_plan']).get('groups',[]) if root.get('split_plan') and len(tickets)>1 else []
        mapping=[dict(ordinal=i,issue_number=n,
                      message_ids=scopes[i]['message_ids'] if i<len(scopes) else [m.message_id for m in originals],
                      scope=scopes[i]['summary'] if i<len(scopes) else '') for i,n in enumerate(tickets)]
        raw=ask(originals,reply,mapping,history) if ask else None
        value=validate_followup(raw)
    except Exception as error:
        LOG.warning('Followup classifier failed exception=%s',type(error).__name__)
        value=None
    if value is None or value['confidence']<pipeline.config.ollama.confidence_threshold:
        return dict(action='clarify',questions=[SCOPE_QUESTION]),[]
    action=value['action']
    if action in ('chat','clarify'):
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
    if (not ids or len(assigned)!=len(ids) or set(assigned)!=set(ids)
            or len(set(assigned))!=len(assigned) or ids[0] not in value['groups'][0]['message_ids']
            or len(value['groups'])>MAX_SPLIT):
        return dict(action='clarify',questions=[SCOPE_QUESTION]),[]
    if len(tickets)>1 or pipeline.store.followup_split_attempted(job['root_key'],job['id']):
        return dict(action='clarify',questions=['Em đã ghi nhận lần tách trước. Bạn muốn chỉnh phạm vi của ticket thứ mấy? Em cần xác nhận trước khi tạo thêm.']),[]
    operations=[]
    for group in value['groups'][1:]:
        selected=[m for m in originals if m.message_id in group['message_ids']]
        c=Classification(group['type'],value['confidence'],group['title'],group['summary'],group['priority'])
        images,documents=pipeline.store.followup_media(root,selected,pipeline.config.gh_repo)
        issue=format_issue(selected[0],c,'model',bool(group['missing_info']),pipeline.config.ollama.model,
                           case_messages=selected,max_quoted=len(selected),images=images,documents=documents,
                           clarify=group['missing_info'],text=(c.title,c.summary),details={})
        body=issue.body+'\n\nTicket nguồn: '+_link(pipeline.config.gh_repo,tickets[0])
        operations.append(('create',dict(title=issue.title,body=body,labels=list(issue.labels))))
    first=value['groups'][0]
    scope='### Điều chỉnh phạm vi sau phản hồi Telegram\nTicket này theo dõi yêu cầu thứ nhất: '+_safe(first['title'])+'\n'+_safe(first['summary'])
    scope+='\nID tin nguồn: '+', '.join(str(i) for i in first['message_ids'])
    if first['missing_info']:
        scope+='\nCần làm rõ:\n'+'\n'.join('• '+_safe(q) for q in first['missing_info'])
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
        text=_clarify(plan.get('questions',[])) if plan['action']=='clarify' else 'Em đã nhận phản hồi của bạn rồi nhé 😊'
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
    questions=[q for g in plan.get('groups',[]) for q in g['missing_info']]
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
