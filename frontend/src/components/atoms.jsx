// 一组展示型小组件。放在一个文件里，因为它们都很小、且总是被一起用到。

const STATUS_STYLE = {
  pending: { label: '待识别', className: 'st-pending' },
  asking: { label: '待追问', className: 'st-asking' },
  ready: { label: '可执行', className: 'st-ready' },
  done: { label: '已分拣', className: 'st-done' },
}

export function StatusPill({ status, size = 'md' }) {
  const style = STATUS_STYLE[status] || STATUS_STYLE.pending
  return <span className={`pill ${style.className} pill-${size}`}>{style.label}</span>
}

export function statusLabel(status) {
  return (STATUS_STYLE[status] || STATUS_STYLE.pending).label
}

export function IntentTag({ name, color, score, muted = false }) {
  return (
    <span
      className={`intent-tag ${muted ? 'muted' : ''}`}
      style={{
        color: muted ? 'var(--text-dim)' : color,
        borderColor: muted ? 'var(--border)' : `${color}66`,
        background: muted ? 'transparent' : `${color}1a`,
      }}
    >
      {name}
      {score !== undefined && score !== null && (
        <b className="intent-score">{Math.round(score * 100)}%</b>
      )}
    </span>
  )
}

/**
 * 置信度进度条。
 * 除了长度和颜色，还画了一条"阈值刻度线" —— 让"为什么这条被判成需要人工"
 * 变成一眼可见的事实，而不是让人去猜 0.62 这个数字。
 */
export function ConfidenceBar({ value = 0, threshold = 0.62, showLabel = true }) {
  const pct = Math.max(0, Math.min(1, value)) * 100
  const level = value >= threshold + 0.15 ? 'high' : value >= threshold ? 'mid' : 'low'
  const levelText = { high: '高', mid: '中', low: '低' }[level]

  return (
    <div className="conf-wrap">
      <div className="conf-track">
        <div className={`conf-fill conf-${level}`} style={{ width: `${pct}%` }} />
        <div className="conf-threshold" style={{ left: `${threshold * 100}%` }} title={`阈值 ${threshold}`} />
      </div>
      {showLabel && (
        <div className="conf-meta">
          <span className={`conf-value conf-text-${level}`}>{value.toFixed(2)}</span>
          <span className="conf-level">置信度{levelText}</span>
        </div>
      )}
    </div>
  )
}

export function Field({ label, children, hint }) {
  return (
    <div className="field">
      <label className="field-label">
        {label}
        {hint && <span className="field-hint">{hint}</span>}
      </label>
      {children}
    </div>
  )
}

export function Empty({ icon = '📭', title, desc }) {
  return (
    <div className="empty">
      <div className="empty-icon">{icon}</div>
      <div className="empty-title">{title}</div>
      {desc && <div className="empty-desc">{desc}</div>}
    </div>
  )
}

export function Toast({ toast }) {
  if (!toast) return null
  return <div className={`toast toast-${toast.type || 'info'}`}>{toast.message}</div>
}
