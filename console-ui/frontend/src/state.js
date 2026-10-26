/* Per-service memory state. Credentials are never persisted or copied into settings. */
'use strict';
window.AgentFleetState = (() => {
  const clone = value => structuredClone(value);
  const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);
  function create() {
    const entries = Object.create(null), descriptors = Object.create(null);
    const descriptor = id => descriptors[id];
    const providerField = id => descriptor(id)?.fields.find(field => field.widget === 'provider');
    const provider = id => descriptor(id)?.providers?.find(item => item.id === entries[id]?.draft[providerField(id)?.id]);
    const scope = settings => `${settings.provider}|${settings.baseUrl}`;
    function setDescriptors(values) {
      for (const id of Object.keys(descriptors)) delete descriptors[id];
      for (const [id, value] of Object.entries(values)) descriptors[id] = value;
      for (const id of Object.keys(entries)) if (!descriptors[id]) entries[id].available = false;
    }
    function accept(id, raw, resetDraft = false) {
      const previous = entries[id], spec = descriptor(id);
      const saved = clone(raw.settings || {});
      const defaults = Object.fromEntries((spec?.fields || []).map(field => [field.id, clone(field.default)]));
      const reset = resetDraft || !previous;
      entries[id] = {
        ...previous, ...raw, saved,
        draft: reset ? {...defaults, ...clone(saved)} : previous.draft,
        key: reset ? '' : previous.key,
        keyVersion: (previous?.keyVersion || 0) + (reset ? 1 : 0),
        testJob: (previous?.testJob || 0) + (reset ? 1 : 0),
        test: reset ? {status:'idle', message:'Chưa kiểm tra bản nháp.'} : previous.test,
        tab: spec?.groups.some(group => group.id === previous?.tab) ? previous.tab : spec?.groups[0]?.id,
        applying: previous?.applying || false, actionError:'',
        contractRevision: raw.contractRevision || spec?.contractRevision,
        available: raw.available !== false && Boolean(spec),
      };
      return entries[id];
    }
    const dirty = id => Boolean(entries[id]) && (!same(entries[id].draft, entries[id].saved) || Boolean(entries[id].key));
    function clearKey(id) { entries[id].key = ''; entries[id].keyVersion++; }
    function discard(id) {
      const entry = entries[id]; entry.draft = clone(entry.saved); clearKey(id); entry.testJob++;
      entry.test = {status:'idle', message:'Bản nháp đã trở về cấu hình đã lưu; cần test lại.'};
    }
    function switchProvider(id, value) {
      const entry = entries[id], field = providerField(id);
      const choice = descriptor(id)?.providers?.find(item => item.id === value);
      if (!field || !choice) return;
      entry.draft[field.id] = choice.id;
      for (const [key, setting] of Object.entries(choice.defaults)) {
        if (descriptor(id).fields.some(item => item.id === key)) entry.draft[key] = clone(setting);
      }
      if ('cloudConsent' in entry.draft) entry.draft.cloudConsent = false;
      const think = descriptor(id).fields.find(item => item.id === 'think');
      if (think && window.AgentFleetSchema.matches(think.disabledWhen, entry.draft)) entry.draft.think = false;
      clearKey(id);
    }
    const signature = id => JSON.stringify(entries[id].draft) + ':' + entries[id].keyVersion;
    return {entries, descriptors, descriptor, setDescriptors, accept, dirty, discard, provider, providerField, scope, clearKey, switchProvider, signature, same};
  }
  return {create};
})();
