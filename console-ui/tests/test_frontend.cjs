/* Deferred API feedback regressions against the actual Console scripts. Node standard library only. */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = path.resolve(__dirname, '../frontend/src');

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return {promise, resolve, reject};
}
function environment() {
  const probe = deferred(), save = deferred(), elements = new Map();
  let context;
  const active = () => context ? vm.runInContext('current', context) : 'triage';
  function element(key) {
    if (elements.has(key)) return elements.get(key);
    const attributes = new Map(), listeners = new Map();
    const item = {
      textContent:'', innerHTML:'', dataset:{}, hidden:true, disabled:false, open:false,
      isConnected:true, value:'', style:{},
      setAttribute(name, value) { attributes.set(name, value); },
      getAttribute(name) { return attributes.get(name) ?? null; },
      removeAttribute(name) { attributes.delete(name); },
      addEventListener(name, callback) { listeners.set(name, callback); },
      querySelectorAll() { return []; },
      focus() { item.focused = true; },
      showModal() { item.open = true; },
      close() { item.open = false; listeners.get('close')?.(); },
      classList:{remove() {}, toggle() { return false; }},
    };
    elements.set(key, item);
    return item;
  }
  const workerElement = name => element(active() + ':' + name);
  const globalIds = new Set(['review-dialog','save-error','confirm-save','review-subtitle','review-body','announcement','confirm-leave','menu-toggle','sidebar','crumb','binding-name','binding-mode','connection-banner','open-search','search-input','search-results','search-dialog','leave-dialog']);
  const byId = id => globalIds.has(id) ? element(id) : workerElement(id);
  const document = {
    activeElement:element('original-focus'),
    querySelector(selector) { return selector.startsWith('#') ? byId(selector.slice(1)) : element(selector); },
    getElementById:byId,
    getElementsByName(name) { return [workerElement('field-' + name)]; },
    querySelectorAll(selector) {
      if (selector === 'dialog') return [element('review-dialog')];
      if (selector === '[aria-invalid]') return [workerElement('field-baseUrl')].filter(item => item.getAttribute('aria-invalid'));
      if (selector === '[data-helper]') return [workerElement('help-baseUrl')];
      return [];
    },
    addEventListener() {},
  };
  context = vm.createContext({
    document, structuredClone, URL, Intl, Date,
    location:{hash:'#triage'}, localStorage:{getItem() { return null; }, setItem() {}},
    window:{addEventListener() {}, history:{replaceState() {}}, AgentFleetAPI:{
      bootstrap:() => new Promise(() => {}), test:() => probe.promise, save:() => save.promise,
    }},
  });
  // Load unmodified production modules; a pending bootstrap isolates controlled service fixtures.
  for (const name of ['schema-form.js','state.js','app.js']) {
    vm.runInContext(fs.readFileSync(path.join(source, name), 'utf8'), context, {filename:name});
  }
  vm.runInContext(`
    loading=false; bootstrapError='';
    for(const id of ['triage','nailhub']){
      const spec={id,displayName:id,contractRevision:id+'-v1',schemaVersion:1,
        capabilities:{settings:true,probe:true,usage:false},providers:[],
        groups:[{id:'ai',label:'AI',sections:[{id:'connection',title:'Connection',description:''}]}],
        fields:[{id:'baseUrl',label:'Base URL',type:'string',widget:'text',default:'http://127.0.0.1:11434',group:'ai',section:'connection',help:'Original help'}]};
      descriptors[id]=spec; names[id]=id;
      session.accept(id,{settings:{baseUrl:spec.fields[0].default},revision:0,canSave:true,available:true,status:{state:'local'}},true);
    }
  `, context);
  for (const id of ['triage','nailhub']) {
    const help = element(id + ':help-baseUrl');
    help.dataset.helper = id + ' original help'; help.textContent = help.dataset.helper;
  }
  const run = code => vm.runInContext(code, context);
  const reject = target => target.reject(Object.assign(new Error('Triage endpoint error'), {field:'baseUrl',status:400}));
  const pristine = id => {
    assert.equal(element(id + ':field-baseUrl').getAttribute('aria-invalid'), null);
    assert.equal(element(id + ':help-baseUrl').textContent, id + ' original help');
  };
  return {probe,save,run,reject,pristine,element};
}

const cases = [
  ['local rail status appears once while installed compatibility stays visible', async () => {
    const e = environment();
    e.run("state.triage.status.message='Local configuration files; no installed service.'; state.triage.compatibility={state:'local',reason:state.triage.status.message}");
    assert.equal(e.run("rail('triage')").split('Local configuration files; no installed service.').length - 1, 1);
    e.run("state.triage.compatibility={state:'mismatch',reason:'Installed source changed'}");
    assert(e.run("rail('triage')").includes('Installed source changed'));
  }],
  ['background probe rejection does not annotate another worker', async () => {
    const e = environment(), pending = e.run("testConnection('triage')");
    e.run("current='nailhub'"); e.reject(e.probe); await pending;
    e.pristine('nailhub');
    assert.equal(e.element('announcement').textContent, '');
    assert.equal(e.run('state.triage.test.status'), 'error');
  }],
  ['discarded probe job cannot mark the new draft', async () => {
    const e = environment(), pending = e.run("testConnection('triage')");
    e.run("session.discard('triage')"); e.reject(e.probe); await pending;
    e.pristine('triage'); assert.equal(e.run('state.triage.test.status'), 'idle');
  }],
  ['changed key signature rejects obsolete field feedback', async () => {
    const e = environment(), pending = e.run("testConnection('triage')");
    e.run("session.clearKey('triage')"); e.reject(e.probe); await pending;
    e.pristine('triage'); assert.equal(e.run('state.triage.test.status'), 'stale');
  }],
  ['active current probe error annotates its matching field', async () => {
    const e = environment(), pending = e.run("testConnection('triage')");
    e.reject(e.probe); await pending;
    assert.equal(e.element('triage:field-baseUrl').getAttribute('aria-invalid'), 'true');
    assert.equal(e.element('triage:help-baseUrl').textContent, 'Triage endpoint error');
    assert.equal(e.run('state.triage.test.status'), 'error');
  }],
  ['closed save dialog and navigation cannot annotate another worker', async () => {
    const e = environment(); e.run("state.triage.draft.baseUrl='http://127.0.0.1:11435'; review('triage')");
    const pending = e.element('confirm-save').onclick();
    e.element('review-dialog').close(); e.run("current='nailhub'"); e.reject(e.save); await pending;
    e.pristine('nailhub'); assert.equal(e.element('save-error').hidden, true);
    assert.equal(e.element('announcement').textContent, '');
  }],
  ['save rejection for a discarded snapshot cannot mark the new draft', async () => {
    const e = environment(); e.run("state.triage.draft.baseUrl='http://127.0.0.1:11435'; review('triage')");
    const pending = e.element('confirm-save').onclick();
    e.element('review-dialog').close(); e.run("session.discard('triage')"); e.reject(e.save); await pending;
    e.pristine('triage'); assert.equal(e.run('state.triage.actionError'), '');
  }],
  ['active valid save rejection retains field feedback', async () => {
    const e = environment(); e.run("state.triage.draft.baseUrl='http://127.0.0.1:11435'; review('triage')");
    const pending = e.element('confirm-save').onclick(); e.reject(e.save); await pending;
    assert.equal(e.element('triage:field-baseUrl').getAttribute('aria-invalid'), 'true');
    assert.equal(e.element('triage:help-baseUrl').textContent, 'Triage endpoint error');
    assert.equal(e.element('save-error').hidden, false);
  }],
];
(async () => {
  for (const [name, check] of cases) { await check(); process.stdout.write('PASS ' + name + '\n'); }
  process.stdout.write('FRONTEND_ASYNC_FEEDBACK_REGRESSIONS=' + cases.length + '/' + cases.length + ' PASS\n');
})().catch(error => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; });
