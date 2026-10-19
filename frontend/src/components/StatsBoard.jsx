import { useMemo } from 'react'
import EChart from './EChart.jsx'
import { statusLabel } from './atoms.jsx'

const AXIS_STYLE = {
  axisLine: { lineStyle: { color: '#334155' } },
  axisLabel: { color: '#94a3b8', fontSize: 11 },
  splitLine: { lineStyle: { color: 'rgba(148,163,184,0.12)' } },
}

function KpiCard({ label, value, sub, accent }) {
  return (
    <div className="kpi" style={accent ? { '--kpi-accent': accent } : undefined}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      {sub && <div className="kpi-sub">{sub}</div>}
    </div>
  )
}

/** 意图分布环形图。颜色直接取后端下发的色板，保证和标签颜色一致。 */
function IntentPie({ distribution }) {
  const option = useMemo(
    () => ({
      tooltip: {
        trigger: 'item',
        backgroundColor: 'rgba(15,23,42,0.94)',
        borderColor: '#334155',
        textStyle: { color: '#e2e8f0', fontSize: 12 },
        formatter: (p) =>
          `${p.marker} <b>${p.name}</b><br/>目标数：${p.value} 个（${p.percent}%）<br/>平均置信度：${p.data.avg.toFixed(2)}`,
      },
      legend: {
        type: 'scroll',
        orient: 'vertical',
        right: 0,
        top: 'middle',
        itemWidth: 9,
        itemHeight: 9,
        textStyle: { color: '#cbd5e1', fontSize: 11 },
      },
      series: [
        {
          type: 'pie',
          radius: ['46%', '72%'],
          center: ['34%', '50%'],
          avoidLabelOverlap: true,
          itemStyle: { borderColor: '#0b1220', borderWidth: 2, borderRadius: 4 },
          label: { show: false },
          labelLine: { show: false },
          emphasis: {
            scale: true,
            scaleSize: 6,
            label: {
              show: true,
              formatter: '{b}\n{c} 个',
              color: '#f1f5f9',
              fontSize: 12,
              lineHeight: 17,
            },
          },
          data: distribution.map((item) => ({
            name: item.intent,
            value: item.count,
            avg: item.avg_confidence,
            itemStyle: { color: item.color },
          })),
        },
      ],
    }),
    [distribution],
  )

  if (!distribution.length) return <div className="chart-placeholder">暂无数据</div>
  return <EChart option={option} height={252} />
}

/** 分拣准确率仪表盘。 */
function AccuracyGauge({ accuracy, totalGoals = 0 }) {
  const pct = Math.round((accuracy?.overall ?? 0) * 100)
  // 只有"已经被裁决过"（自动放行 or 人工看过）的目标才计入准确率。
  // 把这个分母显式标出来，否则一个 95% 会让人误以为包含了那 3 个还没人看的目标。
  const adjudicated =
    (accuracy?.auto_ready ?? 0) +
    (accuracy?.human_approved ?? 0) +
    (accuracy?.human_corrected ?? 0) +
    (accuracy?.human_rejected ?? 0)
  const option = useMemo(
    () => ({
      series: [
        {
          type: 'gauge',
          startAngle: 210,
          endAngle: -30,
          min: 0,
          max: 100,
          center: ['50%', '62%'],
          radius: '96%',
          progress: {
            show: true,
            width: 14,
            roundCap: true,
            itemStyle: {
              color: pct >= 90 ? '#34d399' : pct >= 75 ? '#fbbf24' : '#fb7185',
            },
          },
          axisLine: { lineStyle: { width: 14, color: [[1, 'rgba(148,163,184,0.16)']] } },
          pointer: { show: false },
          axisTick: { show: false },
          splitLine: { show: false },
          axisLabel: { show: false },
          anchor: { show: false },
          title: { show: false },
          detail: {
            valueAnimation: true,
            offsetCenter: [0, 0],
            formatter: '{value}%',
            color: '#f1f5f9',
            fontSize: 30,
            fontWeight: 700,
          },
          data: [{ value: pct }],
        },
      ],
    }),
    [pct],
  )

  return (
    <div className="gauge-box">
      <EChart option={option} height={158} />
      <div className="gauge-caption">
        分拣准确率（已裁决 {adjudicated} / {totalGoals} 个目标）
      </div>
      <div className="gauge-breakdown">
        <span>自动分拣 <b>{(accuracy?.auto_rate * 100 || 0).toFixed(1)}%</b></span>
        <span>人工采纳率 <b>{(accuracy?.human_accuracy * 100 || 0).toFixed(1)}%</b></span>
      </div>
    </div>
  )
}

/** 工单状态分布：用堆叠横条，比饼图更容易比较大小。 */
function StatusBars({ distribution, total }) {
  const option = useMemo(
    () => ({
      grid: { left: 8, right: 40, top: 6, bottom: 6, containLabel: true },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        backgroundColor: 'rgba(15,23,42,0.94)',
        borderColor: '#334155',
        textStyle: { color: '#e2e8f0', fontSize: 12 },
      },
      xAxis: { type: 'value', ...AXIS_STYLE, minInterval: 1 },
      yAxis: {
        type: 'category',
        inverse: true,
        data: distribution.map((d) => statusLabel(d.status)),
        axisLine: AXIS_STYLE.axisLine,
        axisLabel: { color: '#cbd5e1', fontSize: 11 },
        axisTick: { show: false },
      },
      series: [
        {
          type: 'bar',
          barWidth: 14,
          itemStyle: {
            borderRadius: [0, 7, 7, 0],
            color: (p) =>
              ['#94a3b8', '#fbbf24', '#38bdf8', '#34d399'][p.dataIndex % 4],
          },
          label: {
            show: true,
            position: 'right',
            color: '#94a3b8',
            fontSize: 11,
            formatter: '{c}',
          },
          data: distribution.map((d) => d.count),
        },
      ],
    }),
    [distribution],
  )

  if (!distribution.length) return <div className="chart-placeholder">暂无数据</div>
  return (
    <>
      <EChart option={option} height={Math.max(120, distribution.length * 34 + 30)} />
      <div className="mini-note">共 {total} 条工单</div>
    </>
  )
}

/** 缺槽位 TOP：告诉运营"用户最常漏说什么"，是流程优化的直接依据。 */
function MissingSlots({ items }) {
  const option = useMemo(
    () => ({
      grid: { left: 8, right: 34, top: 6, bottom: 6, containLabel: true },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        backgroundColor: 'rgba(15,23,42,0.94)',
        borderColor: '#334155',
        textStyle: { color: '#e2e8f0', fontSize: 12 },
      },
      xAxis: { type: 'value', ...AXIS_STYLE, minInterval: 1 },
      yAxis: {
        type: 'category',
        inverse: true,
        data: items.map((i) => i.slot),
        axisLine: AXIS_STYLE.axisLine,
        axisLabel: { color: '#cbd5e1', fontSize: 11 },
        axisTick: { show: false },
      },
      series: [
        {
          type: 'bar',
          barWidth: 14,
          itemStyle: {
            borderRadius: [0, 7, 7, 0],
            color: {
              type: 'linear',
              x: 0,
              y: 0,
              x2: 1,
              y2: 0,
              colorStops: [
                { offset: 0, color: 'rgba(251,191,36,0.45)' },
                { offset: 1, color: '#fbbf24' },
              ],
            },
          },
          label: { show: true, position: 'right', color: '#94a3b8', fontSize: 11 },
          data: items.map((i) => i.count),
        },
      ],
    }),
    [items],
  )

  if (!items.length) return <div className="chart-placeholder">没有缺槽位记录 🎉</div>
  return <EChart option={option} height={Math.max(120, items.length * 32 + 26)} />
}

export default function StatsBoard({ stats, threshold }) {
  if (!stats) return null
  const acc = stats.accuracy || {}

  return (
    <section className="stats-board">
      <div className="kpi-row">
        <KpiCard
          label="今日新增工单"
          value={stats.today_tickets}
          sub={`累计 ${stats.total_tickets} 条`}
          accent="#38bdf8"
        />
        <KpiCard
          label="识别出的目标"
          value={stats.total_goals}
          sub="一句话可能拆出多个目标"
          accent="#22d3ee"
        />
        <KpiCard
          label="平均置信度"
          value={stats.avg_confidence?.toFixed(2) ?? '—'}
          sub={`低于 ${threshold} 自动转人工`}
          accent="#a78bfa"
        />
        <KpiCard
          label="待人工确认"
          value={stats.needs_attention}
          sub={stats.needs_attention > 0 ? '需要坐席介入' : '全部已处理'}
          accent={stats.needs_attention > 0 ? '#fbbf24' : '#34d399'}
        />
      </div>

      <div className="chart-grid">
        <div className="chart-card chart-card-wide">
          <div className="chart-head">
            <h3>意图分布</h3>
            <span className="chart-sub">按目标数统计 · 悬停看平均置信度</span>
          </div>
          <IntentPie distribution={stats.intent_distribution} />
        </div>

        <div className="chart-card">
          <div className="chart-head">
            <h3>质检准确率</h3>
          </div>
          <AccuracyGauge accuracy={acc} totalGoals={stats.total_goals} />
          <div className="acc-detail">
            <div>
              <span>自动放行</span>
              <b>{acc.auto_ready ?? 0}</b>
            </div>
            <div>
              <span>转人工</span>
              <b>{acc.need_human ?? 0}</b>
            </div>
            <div>
              <span>人工修正</span>
              <b className={acc.human_corrected > 0 ? 'warn' : ''}>{acc.human_corrected ?? 0}</b>
            </div>
          </div>
        </div>

        <div className="chart-card">
          <div className="chart-head">
            <h3>工单状态分布</h3>
          </div>
          <StatusBars distribution={stats.status_distribution} total={stats.total_tickets} />
        </div>

        <div className="chart-card">
          <div className="chart-head">
            <h3>缺失槽位 TOP</h3>
            <span className="chart-sub">驱动追问话术优化</span>
          </div>
          <MissingSlots items={stats.top_slots_missing} />
        </div>
      </div>
    </section>
  )
}
