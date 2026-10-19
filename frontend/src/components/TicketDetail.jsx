import { StatusPill, Empty } from './atoms.jsx'
import GoalCard from './GoalCard.jsx'
import { StateMachine, TraceTimeline } from './Pipeline.jsx'

function fmtTime(iso) {
  if (!iso) return '—'
  return iso.slice(0, 19).replace('T', ' ')
}

export default function TicketDetail({
  ticket,
  meta,
  threshold,
  busy,
  onConfirm,
  onAdvance,
  onDispatch,
  onReanalyze,
  onDelete,
}) {
  if (!ticket) {
    return (
      <section className="detail detail-empty">
        <Empty
          icon="🧭"
          title="从左侧选择一个工单"
          desc="右侧会展示 Agent 的识别结果：拆出了几个目标、每个目标的意图标签、抽取到的槽位、置信度，以及完整的执行链路。"
        />
      </section>
    )
  }

  const goals = ticket.goals || []
  const readyCount = goals.filter((g) => g.status === 'ready').length

  return (
    <section className="detail">
      <header className="detail-head">
        <div className="dh-left">
          <div className="dh-title">
            <h2>{ticket.customer}</h2>
            <StatusPill status={ticket.status} />
            <span className={`prio prio-${ticket.priority}`}>{ticket.priority}优先</span>
          </div>
          <div className="dh-meta">
            <span>{ticket.channel}</span>
            <span>·</span>
            <span>{fmtTime(ticket.created_at)}</span>
            <span>·</span>
            <code className="mono">{ticket.id}</code>
          </div>
        </div>

        <div className="dh-actions">
          <button className="btn btn-ghost" disabled={busy} onClick={onReanalyze}>
            重新分析
          </button>
          <button
            className="btn btn-primary"
            disabled={busy || readyCount === 0}
            onClick={onDispatch}
            title={readyCount === 0 ? '没有处于「可执行」状态的目标' : `分拣 ${readyCount} 个目标`}
          >
            一键分拣{readyCount > 0 ? ` (${readyCount})` : ''}
          </button>
          <button className="btn btn-danger" disabled={busy} onClick={onDelete}>
            删除
          </button>
        </div>
      </header>

      <blockquote className="ticket-quote">{ticket.text}</blockquote>

      {goals.length === 0 ? (
        <div className="detail-no-goal">
          这条工单还没跑过 Agent，点右上角「重新分析」即可触发两阶段识别。
        </div>
      ) : (
        <>
          <div className="panel">
            <div className="section-label">目标生命周期</div>
            <StateMachine goals={goals} ticketStatus={ticket.status} />
          </div>

          <div className="goal-grid">
            {goals.map((goal, i) => (
              <GoalCard
                key={goal.id}
                goal={goal}
                index={i}
                meta={meta}
                threshold={threshold}
                busy={busy}
                onConfirm={onConfirm}
                onAdvance={onAdvance}
              />
            ))}
          </div>
        </>
      )}

      <div className="panel">
        <TraceTimeline trace={ticket.trace} engine={ticket.engine} />
      </div>
    </section>
  )
}
