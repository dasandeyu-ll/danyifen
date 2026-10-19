const METRICS = [
  { key: 'intent_accuracy', label: '意图准确率', fmt: 'pct' },
  { key: 'segmentation_accuracy', label: '多目标拆分准确率', fmt: 'pct' },
  { key: 'slot_f1', label: '槽位 F1', fmt: 'pct' },
  { key: 'slot_precision', label: '槽位精确率', fmt: 'pct' },
  { key: 'slot_recall', label: '槽位召回率', fmt: 'pct' },
  { key: 'escalation_recall', label: '转人工召回', fmt: 'pct' },
  { key: 'ask_recall', label: '追问召回', fmt: 'pct' },
  { key: 'false_escalation_rate', label: '误转人工率', fmt: 'pct', invert: true },
]

const TAG_LABEL = {
  single: '单目标完整表达',
  multi: '一句话两个目标',
  paraphrase: '改写留出集（未调优）',
  missing_slot: '缺必需槽位',
}

export default function EvalPanel({ report, busy, onRun, engine }) {
  return (
    <section className="eval-panel">
      <div className="eval-head">
        <div>
          <h3>自建评测集</h3>
          <p className="eval-desc">
            200 条中文工单。关键在于分组：前 4 组是按表达式配比生成的调优集，
            最后 30 条是<strong>刻意避开规则词典用词的改写留出集</strong>，
            用来量化「关键词匹配的天花板」。
          </p>
        </div>
        <div className="eval-actions">
          <span className="engine-badge">{engine === 'llm' ? 'DeepSeek' : '规则引擎'}</span>
          <button className="btn btn-primary" disabled={busy} onClick={onRun}>
            {busy ? '评测中…' : report ? '重新评测' : '运行评测'}
          </button>
        </div>
      </div>

      {report ? (
        <>
          <div className="eval-metrics">
            {METRICS.map((m) => (
              <div key={m.key} className="eval-metric">
                <span>{m.label}</span>
                <b className={m.invert && report[m.key] > 0.05 ? 'warn' : ''}>
                  {((report[m.key] ?? 0) * 100).toFixed(1)}%
                </b>
              </div>
            ))}
          </div>

          <div className="eval-table">
            <div className="et-head">
              <span>分组</span>
              <span>样本数</span>
              <span>意图准确率</span>
              <span>槽位 F1</span>
            </div>
            {(report.by_tag || []).map((row) => (
              <div key={row.tag} className={`et-row ${row.tag === 'paraphrase' ? 'highlight' : ''}`}>
                <span>{TAG_LABEL[row.tag] || row.tag}</span>
                <span>{row.samples}</span>
                <span>
                  {row.intent_accuracy == null ? '—' : `${(row.intent_accuracy * 100).toFixed(1)}%`}
                </span>
                <span>{row.slot_f1 == null ? '—' : `${(row.slot_f1 * 100).toFixed(1)}%`}</span>
              </div>
            ))}
          </div>

          <div className="eval-note">
            调优集接近满分、留出集明显落后 —— 这个落差不是 bug，而是把规则引擎的局限量化了出来。
            在 `{report.engine}` 引擎下共 {report.total_samples} 条样本，
            意图错误会记入错例（当前 {report.errors?.length || 0} 条）。
          </div>
        </>
      ) : (
        <div className="eval-placeholder">
          还没有评测结果，点右上角「运行评测」跑一次（规则引擎约 3 秒）。
        </div>
      )}
    </section>
  )
}
