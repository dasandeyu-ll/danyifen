import { useEffect, useRef } from 'react'
import * as echarts from 'echarts'

/**
 * ECharts 的极简 React 封装。
 *
 * 为什么不直接用 echarts-for-react？
 * 因为这里只需要"给 option、渲染、跟随容器缩放"三件事，
 * 自己写 20 行能把生命周期控制得更死，也少一个依赖。
 *
 * 三个必须处理的点：
 *   1. 实例只能 init 一次 —— 用 useRef 持有，不能放进 state，
 *      否则每次 setState 都会重建图表（表现为闪屏 + 内存泄漏）。
 *   2. 容器尺寸变化要 resize —— 用 ResizeObserver 而不是 window.resize，
 *      因为我们有可折叠的侧栏，容器变了但窗口没变。
 *   3. 卸载必须 dispose —— 否则 echarts 内部定时器和 canvas 不会释放。
 */
export default function EChart({ option, height = 260, onEvents }) {
  const containerRef = useRef(null)
  const chartRef = useRef(null)

  useEffect(() => {
    if (!containerRef.current) return undefined
    const chart = echarts.init(containerRef.current, null, { renderer: 'canvas' })
    chartRef.current = chart

    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(containerRef.current)

    return () => {
      observer.disconnect()
      chart.dispose()
      chartRef.current = null
    }
  }, [])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !option) return
    // notMerge=false 让 ECharts 做增量更新，切换筛选时不会闪
    chart.setOption(option, { notMerge: false })
  }, [option])

  useEffect(() => {
    const chart = chartRef.current
    if (!chart || !onEvents) return undefined
    Object.entries(onEvents).forEach(([event, handler]) => chart.on(event, handler))
    return () => {
      Object.keys(onEvents).forEach((event) => chart.off(event))
    }
  }, [onEvents])

  return <div ref={containerRef} style={{ width: '100%', height }} />
}
