"""Offline scheduling primitive; a producer must actually enforce call deadlines."""
import time
import hashlib
import math

class BudgetError(ValueError): pass
class GenerationStopped(ValueError):
    """The producer confirms generation and all children have terminated."""
    pass

def valid_result(item,result):
    if not isinstance(result,dict) or set(result)!={"ordinal","image_bytes","evidence"}:return False
    data=result["image_bytes"];e=result["evidence"]
    return (result["ordinal"]==item["ordinal"] and isinstance(data,bytes) and bool(data)
            and isinstance(e,dict) and e.get("ordinal")==item["ordinal"]
            and e.get("image_sha256")==hashlib.sha256(data).hexdigest())

def socket_budget(seconds):
    """Floor IPC seconds so request never exceeds the caller's remaining budget."""
    if type(seconds) not in (int,float) or not math.isfinite(seconds) or seconds<=0:return None
    bounded=math.floor(min(seconds,65))
    return bounded if bounded>=1 else None

def collect(plan, generate, *, total_seconds=360, call_seconds=65, clock=time.monotonic):
    if not isinstance(plan,list) or not 3<=len(plan)<=5:raise BudgetError('plan must contain 3–5 images')
    if type(total_seconds) not in (int,float) or not 0<total_seconds<=360 or type(call_seconds) not in (int,float) or not 0<call_seconds<=65:raise BudgetError('invalid budget')
    start=clock();deadline=start+total_seconds;success=[];omitted=[]
    for item in plan:
        left=deadline-clock()
        if left<=0:
            omitted.append({'ordinal':item['ordinal'],'reason':'budget_exhausted'});continue
        # Generator contract: enforce the supplied timeout and stop its work before returning.
        try:result=generate(item,min(call_seconds,left))
        except TimeoutError:
            omitted.append({'ordinal':item['ordinal'],'reason':'timeout'})
            # A timeout may leave remote inference alive. Do not start another request.
            omitted.extend({'ordinal':tail['ordinal'],'reason':'not_started_after_timeout'} for tail in plan[len(success)+len(omitted):])
            break
        except GenerationStopped:
            omitted.append({'ordinal':item['ordinal'],'reason':'generation_failed'});continue
        except Exception:
            omitted.append({'ordinal':item['ordinal'],'reason':'transport_uncertain'})
            omitted.extend({'ordinal':tail['ordinal'],'reason':'not_started_after_uncertain_failure'} for tail in plan[len(success)+len(omitted):])
            break
        if not valid_result(item,result):
            omitted.append({'ordinal':item['ordinal'],'reason':'invalid_result'})
            omitted.extend({'ordinal':tail['ordinal'],'reason':'not_started_after_invalid_result'} for tail in plan[len(success)+len(omitted):])
            break
        if clock()>deadline:
            omitted.append({'ordinal':item['ordinal'],'reason':'late_result'})
            omitted.extend({'ordinal':tail['ordinal'],'reason':'not_started_after_timeout'} for tail in plan[len(success)+len(omitted):])
            break
        success.append(result)
    return {'images':success,'omitted':omitted,'requested':len(plan),'elapsed_seconds':max(0,clock()-start)}
