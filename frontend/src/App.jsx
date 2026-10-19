import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from './api.js'
import StatsBoard from './components/StatsBoard.jsx'
import TicketList from './components/TicketList.jsx'
import TicketDetail from './components/TicketDetail.jsx'
import NewTicketModal from './components/NewTicketModal.jsx'
import EvalPanel from './components/EvalPanel.jsx'
import { Toast } from './components/atoms.jsx'

const FALLBACK_META = {
  intents: [],
  confidence_threshold: 0.62,
  status_flow: ['pending', 'asking', 'ready', 'done'],
}

export default function App() {
  const [meta, setMeta] = useState(FALLBACK_META)
  const [tickets, setTickets] = useState([])
  const [stats, setStats] = useState(null)
  const [evalReport, setEvalReport] = useState(null)

  const [selectedId, setSelectedId] = useState(null)
  const [filter, setFilter] = useState('')
  const [keyword, setKeyword] = useState('')
  const [showModal, setShowModal] = useState(false)
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState(null)

  const notify = useCallback((message, type = 'info') => {
    setToast({ message, type })
    setTimeout(() => setToast(null), 3200)
  }, [])

  // 统一的错误兜底：任何接口异常都要变成用户能看懂的一句话，而不是白屏
  const guard = useCallback(
    async (fn, okMessage) => {
      setBusy(true)
      try {
        const result = await fn()
        if (okMessage) notify(okMessage, 'success')
        return result
      } catch (err) {
        notify(`操作失败：${err.message}`, 'error')
        return null
      } finally {
        setBusy(false)
      }
    },
    [notify],
  )

  const refresh = useCallback(async () => {
    try {
      const [list, st] = await Promise.all([api.listTickets(), api.stats()])
      setTickets(list)
      setStats(st)
      setSelectedId((prev) => (prev && list.some((t) => t.id === prev) ? prev : list[0]?.id ?? null))
    } catch (err) {
      notify(`加载失败：${err.message}。请确认后端已启动（默认 http://127.0.0.1:8000）`, 'error')
    }
  }, [notify])

  useEffect(() => {
    api
      .meta()
      .then(setMeta)
      .catch(() => {})
    api
      .lastEval()
      .then((r) => r?.total_samples && setEvalReport(r))
      .catch(() => {})
    refresh()
    // 看板做轻量轮询，模拟"驾驶舱"的实时感；20s 足够，又不会打爆后端
    const timer = setInterval(refresh, 20000)
    return () => clearInterval(timer)
  }, [refresh])

  const intentColors = useMemo(
    () => Object.fromEntries((meta.intents || []).map((i) => [i.name, i.color])),
    [meta],
  )

  const visibleTickets = useMemo(() => {
    const kw = keyword.trim().toLowerCase()
    return tickets.filter((t) => {
      if (filter && t.status !== filter) return false
      if (!kw) return true
      return (
        t.text.toLowerCase().includes(kw) ||
        t.customer.toLowerCase().includes(kw) ||
        t.intents.some((i) => i.toLowerCase().includes(kw))
      )
    })
  }, [tickets, filter, keyword])

  const selected = useMemo(
    () => tickets.find((t) => t.id === selectedId) || null,
    [tickets, selectedId],
  )

  // 列表和详情共用同一份 tickets 数据，操作成功后整体刷新一次即可。
  // 没有做局部乐观更新 —— 工单量级下多一次 refetch 换取"绝不出现界面和数据不一致"，更划算。
  const refreshAndSelect = async (ticketId) => {
    await refresh()
    if (ticketId) setSelectedId(ticketId)
  }

  const handleConfirm = (goalId, payload) =>
    guard(async () => {
      const updated = await api.confirm({ ticket_id: selectedId, goal_id: goalId, ...payload })
      await refreshAndSelect(updated.id)
      return updated
    }, payload.action === 'reject' ? '已驳回，目标退回待识别' : '已确认，目标状态已推进')

  const handleAdvance = (goalId, target) =>
    guard(async () => {
      const updated = await api.advance({ ticket_id: selectedId, goal_id: goalId, target })
      await refreshAndSelect(updated.id)
      return updated
    }, '状态已推进')

  const handleDispatch = () =>
    guard(async () => {
      await api.dispatch(selectedId)
      await refreshAndSelect(selectedId)
    }, '已分拣该工单下所有就绪目标')

  const handleReanalyze = () =>
    guard(async () => {
      const updated = await api.analyze({ ticket_id: selectedId })
      await refreshAndSelect(updated.id)
    }, '已重新跑一遍两阶段识别')

  const handleDelete = () =>
    guard(async () => {
      await api.deleteTicket(selectedId)
      await refreshAndSelect(null)
    }, '工单已删除')

  const handleCreate = async (payload) => {
    const created = await guard(() => api.createTicket(payload), '工单已创建并完成识别')
    if (created) {
      setShowModal(false)
      await refreshAndSelect(created.id)
    }
  }

  const handleProbe = (text) => guard(() => api.analyze({ text }), '')

  const handleSeed = () =>
    guard(async () => {
      await api.seed()
      await refreshAndSelect(null)
    }, '演示数据已灌入')

  const handleDispatchAll = () =>
    guard(async () => {
      const r = await api.dispatchAll()
      await refresh()
      return r
    }, '所有就绪目标已一次性分拣')

  const handleRunEval = () =>
    guard(async () => {
      const report = await api.runEval(meta.llm_enabled ? 'llm' : 'rule')
      setEvalReport(report)
      return report
    }, '评测完成')

  const engineLabel = meta.llm_enabled ? 'DeepSeek 大模型' : '规则引擎（未配置 API Key）'

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="logo">单易分</div>
          <div className="brand-sub">智能工单分拣台 · 基于目标识别</div>
        </div>

        <div className="topbar-status">
          <span className="engine-badge" title="分析引擎">
            {engineLabel}
          </span>
          <span className={`engine-badge ${meta.langgraph ? '' : 'badge-warn'}`}>
            LangGraph {meta.langgraph ? '已启用' : '未安装'}
          </span>
          <span className="engine-badge">阈值 {meta.confidence_threshold}</span>
        </div>

        <div className="topbar-actions">
          <button className="btn btn-ghost" disabled={busy} onClick={handleSeed}>
            灌入演示数据
          </button>
          <button className="btn btn-ghost" disabled={busy} onClick={handleDispatchAll}>
            一键全部分拣
          </button>
          <button className="btn btn-ghost" disabled={busy} onClick={refresh}>
            刷新
          </button>
          <button className="btn btn-primary" onClick={() => setShowModal(true)}>
            + 新建工单
          </button>
        </div>
      </header>

      <main className="main">
        <StatsBoard stats={stats} threshold={meta.confidence_threshold} />

        <div className="workbench">
          <TicketList
            tickets={visibleTickets}
            selectedId={selectedId}
            onSelect={setSelectedId}
            filter={filter}
            onFilterChange={setFilter}
            keyword={keyword}
            onKeywordChange={setKeyword}
            intentColors={intentColors}
          />

          <TicketDetail
            ticket={selected}
            meta={meta}
            threshold={meta.confidence_threshold}
            busy={busy}
            onConfirm={handleConfirm}
            onAdvance={handleAdvance}
            onDispatch={handleDispatch}
            onReanalyze={handleReanalyze}
            onDelete={handleDelete}
          />
        </div>

        <EvalPanel
          report={evalReport}
          busy={busy}
          onRun={handleRunEval}
          engine={meta.llm_enabled ? 'llm' : 'rule'}
        />
      </main>

      <footer className="footer">
        React + Vite + ECharts · FastAPI + LangGraph + Pydantic + SQLite
        {meta.llm_enabled ? ' · DeepSeek API' : ' · 规则引擎降级模式'}
      </footer>

      {showModal && (
        <NewTicketModal
          meta={meta}
          busy={busy}
          onClose={() => setShowModal(false)}
          onCreate={handleCreate}
          onProbe={handleProbe}
        />
      )}

      <Toast toast={toast} />
    </div>
  )
}
