import { useEffect, useState } from 'react'
import { ConfidenceBar, IntentTag, StatusPill, statusLabel } from './atoms.jsx'

const ACTIONS_BY_STATUS = {
  pending: [
    { key: 'approve', label: '确认采纳', primary: true, hint: '按 Agent 的判定放行' },
    { key: 'correct', label: '修正后确认', hint: '改意图 / 补槽位' },
    { key: 'reject', label: '驳回重识别', danger: true, hint: '判定为误识别' },
  ],
  asking: [
    { key: 'correct', label: '补齐信息并确认', primary: true, hint: '人工代填用户缺失的信息' },
    { key: 'approve', label: '标记为无需追问', hint: '槽位其实可以缺省' },
  ],
  ready: [
    { key: 'dispatch', label: '分拣执行', primary: true, hint: 'ready → done' },
    { key: 'correct', label: '撤回修正', hint: '改回 asking 或纠正判定' },
  ],
  done: [
    { key: 'revoke', label: '撤销分拣', hint: 'done → ready' },
  ],
}

/** 槽位表格：值 + 来源 + 置信度。来源很重要，"用户说的"和"模型猜的"待遇不同。 */
function SlotTable({ goal, editingSlots, onSlotChange }) {
  if (!goal.slots?.length) {
    return <div className="slot-empty">该意图不需要结构化槽位</div>
  }
  return (
    <div className="slot-table">
      {goal.slots.map((slot) => {
        const missing = slot.required && !slot.value
        return (
          <div key={slot.name} className={`slot-row ${missing ? 'missing' : ''}`}>
            <div className="slot-name">
              {slot.name}
              {slot.required && <i className="req" title="必需槽位">必填</i>}
            </div>

            <div className="slot-value">
              {editingSlots ? (
                <input
                  value={editingSlots[slot.name] ?? slot.value ?? ''}
                  placeholder={missing ? '请补充…' : ''}
                  onChange={(e) => onSlotChange(slot.name, e.target.value)}
                />
              ) : slot.value ? (
                <code>{slot.value}</code>
              ) : (
                <span className="slot-none">未识别</span>
              )}
            </div>

            <div className="slot-meta">
              {slot.value && !editingSlots && (
                <>
                  <span className={`src src-${slot.source}`}>
                    {slot.source === 'human' ? '人工' : slot.source === 'inferred' ? '推断' : '原文'}
                  </span>
                  <span className="slot-conf">{slot.confidence.toFixed(2)}</span>
                </>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

export default function GoalCard({ goal, index, meta, threshold, onConfirm, onAdvance, busy }) {
  const [editing, setEditing] = useState(false)
  const [draftIntent, setDraftIntent] = useState(goal.intent)
  const [draftSlots, setDraftSlots] = useState({})

  // 只依赖 updated_at —— 它只在后端真正改了目标时才会变。
  // 如果依赖 goal.slots（每次轮询都是新数组），看板 20 秒一次刷新
  // 就会把坐席正在填的修正表单直接冲掉，这在人工复核场景里是不可接受的。
  useEffect(() => {
    setDraftIntent(goal.intent)
    setDraftSlots(
      Object.fromEntries((goal.slots || []).map((s) => [s.name, s.value ?? ''])),
    )
    setEditing(false)
  }, [goal.id, goal.updated_at])

  const intentColor = (name) =>
    meta.intents.find((i) => i.name === name)?.color || '#94a3b8'

  // 多标签：把 intent_scores 排序后全部展示，主意图高亮、次意图淡化
  const ranked = Object.entries(goal.intent_scores || {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 4)

  const primary = goal.intent
  const alternatives = ranked.filter(([name]) => name !== primary)

  const actions = ACTIONS_BY_STATUS[goal.status] || []
  const missing = (goal.slots || []).filter((s) => s.required && !s.value).map((s) => s.name)

  const runAction = (key) => {
    if (key === 'dispatch') {
      onAdvance(goal.id, 'done')
      return
    }
    if (key === 'revoke') {
      onAdvance(goal.id, 'ready')
      return
    }
    if (key === 'correct') {
      if (!editing) {
        setEditing(true) // 第一次点进入编辑态，第二次才提交
        return
      }
      onConfirm(goal.id, {
        action: 'correct',
        intent: draftIntent,
        slots: draftSlots,
      })
      setEditing(false)
      return
    }
    setEditing(false)
    onConfirm(goal.id, { action: key })
  }

  return (
    <article
      className={`goal-card ${goal.need_human ? 'goal-warn' : ''} ${
        goal.status === 'done' ? 'goal-done' : ''
      }`}
    >
      <header className="goal-head">
        <div className="goal-idx">目标 {index + 1}</div>
        <div className="goal-head-right">
          {goal.need_human && <span className="warn-badge">需人工确认</span>}
          <StatusPill status={goal.status} size="sm" />
        </div>
      </header>

      <div className="goal-segment" title="从用户原话中拆出的片段">
        “{goal.segment}”
      </div>

      {/* ---- 意图标签（多标签） ---- */}
      <div className="goal-intents">
        <IntentTag name={primary} color={intentColor(primary)} score={goal.intent_scores?.[primary]} />
        {alternatives.map(([name, score]) => (
          <IntentTag key={name} name={name} color={intentColor(name)} score={score} muted />
        ))}
        {alternatives.length > 0 && <span className="alt-hint">次选意图</span>}
      </div>

      {/* ---- 置信度 ---- */}
      <div className="goal-conf">
        <ConfidenceBar value={goal.confidence} threshold={threshold} />
      </div>

      {/* ---- 双引擎证据：这是"可解释性"的落点 ---- */}
      <div className="goal-evidence">
        <span className={`evo ${goal.engine === 'deepseek' ? 'evo-llm' : 'evo-rule'}`}>
          {goal.engine === 'deepseek' ? 'DeepSeek' : goal.engine === 'human' ? '人工' : '规则引擎'}
        </span>
        {goal.llm_confidence != null && (
          <span className="evo-item">LLM {goal.llm_confidence.toFixed(2)}</span>
        )}
        {goal.rule_confidence != null && (
          <span className="evo-item">规则 {goal.rule_confidence.toFixed(2)}</span>
        )}
        {/* 只有真的调了大模型，"双引擎一致"才有意义 —— 单跑规则时它永远是自己跟自己比 */}
        {goal.llm_confidence != null && goal.agreement === true && (
          <span className="evo-item evo-ok">双引擎一致</span>
        )}
        {goal.llm_confidence != null && goal.agreement === false && (
          <span className="evo-item evo-bad">双引擎分歧</span>
        )}
      </div>

      {/* ---- 状态为 ready/pending 且槽位不齐时的追问 ---- */}
      {goal.questions?.length > 0 && goal.status === 'asking' && (
        <div className="goal-questions">
          <div className="q-title">Agent 待追问用户：</div>
          <ul>
            {goal.questions.map((q, i) => (
              <li key={i}>{q}</li>
            ))}
          </ul>
        </div>
      )}

      {/* ---- 判定原因 ---- */}
      {goal.reason && (
        <div className={`goal-reason ${goal.need_human ? 'is-warn' : ''}`}>↳ {goal.reason}</div>
      )}

      {/* ---- 槽位 ---- */}
      <div className="goal-slots">
        <div className="section-label">
          槽位抽取
          <span className="section-note">
            {missing.length > 0 ? `缺 ${missing.join('、')}` : `填充率 ${Math.round(goal.slot_fill_rate * 100)}%`}
          </span>
        </div>
        <SlotTable
          goal={goal}
          editingSlots={editing ? draftSlots : null}
          onSlotChange={(name, value) => setDraftSlots((prev) => ({ ...prev, [name]: value }))}
        />
      </div>

      {/* ---- 修正意图的下拉 ---- */}
      {editing && (
        <div className="edit-intent">
          <label>人工修正意图</label>
          <select value={draftIntent} onChange={(e) => setDraftIntent(e.target.value)}>
            {meta.intents.map((i) => (
              <option key={i.name} value={i.name}>
                {i.name}
              </option>
            ))}
          </select>
        </div>
      )}

      {/* ---- 动作区 ---- */}
      <footer className="goal-actions">
        {actions.map((a) => (
          <button
            key={a.key}
            className={`btn ${a.primary ? 'btn-primary' : ''} ${a.danger ? 'btn-danger' : ''} ${
              a.key === 'correct' && editing ? 'btn-confirm-edit' : ''
            }`}
            disabled={busy}
            title={a.hint}
            onClick={() => runAction(a.key)}
          >
            {a.key === 'correct' && editing ? '提交修正' : a.label}
          </button>
        ))}
        {editing && (
          <button className="btn btn-ghost" disabled={busy} onClick={() => setEditing(false)}>
            取消
          </button>
        )}
      </footer>
    </article>
  )
}
