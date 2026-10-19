import { useState } from 'react'
import { StatusPill } from './atoms.jsx'

const SAMPLES = [
  '订单号 SF20240715001，我买的那台显示器屏幕有坏点，想申请退货退款，另外顺便问下发票怎么开。',
  '快递显示两天前就签收了，但我根本没收到货，订单号 JD7788990011，帮我查一下顺便投诉这个快递。',
  '那个东西怎么又出问题了，我说不清楚，反正就是很烦，你们自己看着办吧。',
]

export default function NewTicketModal({ meta, busy, onClose, onCreate, onProbe }) {
  const [text, setText] = useState('')
  const [customer, setCustomer] = useState('匿名用户')
  const [channel, setChannel] = useState('在线客服')
  const [priority, setPriority] = useState('中')
  const [probe, setProbe] = useState(null)

  const submit = async () => {
    if (text.trim().length < 2) return
    await onCreate({ text: text.trim(), customer, channel, priority, auto_analyze: true })
  }

  const runProbe = async () => {
    if (text.trim().length < 2) return
    const result = await onProbe(text.trim())
    setProbe(result)
  }

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>新建工单</h3>
          <button className="icon-btn" onClick={onClose}>
            ×
          </button>
        </div>

        <div className="modal-body">
          <textarea
            className="ta"
            rows={5}
            autoFocus
            placeholder="粘贴用户留言，例如：订单号 XXX，我要退货，另外投诉一下物流…"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />

          <div className="samples">
            {SAMPLES.map((s, i) => (
              <button key={i} className="sample" onClick={() => setText(s)}>
                示例 {i + 1}
              </button>
            ))}
          </div>

          <div className="modal-row">
            <input value={customer} placeholder="客户名" onChange={(e) => setCustomer(e.target.value)} />
            <select value={channel} onChange={(e) => setChannel(e.target.value)}>
              {['在线客服', 'APP', '电话', '小程序', '微博'].map((c) => (
                <option key={c}>{c}</option>
              ))}
            </select>
            <select value={priority} onChange={(e) => setPriority(e.target.value)}>
              {['低', '中', '高'].map((p) => (
                <option key={p}>{p}</option>
              ))}
            </select>
          </div>

          {probe && (
            <div className="probe">
              <div className="probe-head">
                试算结果（未入库）
                <button className="icon-btn" onClick={() => setProbe(null)}>
                  ×
                </button>
              </div>
              <div className="probe-goals">
                {probe.goals.map((g, i) => (
                  <div key={g.id} className="probe-goal">
                    <div className="probe-top">
                      <b>目标 {i + 1}</b>
                      <StatusPill status={g.status} size="sm" />
                      <span className="probe-conf">{g.confidence.toFixed(2)}</span>
                      {g.need_human && <span className="warn-badge">需人工确认</span>}
                    </div>
                    <div className="probe-seg">“{g.segment}”</div>
                    <div className="probe-intent" style={{ color: g.intent_color }}>
                      {g.intent}
                    </div>
                    <div className="probe-slots">
                      {(g.slots || [])
                        .filter((s) => s.value)
                        .map((s) => (
                          <span key={s.name} className="probe-chip">
                            {s.name}：{s.value}
                          </span>
                        ))}
                      {(g.missing_required || []).map((s) => (
                        <span key={s} className="probe-chip probe-chip-miss">
                          缺 {s}
                        </span>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        <div className="modal-foot">
          <button className="btn btn-ghost" disabled={busy || !text.trim()} onClick={runProbe}>
            仅试算（不落库）
          </button>
          <button className="btn btn-primary" disabled={busy || !text.trim()} onClick={submit}>
            提交并分析
          </button>
        </div>
      </div>
    </div>
  )
}
