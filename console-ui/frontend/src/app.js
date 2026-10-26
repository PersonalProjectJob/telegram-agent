/* Descriptor-composed Console. Public descriptors own the field catalog. */
'use strict';
const schema = window.AgentFleetSchema;
const session = window.AgentFleetState.create();
const state = session.entries;
const descriptors = session.descriptors;
const names = Object.assign(Object.create(null), {overview:'Tổng quan',usage:'Chi phí API',history:'Lịch sử thay đổi'});
const history = [];
let host = {mode:'local',label:'LOCAL',canApply:false};
let loading = true, bootstrapError = '', bootstrapJob = 0, reloadTarget = null;
const main = document.querySelector('main');
let current = location.hash.slice(1), pendingPage = null, previousFocus = null, saveTarget = null, saveBusy = false;
const esc = schema.escape;
const isDirty = session.dirty;
const announce = message => { document.querySelector('#announcement').textContent = message; };
const serviceIds = () => Object.keys(state);
const usageServices = () => serviceIds().filter(id => state[id].available && descriptors[id]?.capabilities.usage);
const fieldsFor = id => descriptors[id]?.fields || [];
const fieldFor = (id, key) => fieldsFor(id).find(field => field.id === key);
const canSave = id => state[id]?.available && descriptors[id]?.capabilities.settings && state[id].canSave === true;
const canApply = id => canSave(id) && host.canApply && state[id].canApply === true;
const keyStatus = id => !session.provider(id)?.requiresKey ? 'Không yêu cầu' : state[id].key ? 'Sẽ thay mới' : state[id].hasKey && state[id].keyScope === session.scope(state[id].draft) ? 'Đã lưu' : 'Chưa có';
const testLabel = status => ({idle:'Chưa test',loading:'Đang test',success:'Đạt',error:'Lỗi',stale:'Cần test lại'}[status]);
const displayValue = (id, key, value) => schema.display(fieldFor(id, key), value);
const label = (id, key) => fieldFor(id, key)?.label || key;
const section = schema.section;
function acceptService(id, raw, resetDraft=false) { return session.accept(id, raw, resetDraft); }
function acceptHistory(items) {
  if(Array.isArray(items))history.splice(0,history.length,...items.map(item=>({...item,time:item.time||new Date(item.timestamp*1000).toISOString(),fields:item.fields||item.message||''})));
}
function renderNavigation() {
  const nav=document.querySelector('nav[aria-label="Điều hướng chính"]');
  nav.innerHTML=`<a href="#overview" data-page="overview"><span class="nav-icon" aria-hidden="true">▦</span>Tổng quan</a><p class="nav-label">Dịch vụ</p>${serviceIds().map(id=>`<a href="#${esc(id)}" data-page="${esc(id)}"><span class="service-monogram" aria-hidden="true">${esc(names[id].slice(0,1))}</span>${esc(names[id])}<span class="nav-indicator" aria-hidden="true"></span></a>`).join('')}<p class="nav-label">Quản lý</p>${usageServices().length?'<a href="#usage" data-page="usage"><span class="nav-icon" aria-hidden="true">$</span>Chi phí API</a>':''}<a href="#history" data-page="history"><span class="nav-icon" aria-hidden="true">↶</span>Lịch sử thay đổi</a>`;
}
async function bootstrap(resetId=null) {
  if(saveBusy||Object.values(state).some(item=>item.applying)){announce('Chờ thao tác lưu hoặc áp dụng hoàn tất trước khi đọc lại.');return;}
  const job=++bootstrapJob;reloadTarget=resetId;loading=true;bootstrapError='';render();
  try {
    const data=await window.AgentFleetAPI.bootstrap();if(job!==bootstrapJob)return;
    if(data.contractVersion!==1)throw new Error('Phiên bản hợp đồng Console chưa được hỗ trợ. Không thể thay đổi cấu hình.');
    const received=Object.entries(data.descriptors||{});if(received.length>16)throw new Error('Danh sách dịch vụ vượt giới hạn hỗ trợ.');
    const valid=Object.create(null);
    for(const [id,spec] of received){if(schema.supported(spec)&&id===spec.id)valid[id]=spec;}
    session.setDescriptors(valid);host=data.host;
    for(const id of Object.keys(names))if(!['overview','usage','history'].includes(id))delete names[id];
    const services=Object.entries(data.services||{});if(services.length>16)throw new Error('Danh sách dịch vụ vượt giới hạn hỗ trợ.');
    for(const id of serviceIds())if(!Object.hasOwn(data.services,id))delete state[id];
    for(const [id,raw] of services){
      if(!/^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/.test(id)||['overview','usage','history'].includes(id))continue;
      names[id]=String(data.descriptors?.[id]?.displayName||raw.displayName||id);
      const service=acceptService(id,raw,!resetId||resetId===id);
      if(!valid[id]){service.available=false;service.reason=raw.reason||'Descriptor hoặc phiên bản schema chưa được hỗ trợ.';}
    }
    acceptHistory(data.history);renderNavigation();
    if(!names[current]||(current==='usage'&&!usageServices().length))current=serviceIds()[0]||'overview';
    document.querySelector('.brand').href='#'+(serviceIds()[0]||'overview');
    document.querySelector('#binding-name').textContent=host.label;
    document.querySelector('#binding-mode').textContent=host.mode==='installed'?'Service trên host':'Cấu hình local';
    document.querySelector('#connection-banner').textContent=host.mode==='installed'?'Đã nối cấu hình service trên host. Lưu và áp dụng riêng từng service.':'Đã nối backend local · cấu hình được lưu trên đĩa. Áp dụng runtime cần chạy console trên host cài service.';
  } catch(error) { if(job!==bootstrapJob)return;bootstrapError=error.message;document.querySelector('#connection-banner').textContent=`Không kết nối được backend: ${error.message}`; }
  loading=false;render();if(current==='usage')loadUsage();
}
function heading(page) {
  const descriptions={overview:'Các service độc lập, một nơi quản lý cấu hình.',usage:'Chi phí gọi API từ tệp tổng hợp runtime.',history:'Theo dõi các lần lưu và áp dụng được backend ghi nhận.'};
  const item=state[page];
  return `<div class="page-heading"><div><h1>${esc(names[page]||page)}</h1><p>${esc(descriptors[page]?.description||descriptions[page]||'')}</p></div><div class="heading-actions"><span class="status">${esc(item?.status?.state||'Backend đã nối')}</span>${item&&item.saved.mode?`<span class="badge">${esc(item.saved.mode)} · đã lưu</span>`:''}</div></div>`;
}
function tabs(id) {
  return `<div class="tabs" role="tablist" aria-label="Nhóm cài đặt">${descriptors[id].groups.map(group=>`<button class="tab" role="tab" id="tab-${esc(group.id)}" aria-controls="settings-panel" aria-selected="${state[id].tab===group.id}" tabindex="${state[id].tab===group.id?0:-1}" data-tab="${esc(group.id)}">${esc(group.label)}</button>`).join('')}</div>`;
}
function credentialFields(id) {
  const item=state[id],provider=session.provider(id);
  if(!provider)return '';
  if(!provider.requiresKey)return '<div class="notice field-wide">Provider local không cần API key. Nội dung được xử lý trên máy chạy model.</div>';
  return `<div class="field field-wide"><label for="api-key">API key</label><div class="input-affix"><input id="api-key" name="apiKey" type="password" value="${esc(item.key)}" placeholder="${item.hasKey&&item.keyScope===session.scope(item.draft)?'Đã có key · để trống để giữ nguyên':'Nhập API key của provider'}" autocomplete="off" spellcheck="false" aria-describedby="help-apiKey"><button class="icon-button" type="button" id="toggle-key" aria-label="Hiện API key">Hiện</button></div><small id="help-apiKey" data-helper="Key lưu riêng tại backend, không trả về trình duyệt hoặc ghi vào lịch sử.">Key lưu riêng tại backend, không trả về trình duyệt hoặc ghi vào lịch sử.</small></div><div class="notice field-wide">Đổi provider hoặc endpoint cần key riêng và test lại. Test chỉ gửi mẫu tổng hợp; runtime chỉ gửi nội dung tác vụ khi bạn bật quyền bên dưới.</div>`;
}
function panel(id) {
  const spec=descriptors[id],item=state[id],connection=fieldFor(id,'baseUrl');
  return schema.renderGroup(spec,item.tab,item.draft,{supplement:(part,phase)=>{
    if(phase==='fields'&&spec.capabilities.probe&&connection?.group===item.tab&&connection.section===part.id)return credentialFields(id);
    if(phase==='after'&&spec.capabilities.probe&&part.id==='probe')return `<div class="test-row"><button class="button" type="button" id="test-connection"${item.test.status==='loading'?' disabled data-state="loading"':''}>${item.test.status==='loading'?'Đang test…':'Test kết nối'}<span aria-hidden="true">↗</span></button><small>Kiểm tra key, model và đầu ra JSON.</small></div><div id="test-result" class="test-result" data-state="${esc(item.test.status)}" role="status" aria-live="polite">${esc(item.test.message)}</div>`;
    return '';
  }});
}
function draftSummary(id,values,summaryIds=false) {
  const provider=session.providerField(id),model=fieldFor(id,'model');
  const chosen=provider?[provider,...(model?[model]:[])]:fieldsFor(id).slice(0,2);
  return chosen.map(field=>`<div><dt>${esc(field.label)}</dt><dd data-summary-field="${esc(field.id)}"${summaryIds&&field===provider?' id="summary-provider"':summaryIds&&field===model?' id="summary-model"':''}>${esc(displayValue(id,field.id,values[field.id]))}</dd></div>`).join('');
}
function rail(id) {
  const item=state[id],others=serviceIds().filter(other=>other!==id).map(other=>names[other]);
  return `<aside class="summary-rail" aria-label="Tóm tắt cấu hình"><section><h2>Trạng thái cấu hình</h2><dl><div><dt>Backend</dt><dd>${esc(host.label)}</dd></div><div><dt>Bản đã lưu</dt><dd id="saved-revision">${esc(item.revision)}</dd></div><div><dt>Bản đã áp dụng</dt><dd id="applied-revision">${esc(item.appliedRevision??'Chưa xác nhận')}</dd></div><div><dt>PID đã xác nhận</dt><dd>${esc(item.status?.mainPid||'—')}</dd></div></dl><p class="muted">${esc(item.status?.message||'')}</p>${item.compatibility?.state!=='local'&&item.compatibility?.reason?`<p class="muted">${esc(item.compatibility.reason)}</p>`:''}</section><section><h2>${session.providerField(id)?'AI của bản nháp':'Bản nháp'}</h2><dl>${draftSummary(id,item.draft,true)}${descriptors[id].capabilities.probe?`<div><dt>Key</dt><dd id="summary-key">${esc(keyStatus(id))}</dd></div><div><dt>Test</dt><dd id="summary-test">${esc(testLabel(item.test.status))}</dd></div>`:''}</dl></section>${others.length?`<div class="rail-note"><strong>Áp dụng riêng service</strong><p>${esc(others.join(', '))} giữ nguyên cấu hình khi bạn lưu trang này.</p></div>`:''}<div class="rail-note"><strong>${host.mode==='installed'?'Runtime trên host':'Lưu trữ local'}</strong><p>${host.mode==='installed'?'Áp dụng chỉ được xác nhận khi runtime khởi động và nhận đúng revision.':'Cấu hình được lưu trên máy chạy terminal. Chạy console với profile installed trên host để áp dụng service thật.'}</p></div></aside>`;
}
function overview() {
  return heading('overview')+`<div class="notice">${esc(host.label)} · ${host.mode==='installed'?'Đã nối cấu hình host':'Lưu cấu hình local'} · chạy bằng <strong>python console-ui/run.py</strong>.</div><div class="overview-list">${serviceIds().map(id=>`<section class="service-block"><div class="service-block-header"><h2>${esc(names[id])}</h2><span class="status">${esc(state[id].status?.state||'unknown')}</span>${state[id].saved.mode?`<span class="badge">${esc(state[id].saved.mode)} · đã lưu</span>`:''}</div><div class="overview-data">${draftSummary(id,state[id].saved).replaceAll("<dt>","<span>").replaceAll("</dt>","</span>").replaceAll("<dd>","<strong>").replaceAll("</dd>","</strong>")}<div><span>PID đã xác nhận</span><strong>${esc(state[id].status?.mainPid||'—')}</strong></div><div><span>Revision runtime</span><strong>${esc(state[id].appliedRevision??'Chưa xác nhận')}</strong></div></div><div><a class="button" href="#${esc(id)}">Mở cấu hình <span aria-hidden="true">→</span></a></div></section>`).join('')}</div>`;
}
function render() {
  document.querySelector('#crumb').textContent=names[current]||'';
  document.querySelectorAll('[data-page]').forEach(link=>{if(link.dataset.page===current)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');});
  if(loading){main.innerHTML='<div class="empty-state" role="status"><h1>Đang đọc cấu hình…</h1><p class="muted">Kết nối backend chạy từ terminal.</p></div>';return;}
  if(bootstrapError){main.innerHTML=`<div class="empty-state" role="alert"><h1>Chưa đọc được cấu hình</h1><p>${esc(bootstrapError)}</p><button class="button" id="retry-bootstrap">Kết nối lại</button></div>`;document.querySelector('#retry-bootstrap').onclick=()=>bootstrap(reloadTarget);return;}
  if(state[current]){
    const id=current,item=state[id];
    if(!item.available||!descriptors[id]?.capabilities.settings){main.innerHTML=heading(id)+`<div class="notice" role="alert">${esc(item.reason||item.compatibility?.reason||item.status?.message||'Cấu hình service chưa khả dụng.')}</div>`;return;}
    main.innerHTML=heading(id)+tabs(id)+`<div class="settings-layout"><form id="settings-form" class="settings-form" novalidate><div id="settings-panel" role="tabpanel" aria-labelledby="tab-${esc(item.tab)}">${panel(id)}</div><div class="action-bar"><div class="dirty-marker" id="dirty-status">${isDirty(id)?'Có thay đổi chưa lưu':'Bản nháp chưa thay đổi'}</div><div class="actions"><button class="button" id="discard" type="button"${isDirty(id)?'':' disabled'}>Huỷ thay đổi</button><button class="button primary" type="submit" id="review-changes"${canSave(id)?'':' disabled'}>Xem thay đổi<span aria-hidden="true">→</span></button></div></div><div class="revision-line"><span id="revision-note">${esc(!canApply(id)?item.compatibility?.reason||'Host hiện tại chưa đủ điều kiện áp dụng runtime.':item.revision!==item.appliedRevision?'Bản đã lưu chưa được runtime xác nhận.':'Runtime đã nhận bản đã lưu.')}</span><button class="button" type="button" id="apply-settings"${!canApply(id)||item.applying||item.revision===item.appliedRevision?' disabled':''}>${item.applying?'Đang áp dụng…':'Áp dụng bản đã lưu'}</button></div><div class="test-result" id="form-error" role="alert"${item.actionError?'':' hidden'}>${esc(item.actionError)}</div><button class="button" type="button" id="reload-service">Bỏ nháp, đọc lại từ backend</button></form>${rail(id)}</div>`;
    wireForm(id);
  }else if(current==='overview')main.innerHTML=overview();
  else if(current==='usage'){main.innerHTML=heading(current)+usageHTML();wireUsage();}
  else main.innerHTML=heading('history')+(history.length?history.map(item=>`<article class="history-row"><div><strong>${esc(item.action)} · ${esc(names[item.service]||item.service)}</strong><span class="badge">${esc(item.revision)}</span></div><time datetime="${esc(item.time)}">${new Date(item.time).toLocaleString('vi-VN',{timeZone:'Asia/Saigon'})}</time><p>${esc(Array.isArray(item.fields)?item.fields.map(key=>label(item.service,key)).join(', '):item.fields)}</p></article>`).join(''):`<div class="empty-state"><h2>Chưa có thay đổi</h2><p class="muted">Các lần lưu và áp dụng sẽ xuất hiện ở đây. API key không được ghi vào lịch sử.</p>${serviceIds().length?`<a class="button" href="#${esc(serviceIds()[0])}">Mở ${esc(names[serviceIds()[0]])} →</a>`:''}</div>`);
}
/* Chi phí API: chỉ đọc tệp tổng hợp mà runtime ghi; console không mở database của runtime. */
const usageRanges = [['today','Hôm nay'],['7d','7 ngày'],['30d','30 ngày']];
const usageStorageKey = 'agentfleet.usageRange';
const usageView = { serviceId:null, status:'idle', data:null, error:'', range:'today', job:0 };
try { const saved=localStorage.getItem(usageStorageKey); if(usageRanges.some(([key])=>key===saved))usageView.range=saved; } catch {}
const numberFormat = new Intl.NumberFormat('vi-VN');
const fmtInt = value => numberFormat.format(Number.isFinite(value)?value:0);
function fmtMoney(value, currency) {
  const amount=Number.isFinite(value)?value:0;
  try { return new Intl.NumberFormat('vi-VN',{style:'currency',currency,minimumFractionDigits:2,maximumFractionDigits:4}).format(amount); }
  catch { return `${amount.toFixed(4)} ${currency}`; }
}
const fmtTime = seconds => new Date(seconds*1000).toLocaleString('vi-VN',{timeZone:'Asia/Saigon'});
const fmtDay = date => `${date.slice(8,10)}/${date.slice(5,7)}`;
function usageNotice() {
  const {status,error,data}=usageView;
  if(status==='error')return `<div class="test-result" data-state="error" role="alert">${esc(error||'Không đọc được dữ liệu chi phí từ backend.')}</div>`;
  if(status==='missing')return '<div class="notice" role="status"><strong>Runtime chưa ghi dữ liệu chi phí.</strong> Tệp tổng hợp sẽ xuất hiện sau khi service gọi API lần đầu. Kiểm tra runtime có ghi dữ liệu chi phí.</div>';
  if(status==='invalid')return '<div class="test-result" data-state="error" role="alert"><strong>Dữ liệu chi phí không hợp lệ hoặc không đọc được.</strong> Console không hiển thị nội dung tệp. Kiểm tra runtime và quyền đọc của tài khoản chạy console.</div>';
  if(status==='stale')return `<div class="test-result" data-state="stale" role="status"><strong>Dữ liệu chi phí đã cũ.</strong> Cập nhật lần cuối ${esc(fmtTime(data.generatedAt))}; runtime có thể đang dừng. Số liệu bên dưới là lần ghi cuối cùng.</div>`;
  return '';
}
function usageChart(daily, currency) {
  const days=daily.slice(-30);
  if(!days.length)return '<p class="muted">Chưa có dữ liệu theo ngày.</p>';
  const max=Math.max(...days.map(day=>day.cost)),label=value=>fmtMoney(value,currency);
  const total=days.reduce((sum,day)=>sum+day.cost,0);
  const bars=days.map(day=>{
    const percent=max>0&&day.cost>0?Math.max(2,day.cost/max*100):0;
    const title=`${day.date}: ${label(day.cost)} · ${fmtInt(day.calls)} lượt gọi · ${fmtInt(day.errors)} lỗi · ${fmtInt(day.fallbacks)} fallback`;
    return `<div class="chart-col" title="${esc(title)}"><div class="chart-bar${percent?'':' is-zero'}" data-height="${percent.toFixed(2)}"></div></div>`;
  }).join('');
  const mid=days[Math.floor((days.length-1)/2)];
  return `<div class="chart" role="img" aria-label="Biểu đồ cột chi phí theo ngày, ${days.length} ngày gần nhất, tổng ${esc(label(total))}"><div class="chart-y" aria-hidden="true"><span>${esc(label(max))}</span><span>${esc(label(max/2))}</span><span>${esc(label(0))}</span></div><div class="chart-plot">${bars}</div><div class="chart-x" aria-hidden="true"><span>${esc(fmtDay(days[0].date))}</span><span>${esc(fmtDay(mid.date))}</span><span>${esc(fmtDay(days[days.length-1].date))}</span></div></div>`+
    `<details class="demo-options"><summary>Xem số liệu từng ngày</summary><div class="table-scroll"><table class="data-table"><thead><tr><th scope="col">Ngày</th><th scope="col">Lượt gọi</th><th scope="col">Lỗi</th><th scope="col">Fallback</th><th scope="col">Chi phí</th></tr></thead><tbody>${[...days].reverse().map(day=>`<tr><th scope="row">${esc(day.date)}</th><td>${fmtInt(day.calls)}</td><td>${fmtInt(day.errors)}</td><td>${fmtInt(day.fallbacks)}</td><td>${esc(label(day.cost))}</td></tr>`).join('')}</tbody></table></div></details>`;
}
function usageHTML() {
  const {status,data,range}=usageView,busy=status==='loading',window_=data?.windows?.[range],currency=data?.currency||'USD';
  const picker=usageServices().length>1?`<div class="field"><label for="usage-service">Dịch vụ</label><select id="usage-service">${usageServices().map(id=>`<option value="${esc(id)}"${id===usageView.serviceId?' selected':''}>${esc(names[id])}</option>`).join('')}</select></div>`:'';
  const toolbar=picker+`<div class="usage-toolbar"><div class="segmented" role="group" aria-label="Khoảng thời gian">${usageRanges.map(([key,title])=>`<button type="button" data-range="${key}" aria-pressed="${range===key}">${title}</button>`).join('')}</div><div class="usage-meta">${data?`<small>Cập nhật lúc ${esc(fmtTime(data.generatedAt))}</small>`:''}<button class="button" type="button" id="usage-refresh" ${busy?'disabled data-state="loading"':''}>${busy?'Đang làm mới…':'Làm mới'}</button></div></div>`;
  if(busy&&!data)return toolbar+'<div class="empty-state" role="status"><h2>Đang đọc dữ liệu chi phí…</h2></div>';
  const notice=usageNotice();
  if(!window_)return toolbar+notice;
  const metrics=[
    ['Số lần gọi',fmtInt(window_.calls),`${fmtInt(window_.ok)} thành công`],
    ['Token vào',fmtInt(window_.promptTokens),`${fmtInt(window_.cacheHitTokens)} cache hit`],
    ['Token ra',fmtInt(window_.completionTokens),''],
    ['Chi phí',fmtMoney(window_.cost,currency),currency],
    ['Số lần lỗi',fmtInt(window_.errors),''],
    ['Fallback sang local',fmtInt(window_.fallbacks),'']
  ].map(([title,value,note])=>`<div class="usage-metric"><span>${esc(title)}</span><strong>${esc(value)}</strong><small>${esc(note)||'&nbsp;'}</small></div>`).join('');
  const rows=window_.byModel.length?`<div class="table-scroll"><table class="data-table data-table-models"><thead><tr><th scope="col">Provider</th><th scope="col">Model</th><th scope="col">Lượt gọi</th><th scope="col">Lỗi</th><th scope="col">Fallback</th><th scope="col">Token vào</th><th scope="col">Cache hit</th><th scope="col">Token ra</th><th scope="col">Chi phí</th></tr></thead><tbody>${window_.byModel.map(row=>`<tr><th scope="row">${esc(row.provider)||'—'}</th><td>${esc(row.model)||'—'}</td><td>${fmtInt(row.calls)}</td><td>${fmtInt(row.errors)}</td><td>${fmtInt(row.fallbacks)}</td><td>${fmtInt(row.promptTokens)}</td><td>${fmtInt(row.cacheHitTokens)}</td><td>${fmtInt(row.completionTokens)}</td><td>${esc(fmtMoney(row.cost,currency))}</td></tr>`).join('')}</tbody></table></div>`:'<p class="muted">Không có lượt gọi trong khoảng này.</p>';
  return toolbar+notice+`<section class="section"><div class="usage-grid">${metrics}</div></section>`+
    section('Theo provider và model','Tách theo nơi xử lý trong khoảng đã chọn.',rows)+
    section('Chi phí theo ngày','30 ngày gần nhất, không phụ thuộc khoảng đã chọn. Rê chuột lên cột để xem chi tiết.',usageChart(data.daily,currency));
}
async function loadUsage(focusRefresh=false) {
  const selected=usageServices(); if(!selected.length)return; if(!selected.includes(usageView.serviceId))usageView.serviceId=selected[0]; const job=++usageView.job;usageView.status='loading';usageView.error='';
  if(current==='usage'&&!loading&&!bootstrapError)render();
  try { const result=await window.AgentFleetAPI.usage(usageView.serviceId);if(job!==usageView.job)return;
    if(!['ok','missing','stale','invalid'].includes(result.status))throw new Error('Phản hồi chi phí không hợp lệ.');
    usageView.status=result.status;usageView.data=result.usage; }
  catch(error) { if(job!==usageView.job)return;usageView.status='error';usageView.data=null;usageView.error=error.message; }
  if(current==='usage'&&!loading&&!bootstrapError){render();if(focusRefresh)document.querySelector('#usage-refresh')?.focus({preventScroll:true});announce(usageView.status==='ok'?'Đã cập nhật dữ liệu chi phí.':'Trạng thái dữ liệu chi phí: '+usageView.status+'.');}
}
function wireUsage() {
  // CSP forbids inline style attributes; CSSOM assignment is allowed.
  document.querySelectorAll('.chart-bar[data-height]').forEach(bar=>{bar.style.height=`${bar.dataset.height}%`;});
  document.querySelector('#usage-refresh').onclick=()=>loadUsage(true); document.querySelector('#usage-service')?.addEventListener('change',event=>{usageView.serviceId=event.target.value;usageView.data=null;loadUsage();});
  document.querySelectorAll('[data-range]').forEach(button=>button.onclick=()=>{usageView.range=button.dataset.range;try{localStorage.setItem(usageStorageKey,usageView.range);}catch{}render();document.querySelector(`[data-range="${usageView.range}"]`)?.focus({preventScroll:true});});
}

function setTest(id,status,message) {const item=state[id];item.test={status,message};if(current!==id)return;const result=document.querySelector('#test-result');if(result){result.dataset.state=status;result.textContent=message;}updateSummary(id);}
function invalidate(id) {if(state[id].test.status!=='idle')setTest(id,'stale','Bản nháp đã đổi. Test lại trước khi áp dụng.');}
function updateSummary(id) {
  if(current!==id||loading||bootstrapError)return;const item=state[id],provider=session.providerField(id);
  const values={'summary-provider':provider?displayValue(id,provider.id,item.draft[provider.id]):'','summary-model':item.draft.model||'Chưa chọn','summary-key':keyStatus(id),'summary-test':testLabel(item.test.status),'dirty-status':isDirty(id)?'Có thay đổi chưa lưu':'Bản nháp chưa thay đổi'};
  for(const [key,value] of Object.entries(values)){const element=document.getElementById(key);if(element)element.textContent=value;}
  document.querySelectorAll('[data-summary-field]').forEach(element=>{const key=element.dataset.summaryField;element.textContent=displayValue(id,key,item.draft[key]);});
  const discard=document.querySelector('#discard');if(discard)discard.disabled=!isDirty(id);
}
function showError(key,message) {const input=document.getElementsByName(key)[0],help=document.getElementById(`help-${key}`);if(input){input.setAttribute('aria-invalid','true');if(help)help.textContent=message;}}
function clearErrors() {document.querySelectorAll('[aria-invalid]').forEach(element=>element.removeAttribute('aria-invalid'));document.querySelectorAll('[data-helper]').forEach(element=>element.textContent=element.dataset.helper);}
function focusErrors(id,errors) {
  if(current!==id||!state[id])return;
  const field=fieldFor(id,errors[0][0])||(errors[0][0]==='apiKey'?fieldFor(id,'baseUrl'):null);if(field&&field.group!==state[id].tab){state[id].tab=field.group;render();}
  errors.forEach(([key,message])=>showError(key,message));document.getElementsByName(errors[0][0])[0]?.focus();
  const error=document.querySelector('#form-error');if(error){error.textContent=errors.map(([key,message])=>`${label(id,key)}: ${message}`).join(' ');error.hidden=false;}
  announce('Cần sửa các trường chưa hợp lệ.');
}
function validate(id) {
  clearErrors();const item=state[id],errors=schema.validate(descriptors[id],item.draft);
  if(session.provider(id)?.requiresKey&&!item.key&&!(item.hasKey&&item.keyScope===session.scope(item.draft)))errors.push(['apiKey','Nhập API key cho provider và endpoint này.']);
  if(errors.length){focusErrors(id,errors);return false;}return true;
}
function refreshConditions(id) {
  const spec=descriptors[id],values=state[id].draft;
  for(const field of spec.fields){const input=document.getElementById(`field-${field.id}`);if(!input)continue;input.disabled=schema.disabled(spec,field,values);}
}
function wireForm(id) {
  const form=document.querySelector('#settings-form');
  form.addEventListener('input',event=>{
    const element=event.target,key=element.name,item=state[id],field=fieldFor(id,key);
    if(key==='apiKey'){item.key=element.value;item.keyVersion++;invalidate(id);updateSummary(id);return;}
    if(!field||field.widget==='provider'||element.disabled)return;
    const before=specVisibility(id);item.draft[key]=schema.read(field,element);
    if(key==='baseUrl'){session.clearKey(id);if('cloudConsent' in item.draft)item.draft.cloudConsent=false;const consent=document.getElementsByName('cloudConsent')[0];if(consent)consent.checked=false;const keyInput=document.querySelector('#api-key');if(keyInput)keyInput.value='';}
    if(descriptors[id].capabilities.probe)invalidate(id);
    const position=element.selectionStart;
    if(before!==specVisibility(id)){render();const replacement=document.getElementsByName(key)[0];replacement?.focus({preventScroll:true});if(typeof position==='number'&&replacement?.setSelectionRange)replacement.setSelectionRange(position,position);}
    else refreshConditions(id);
    if(element.getAttribute('aria-invalid')==='true'){element.removeAttribute('aria-invalid');const help=document.getElementById(`help-${key}`);if(help)help.textContent=help.dataset.helper||'';}
    updateSummary(id);
  });
  form.addEventListener('change',event=>{const field=fieldFor(id,event.target.name);if(field?.widget==='provider'&&!event.target.disabled){session.switchProvider(id,event.target.value);invalidate(id);render();[...document.getElementsByName(field.id)].find(element=>element.value===event.target.value)?.focus({preventScroll:true});}});
  form.addEventListener('submit',event=>{event.preventDefault();if(canSave(id)&&validate(id))review(id);});
  document.querySelector('#discard').onclick=()=>{session.discard(id);render();announce('Đã huỷ thay đổi chưa lưu.');};
  document.querySelector('#apply-settings').onclick=()=>applySettings(id);
  document.querySelector('#reload-service').onclick=()=>bootstrap(id);
  document.querySelector('#toggle-key')?.addEventListener('click',event=>{const input=document.querySelector('#api-key'),show=input.type==='password';input.type=show?'text':'password';event.currentTarget.textContent=show?'Ẩn':'Hiện';event.currentTarget.setAttribute('aria-label',`${show?'Ẩn':'Hiện'} API key`);});
  document.querySelector('#test-connection')?.addEventListener('click',()=>testConnection(id));
  document.querySelectorAll('[data-tab]').forEach(button=>{button.onclick=()=>{state[id].tab=button.dataset.tab;render();document.getElementById(`tab-${state[id].tab}`).focus({preventScroll:true});};button.onkeydown=event=>{const buttons=[...document.querySelectorAll('[data-tab]')],index=buttons.indexOf(button);let next;if(event.key==='ArrowRight')next=(index+1)%buttons.length;else if(event.key==='ArrowLeft')next=(index-1+buttons.length)%buttons.length;else if(event.key==='Home')next=0;else if(event.key==='End')next=buttons.length-1;if(next!==undefined){event.preventDefault();buttons[next].click();}};});
}
function specVisibility(id) {return JSON.stringify(fieldsFor(id).map(field=>schema.visible(field,state[id].draft)));}
async function testConnection(id) {
  if(!state[id].available||!descriptors[id]?.capabilities.probe||!validate(id))return;
  const item=state[id],settings=structuredClone(item.draft),key=item.key,signature=session.signature(id),job=++item.testJob;
  setTest(id,'loading','Đang gửi mẫu kiểm tra đến provider…');const button=document.querySelector('#test-connection');button.disabled=true;button.dataset.state='loading';button.textContent='Đang test…';
  let result,fieldError;try{result=await window.AgentFleetAPI.test(id,settings,key);}catch(error){result={status:'error',message:error.message};if(error.field)fieldError=[error.field,error.message];}
  if(!state[id]||job!==state[id].testJob)return;
  if(signature!==session.signature(id)){setTest(id,'stale','Bản nháp hoặc key đã đổi trong lúc test. Test lại cấu hình hiện tại.');if(current===id)render();return;}
  if(fieldError)focusErrors(id,[fieldError]);
  setTest(id,result.status==='success'?'success':'error',`${result.message}${Number.isFinite(result.elapsedMs)?` · ${result.elapsedMs} ms`:''}`);
  if(current===id){const latest=document.querySelector('#test-connection');if(latest){latest.disabled=false;latest.dataset.state=state[id].test.status;latest.innerHTML='Test lại <span aria-hidden="true">↗</span>';}announce(result.message);}
}
async function applySettings(id) {
  if(saveBusy||Object.values(state).some(item=>item.applying)||!canApply(id)){announce('Chờ thao tác hiện tại hoàn tất hoặc kiểm tra điều kiện host.');return;}
  const item=state[id];item.applying=true;item.actionError='';render();
  try{const result=await window.AgentFleetAPI.apply(id,item.revision);acceptService(id,result.service);acceptHistory(result.history);announce(`Runtime ${names[id]} đã nhận cấu hình.`);}catch(error){state[id].actionError=error.message;announce(error.message);}finally{state[id].applying=false;render();}
}
function review(id) {
  if(saveBusy||Object.values(state).some(item=>item.applying)||!canSave(id)){announce('Chờ thao tác lưu hoặc áp dụng hiện tại hoàn tất.');return;}
  const item=state[id],changes=fieldsFor(id).filter(field=>!session.same(item.draft[field.id],item.saved[field.id]));saveTarget=id;
  document.querySelector('#review-subtitle').textContent=`${names[id]} · Lưu tại backend. Áp dụng runtime là bước riêng.`;
  document.querySelector('#review-body').innerHTML=changes.map(field=>`<div class="diff-row"><strong>${esc(field.label)}</strong><div><del>${esc(displayValue(id,field.id,item.saved[field.id]))}</del><span aria-hidden="true">→</span><ins>${esc(displayValue(id,field.id,item.draft[field.id]))}</ins></div></div>`).join('')+(item.key?'<div class="diff-row"><strong>API key</strong><div>Thay key riêng của service (giá trị được ẩn)</div></div>':'')+(!changes.length&&!item.key?'<p class="notice">Chưa có thay đổi để lưu.</p>':'');
  document.querySelector('#save-error').hidden=true;document.querySelector('#confirm-save').textContent='Lưu cấu hình';document.querySelector('#confirm-save').disabled=!changes.length&&!item.key;openDialog('review-dialog');
}
function openDialog(id){previousFocus=document.activeElement;document.getElementById(id).showModal();}
function closeDialog(dialog){dialog.close();}
document.querySelectorAll('dialog').forEach(dialog=>{dialog.addEventListener('click',event=>{if(event.target===dialog){const rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)closeDialog(dialog);}});dialog.addEventListener('close',()=>{previousFocus?.isConnected&&previousFocus.focus({preventScroll:true});});dialog.querySelectorAll('[data-close]').forEach(button=>button.onclick=()=>closeDialog(dialog));});
document.querySelector('#confirm-save').onclick=async()=>{
  if(saveBusy||!canSave(saveTarget))return;saveBusy=true;
  const id=saveTarget,item=state[id],snapshot=structuredClone(item.draft),key=item.key,keyVersion=item.keyVersion,button=document.querySelector('#confirm-save');
  button.disabled=true;button.textContent='Đang lưu…';document.querySelector('#save-error').hidden=true;
  try{
    const result=await window.AgentFleetAPI.save(id,snapshot,key,item.revision);
    const unchanged=session.same(state[id].draft,snapshot),sameKey=state[id].keyVersion===keyVersion;
    acceptService(id,result.service);acceptHistory(result.history);
    if(unchanged&&sameKey)state[id].draft=structuredClone(result.service.settings);
    if(sameKey){session.clearKey(id);invalidate(id);}
    if(document.querySelector('#review-dialog').open)closeDialog(document.querySelector('#review-dialog'));render();announce(`Đã lưu ${names[id]}. Runtime chưa được áp dụng.`);
  }catch(error){
    if(!state[id]||!session.same(state[id].draft,snapshot)||state[id].keyVersion!==keyVersion)return;
    const text=error.status===409?`${error.message} Đóng hộp thoại rồi dùng “Bỏ nháp, đọc lại từ backend” để tải bản mới.`:error.message;
    state[id].actionError=text;
    if(current===id){
      if(document.querySelector('#review-dialog').open){const message=document.querySelector('#save-error');message.textContent=text;message.hidden=false;}
      if(error.field)focusErrors(id,[[error.field,error.message]]);
      announce(text);
    }
  }
  finally{saveBusy=false;button.disabled=false;button.textContent='Lưu cấu hình';}
};
function navigate(page){current=page;historyReplace(page);document.querySelector('#sidebar').classList.remove('is-open');document.querySelector('#menu-toggle').setAttribute('aria-expanded','false');render();if(page==='usage')loadUsage();}
function historyReplace(page){window.history.replaceState(null,'',`#${page}`);}
function requestNavigation(page){if(!names[page]||page===current||(page==='usage'&&!usageServices().length))return;if(state[current]&&isDirty(current)){pendingPage=page;openDialog('leave-dialog');}else navigate(page);}
document.addEventListener('click',event=>{const link=event.target.closest('a[href^="#"]');if(link&&link.getAttribute('href')!=='#main'){event.preventDefault();requestNavigation(link.hash.slice(1));}});
window.addEventListener('hashchange',()=>{const page=location.hash.slice(1);historyReplace(current);requestNavigation(page);});
document.querySelector('#confirm-leave').onclick=()=>{closeDialog(document.querySelector('#leave-dialog'));navigate(pendingPage);};
document.querySelector('#menu-toggle').onclick=event=>{const open=document.querySelector('#sidebar').classList.toggle('is-open');event.currentTarget.setAttribute('aria-expanded',String(open));event.currentTarget.setAttribute('aria-label',open?'Đóng điều hướng':'Mở điều hướng');};
const searchInput=document.querySelector('#search-input');
function renderSearch(){const query=searchInput.value.toLocaleLowerCase('vi');const entries=Object.entries(names).filter(([id,title])=>(id!=='usage'||usageServices().length)&&title.toLocaleLowerCase('vi').includes(query));document.querySelector('#search-results').innerHTML=entries.length?entries.map(([id,title])=>`<button data-destination="${esc(id)}">${esc(title)}</button>`).join(''):'<p class="muted">Không có màn hình khớp. Thử tên dịch vụ.</p>';document.querySelectorAll('[data-destination]').forEach(button=>button.onclick=()=>{closeDialog(document.querySelector('#search-dialog'));requestNavigation(button.dataset.destination);});}
function openSearch(){searchInput.value='';renderSearch();openDialog('search-dialog');searchInput.focus();}
document.querySelector('#open-search').onclick=openSearch;searchInput.oninput=renderSearch;
document.querySelector('#search-dialog').addEventListener('keydown',event=>{const buttons=[...document.querySelectorAll('[data-destination]')],index=buttons.indexOf(document.activeElement);if(event.key==='ArrowDown'){event.preventDefault();buttons[(index+1)%buttons.length]?.focus();}else if(event.key==='ArrowUp'){event.preventDefault();buttons[(index-1+buttons.length)%buttons.length]?.focus();}else if(event.key==='Enter'&&document.activeElement===searchInput){event.preventDefault();buttons[0]?.click();}});
document.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'){event.preventDefault();if(!document.querySelector('dialog[open]'))openSearch();}if(event.key==='Escape'){document.querySelector('#sidebar').classList.remove('is-open');document.querySelector('#menu-toggle').setAttribute('aria-expanded','false');}});
window.addEventListener('beforeunload',event=>{if(serviceIds().some(isDirty)){event.preventDefault();event.returnValue='';}});
bootstrap();
