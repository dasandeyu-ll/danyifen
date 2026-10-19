import { statusLabel } from './atoms.jsx'

const FLOW = ['pending', 'asking', 'ready', 'done']

/**
 * 目标生命周期可视化。
 *
 * 只画一条线是不够的 —— 关键是标注出「当前位置」和「允许往哪走」。
 * 允许的下一步从后端下发（goal.next_statuses），
 * 前端不复制一份迁移规则，保证"能点的按钮"和"后端会接受的请求"永远一致。
 */
export function StateMachine({ goals, ticketStatus }) {
  return (
    <div className="sm-wrap">
      {goals.map((goal, idx) => {
        const currentIdx = FLOW.indexOf(goal.status)
        return (
          <div className="sm-row" key={goal.id}>
            <span className="sm-label" title={goal.segment}>
              目标 {idx + 1}
            </span>
            <div className="sm-track">
              {FLOW.map((state, i) => {
                const isCurrent = state === goal.status
                const isPast = i < currentIdx
                return (
                  <div key={state} className="sm-node-group">
                    <div
                      className={`sm-node ${isCurrent ? 'current' : ''} ${isPast ? 'past' : ''}`}
                      title={isCurrent ? '当前状态' : ''}
                    >
                      <span className="sm-dot" />
                      <span className="sm-text">{statusLabel(state)}</span>
                    </div>
                    {i < FLOW.length - 1 && (
                      <div className={`sm-link ${i < currentIdx ? 'past' : ''}`} />
                    )}
                  </div>
                )
              })}
            </div>
            <span className="sm-next" title="下一步允许迁移到的状态">
              → {(goal.next_statuses || []).map(statusLabel).join(' / ') || '终态'}
            </span>
          </div>
        )
      })}
      {goals.length === 0 && <div className="sm-empty">尚未识别，无状态可展示</div>}
      {ticketStatus === 'done' && <div className="sm-ticket-done">✓ 该工单所有目标均已分拣完成</div>}
    </div>
  )
}

const NODE_LABEL = {
  split: '拆分',
  classify: '分类',
  extract: '抽槽',
  verify: '校验',
  ask: '追问',
  ready: '就绪',
  human: '人工',
}

/** Agent 执行链路：把 LangGraph 的每个节点执行过程摊开给运营看。 */
export function TraceTimeline({ trace, engine }) {
  if (!trace?.length) return null
  const total = trace.reduce((sum, s) => sum + (s.ms || 0), 0)

  return (
    <div className="trace">
      <div className="trace-head">
        <span>Agent 执行链路</span>
        <span className="trace-engine">{engine === 'deepseek' ? 'DeepSeek 大模型' : '规则引擎'}</span>
        <span className="trace-total">总耗时 {total} ms</span>
      </div>
      <ol className="trace-list">
        {trace.map((step, i) => (
          <li key={`${step.node}-${i}`} className="trace-item">
            <span className={`trace-node node-${step.node}`}>{NODE_LABEL[step.node] || step.node}</span>
            <div className="trace-body">
              <div className="trace-label">
                {step.label}
                {step.ms > 0 && <b className="trace-ms">{step.ms} ms</b>}
              </div>
              <div className="trace-detail">{step.detail}</div>
            </div>
          </li>
        ))}
      </ol>
    </div>
  )
}
