/* Hallmark · Workbench / Cobalt. Source-bundled console, backed by the local API. */
'use strict';

const providers = {
  ollama: { name: 'Ollama', note: 'Local', url: 'http://127.0.0.1:11434', model: 'qwen3.5:9b' },
  anthropic: { name: 'Claude', note: 'Anthropic API', url: 'https://api.anthropic.com', model: '' },
  openai: { name: 'OpenAI', note: 'OpenAI API', url: 'https://api.openai.com/v1', model: '' },
  deepseek: { name: 'DeepSeek', note: 'DeepSeek API', url: 'https://api.deepseek.com', model: '' },
  compatible: { name: 'Custom API', note: 'OpenAI-compatible', url: '', model: '' }
};
const names = { triage: 'Telegram Triage', nailhub: 'NailHub Worker', overview: 'Tổng quan', history: 'Lịch sử thay đổi' };
const state = {};
const history = [];
let host = {mode:'local',label:'LOCAL',canApply:false};
let loading = true;
let bootstrapError = '';
let bootstrapJob = 0;
let reloadTarget = null;
const main = document.querySelector('main');
let current = names[location.hash.slice(1)] ? location.hash.slice(1) : 'triage';
let pendingPage = null;
let previousFocus = null;
let saveTarget = null;
let saveBusy = false;
const testJobs = { triage: 0, nailhub: 0 };
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const announce = (message) => { document.querySelector('#announcement').textContent = message; };
const isDirty = (id) => JSON.stringify(state[id].draft) !== JSON.stringify(state[id].saved) || Boolean(state[id].key);
const scope = (data) => `${data.provider}|${data.baseUrl}`;
const labels = { provider:'Provider',model:'Model ID',baseUrl:'Base URL',timeout:'Thời gian chờ (giây)',maxOutput:'Giới hạn output (token)',think:'Suy luận',mode:'Chế độ',confidence:'Ngưỡng tin cậy',issueLimit:'Issue mỗi giờ',window:'Thời gian gom tin (phút)',maxCase:'Tin mỗi case',maxWindow:'Tin mỗi cửa sổ',allGroups:'Nghe tất cả nhóm',chatIds:'Nhóm Telegram',ignored:'Lệnh bỏ qua',proxyHost:'Proxy host',proxyPort:'Proxy port',maxMB:'Dung lượng file (MiB)',maxImages:'Ảnh mỗi case',maxDocs:'Tài liệu mỗi case',maxUploads:'Upload mỗi giờ',interval:'Chu kỳ quét (giây)',batch:'Bài mỗi lượt',pages:'Số trang tối đa',headless:'Browser headless',rangePolicy:'Quy tắc khoảng giá',salaryRange:'Hiển thị khoảng giá',salaryPlaces:'Số chữ số thập phân' };
labels.cloudConsent = 'Cho phép gửi nội dung đến cloud';
const aiKeys = new Set(['provider','model','baseUrl','timeout','maxOutput','think','cloudConsent']);
function acceptService(id, raw, resetDraft=false) {
  const existing=state[id];
  state[id]={...existing,saved:structuredClone(raw.settings),revision:raw.revision,
    appliedRevision:raw.appliedRevision,hasKey:raw.hasKey,keyScope:raw.keyScope,
    status:raw.status||{state:'unknown',message:'Chưa có trạng thái runtime.'},canApply:raw.canApply,
    draft:resetDraft||!existing?structuredClone(raw.settings):existing.draft,
    key:resetDraft||!existing?'':existing.key,keyVersion:(existing?.keyVersion||0)+(resetDraft?1:0),
    test:resetDraft?{status:'idle',message:'Đã đọc cấu hình từ backend. Cần test lại.'}:existing?.test||{status:'idle',message:'Chưa kiểm tra bản nháp.'},tab:existing?.tab||'ai',
    applying:existing?.applying||false,actionError:''};
}
function acceptHistory(items) {
  if(Array.isArray(items))history.splice(0,history.length,...items.map(item=>({...item,
    time:item.time||new Date(item.timestamp*1000).toISOString(),fields:item.fields||item.message||''})));
}
async function bootstrap(resetId=null) {
  if(saveBusy||Object.values(state).some(s=>s.applying)){announce('Chờ thao tác lưu hoặc áp dụng hoàn tất trước khi đọc lại.');return;}
  const job=++bootstrapJob;reloadTarget=resetId;
  if(resetId)testJobs[resetId]++;
  loading=true;bootstrapError='';render();
  try {
    const data=await window.AgentFleetAPI.bootstrap();if(job!==bootstrapJob)return;host=data.host;
    for(const id of resetId?[resetId]:['triage','nailhub'])acceptService(id,data.services[id],true);
    acceptHistory(data.history);
    document.querySelector('#binding-name').textContent=host.label;
    document.querySelector('#binding-mode').textContent=host.mode==='installed'?'Service trên host':'Cấu hình local';
    document.querySelector('#connection-banner').textContent=host.mode==='installed'?'Đã nối cấu hình service trên host. Lưu và áp dụng riêng từng service.':'Đã nối backend local · cấu hình được lưu trên đĩa. Áp dụng runtime cần chạy console trên host cài hai service.';
  } catch(error) { if(job!==bootstrapJob)return;bootstrapError=error.message;document.querySelector('#connection-banner').textContent=`Không kết nối được backend: ${error.message}`; }
  loading=false;render();
}
function field(key, value, helper='', options={}) {
  const id = `field-${key}`;
  const input = options.choices ? `<select id="${id}" name="${key}">${options.choices.map(([v,n])=>`<option value="${esc(v)}" ${v===value?'selected':''}>${esc(n)}</option>`).join('')}</select>` : options.multiline ? `<textarea id="${id}" name="${key}" spellcheck="false" ${options.readonly?'readonly':''}>${esc(value)}</textarea>` : `<input id="${id}" name="${key}" type="${options.type || 'text'}" value="${esc(value)}" ${options.min!==undefined?`min="${options.min}"`:''} ${options.max!==undefined?`max="${options.max}"`:''} ${options.step?`step="${options.step}"`:''} ${options.placeholder?`placeholder="${esc(options.placeholder)}"`:''} ${options.readonly?'readonly':''} autocomplete="off" spellcheck="false">`;
  return `<div class="field ${options.wide?'field-wide':''}"><label for="${id}">${esc(labels[key]||({protectedOwner:'Owner binding',protectedContract:'Contract'}[key])||key)}</label>${input.replace('name=',`aria-describedby="help-${key}" name=`)}<small id="help-${key}" data-helper="${esc(helper)}">${esc(helper) || '&nbsp;'}</small></div>`;
}
const number = (key,data,helper,min,max,step) => field(key,data[key],helper,{type:'number',min,max,step});
const toggle = (key,value,title,help,disabled=false) => `<div class="toggle-row"><div><label class="field-label" for="field-${key}">${esc(title)}</label><p id="help-${key}">${esc(help)}</p></div><label class="switch-label"><input id="field-${key}" name="${key}" type="checkbox" class="switch" ${value?'checked':''} ${disabled?'disabled':''} aria-describedby="help-${key}"></label></div>`;
const section = (title,subtitle,body) => `<section class="section"><div class="section-heading"><h2>${title}</h2><p>${subtitle}</p></div>${body}</section>`;
function heading(page) {
  const descriptions = { triage:'Theo dõi tin nhắn, phân loại nội dung và tạo GitHub issue.', nailhub:'Cấu hình chu kỳ quét, xử lý và kiểm tra bài đăng NailHub.', overview:'Hai service độc lập, một nơi quản lý cấu hình.', history:'Theo dõi các lần lưu và áp dụng được backend ghi nhận.' };
  return `<div class="page-heading"><div><h1>${names[page]}</h1><p>${descriptions[page]}</p></div><div class="heading-actions"><span class="status">${esc(state[page]?.status.state||'Backend đã nối')}</span>${state[page]?`<span class="badge">${esc(state[page].saved.mode)} · đã lưu</span>`:''}</div></div>`;
}
function tabs(id) {
  const list = id==='triage' ? [['ai','AI & API'],['operation','Vận hành'],['telegram','Telegram'],['aggregation','Gom tin'],['files','Tệp đính kèm']] : [['ai','AI & API'],['operation','Vận hành'],['rules','Quy tắc'],['connection','Kết nối']];
  return `<div class="tabs" role="tablist" aria-label="Nhóm cài đặt">${list.map(([key,title])=>`<button class="tab" role="tab" id="tab-${key}" aria-controls="settings-panel" aria-selected="${state[id].tab===key}" tabindex="${state[id].tab===key?0:-1}" data-tab="${key}">${title}</button>`).join('')}</div>`;
}
function aiForm(id) {
  const s=state[id], d=s.draft;
  return section('Model xử lý', 'Chọn nguồn AI cho service này. Cấu hình của service còn lại được giữ riêng.', `<fieldset class="provider-grid"><legend class="sr-only">Provider AI</legend>${Object.entries(providers).map(([key,p])=>`<label class="provider-choice"><input type="radio" name="provider" value="${key}" ${d.provider===key?'checked':''}><span><strong>${p.name}</strong><small>${p.note}</small></span></label>`).join('')}</fieldset>`)+
  section('Thông tin kết nối', 'Thử bản nháp trước khi lưu. Mỗi service dùng API key riêng.', `<div class="field-grid">${field('model',d.model,'Nhập model ID được cấp cho tài khoản của bạn.',{wide:true,placeholder:d.provider==='ollama'?'qwen3.5:9b':'Model ID từ nhà cung cấp'})}${field('baseUrl',d.baseUrl,d.provider==='compatible'?'Base URL HTTPS của endpoint tương thích OpenAI.':'Endpoint theo provider; không kèm API key trong URL.',{wide:true,type:'url',readonly:!['compatible','ollama'].includes(d.provider)})}${d.provider==='ollama'?'<div class="notice field-wide">Ollama local không cần API key. Nội dung được xử lý trên máy chạy model.</div>':`<div class="field field-wide"><label for="api-key">API key</label><div class="input-affix"><input id="api-key" type="password" name="apiKey" value="${esc(s.key)}" placeholder="${s.hasKey&&s.keyScope===scope(d)?'Đã có key · để trống để giữ nguyên':'Nhập API key của provider'}" autocomplete="off" spellcheck="false" aria-describedby="help-apiKey"><button type="button" class="icon-button" id="toggle-key" aria-label="Hiện API key">Hiện</button></div><small id="help-apiKey" data-helper="Key lưu riêng tại backend, không trả về trình duyệt hoặc ghi vào lịch sử.">Key lưu riêng tại backend, không trả về trình duyệt hoặc ghi vào lịch sử.</small></div><div class="notice field-wide">Đổi provider hoặc endpoint cần key riêng và test lại. Test chỉ gửi mẫu tổng hợp; runtime chỉ gửi nội dung tác vụ khi bạn bật quyền bên dưới.</div>`}</div>${d.provider!=='ollama'?toggle('cloudConsent',d.cloudConsent,'Cho phép gửi nội dung tác vụ đến cloud','Cho phép service này gửi nội dung đến provider đã chọn. Tắt để runtime dừng xử lý bằng cloud.'):''}`)+
  section('Giới hạn xử lý', 'Cloud dùng nội dung dạng text và đầu ra JSON; kiểm tra nghiệp vụ vẫn do runtime thực hiện.', `<div class="field-grid">${number('timeout',d,'Giới hạn thời gian cho một lần gọi.',1,1800)}${number('maxOutput',d,'Giới hạn số token trả về.',256,8192)}</div>${toggle('think',d.think,'Suy luận (thinking)',id==='nailhub'?'NailHub giữ thinking tắt theo policy hiện tại.':'Adapter hiện chỉ bật thinking cho Ollama.',d.provider!=='ollama'||id==='nailhub')}`)+
  section('Kiểm tra kết nối', 'Gửi một mẫu JSON tổng hợp đến endpoint. Cloud provider có thể tính phí cho lượt test.', `<div class="test-row"><button class="button" type="button" id="test-connection" ${s.test.status==='loading'?'disabled data-state="loading"':''}>${s.test.status==='loading'?'Đang test…':'Test kết nối'}<span aria-hidden="true">↗</span></button><small>Kiểm tra key, model và đầu ra JSON.</small></div><div id="test-result" class="test-result" data-state="${s.test.status}" role="status" aria-live="polite">${esc(s.test.message)}</div>`);
}
function panel(id) {
  const d=state[id].draft, tab=state[id].tab;
  if(tab==='ai') return aiForm(id);
  if(tab==='operation') return section('Chế độ hoạt động','Thay đổi mode chỉ có hiệu lực khi áp dụng cho service.', `<div class="field-grid">${field('mode',d.mode,'Live có thể tạo issue hoặc thay đổi bài thật.',{choices:id==='triage'?[['dry-run','Dry run — không tạo issue'],['live','Live — tạo GitHub issue']]:[['preview','Preview — chỉ xem đề xuất'],['live','Live — cập nhật bài']]})}${id==='triage'?number('issueLimit',d,'Giới hạn issue trong một giờ.',1):number('interval',d,'Thời gian giữa hai lượt quét.',10)}</div>`)+(id==='triage'?section('Độ tin cậy','Giữ nội dung chưa chắc chắn để người kiểm tra.',`<div class="field-grid">${number('confidence',d,'Giá trị từ 0 đến 1.',0,1,'0.05')}</div>`):section('Khối lượng mỗi lượt','Giới hạn phạm vi xử lý để dễ theo dõi.',`<div class="field-grid">${number('batch',d,'Từ 1 đến 20 bài.',1,20)}${number('pages',d,'Từ 1 đến 500 trang.',1,500)}</div>`));
  if(tab==='telegram') return section('Nhóm Telegram','Dùng group ID âm, mỗi dòng một ID.', `${toggle('allGroups',d.allGroups,'Nghe tất cả nhóm','Khi bật, danh sách nhóm riêng sẽ được xoá.') }<div class="field-grid">${field('chatIds',d.chatIds,d.allGroups?'Đang nghe tất cả nhóm; không dùng danh sách riêng.':'Danh sách nhóm trong cấu hình đã nối.',{wide:true,multiline:true,readonly:d.allGroups})}${field('ignored',d.ignored,'Một lệnh mỗi dòng, ví dụ /nailhub.',{wide:true,multiline:true})}</div>`)+section('Proxy Telegram','Bỏ trống host để kết nối trực tiếp.',`<div class="field-grid">${field('proxyHost',d.proxyHost,'Không chứa proxy secret.')}${number('proxyPort',d,'Port từ 1 đến 65535.',1,65535)}</div>`);
  if(tab==='aggregation') return section('Cửa sổ gom tin','Gom các tin liên quan trước khi phân loại.',`<div class="field-grid">${number('window',d,'0 = không đợi gom tin.',0)}${number('maxCase',d,'Số tin tối đa trong một case.',1)}${number('maxWindow',d,'Số tin tối đa mỗi lần phân tích.',1)}</div>`);
  if(tab==='files') return section('Tệp đính kèm','Ảnh và tài liệu có giới hạn từng case riêng.',`<div class="field-grid">${number('maxMB',d,'Dung lượng tối đa mỗi file; cho phép phần lẻ MiB.',1/1048576,100,'any')}${number('maxImages',d,'0 = tắt upload ảnh.',0)}${number('maxDocs',d,'0 = tắt upload tài liệu.',0)}${number('maxUploads',d,'Tính chung ảnh và tài liệu.',0)}</div>`);
  if(tab==='rules') return section('Quy tắc giá và lương','Đổi cấu hình không bỏ bước xác minh dữ liệu từ bài gốc.',`<div class="field-grid">${field('rangePolicy',d.rangePolicy,'Policy hiện có của worker.',{wide:true,choices:[['upper_floor_100','Cận trên, làm tròn xuống $100'],['hold','Giữ lại để kiểm tra']]})}${number('salaryPlaces',d,'Từ 0 đến 2 chữ số thập phân.',0,2)}</div>${toggle('salaryRange',d.salaryRange,'Hiển thị khoảng giá','Giá trị phải phù hợp contract đã xác minh.')}`);
  return section('Trình duyệt NailHub','Worker kiểm tra session và quyền trước khi xử lý bài.', `${toggle('headless',d.headless,'Chạy browser headless','Browser chạy nền trên máy worker.')}<div class="notice">Owner binding và VERIFIED contract được runtime xác minh. UI không thay đổi các điều kiện bảo vệ này.</div>`);
}
function rail(id) {
  const s=state[id];
  return `<aside class="summary-rail" aria-label="Tóm tắt cấu hình"><section><h2>Trạng thái cấu hình</h2><dl><div><dt>Backend</dt><dd>${esc(host.label)}</dd></div><div><dt>Bản đã lưu</dt><dd id="saved-revision">${esc(s.revision)}</dd></div><div><dt>Bản đã áp dụng</dt><dd id="applied-revision">${esc(s.appliedRevision??'Chưa xác nhận')}</dd></div><div><dt>PID đã xác nhận</dt><dd>${esc(s.status.mainPid||'—')}</dd></div></dl><p class="muted">${esc(s.status.message)}</p></section><section><h2>AI của bản nháp</h2><dl><div><dt>Provider</dt><dd id="summary-provider">${providers[s.draft.provider].name}</dd></div><div><dt>Model</dt><dd id="summary-model">${esc(s.draft.model)||'Chưa chọn'}</dd></div><div><dt>Key</dt><dd id="summary-key">${keyStatus(s)}</dd></div><div><dt>Test</dt><dd id="summary-test">${testLabel(s.test.status)}</dd></div></dl></section><div class="rail-note"><strong>Áp dụng riêng service</strong><p>${id==='triage'?'NailHub Worker':'Telegram Triage'} giữ nguyên cấu hình khi bạn lưu trang này.</p></div><div class="rail-note"><strong>${host.mode==='installed'?'Runtime trên host':'Lưu trữ local'}</strong><p>${host.mode==='installed'?'Áp dụng chỉ được xác nhận khi runtime khởi động và nhận đúng revision.':'Cấu hình được lưu trên máy chạy terminal. Chạy console với profile installed trên host để áp dụng service thật.'}</p></div></aside>`;
}
const keyStatus = s => s.draft.provider==='ollama'?'Không yêu cầu':s.key?'Sẽ thay mới':s.hasKey&&s.keyScope===scope(s.draft)?'Đã lưu':'Chưa có';
const testLabel = status => ({idle:'Chưa test',loading:'Đang test',success:'Đạt',error:'Lỗi',stale:'Cần test lại'}[status]);
function render() {
  document.querySelector('#crumb').textContent=names[current];
  document.querySelectorAll('[data-page]').forEach(a=>{ if(a.dataset.page===current)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current'); });
  if(loading){main.innerHTML='<div class="empty-state" role="status"><h1>Đang đọc cấu hình…</h1><p class="muted">Kết nối backend chạy từ terminal.</p></div>';return;}
  if(bootstrapError){main.innerHTML=`<div class="empty-state" role="alert"><h1>Chưa đọc được cấu hình</h1><p>${esc(bootstrapError)}</p><button class="button" id="retry-bootstrap">Kết nối lại</button></div>`;document.querySelector('#retry-bootstrap').onclick=()=>bootstrap(reloadTarget);return;}
  if(state[current]) {
    const id=current;
    const s=state[id],canApply=host.canApply&&s.canApply!==false;
    main.innerHTML=heading(id)+tabs(id)+`<div class="settings-layout"><form id="settings-form" class="settings-form" novalidate><div id="settings-panel" role="tabpanel" aria-labelledby="tab-${s.tab}">${panel(id)}</div><div class="action-bar"><div class="dirty-marker" id="dirty-status">${isDirty(id)?'Có thay đổi chưa lưu':'Bản nháp chưa thay đổi'}</div><div class="actions"><button class="button" id="discard" type="button" ${isDirty(id)?'':'disabled'}>Huỷ thay đổi</button><button class="button primary" type="submit" id="review-changes">Xem thay đổi<span aria-hidden="true">→</span></button></div></div><div class="revision-line"><span id="revision-note">${!canApply?'Host hiện tại chưa đủ điều kiện áp dụng runtime.':s.revision!==s.appliedRevision?'Bản đã lưu chưa được runtime xác nhận.':'Runtime đã nhận bản đã lưu.'}</span><button class="button" type="button" id="apply-settings" ${!canApply||s.applying||s.revision===s.appliedRevision?'disabled':''}>${s.applying?'Đang áp dụng…':'Áp dụng bản đã lưu'}</button></div><div class="test-result" role="alert" ${s.actionError?'':'hidden'}>${esc(s.actionError)}</div><button class="button" type="button" id="reload-service">Bỏ nháp, đọc lại từ backend</button></form>${rail(id)}</div>`;
    wireForm(id);
  } else if(current==='overview') {
    main.innerHTML=heading(current)+`<div class="notice">${esc(host.label)} · ${host.mode==='installed'?'Đã nối cấu hình host':'Lưu cấu hình local'} · chạy bằng <strong>python console-ui/run.py</strong>.</div><div class="overview-list">${['triage','nailhub'].map(id=>`<section class="service-block"><div class="service-block-header"><h2>${names[id]}</h2><span class="status">${esc(state[id].status.state)}</span><span class="badge">${esc(state[id].saved.mode)} · đã lưu</span></div><div class="overview-data"><div><span>Provider đã lưu</span><strong>${providers[state[id].saved.provider].name}</strong></div><div><span>Model đã lưu</span><strong>${esc(state[id].saved.model)}</strong></div><div><span>PID đã xác nhận</span><strong>${esc(state[id].status.mainPid||'—')}</strong></div><div><span>Revision runtime</span><strong>${esc(state[id].appliedRevision??'Chưa xác nhận')}</strong></div></div><div><a class="button" href="#${id}">Mở cấu hình <span aria-hidden="true">→</span></a></div></section>`).join('')}</div>`;
  } else {
    main.innerHTML=heading(current)+(history.length?history.map(item=>`<article class="history-row"><div><strong>${esc(item.action)} · ${esc(names[item.service]||item.service)}</strong><span class="badge">${esc(item.revision)}</span></div><time datetime="${esc(item.time)}">${new Date(item.time).toLocaleString('vi-VN',{timeZone:'Asia/Saigon'})}</time><p>${esc(Array.isArray(item.fields)?item.fields.map(k=>labels[k]||k).join(', '):item.fields)}</p></article>`).join(''):`<div class="empty-state"><h2>Chưa có thay đổi</h2><p class="muted">Các lần lưu và áp dụng sẽ xuất hiện ở đây. API key không được ghi vào lịch sử.</p><a class="button" href="#triage">Mở Telegram Triage →</a></div>`);
  }
}
function setTest(id,status,message) { const s=state[id]; s.test={status,message}; if(current!==id)return; const el=document.querySelector('#test-result'); if(el){el.dataset.state=status;el.textContent=message;} updateSummary(id); }
function invalidate(id) { if(state[id].test.status!=='idle')setTest(id,'stale','Bản nháp đã đổi. Test lại trước khi áp dụng.'); }
function updateSummary(id) {
  if(current!==id||loading||bootstrapError)return; const s=state[id];
  const values={'summary-provider':providers[s.draft.provider].name,'summary-model':s.draft.model||'Chưa chọn','summary-key':keyStatus(s),'summary-test':testLabel(s.test.status),'dirty-status':isDirty(id)?'Có thay đổi chưa lưu':'Bản nháp chưa thay đổi'};
  for(const [key,value] of Object.entries(values)){ const el=document.getElementById(key);if(el)el.textContent=value; }
  const discard=document.querySelector('#discard');if(discard)discard.disabled=!isDirty(id);
}
function showError(key,message) { const input=document.querySelector(`[name="${key}"]`),helper=document.getElementById(`help-${key}`); if(input){input.setAttribute('aria-invalid','true');if(helper)helper.textContent=message;} }
function clearErrors() { document.querySelectorAll('[aria-invalid]').forEach(el=>el.removeAttribute('aria-invalid')); document.querySelectorAll('[data-helper]').forEach(el=>el.textContent=el.dataset.helper); }
function validate(id) {
  clearErrors(); const s=state[id],d=s.draft,errors=[];
  if(!d.model.trim())errors.push(['model','Chưa có model ID. Nhập ID được provider cấp.']);
  try{ const u=new URL(d.baseUrl), local=['127.0.0.1','localhost','[::1]'].includes(u.hostname);if(u.username||u.password||u.search||u.hash||!(d.provider==='ollama'?u.protocol==='http:'&&local:u.protocol==='https:'))throw Error(); }catch{ errors.push(['baseUrl',d.provider==='ollama'?'Dùng HTTP loopback, ví dụ http://127.0.0.1:11434.':'Dùng HTTPS Base URL không chứa key, query hoặc credentials.']); }
  if(d.provider!=='ollama'&&!s.key&&!(s.hasKey&&s.keyScope===scope(d)))errors.push(['apiKey','Nhập API key cho provider và endpoint này.']);
  const ranges={timeout:[1,1800],maxOutput:[256,8192],issueLimit:[1,10000],window:[0,1440],maxCase:[1,1000],maxWindow:[1,1000],proxyPort:[1,65535],maxImages:[0,100],maxDocs:[0,100],maxUploads:[0,10000],interval:[10,86400],batch:[1,20],pages:[1,500],salaryPlaces:[0,2]};
  for(const [key,[min,max]] of Object.entries(ranges))if(key in d&&(!Number.isInteger(d[key])||d[key]<min||d[key]>max))errors.push([key,`Nhập số nguyên từ ${min}${Number.isFinite(max)?` đến ${max}`:' trở lên'}.`]);
  if('confidence' in d&&(!Number.isFinite(d.confidence)||d.confidence<0||d.confidence>1))errors.push(['confidence','Nhập ngưỡng từ 0 đến 1.']);
  if('maxMB' in d&&(!Number.isFinite(d.maxMB)||d.maxMB<1/1048576||d.maxMB>100))errors.push(['maxMB','Nhập dung lượng từ 1 byte đến 100 MiB.']);
  if('chatIds' in d&&!d.allGroups&&!/^-\d+(?:\s*[,\n]\s*-\d+)*$/.test(d.chatIds.trim()))errors.push(['chatIds','Nhập ít nhất một group ID âm; mỗi dòng một ID.']);
  if(errors.length){
    const tabFor={ai:['model','baseUrl','apiKey','timeout','maxOutput'],operation:['issueLimit','confidence','interval','batch','pages'],telegram:['chatIds','proxyPort'],aggregation:['window','maxCase','maxWindow'],files:['maxMB','maxImages','maxDocs','maxUploads'],rules:['salaryPlaces']};
    const target=Object.keys(tabFor).find(tab=>tabFor[tab].includes(errors[0][0])); if(target&&target!==s.tab){s.tab=target;render();}
    errors.forEach(([key,message])=>showError(key,message)); document.querySelector(`[name="${errors[0][0]}"]`)?.focus(); announce('Cần sửa các trường chưa hợp lệ.');return false;
  }
  return true;
}
function wireForm(id) {
  const form=document.querySelector('#settings-form');
  form.addEventListener('input',event=>{
    const el=event.target,key=el.name,s=state[id];if(!key||key.startsWith('protected')||key==='provider')return;
    if(key==='apiKey'){s.key=el.value;s.keyVersion++;invalidate(id);}
    else{ s.draft[key]=el.type==='checkbox'?el.checked:el.type==='number'?(el.value===''?NaN:Number(el.value)):el.value;
      if(key==='baseUrl'){s.key='';s.keyVersion++;s.draft.cloudConsent=false;const consent=document.querySelector('[name="cloudConsent"]');if(consent)consent.checked=false;const keyEl=document.querySelector('#api-key');if(keyEl)keyEl.value='';}
      if(aiKeys.has(key))invalidate(id);
      if(key==='allGroups'){if(el.checked)s.draft.chatIds='';render();}
    }
    if(el.getAttribute('aria-invalid')==='true'){el.removeAttribute('aria-invalid');const helper=document.getElementById(`help-${key}`);if(helper)helper.textContent=helper.dataset.helper||'';}
    updateSummary(id);
  });
  form.addEventListener('change',event=>{if(event.target.name==='provider'){const s=state[id],key=event.target.value,p=providers[key];s.draft.provider=key;s.draft.baseUrl=p.url;s.draft.model=p.model;s.draft.cloudConsent=false;if(key!=='ollama')s.draft.think=false;s.key='';s.keyVersion++;invalidate(id);render();document.querySelector(`[name="provider"][value="${key}"]`).focus({preventScroll:true});}});
  form.addEventListener('submit',event=>{event.preventDefault();if(!validate(id))return;review(id);});
  document.querySelector('#discard').onclick=()=>{const s=state[id];s.draft=structuredClone(s.saved);s.key='';s.keyVersion++;testJobs[id]++;s.test={status:'idle',message:'Bản nháp đã trở về cấu hình đã lưu; cần test lại.'};render();announce('Đã huỷ thay đổi chưa lưu.');};
  document.querySelector('#apply-settings').onclick=()=>applySettings(id);
  document.querySelector('#reload-service').onclick=()=>bootstrap(id);
  document.querySelector('#toggle-key')?.addEventListener('click',event=>{const input=document.querySelector('#api-key'),show=input.type==='password';input.type=show?'text':'password';event.currentTarget.textContent=show?'Ẩn':'Hiện';event.currentTarget.setAttribute('aria-label',`${show?'Ẩn':'Hiện'} API key`);});
  document.querySelector('#test-connection')?.addEventListener('click',()=>testConnection(id));
  document.querySelectorAll('[data-tab]').forEach(button=>{button.onclick=()=>{state[id].tab=button.dataset.tab;render();document.getElementById(`tab-${state[id].tab}`).focus({preventScroll:true});};button.onkeydown=event=>{const buttons=[...document.querySelectorAll('[data-tab]')],index=buttons.indexOf(button);let next;if(event.key==='ArrowRight')next=(index+1)%buttons.length;else if(event.key==='ArrowLeft')next=(index-1+buttons.length)%buttons.length;else if(event.key==='Home')next=0;else if(event.key==='End')next=buttons.length-1;if(next!==undefined){event.preventDefault();buttons[next].click();}};});
}
async function testConnection(id) {
  if(!validate(id))return;const s=state[id],settings=structuredClone(s.draft),key=s.key,signature=testSignature(s),job=++testJobs[id];
  setTest(id,'loading','Đang gửi mẫu kiểm tra đến provider…');const button=document.querySelector('#test-connection');button.disabled=true;button.dataset.state='loading';button.textContent='Đang test…';
  let result;
  try { result=await window.AgentFleetAPI.test(id,settings,key); }
  catch(error) { result={status:'error',message:error.message}; }
  if(job!==testJobs[id])return;
  if(signature!==testSignature(state[id])){setTest(id,'stale','Bản nháp hoặc key đã đổi trong lúc test. Test lại cấu hình hiện tại.');if(current===id)render();return;}
  setTest(id,result.status==='success'?'success':'error',`${result.message}${Number.isFinite(result.elapsedMs)?` · ${result.elapsedMs} ms`:''}`);
  if(current===id){const b=document.querySelector('#test-connection');if(b){b.disabled=false;b.dataset.state=state[id].test.status;b.innerHTML='Test lại <span aria-hidden="true">↗</span>';}}announce(result.message);
}
const testSignature = s => JSON.stringify([...aiKeys].map(k=>s.draft[k]))+s.keyVersion;
async function applySettings(id) {
  const s=state[id];if(saveBusy||Object.values(state).some(item=>item.applying)||!host.canApply||s.canApply===false){announce('Chờ thao tác hiện tại hoàn tất hoặc kiểm tra điều kiện host.');return;}
  s.applying=true;s.actionError='';render();
  try { const result=await window.AgentFleetAPI.apply(id,s.revision);acceptService(id,result.service);acceptHistory(result.history);announce(`Runtime ${names[id]} đã nhận cấu hình.`); }
  catch(error) { state[id].actionError=error.message;announce(error.message); }
  finally { state[id].applying=false;render(); }
}
const displayValue=(key,value)=>key==='provider'?providers[value].name:typeof value==='boolean'?(value?'Bật':'Tắt'):String(value===''||value==null?'Trống':value);
function review(id) {
  if(saveBusy||Object.values(state).some(s=>s.applying)){announce('Chờ thao tác lưu hoặc áp dụng hiện tại hoàn tất.');return;}
  const s=state[id],changes=Object.keys(s.draft).filter(key=>s.draft[key]!==s.saved[key]);saveTarget=id;
  document.querySelector('#review-subtitle').textContent=`${names[id]} · Lưu tại backend. Áp dụng runtime là bước riêng.`;
  document.querySelector('#review-body').innerHTML=changes.map(key=>`<div class="diff-row"><strong>${esc(labels[key]||key)}</strong><div><del>${esc(displayValue(key,s.saved[key]))}</del><span aria-hidden="true">→</span><ins>${esc(displayValue(key,s.draft[key]))}</ins></div></div>`).join('')+(s.key?'<div class="diff-row"><strong>API key</strong><div>Thay key riêng của service (giá trị được ẩn)</div></div>':'')+(!changes.length&&!s.key?'<p class="notice">Chưa có thay đổi để lưu.</p>':'');
  document.querySelector('#save-error').hidden=true;document.querySelector('#confirm-save').textContent='Lưu cấu hình';
  document.querySelector('#confirm-save').disabled=!changes.length&&!s.key;openDialog('review-dialog');
}
function openDialog(id) { previousFocus=document.activeElement;document.getElementById(id).showModal(); }
function closeDialog(dialog) { dialog.close(); }
document.querySelectorAll('dialog').forEach(dialog=>{dialog.addEventListener('click',event=>{if(event.target===dialog){const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)closeDialog(dialog);}});dialog.addEventListener('close',()=>{previousFocus?.isConnected&&previousFocus.focus({preventScroll:true});});dialog.querySelectorAll('[data-close]').forEach(button=>button.onclick=()=>closeDialog(dialog));});
document.querySelector('#confirm-save').onclick=async()=>{
  if(saveBusy)return;saveBusy=true;
  const id=saveTarget,s=state[id],snapshot=structuredClone(s.draft),key=s.key,keyVersion=s.keyVersion,button=document.querySelector('#confirm-save');
  button.disabled=true;button.textContent='Đang lưu…';document.querySelector('#save-error').hidden=true;
  try {
    const result=await window.AgentFleetAPI.save(id,snapshot,key,s.revision);
    const unchanged=JSON.stringify(state[id].draft)===JSON.stringify(snapshot),sameKey=state[id].keyVersion===keyVersion;
    acceptService(id,result.service);acceptHistory(result.history);
    if(unchanged&&sameKey)state[id].draft=structuredClone(result.service.settings);
    if(sameKey){state[id].key='';state[id].keyVersion++;invalidate(id);}
    if(document.querySelector('#review-dialog').open)closeDialog(document.querySelector('#review-dialog'));
    render();announce(`Đã lưu ${names[id]}. Runtime chưa được áp dụng.`);
  } catch(error) {
    const el=document.querySelector('#save-error');el.textContent=error.status===409?`${error.message} Đóng hộp thoại rồi dùng “Bỏ nháp, đọc lại từ backend” để tải bản mới.`:error.message;el.hidden=false;announce(el.textContent);
  } finally {saveBusy=false;button.disabled=false;button.textContent='Lưu cấu hình';}
};
function navigate(page) { current=page;historyReplace(page);document.querySelector('#sidebar').classList.remove('is-open');document.querySelector('#menu-toggle').setAttribute('aria-expanded','false');render(); }
function historyReplace(page){window.history.replaceState(null,'',`#${page}`);}
function requestNavigation(page) { if(!names[page]||page===current)return;if(state[current]&&isDirty(current)){pendingPage=page;openDialog('leave-dialog');}else navigate(page); }
document.addEventListener('click',event=>{const link=event.target.closest('a[href^="#"]');if(link&&link.getAttribute('href')!=='#main'){event.preventDefault();requestNavigation(link.hash.slice(1));}});
window.addEventListener('hashchange',()=>{const page=location.hash.slice(1);historyReplace(current);requestNavigation(page);});
document.querySelector('#confirm-leave').onclick=()=>{closeDialog(document.querySelector('#leave-dialog'));navigate(pendingPage);};
document.querySelector('#menu-toggle').onclick=event=>{const open=document.querySelector('#sidebar').classList.toggle('is-open');event.currentTarget.setAttribute('aria-expanded',String(open));event.currentTarget.setAttribute('aria-label',open?'Đóng điều hướng':'Mở điều hướng');};
const searchInput=document.querySelector('#search-input');
function renderSearch(){const query=searchInput.value.toLocaleLowerCase('vi');const entries=Object.entries(names).filter(([,label])=>label.toLocaleLowerCase('vi').includes(query));document.querySelector('#search-results').innerHTML=entries.length?entries.map(([id,label])=>`<button data-destination="${id}">${label}</button>`).join(''):'<p class="muted">Không có màn hình khớp. Thử Telegram hoặc NailHub.</p>';document.querySelectorAll('[data-destination]').forEach(button=>button.onclick=()=>{closeDialog(document.querySelector('#search-dialog'));requestNavigation(button.dataset.destination);});}
function openSearch(){searchInput.value='';renderSearch();openDialog('search-dialog');searchInput.focus();}
document.querySelector('#open-search').onclick=openSearch;searchInput.oninput=renderSearch;
document.querySelector('#search-dialog').addEventListener('keydown',event=>{const buttons=[...document.querySelectorAll('[data-destination]')],index=buttons.indexOf(document.activeElement);if(event.key==='ArrowDown'){event.preventDefault();buttons[(index+1)%buttons.length]?.focus();}else if(event.key==='ArrowUp'){event.preventDefault();buttons[(index-1+buttons.length)%buttons.length]?.focus();}else if(event.key==='Enter'&&document.activeElement===searchInput){event.preventDefault();buttons[0]?.click();}});
document.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'){event.preventDefault();if(!document.querySelector('dialog[open]'))openSearch();}if(event.key==='Escape'){document.querySelector('#sidebar').classList.remove('is-open');document.querySelector('#menu-toggle').setAttribute('aria-expanded','false');}});
window.addEventListener('beforeunload',event=>{if(Object.keys(state).some(isDirty)){event.preventDefault();event.returnValue='';}});
bootstrap();
