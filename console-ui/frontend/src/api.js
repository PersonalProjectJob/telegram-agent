/* Same-origin API boundary. Credentials and CSRF token live only in memory. */
'use strict';
window.AgentFleetAPI = (() => {
  let csrfToken = '';
  let registry = new Map();
  let bootstrapJob = 0;
  class APIError extends Error {
    constructor(message, code, status, field) { super(message); this.code = code; this.status = status; this.field = field; }
  }
  async function request(path, { method = 'GET', body, timeout = 90000 } = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const headers = { Accept: 'application/json' };
      if (method !== 'GET') { headers['Content-Type'] = 'application/json'; headers['X-CSRF-Token'] = csrfToken; }
      const response = await fetch(path, { method, headers, credentials:'same-origin', cache:'no-store', signal:controller.signal, ...(body === undefined ? {} : { body:JSON.stringify(body) }) });
      if (!response.headers.get('content-type')?.includes('application/json')) throw new APIError('Server chưa hỗ trợ API. Dừng bản preview cũ và chạy lại python console-ui/run.py.', 'SERVER_UNAVAILABLE', response.status);
      const data = await response.json();
      if (!response.ok) throw new APIError(data.error?.message || 'Request bị từ chối. Kiểm tra cấu hình rồi thử lại.', data.error?.code || 'REQUEST_FAILED', response.status, data.error?.field);
      return data;
    } catch(error) {
      if (error instanceof APIError) throw error;
      if (error.name === 'AbortError') throw new APIError('Request hết thời gian chờ. Kiểm tra trạng thái server trước khi thử lại.', 'TIMEOUT', 0);
      throw new APIError('Không kết nối được backend. Kiểm tra cửa sổ terminal rồi thử lại.', 'NETWORK_ERROR', 0);
    } finally { clearTimeout(timer); }
  }
  const servicePath = id => {
    if (typeof id !== 'string' || !registry.has(id)) throw new APIError('Service không hợp lệ.', 'INVALID_SERVICE', 400);
    return `/api/services/${encodeURIComponent(id)}`;
  };
  const contract = id => ({contractRevision:registry.get(id)?.contractRevision});
  return {
    APIError,
    async bootstrap() {
      const job = ++bootstrapJob;
      const data = await request('/api/bootstrap', {timeout:15000});
      if (job === bootstrapJob) { csrfToken = data.csrfToken; registry = new Map(Object.entries(data.descriptors || {})); }
      return data;
    },
    usage(id) {
      const path = servicePath(id);
      if (!registry.get(id)?.capabilities?.usage) throw new APIError('Service không hỗ trợ dữ liệu chi phí.', 'UNSUPPORTED_CAPABILITY', 400);
      return request(`${path}/usage`, {timeout:15000});
    },
    test(id, settings, apiKey) { return request(`${servicePath(id)}/test`, {method:'POST',body:{settings,...(apiKey ? {apiKey} : {}),...contract(id)},timeout:120000}); },
    save(id, settings, apiKey, expectedRevision) { return request(`${servicePath(id)}/save`, {method:'POST',body:{settings,...(apiKey ? {apiKey} : {}),expectedRevision,...contract(id)},timeout:30000}); },
    apply(id, expectedRevision) { return request(`${servicePath(id)}/apply`, {method:'POST',body:{expectedRevision,...contract(id)},timeout:210000}); }
  };
})();
