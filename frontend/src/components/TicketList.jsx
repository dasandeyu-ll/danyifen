import { useMemo, useState } from 'react'
import { StatusPill } from './atoms.jsx'

const FILTERS = [
  { key: '', label: '全部' },
  { key: 'pending', label: '待处理' },
  { key: 'asking', label: '待追问' },
  { key: 'ready', label: '待分拣' },
  { key: 'done', label: '已分拣' },
]

function timeAgo(iso) {
  if (!iso) return ''
  const diff = (Date.now() - new Date(iso).getTime()) / 1000
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  return iso.slice(5, 16).replace('T', ' ')
}

function IntentDots({ goals, intentColors }) {
  if (!goals?.length) return <span className="dot-empty">未识别</span>
  return (
    <div className="dot-row">
      {goals.map((g) => (
        <span
          key={g.id}
          className="dot"
          style={{ background: intentColors[g.intent] || '#94a3b8' }}
          title={`${g.intent} · 置信度 ${g.confidence}`}
        />
      ))}
      {goals.filter((g) => g.need_human).length > 0 && (
        <span className="dot-flag" title="含需人工确认的目标">
          需确认
        </span>
      )}
    </div>
  )
}

export default function TicketList({
  tickets,
  selectedId,
  onSelect,
  filter,
  onFilterChange,
  keyword,
  onKeywordChange,
  intentColors,
}) {
  const [collapsed, setCollapsed] = useState(false)

  // 计数在前端算：后端已经返回过滤后的列表，但 tabs 上想显示全量分布，
  // 所以这里用「传进来的全量 + 当前过滤条件」两个口径分别算一次。
  const counts = useMemo(() => {
    const map = {}
    tickets.forEach((t) => {
      map[t.status] = (map[t.status] || 0) + 1
    })
    return map
  }, [tickets])

  if (collapsed) {
    return (
      <button className="list-collapsed" onClick={() => setCollapsed(false)} title="展开工单列表">
        <span>工单列表</span>
        <b>{tickets.length}</b>
      </button>
    )
  }

  return (
    <aside className="ticket-list">
      <div className="list-head">
        <div className="list-title">
          <h2>待处理工单</h2>
          <button className="icon-btn" onClick={() => setCollapsed(true)} title="收起">
            ‹
          </button>
        </div>

        <input
          className="search"
          placeholder="搜索留言内容 / 客户名…"
          value={keyword}
          onChange={(e) => onKeywordChange(e.target.value)}
        />

        <div className="tabs">
          {FILTERS.map((f) => (
            <button
              key={f.key || 'all'}
              className={`tab ${filter === f.key ? 'active' : ''}`}
              onClick={() => onFilterChange(f.key)}
            >
              {f.label}
              <b>{f.key ? counts[f.key] || 0 : tickets.length}</b>
            </button>
          ))}
        </div>
      </div>

      <div className="list-body">
        {tickets.length === 0 && (
          <div className="list-empty">
            没有符合条件的工单
            <span>试试切换筛选条件，或点右上角「灌入演示数据」</span>
          </div>
        )}

        {tickets.map((t) => (
          <button
            key={t.id}
            className={`ticket-item ${t.id === selectedId ? 'selected' : ''} ${
              t.need_human ? 'needs-attention' : ''
            }`}
            onClick={() => onSelect(t.id)}
          >
            <div className="ti-top">
              <span className="ti-customer">{t.customer}</span>
              <span className="ti-time">{timeAgo(t.created_at)}</span>
            </div>

            <div className="ti-text">{t.text}</div>

            <div className="ti-bottom">
              <IntentDots goals={t.goals} intentColors={intentColors} />
              <StatusPill status={t.status} size="sm" />
            </div>

            {t.goals?.length > 0 && (
              <div className="ti-conf">
                <div className="ti-conf-track">
                  <div
                    className={`ti-conf-fill ${
                      t.confidence >= 0.77 ? 'high' : t.confidence >= 0.62 ? 'mid' : 'low'
                    }`}
                    style={{ width: `${Math.round(t.confidence * 100)}%` }}
                  />
                </div>
                <span>{t.confidence.toFixed(2)}</span>
              </div>
            )}
          </button>
        ))}
      </div>
    </aside>
  )
}
