// 统一的后端访问层。
// 为什么单独抽出来：组件里不该出现 fetch 和 URL 字符串。
// 接口一旦改名，只改这一个文件，不用担心漏改某个组件。

const BASE = '/api'

async function request(path, options = {}) {
  const resp = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`
    try {
      const body = await resp.json()
      if (body?.detail) {
        detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
      }
    } catch {
      /* 响应不是 JSON，保留 HTTP 状态码 */
    }
    throw new Error(detail)
  }
  return resp.status === 204 ? null : resp.json()
}

export const api = {
  meta: () => request('/meta'),
  health: () => request('/health'),

  listTickets: (status, keyword) => {
    const params = new URLSearchParams()
    if (status) params.set('status', status)
    if (keyword) params.set('q', keyword)
    const qs = params.toString()
    return request(`/tickets${qs ? `?${qs}` : ''}`)
  },
  getTicket: (id) => request(`/tickets/${id}`),
  createTicket: (payload) => request('/tickets', { method: 'POST', body: JSON.stringify(payload) }),
  deleteTicket: (id) => request(`/tickets/${id}`, { method: 'DELETE' }),

  analyze: (payload) => request('/tickets/analyze', { method: 'POST', body: JSON.stringify(payload) }),
  confirm: (payload) => request('/tickets/confirm', { method: 'POST', body: JSON.stringify(payload) }),
  advance: (payload) => request('/tickets/advance', { method: 'POST', body: JSON.stringify(payload) }),
  dispatch: (ticketId) => request(`/tickets/${ticketId}/dispatch`, { method: 'POST' }),
  dispatchAll: () => request('/tickets/dispatch-all', { method: 'POST' }),

  stats: () => request('/stats'),
  seed: () => request('/seed', { method: 'POST' }),
  runEval: (engine = 'rule') =>
    request('/eval/run', { method: 'POST', body: JSON.stringify({ engine }) }),
  lastEval: () => request('/eval/last'),
}
