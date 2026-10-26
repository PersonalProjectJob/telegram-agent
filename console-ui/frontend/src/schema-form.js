/* Public descriptor rendering only: no worker IDs, TOML mappings or duplicated field catalogs. */
'use strict';
window.AgentFleetSchema = (() => {
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const types = new Set(['string','integer','number','boolean','enum','list']);
  const widgets = new Set(['text','number','toggle','select','textarea','provider']);
  const identifier = value => typeof value === 'string' && /^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/.test(value);
  function matches(condition, values, depth = 0) {
    if (!condition) return false;
    if (depth > 8 || typeof condition !== 'object' || Array.isArray(condition)) return false;
    if (condition.equals) return values[condition.equals.field] === condition.equals.value;
    if (condition.in) return Array.isArray(condition.in.values) && condition.in.values.includes(values[condition.in.field]);
    return Array.isArray(condition.all) && condition.all.every(item => matches(item, values, depth + 1));
  }
  function supported(spec) {
    if (!spec || spec.schemaVersion !== 1 || !identifier(spec.id) || typeof spec.contractRevision !== 'string') return false;
    if (!Array.isArray(spec.fields) || spec.fields.length > 128 || !Array.isArray(spec.groups) || !spec.groups.length) return false;
    const fields = new Set(), groups = new Map();
    for (const group of spec.groups) {
      if (!identifier(group.id) || groups.has(group.id) || !Array.isArray(group.sections)) return false;
      const sections = new Set();
      for (const section of group.sections) {
        if (!identifier(section.id) || sections.has(section.id)) return false;
        sections.add(section.id);
      }
      groups.set(group.id, sections);
    }
    for (const field of spec.fields) {
      if (!identifier(field.id) || fields.has(field.id) || !types.has(field.type) || !widgets.has(field.widget) || !groups.get(field.group)?.has(field.section)) return false;
      if (field.type === 'enum' && (!Array.isArray(field.options) || field.options.length > 256)) return false;
      fields.add(field.id);
    }
    return Boolean(spec.capabilities && Array.isArray(spec.providers));
  }
  const visible = (field, values) => !field.visibleWhen || matches(field.visibleWhen, values);
  function disabled(spec, field, values) {
    const section = spec.groups.find(group => group.id === field.group)?.sections.find(item => item.id === field.section);
    return matches(section?.disabledWhen, values) || matches(field.disabledWhen, values);
  }
  const section = (title, subtitle, body) => `<section class="section"><div class="section-heading"><h2>${escape(title)}</h2><p>${escape(subtitle)}</p></div>${body}</section>`;
  function display(field, value) {
    if (field?.type === 'enum') return field.options.find(option => option.value === value)?.label ?? String(value ?? '');
    if (typeof value === 'boolean') return value ? 'Bật' : 'Tắt';
    if (Array.isArray(value)) return value.join('\n');
    return value === '' || value == null ? 'Trống' : String(value);
  }
  function renderField(spec, field, values, mutationDisabled = false) {
    if (!visible(field, values)) return '';
    const value = values[field.id], id = `field-${field.id}`, off = mutationDisabled || disabled(spec, field, values);
    const attrs = `id="${escape(id)}" name="${escape(field.id)}" aria-describedby="help-${escape(field.id)}"${off ? ' disabled' : ''}`;
    const help = `<small id="help-${escape(field.id)}" data-helper="${escape(field.help)}">${escape(field.help) || '&nbsp;'}</small>`;
    if (field.widget === 'provider') {
      return `<fieldset class="provider-grid"><legend class="sr-only">${escape(field.label)}</legend>${spec.providers.map(provider => `<label class="provider-choice"><input type="radio" name="${escape(field.id)}" value="${escape(provider.id)}"${provider.id === value ? ' checked' : ''}${off ? ' disabled' : ''}><span><strong>${escape(provider.label)}</strong><small>${escape(provider.note)}</small></span></label>`).join('')}</fieldset>`;
    }
    if (field.widget === 'toggle') {
      return `<div class="toggle-row"><div><label class="field-label" for="${escape(id)}">${escape(field.label)}</label><p id="help-${escape(field.id)}" data-helper="${escape(field.help)}">${escape(field.help)}</p></div><label class="switch-label"><input ${attrs} type="checkbox" class="switch"${value ? ' checked' : ''}></label></div>`;
    }
    let control;
    if (field.widget === 'select') control = `<select ${attrs}>${field.options.map(option => `<option value="${escape(option.value)}"${option.value === value ? ' selected' : ''}>${escape(option.label)}</option>`).join('')}</select>`;
    else if (field.widget === 'textarea' || field.type === 'list') control = `<textarea ${attrs} spellcheck="false">${escape(Array.isArray(value) ? value.join('\n') : value)}</textarea>`;
    else control = `<input ${attrs} type="${field.widget === 'number' ? 'number' : 'text'}" value="${escape(value)}"${field.minimum !== undefined ? ` min="${escape(field.minimum)}"` : ''}${field.maximum !== undefined ? ` max="${escape(field.maximum)}"` : ''}${field.maxLength !== undefined ? ` maxlength="${escape(field.maxLength)}"` : ''}${field.step !== undefined ? ` step="${escape(field.step)}"` : ''}${field.placeholder ? ` placeholder="${escape(field.placeholder)}"` : ''} autocomplete="off" spellcheck="false">`;
    return `<div class="field${field.wide ? ' field-wide' : ''}"><label for="${escape(id)}">${escape(field.label)}</label>${control}${help}</div>`;
  }
  function renderGroup(spec, groupId, values, {mutationDisabled = false, supplement = () => ''} = {}) {
    const group = spec.groups.find(item => item.id === groupId);
    if (!group) return '';
    return group.sections.map(item => {
      const fields = spec.fields.filter(field => field.group === group.id && field.section === item.id && visible(field, values));
      let body = '', grid = [];
      let supplementalFields = false;
      const flush = () => { if (!supplementalFields) { const extra = supplement(item, 'fields'); if (extra) grid.push(extra); supplementalFields = true; } if (grid.length) { body += `<div class="field-grid">${grid.join('')}</div>`; grid = []; } };
      for (const field of fields) {
        const html = renderField(spec, field, values, mutationDisabled);
        if (['toggle','provider'].includes(field.widget)) { flush(); body += html; }
        else grid.push(html);
      }
      flush(); body += supplement(item, 'after');
      if (matches(item.disabledWhen, values) && item.disabledHelp) body += `<div class="notice">${escape(item.disabledHelp)}</div>`;
      return body ? section(item.title, item.description, body) : '';
    }).join('');
  }
  function read(field, element) {
    if (field.type === 'boolean') return element.checked;
    if (field.type === 'number' || field.type === 'integer') return element.value === '' ? NaN : Number(element.value);
    if (field.type !== 'list') return element.value;
    return element.value.split(/\r?\n/).filter(line => line.trim()).map(line => {
      if (field.itemType === 'number' || field.itemType === 'integer') return Number(line);
      if (field.itemType === 'boolean') return line.trim() === 'true' ? true : line.trim() === 'false' ? false : line;
      return line;
    });
  }
  function validate(spec, values) {
    const errors = [];
    const validPrimitive = (type, value) => type === 'string' ? typeof value === 'string' : type === 'boolean' ? typeof value === 'boolean' : type === 'integer' ? Number.isSafeInteger(value) : typeof value === 'number' && Number.isFinite(value);
    for (const field of spec.fields) {
      const value = values[field.id]; let error = '';
      if (field.type === 'enum') { if (!field.options.some(option => option.value === value)) error = 'Chọn một giá trị hợp lệ.'; }
      else if (field.type === 'list') {
        if (!Array.isArray(value) || value.length > field.maxItems || value.some(item => !validPrimitive(field.itemType, item) || (typeof item === 'string' && field.maxLength !== undefined && item.length > field.maxLength))) error = 'Danh sách chứa giá trị không hợp lệ.';
      } else if (!validPrimitive(field.type, value)) error = 'Nhập giá trị đúng kiểu dữ liệu.';
      if (!error && typeof value === 'number' && (value < field.minimum || value > field.maximum)) error = `Nhập giá trị${field.minimum !== undefined ? ` từ ${field.minimum}` : ''}${field.maximum !== undefined ? ` đến ${field.maximum}` : ''}.`;
      if (!error && typeof value === 'string' && field.maxLength !== undefined && value.length > field.maxLength) error = `Nhập tối đa ${field.maxLength} ký tự.`;
      if (error) errors.push([field.id, error]);
    }
    return errors;
  }
  return {escape, matches, supported, visible, disabled, section, display, renderField, renderGroup, read, validate};
})();
