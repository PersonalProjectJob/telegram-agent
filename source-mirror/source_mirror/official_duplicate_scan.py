"""Two bounded observations of the admin list, not an atomic duplicate lock.

An independent writer can still create an article between scan and Create.
"""
import math
import re
import time
from urllib.parse import urlsplit,parse_qs
from .localization import strict_json
from .store import canonical

MAX_PAGES=100
MAX_SECONDS=90
MAX_BODY=2_000_000

class DuplicateSource(ValueError):
    """A stable list already contains this candidate's source."""

def _known_unrelated_bare_host(value):
    # Only simple ASCII DNS hostnames with no sign of a government host can
    # be distinguished from our allowlisted .gov sources without guessing.
    if not isinstance(value,str) or len(value)>253 or 'gov' in value.lower():return False
    labels=value.split('.')
    return len(labels)>=2 and all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?',label) for label in labels) and labels[-1].isalpha()

def _response(response,number):
    try:
        url=urlsplit(response.url); q=parse_qs(url.query)
        return (response.request.method=='GET' and url.scheme=='https' and url.hostname=='api.nailhub.ai'
                and url.path=='/api/v1/admin/blogs' and q.get('pageNumber')==[str(number)]
                and q.get('pageSize')==['10'] and not (set(q)-{'pageNumber','pageSize'}))
    except (TypeError,ValueError): return False

def _scan_timeout(deadline,error_type,message):
    remaining=deadline-time.monotonic()
    if remaining<=0:raise error_type(message)
    return min(15000,max(1,int(remaining*1000)))


def _scan_decode(response,number,matcher,parser,limit,error_type,bad_response,too_large,non_object):
    if not response.ok or not matcher(response,number):raise error_type(bad_response)
    body=response.body()
    if len(body)>limit:raise error_type(too_large)
    result=parser(body.decode('utf-8'))
    if not isinstance(result,dict):raise error_type(non_object)
    return result


def _one(page,deadline):
    def timeout():return _scan_timeout(deadline,ValueError,'source list scan timed out')
    def decode(response,number):
        return _scan_decode(response,number,_response,strict_json,MAX_BODY,ValueError,'source list response invalid','source list response too large','source list response invalid')
    with page.expect_response(lambda r:_response(r,1),timeout=timeout()) as first:
        navigation=page.goto('https://nailhub.ai/admin/blogs',wait_until='domcontentloaded',timeout=timeout())
    if not navigation or navigation.status!=200 or page.url!='https://nailhub.ai/admin/blogs':raise ValueError('admin list unavailable')
    records=[];identifiers=set(); expected=None
    for number in range(1,MAX_PAGES+1):
        if number==1: response=first.value
        else:
            button=page.get_by_role('button',name='Trang sau',exact=True)
            if button.count()!=1 or not button.is_enabled():raise ValueError('admin next page unavailable')
            with page.expect_response(lambda r:_response(r,number),timeout=timeout()) as received: button.click(timeout=timeout())
            response=received.value
        value=decode(response,number)
        total=value.get('totalCount'); pages=value.get('totalPages'); items=value.get('items')
        if (type(total) is not int or total<0 or type(pages) is not int or pages!=math.ceil(total/10)
                or pages>MAX_PAGES or value.get('pageNumber')!=number or type(value.get('pageNumber')) is not int
                or type(value.get('hasNextPage')) is not bool or value['hasNextPage']!=(number<pages)
                or type(value.get('hasPreviousPage')) is not bool or value['hasPreviousPage']!=(number>1)
                or not isinstance(items,list) or len(items)!=min(10,max(0,total-(number-1)*10))):
            raise ValueError('source list pagination invalid')
        if expected is None:expected=(total,pages)
        elif expected!=(total,pages):raise ValueError('source list changed during scan')
        current=[]
        for item in items:
            if not isinstance(item,dict) or not isinstance(item.get('id'),str) or not item['id'] or item['id'] in identifiers:
                raise ValueError('source list item invalid')
            if 'externalSourceUrl' not in item:raise ValueError('source list source missing')
            url=item['externalSourceUrl']
            if url is not None and not isinstance(url,str):raise ValueError('source list source invalid')
            status=item.get('status')
            if status not in ('Published','Draft','Archived'):raise ValueError('source list status invalid')
            identifiers.add(item['id'])
            if status=='Published':current.append((item['id'],url))
        records.append(tuple(current))
        if number>=pages:break
    if len(identifiers)!=expected[0]:raise ValueError('source list incomplete')
    return expected,tuple(records)

def check(page,source_url):
    target=canonical(source_url)
    if source_url!=target or not urlsplit(target).hostname.endswith('.gov'):raise ValueError('source URL is not canonical official source')
    deadline=time.monotonic()+MAX_SECONDS
    first=_one(page,deadline);second=_one(page,deadline)
    if first!=second:raise ValueError('source list changed between scans')
    for items in first[1]:
        for _,url in items:
            if url:
                try:other=canonical(url)
                except (ValueError,TypeError):
                    if _known_unrelated_bare_host(url):continue
                    raise ValueError('source list URL invalid') from None
                if other==target:raise DuplicateSource('source already exists')
    return {'status':'SOURCE_ABSENT_NONATOMIC','count':first[0][0],'pages':first[0][1]}
