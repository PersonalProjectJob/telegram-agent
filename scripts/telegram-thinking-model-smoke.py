"""Opt-in configured-model smoke. All GitHub/Telegram tools and SQLite are isolated fakes.

Run with the listener venv/user and --config /etc/tg-triage/listener.toml. Only the already
configured model may receive synthetic text under its existing consent settings. Never
opens the Telegram session or the configured live DB; never prints credentials/transcripts.
"""
import argparse
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import json
import logging
import sys
import time
from unittest.mock import patch

from tg_triage.config import load_config, AggregationConfig
from tg_triage.models import IncomingMessage, TopicGroup
from tg_triage.pipeline import Pipeline
from tg_triage.provider import build_provider
from tg_triage.store import Store
from tg_triage.thinking import decision_turn
from tg_triage import classifier as classifier_module
from tg_triage import thinking_transport as transport_module
from tg_triage import thinking as thinking_module

CASE_NAMES=('all','three_messages_one_request','three_short_independent_requests',
            'three_clear_independent_requests','ancillary_vs_third_request')


@contextmanager
def budget_experiment(call_seconds=None,turn_seconds=None):
    """Reviewed, bounded experiment in this fake-tool process; never changes the daemon."""
    if call_seconds is None and turn_seconds is None:
        yield
        return
    if (call_seconds,turn_seconds) not in ((20,40),(30,60)):
        raise ValueError('Unsupported paired experimental budget')
    class ExperimentalTurn(thinking_module.Turn):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            self.deadline=self.started+turn_seconds

        def attempt(self,body,*,consume=True):
            super().attempt(body,consume=consume)
            return min(call_seconds,self.remaining())
    with patch.object(thinking_module,'Turn',ExperimentalTurn):
        yield


def _size(value):
    return len(value) if isinstance(value,bytes) else len(value.encode('utf-8',errors='replace')) if isinstance(value,str) else None


class DiagnosticUsage:
    def __init__(self):
        self.attempts=[]

    def record(self,provider,model,role,outcome,usage,latency_ms):
        self.attempts.append(dict(provider=provider,model=model,role=role,outcome=outcome,
            latency_ms=round(latency_ms,2),prompt_tokens=usage.get('prompt_tokens',0),
            completion_tokens=usage.get('completion_tokens',0)))


class DiagnosticJSON:
    """Opt-in transport-module proxy: retain only lengths, status and schema shapes."""
    def __init__(self):
        self.events=[]

    def __getattr__(self,name):
        return getattr(json,name)

    def loads(self,value,*args,**kwargs):
        phase='wire_envelope' if isinstance(value,bytes) else 'model_content'
        event=dict(phase=phase,input_bytes=_size(value))
        try:
            result=json.loads(value,*args,**kwargs)
        except json.JSONDecodeError as error:
            event.update(outcome='invalid_json',error_position=error.pos)
            self.events.append(event)
            raise
        event.update(outcome='parsed_json',value_type=type(result).__name__)
        if phase=='wire_envelope' and isinstance(result,dict):
            choices=result.get('choices')
            choice=choices[0] if isinstance(choices,list) and choices and isinstance(choices[0],dict) else {}
            message=choice.get('message',result.get('message',{}))
            event['content_bytes']=_size(message.get('content')) if isinstance(message,dict) else None
            finish=choice.get('finish_reason')
            event['finish_reason']=finish if finish in ('stop','length','content_filter','tool_calls',None) else 'other'
        elif isinstance(result,dict):
            event['groups_count']=len(result['groups']) if isinstance(result.get('groups'),list) else None
        self.events.append(event)
        return result


class FakeGitHub:
    def __init__(self,repo):
        self.repo=repo; self.created=[]; self.comments=[]

    def create_issue(self,issue):
        self.created.append(issue)
        number=100+len(self.created)
        return number,f'https://github.com/{self.repo}/issues/{number}'

    def add_comment(self,number,body):
        self.comments.append((number,body))
        return 800+len(self.comments)


class DiagnosticProvider:
    def __init__(self,provider,usage):
        self.provider=provider
        self.usage=usage
        self.groups=[]
        self.parses=[]
        self.transport_json=DiagnosticJSON()
        self.call_latency_ms=[]

    def __getattr__(self,name):
        return getattr(self.provider,name)

    def classify_topics(self,messages,**options):
        original=classifier_module.parse_topic_groups
        def parse(content):
            value=original(content)
            event=dict(outcome='valid_groups' if value is not None else 'invalid_output',content_bytes=_size(content))
            try:
                data=json.loads(classifier_module.strip_thinking(content))
                groups=data.get('groups') if isinstance(data,dict) else None
                event['groups_type']=type(groups).__name__
                if isinstance(groups,list):
                    fields=('message_ids','category','type','actionable','verdict','title','summary','priority','confidence','missing_info')
                    event['group_shapes']=[{k:type(g.get(k)).__name__ for k in fields} if isinstance(g,dict)
                        else dict(group_type=type(g).__name__) for g in groups[:10]]
            except (ValueError,TypeError):
                event['content_json']='invalid'
            self.parses.append(event)
            return value
        started=time.monotonic()
        # Only this standalone, fake-tool process is instrumented; daemon modules are untouched.
        with patch.object(transport_module,'json',self.transport_json), patch.object(classifier_module,'parse_topic_groups',parse):
            value=self.provider.classify_topics(messages,**options)
        self.call_latency_ms.append(round((time.monotonic()-started)*1000,2))
        self.groups.append(None if value is None else [dict(ids=g.message_ids,verdict=g.verdict,
            category=g.category,type=g.type,actionable=g.actionable,confidence=g.confidence,
            priority=g.priority,missing_count=len(g.missing_info),title_len=len(g.title),summary_len=len(g.summary)) for g in value])
        return value


def msg(mid,text,**extra):
    fields=dict(chat_id=-1009999999999,message_id=mid,text=text,sender_id=7,
        sender_name='Synthetic user',received_at=datetime.now(timezone.utc),mentions_listener=False)
    fields.update(extra)
    return IncomingMessage(**fields)


def run(config_path,case='all'):
    if case not in CASE_NAMES:
        raise ValueError('Unsupported smoke case')
    # Schema-only identity placeholders are sufficient: no Telegram/GitHub client is created.
    cfg=load_config(config_path,validate_only=True)
    cfg=replace(cfg,mode='live',thinking_enabled=True,aggregation=AggregationConfig(5,3,40))
    results=[]
    # Clear work requires issue; abbreviated requests test partition/reply even when details
    # legitimately require clarification. Labels are fixed before the provider run.
    for name,texts,expected,clear in [
        ('three_messages_one_request',['Nút lưu báo cáo bị lỗi khi bấm.','Ở cùng màn hình báo cáo, bấm lưu không có phản hồi.','Nhờ team sửa lỗi nút lưu báo cáo vừa nêu.'],1,True),
        ('three_short_independent_requests',['Nút lưu báo cáo bị lỗi, nhờ sửa.','Yêu cầu khác: thêm bộ lọc ngày cho danh sách khách hàng.','Yêu cầu độc lập: sửa stop opt out trong automation.'],3,False),
        ('three_clear_independent_requests',['Ở màn hình báo cáo, bấm Lưu không có phản ứng; nhờ sửa để lưu báo cáo thành công.',
            'Yêu cầu riêng: ở danh sách khách hàng, thêm bộ lọc từ ngày đến ngày theo ngày tạo để chỉ hiện khách trong khoảng đã chọn.',
            'Lỗi độc lập ở automation: đã bật Stop opt out nhưng hệ thống vẫn gửi tin cho khách opt out; cần ngừng gửi cho các khách này.'],3,True)]:
        if case not in ('all',name):
            continue
        with closing(Store(':memory:')) as store:
            gh=FakeGitHub(cfg.gh_repo); notices=[]
            usage=DiagnosticUsage()
            provider=DiagnosticProvider(build_provider(cfg,usage=usage),usage)
            p=Pipeline(cfg,store,provider,gh,notice_sender=lambda c,m,t:notices.append(m) or 1000+m)
            for i,text in enumerate(texts,1):
                p.process(msg(i,text))
            records=[json.loads(r[0]) for r in store.connection.execute('SELECT record FROM thinking_decisions')]
            expected_replies=[1] if expected==1 else [1,2,3]
            decisions=[store.get(-1009999999999,i)['decision'] for i in range(1,4)]
            proposals=provider.groups[-1] if provider.groups else None
            valid=(len(gh.created)==expected and notices==expected_replies
                   and all(r['status']=='verified' and r['budget']['model_calls_used']<=2 for r in records)
                   and all(d=='issue' if clear else d in ('issue','needs_human') for d in decisions)
                   and proposals is not None and len(proposals)==expected
                   and all(g['missing_count']>0 for g in proposals if not clear and g['verdict']=='needs_human'))
            results.append(dict(case=name,pass_=valid,tickets=len(gh.created),reply_targets=notices,
                model_attempts=sum(r['budget']['model_calls_used'] for r in records),
                record_statuses=[r['status'] for r in records],reason_codes=[r['reason_code'] for r in records],
                group_proposals=provider.groups,expected_tickets=expected,expected_clear_issue=clear,
                diagnostics=dict(attempts=usage.attempts,transport_json=provider.transport_json.events,
                    parses=provider.parses,semantic_calls_latency_ms=provider.call_latency_ms),
                source_decisions=[dict(id=i,decision=store.get(-1009999999999,i)['decision'],
                    reason=store.get(-1009999999999,i)['reason'],confidence=store.get(-1009999999999,i)['confidence']) for i in range(1,4)]))
    if case in ('all','ancillary_vs_third_request'):
        with closing(Store(':memory:')) as store:
            provider=build_provider(cfg)
            p=Pipeline(cfg,store,provider)
            with decision_turn(p,'synthetic-role','synthetic-root',904):
                value=provider.analyze_source_roles([dict(ordinal=0,message_id=901,text='nên thêm ký tự cho tên biến'),
                    dict(ordinal=1,message_id=903,text='sửa stop opt out trong automation')],
                    [msg(902,'ảnh ở trên minh họa thêm thôi'),msg(904,'Yêu cầu khác: sửa trang thanh toán không lưu được')])
            roles={r['message_id']:r['role'] for r in (value or {}).get('source_roles',[])}
            results.append(dict(case='ancillary_vs_third_request',pass_=roles=={902:'ancillary',904:'request'},roles=roles))
    return dict(status='PASS' if all(r['pass_'] for r in results) else 'FAIL',
        kind='small synthetic configured-model smoke, not accuracy benchmark',cases=results,
        model=dict(provider=cfg.model.provider,name=cfg.model.name,max_output=cfg.model.max_output,
            configured_timeout_seconds=cfg.model.timeout_seconds,semantic_call_limit_seconds=20,turn_limit_seconds=40),
        real_github_writes=0,real_telegram_messages=0,live_database_opened=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True)
    parser.add_argument('--case',choices=CASE_NAMES,default='all')
    parser.add_argument('--experiment-call-seconds',type=int,choices=(20,30))
    parser.add_argument('--experiment-turn-seconds',type=int,choices=(40,60))
    args=parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        with budget_experiment(args.experiment_call_seconds,args.experiment_turn_seconds):
            report=run(args.config,args.case)
        if args.experiment_call_seconds is not None:
            report['experimental_budget']=dict(call_seconds=args.experiment_call_seconds,
                turn_seconds=args.experiment_turn_seconds,production_runtime_changed=False)
            report['model']['semantic_call_limit_seconds']=args.experiment_call_seconds
            report['model']['turn_limit_seconds']=args.experiment_turn_seconds
    except Exception as error:
        report=dict(status='FAIL',error_type=type(error).__name__,real_github_writes=0,real_telegram_messages=0)
    print(json.dumps(report,ensure_ascii=False))
    sys.exit(0 if report['status']=='PASS' else 1)
