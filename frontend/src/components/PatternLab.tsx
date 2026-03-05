import { useState, useEffect } from 'react'
import { Search, ChevronRight, ShieldCheck, ShieldX, BarChart3, TrendingUp, TrendingDown, Zap, FlaskConical } from 'lucide-react'
import { scanPattern, getTopPatterns, getPatternPredictionsReality, getSignalEvents, getUsdReversalBins } from '../api'
import api from '../api'
import clsx from 'clsx'

const ASSETS = ['BTC', 'ETH', 'SOL', 'XRP']
const INTERVALS = [5, 15, 60]

function PatternBuilder({ onScan }: { onScan: (result: any) => void }) {
  const [asset, setAsset] = useState('BTC')
  const [interval, setInterval] = useState(5)
  const [pattern, setPattern] = useState<string[]>(['DOWN', 'DOWN', 'DOWN'])
  const [loading, setLoading] = useState(false)

  const toggleAt = (i: number) => {
    const next = [...pattern]
    next[i] = next[i] === 'UP' ? 'DOWN' : 'UP'
    setPattern(next)
  }

  const addStep = () => setPattern(p => [...p, 'DOWN'])
  const removeStep = () => setPattern(p => p.slice(0, -1))

  const handleScan = async () => {
    setLoading(true)
    try {
      const result = await scanPattern(asset, interval, pattern)
      onScan(result)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="bg-panel border border-border rounded-lg p-5">
      <h3 className="text-sm font-semibold text-neutral uppercase tracking-wider mb-4">Pattern Builder</h3>

      <div className="flex gap-3 mb-5">
        <select className="bg-surface border border-border rounded px-3 py-2 text-sm text-white" value={asset} onChange={e => setAsset(e.target.value)}>
          {ASSETS.map(a => <option key={a}>{a}</option>)}
        </select>
        <select className="bg-surface border border-border rounded px-3 py-2 text-sm text-white" value={interval} onChange={e => setInterval(Number(e.target.value))}>
          {INTERVALS.map(i => <option key={i} value={i}>{i}m</option>)}
        </select>
      </div>

      <div className="flex flex-wrap items-center gap-2 mb-4">
        {pattern.map((step, i) => (
          <div key={i} className="flex items-center gap-1">
            {i > 0 && <ChevronRight size={14} className="text-neutral" />}
            <button
              onClick={() => toggleAt(i)}
              className={clsx(
                'px-3 py-2 rounded-lg font-bold text-sm mono transition-all',
                step === 'UP'
                  ? 'bg-up text-white hover:bg-up/80'
                  : 'bg-down text-white hover:bg-down/80'
              )}
            >
              {step}
            </button>
          </div>
        ))}
        <div className="flex gap-1 ml-2">
          <button onClick={addStep} className="w-7 h-7 rounded bg-border text-neutral hover:text-white transition-colors text-lg leading-none">+</button>
          {pattern.length > 2 && (
            <button onClick={removeStep} className="w-7 h-7 rounded bg-border text-neutral hover:text-white transition-colors text-lg leading-none">−</button>
          )}
        </div>
      </div>

      <div className="text-xs text-neutral mb-4">
        Pattern: {pattern.join(' → ')} → <strong className="text-white">?</strong>
      </div>

      <button
        onClick={handleScan}
        disabled={loading}
        className="flex items-center gap-2 bg-accent hover:bg-accent/80 text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
      >
        <Search size={14} />
        {loading ? 'Scanning...' : 'Scan Pattern'}
      </button>
    </div>
  )
}

function PatternResult({ result }: { result: any }) {
  if (result.error) {
    return (
      <div className="bg-panel border border-border rounded-lg p-5">
        <p className="text-neutral">{result.error}</p>
      </div>
    )
  }

  const edge = Math.abs(result.up_pct - 50)
  const leanUp = result.up_pct > 50
  const strong = edge > 10

  return (
    <div className={clsx(
      'bg-panel border rounded-lg p-5',
      strong ? (leanUp ? 'border-up/40' : 'border-down/40') : 'border-border'
    )}>
      <div className="flex items-center justify-between mb-4">
        <div>
          <span className="text-white font-semibold mono">{result.pattern_str}</span>
          <span className="text-neutral text-sm ml-2">→ ?</span>
        </div>
        <span className="text-sm text-neutral">{result.sample_count} samples</span>
      </div>

      <div className="grid grid-cols-2 gap-4 mb-4">
        <div className="bg-up/10 rounded-lg p-3 text-center">
          <div className="text-up text-2xl font-bold mono">{result.up_pct}%</div>
          <div className="text-up text-xs">Next UP ({result.next_up_count})</div>
        </div>
        <div className="bg-down/10 rounded-lg p-3 text-center">
          <div className="text-down text-2xl font-bold mono">{result.down_pct}%</div>
          <div className="text-down text-xs">Next DOWN ({result.next_down_count})</div>
        </div>
      </div>

      {/* Probability bar */}
      <div className="h-2 rounded-full overflow-hidden bg-border">
        <div
          className="h-full bg-up rounded-full transition-all"
          style={{ width: `${result.up_pct}%` }}
        />
      </div>

      {strong && (
        <div className={clsx(
          'mt-3 text-xs px-3 py-2 rounded font-medium',
          leanUp ? 'bg-up/10 text-up' : 'bg-down/10 text-down'
        )}>
          ⚡ {edge.toFixed(1)}% edge — Lean {leanUp ? 'UP' : 'DOWN'}
          {result.sample_count < 30 && ' (low sample count — use caution)'}
        </div>
      )}

      {result.occurrences > 0 && (
        <div className="text-xs text-neutral mt-2">
          Occurred {result.occurrences} times total
        </div>
      )}
    </div>
  )
}

function TopPatternRow({ p }: { p: any }) {
  const leanUp = p.up_pct > 50
  return (
    <tr className="border-b border-border/50 hover:bg-border/30 transition-colors">
      <td className="px-4 py-3 mono text-sm">{p.pattern_str}</td>
      <td className="px-4 py-3 mono text-neutral text-sm">{p.sample_count}</td>
      <td className="px-4 py-3">
        <span className={clsx('mono font-semibold', leanUp ? 'text-up' : 'text-down')}>
          {leanUp ? `${p.up_pct}% UP` : `${p.down_pct}% DOWN`}
        </span>
      </td>
      <td className="px-4 py-3">
        <span className={clsx(
          'text-xs px-2 py-1 rounded',
          p.edge > 15 ? 'bg-accent/20 text-accent' :
          p.edge > 10 ? 'bg-up/10 text-up' : 'text-neutral'
        )}>
          {p.edge.toFixed(1)}%
        </span>
      </td>
    </tr>
  )
}

function MetricPill({ label, value, positive }: { label: string; value: string; positive?: boolean }) {
  return (
    <div className="bg-panel border border-border rounded-lg px-3 py-2">
      <div className="text-[10px] uppercase tracking-wider text-neutral">{label}</div>
      <div className={clsx('mono text-sm font-semibold', positive === true ? 'text-up' : positive === false ? 'text-down' : 'text-white')}>
        {value}
      </div>
    </div>
  )
}

export default function PatternLab() {
  const [asset, setAsset] = useState('BTC')
  const [interval, setIntervalVal] = useState(5)
  const [scanResult, setScanResult] = useState<any>(null)
  const [topPatterns, setTopPatterns] = useState<any[]>([])
  const [loadingTop, setLoadingTop] = useState(false)
  const [predReality, setPredReality] = useState<any>(null)
  const [loadingPredReality, setLoadingPredReality] = useState(false)
  const [signalEvents, setSignalEvents] = useState<any>(null)
  const [signalFilter, setSignalFilter] = useState<string>('')
  const [usdReversal, setUsdReversal] = useState<any>(null)
  const [usdRevAsset, setUsdRevAsset] = useState('BTC')
  const [activeTab, setActiveTab] = useState<'patterns' | 'signals'>('patterns')
  const [binData, setBinData] = useState<Record<string, any>>({})
  const [binAsset, setBinAsset] = useState('BTC')
  const [binInterval, setBinInterval] = useState(5)

  useEffect(() => {
    setLoadingTop(true)
    getTopPatterns(asset, interval, 15)
      .then(d => setTopPatterns(d.top_patterns || []))
      .finally(() => setLoadingTop(false))
  }, [asset, interval])

  useEffect(() => {
    let mounted = true
    const load = () => {
      getSignalEvents({ asset, interval, decision: signalFilter || undefined, limit: 60 })
        .then(d => { if (mounted) setSignalEvents(d) })
        .catch(() => {})
    }
    load()
    const t = setInterval(load, 10000)
    return () => { mounted = false; clearInterval(t) }
  }, [asset, interval, signalFilter])

  useEffect(() => {
    let mounted = true
    const load = () => {
      api.get('/analytics/usd-reversal', { params: { asset: usdRevAsset, interval } })
        .then((r: any) => { if (mounted) setUsdReversal(r.data) })
        .catch(() => {})
    }
    load()
    const t = setInterval(load, 30000)
    return () => { mounted = false; clearInterval(t) }
  }, [usdRevAsset, interval])

  // Load bin data for all assets when Signals tab is active
  useEffect(() => {
    if (activeTab !== 'signals') return
    let mounted = true
    const load = async () => {
      const results: Record<string, any> = {}
      for (const a of ASSETS) {
        try {
          const d = await getUsdReversalBins(a, binInterval)
          results[a] = d
        } catch {}
      }
      if (mounted) setBinData(results)
    }
    load()
    const t = setInterval(load, 60000)
    return () => { mounted = false; clearInterval(t) }
  }, [activeTab, binInterval])

  useEffect(() => {
    let mounted = true
    const load = () => {
      setLoadingPredReality(true)
      getPatternPredictionsReality(asset, interval, 10, 40)
        .then(d => {
          if (mounted) setPredReality(d)
        })
        .finally(() => {
          if (mounted) setLoadingPredReality(false)
        })
    }
    load()
    const timer = setInterval(load, 15000)
    return () => {
      mounted = false
      clearInterval(timer)
    }
  }, [asset, interval])

  return (
    <div className="space-y-6">
      {/* Tab header */}
      <div className="flex items-center gap-1 border-b border-border pb-0">
        <button
          onClick={() => setActiveTab('patterns')}
          className={clsx(
            'flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors -mb-px',
            activeTab === 'patterns'
              ? 'border-accent text-white'
              : 'border-transparent text-neutral hover:text-white'
          )}
        >
          <FlaskConical size={14} /> Pattern Lab
        </button>
        <button
          onClick={() => setActiveTab('signals')}
          className={clsx(
            'flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors -mb-px',
            activeTab === 'signals'
              ? 'border-accent text-white'
              : 'border-transparent text-neutral hover:text-white'
          )}
        >
          <Zap size={14} /> Signals
        </button>
      </div>

      {activeTab === 'patterns' && <div className="space-y-6">

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="space-y-4">
          <PatternBuilder onScan={setScanResult} />
          {scanResult && <PatternResult result={scanResult} />}
        </div>

        <div>
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-neutral uppercase tracking-wider">Top Patterns by Edge</h3>
            <div className="flex gap-2">
              <select className="bg-panel border border-border rounded px-2 py-1 text-xs text-white" value={asset} onChange={e => setAsset(e.target.value)}>
                {ASSETS.map(a => <option key={a}>{a}</option>)}
              </select>
              <select className="bg-panel border border-border rounded px-2 py-1 text-xs text-white" value={interval} onChange={e => setIntervalVal(Number(e.target.value))}>
                {INTERVALS.map(i => <option key={i} value={i}>{i}m</option>)}
              </select>
            </div>
          </div>

          <div className="bg-panel border border-border rounded-lg overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border">
                  {['Pattern', 'Samples', 'Next Outcome', 'Edge'].map(h => (
                    <th key={h} className="px-4 py-3 text-left text-neutral text-xs uppercase tracking-wider">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {loadingTop ? (
                  <tr><td colSpan={4} className="px-4 py-8 text-center text-neutral">Loading patterns...</td></tr>
                ) : topPatterns.length === 0 ? (
                  <tr><td colSpan={4} className="px-4 py-8 text-center text-neutral">No patterns with sufficient samples</td></tr>
                ) : (
                  topPatterns.map((p, i) => <TopPatternRow key={i} p={p} />)
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-neutral uppercase tracking-wider">
            Predictions vs Reality (Top-10 Auto-Trader Patterns)
          </h3>
          {loadingPredReality && <span className="text-xs text-neutral">Refreshing...</span>}
        </div>

        <div className="grid grid-cols-2 md:grid-cols-6 gap-2">
          <MetricPill label="Total Orders" value={`${predReality?.summary?.total_orders ?? 0}`} />
          <MetricPill label="Active" value={`${predReality?.summary?.active_orders ?? 0}`} />
          <MetricPill label="Resolved" value={`${predReality?.summary?.resolved_orders ?? 0}`} />
          <MetricPill label="Win Rate" value={predReality?.summary?.win_rate != null ? `${predReality.summary.win_rate}%` : '—'} positive={(predReality?.summary?.win_rate ?? 0) >= 50} />
          <MetricPill label="Wins / Losses" value={`${predReality?.summary?.wins ?? 0} / ${predReality?.summary?.losses ?? 0}`} positive={(predReality?.summary?.wins ?? 0) >= (predReality?.summary?.losses ?? 0)} />
          <MetricPill label="Realized PnL" value={`$${(predReality?.summary?.realized_pnl ?? 0).toFixed(2)}`} positive={(predReality?.summary?.realized_pnl ?? 0) >= 0} />
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          <div className="bg-panel border border-border rounded-lg overflow-hidden">
            <div className="px-4 py-3 border-b border-border">
              <h4 className="text-sm font-semibold text-white">Top-10 Pattern Performance</h4>
            </div>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border">
                  {['Pattern', 'Pred', 'Resolved', 'W/L', 'Win%', 'PnL'].map(h => (
                    <th key={h} className="px-4 py-2 text-left text-neutral text-xs uppercase tracking-wider">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(predReality?.top_patterns || []).length === 0 ? (
                  <tr><td colSpan={6} className="px-4 py-8 text-center text-neutral">No auto-trade pattern records yet</td></tr>
                ) : (
                  (predReality?.top_patterns || []).map((p: any, i: number) => (
                    <tr key={i} className="border-b border-border/50">
                      <td className="px-4 py-2 mono text-xs">{p.pattern_str}</td>
                      <td className={clsx('px-4 py-2 mono text-xs font-semibold', p.predicted_side === 'UP' ? 'text-up' : 'text-down')}>
                        {p.predicted_side}
                      </td>
                      <td className="px-4 py-2 mono text-xs">{p.resolved}</td>
                      <td className="px-4 py-2 mono text-xs">{p.wins}/{p.losses}</td>
                      <td className={clsx('px-4 py-2 mono text-xs', (p.win_rate ?? 0) >= 50 ? 'text-up' : 'text-down')}>
                        {p.win_rate == null ? '—' : `${p.win_rate}%`}
                      </td>
                      <td className={clsx('px-4 py-2 mono text-xs font-semibold', p.realized_pnl >= 0 ? 'text-up' : 'text-down')}>
                        {p.realized_pnl >= 0 ? '+' : ''}${p.realized_pnl.toFixed(2)}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>

          <div className="bg-panel border border-border rounded-lg overflow-hidden">
            <div className="px-4 py-3 border-b border-border">
              <h4 className="text-sm font-semibold text-white">Recent Prediction Records</h4>
            </div>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border">
                  {['Time', 'Pattern', 'Side', 'Reality', 'PnL'].map(h => (
                    <th key={h} className="px-4 py-2 text-left text-neutral text-xs uppercase tracking-wider">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(predReality?.recent_trades || []).slice(0, 20).map((t: any, i: number) => (
                  <tr key={i} className="border-b border-border/50 hover:bg-border/20 transition-colors">
                    <td className="px-4 py-2 mono text-xs text-neutral whitespace-nowrap">{new Date((t.created_at || 0) * 1000).toLocaleTimeString()}</td>
                    <td className="px-4 py-2 mono text-xs">
                      <div className="flex items-center gap-1.5">
                        <span className={clsx(
                          'text-[9px] px-1 py-0.5 rounded font-bold tracking-wider',
                          t.trigger_type === 'REVERSAL' ? 'bg-yellow-500/20 text-yellow-400' : 'bg-accent/15 text-accent/80'
                        )}>
                          {t.trigger_type === 'REVERSAL' ? '⚡REV' : 'PAT'}
                        </span>
                        <span className="text-white/70">
                          {t.trigger_type === 'REVERSAL' && t.trigger_usd_move != null
                            ? `prev ${t.trigger_usd_move > 0 ? '+' : ''}${Number(t.trigger_usd_move).toFixed(0)}$`
                            : t.pattern_str}
                        </span>
                      </div>
                    </td>
                    <td className={clsx('px-4 py-2 mono text-xs font-semibold', t.predicted_side === 'UP' ? 'text-up' : 'text-down')}>{t.predicted_side}</td>
                    <td className="px-4 py-2">
                      <span className={clsx(
                        'text-[11px] px-2 py-0.5 rounded',
                        t.reality === 'WIN' ? 'bg-up/10 text-up' :
                        t.reality === 'LOSS' ? 'bg-down/10 text-down' :
                        t.reality === 'ACTIVE' ? 'bg-neutral/10 text-neutral' : 'bg-yellow-500/10 text-yellow-400'
                      )}>
                        {t.reality}
                      </span>
                    </td>
                    <td className={clsx('px-4 py-2 mono text-xs font-semibold', t.pnl >= 0 ? 'text-up' : 'text-down')}>
                      {t.pnl >= 0 ? '+' : ''}${t.pnl.toFixed(2)}
                    </td>
                  </tr>
                ))}
                {(predReality?.recent_trades || []).length === 0 && (
                  <tr><td colSpan={5} className="px-4 py-8 text-center text-neutral">No auto-trade records available</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* USD Move Reversal Analysis */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <BarChart3 size={14} className="text-accent" />
            <h3 className="text-sm font-semibold text-neutral uppercase tracking-wider">
              USD Move Reversal Analysis
            </h3>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-neutral">Asset:</span>
            <select
              className="bg-panel border border-border rounded px-2 py-1 text-xs text-white"
              value={usdRevAsset}
              onChange={e => setUsdRevAsset(e.target.value)}
            >
              {ASSETS.map(a => <option key={a}>{a}</option>)}
            </select>
            <select
              className="bg-panel border border-border rounded px-2 py-1 text-xs text-white"
              value={interval}
              onChange={e => setIntervalVal(Number(e.target.value))}
            >
              {INTERVALS.map(i => <option key={i} value={i}>{i}m</option>)}
            </select>
          </div>
        </div>

        <div className="bg-panel border border-border rounded-lg overflow-hidden">
          <div className="px-4 py-3 border-b border-border">
            <p className="text-xs text-neutral">
              If the previous window moved up/down by $X — how often does the next window reverse vs continue?
              <span className="ml-2 text-accent">Reversal = next window goes opposite direction.</span>
            </p>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                {['Prev Move', 'Threshold', 'Samples', 'Reversed', 'Continued', 'P(Reversal)', 'P(Continue)', 'Signal'].map(h => (
                  <th key={h} className="px-3 py-2 text-left text-neutral text-xs uppercase tracking-wider">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {!usdReversal || (usdReversal.rows || []).length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-4 py-8 text-center text-neutral">
                    No data yet — building as markets resolve (needs ~50 windows with spot data)
                  </td>
                </tr>
              ) : (
                (usdReversal.rows || []).map((r: any, i: number) => {
                  const isReversal = r.signal === 'REVERSAL'
                  const isCont = r.signal === 'CONTINUATION'
                  return (
                    <tr key={i} className="border-b border-border/40 hover:bg-border/20 transition-colors">
                      <td className="px-3 py-2">
                        <span className={clsx(
                          'flex items-center gap-1 text-xs font-semibold',
                          r.prev_direction === 'up' ? 'text-up' : 'text-down'
                        )}>
                          {r.prev_direction === 'up' ? <TrendingUp size={11} /> : <TrendingDown size={11} />}
                          {r.prev_direction === 'up' ? 'UP' : 'DOWN'}
                        </span>
                      </td>
                      <td className="px-3 py-2 mono text-xs font-bold text-white">
                        ${r.usd_threshold}+
                      </td>
                      <td className="px-3 py-2 mono text-xs text-neutral">{r.total}</td>
                      <td className="px-3 py-2 mono text-xs text-down">{r.reversed}</td>
                      <td className="px-3 py-2 mono text-xs text-up">{r.continued}</td>
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-2">
                          <div className="w-16 h-1.5 bg-border rounded-full overflow-hidden">
                            <div
                              className={clsx('h-full rounded-full', isReversal ? 'bg-down' : 'bg-border')}
                              style={{ width: `${r.p_reversal_pct}%` }}
                            />
                          </div>
                          <span className={clsx('mono text-xs font-semibold', isReversal ? 'text-down' : 'text-neutral')}>
                            {r.p_reversal_pct}%
                          </span>
                        </div>
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex items-center gap-2">
                          <div className="w-16 h-1.5 bg-border rounded-full overflow-hidden">
                            <div
                              className={clsx('h-full rounded-full', isCont ? 'bg-up' : 'bg-border')}
                              style={{ width: `${r.p_continuation_pct}%` }}
                            />
                          </div>
                          <span className={clsx('mono text-xs font-semibold', isCont ? 'text-up' : 'text-neutral')}>
                            {r.p_continuation_pct}%
                          </span>
                        </div>
                      </td>
                      <td className="px-3 py-2">
                        <span className={clsx(
                          'text-xs px-2 py-0.5 rounded font-semibold',
                          isReversal ? 'bg-down/15 text-down' :
                          isCont     ? 'bg-up/15 text-up' :
                          'bg-border text-neutral'
                        )}>
                          {r.signal}
                        </span>
                      </td>
                    </tr>
                  )
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-neutral uppercase tracking-wider">
            Signal Decision Log (Intelligence Layer)
          </h3>
          <div className="flex items-center gap-3">
            <div className="flex gap-1">
              {['', 'APPROVE', 'REJECT'].map(f => (
                <button
                  key={f}
                  onClick={() => setSignalFilter(f)}
                  className={clsx(
                    'text-xs px-3 py-1 rounded transition-colors',
                    signalFilter === f
                      ? 'bg-accent text-white'
                      : 'bg-border text-neutral hover:text-white'
                  )}
                >
                  {f || 'All'}
                </button>
              ))}
            </div>
            <div className="flex items-center gap-3 text-xs text-neutral">
              <span className="flex items-center gap-1">
                <ShieldCheck size={12} className="text-up" />
                {signalEvents?.totals?.APPROVE ?? 0} approved
              </span>
              <span className="flex items-center gap-1">
                <ShieldX size={12} className="text-down" />
                {signalEvents?.totals?.REJECT ?? 0} rejected
              </span>
            </div>
          </div>
        </div>

        <div className="bg-panel border border-border rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                {['Time', 'Asset', 'Pattern', 'Side', 'Decision', 'EV', 'Conf', 'Spread', 'Imbalance', 'Reasons'].map(h => (
                  <th key={h} className="px-3 py-2 text-left text-neutral text-xs uppercase tracking-wider">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(signalEvents?.events || []).length === 0 ? (
                <tr><td colSpan={10} className="px-4 py-8 text-center text-neutral">No signal events yet — starts logging once a pattern match is found</td></tr>
              ) : (
                (signalEvents?.events || []).slice(0, 30).map((e: any, i: number) => (
                  <tr key={i} className="border-b border-border/40 hover:bg-border/20 transition-colors">
                    <td className="px-3 py-2 mono text-xs text-neutral whitespace-nowrap">
                      {new Date((e.created_at || 0) * 1000).toLocaleTimeString()}
                    </td>
                    <td className="px-3 py-2 mono text-xs font-semibold text-white">
                      {e.asset} <span className="text-neutral font-normal">{e.interval_minutes}m</span>
                    </td>
                    <td className="px-3 py-2 mono text-xs">{e.pattern_str}</td>
                    <td className={clsx('px-3 py-2 mono text-xs font-bold', e.predicted_side === 'UP' ? 'text-up' : 'text-down')}>
                      {e.predicted_side}
                    </td>
                    <td className="px-3 py-2">
                      <span className={clsx(
                        'flex items-center gap-1 text-xs px-2 py-0.5 rounded w-fit',
                        e.decision === 'APPROVE'
                          ? 'bg-up/10 text-up'
                          : 'bg-down/10 text-down'
                      )}>
                        {e.decision === 'APPROVE'
                          ? <ShieldCheck size={10} />
                          : <ShieldX size={10} />
                        }
                        {e.decision}
                      </span>
                    </td>
                    <td className={clsx('px-3 py-2 mono text-xs', (e.ev_score ?? 0) >= 0 ? 'text-up' : 'text-down')}>
                      {e.ev_score != null ? e.ev_score.toFixed(3) : '—'}
                    </td>
                    <td className="px-3 py-2 mono text-xs">
                      <div className="flex items-center gap-1">
                        <div
                          className="h-1.5 rounded-full bg-accent/30 w-12 overflow-hidden"
                          title={`${e.confidence}%`}
                        >
                          <div
                            className="h-full bg-accent rounded-full"
                            style={{ width: `${Math.min(100, e.confidence ?? 0)}%` }}
                          />
                        </div>
                        <span className="text-neutral">{e.confidence?.toFixed(0) ?? '—'}</span>
                      </div>
                    </td>
                    <td className={clsx('px-3 py-2 mono text-xs', (e.spread_cents ?? 0) > 10 ? 'text-yellow-400' : 'text-neutral')}>
                      {e.spread_cents != null ? `${e.spread_cents.toFixed(1)}¢` : '—'}
                    </td>
                    <td className={clsx('px-3 py-2 mono text-xs',
                      e.depth_imbalance > 0.1 ? 'text-up' : e.depth_imbalance < -0.1 ? 'text-down' : 'text-neutral'
                    )}>
                      {e.depth_imbalance != null ? e.depth_imbalance.toFixed(2) : '—'}
                    </td>
                    <td className="px-3 py-2 text-xs text-neutral max-w-xs">
                      {(e.reject_reasons || []).length > 0
                        ? <span className="text-yellow-400/80">{e.reject_reasons.join(', ')}</span>
                        : <span className="text-neutral/40">—</span>
                      }
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
      </div>} {/* end patterns tab */}

      {activeTab === 'signals' && (
        <div className="space-y-6">
          {/* Controls */}
          <div className="flex items-center gap-3">
            <span className="text-sm text-neutral">Interval:</span>
            {[5, 15, 60].map(i => (
              <button key={i}
                onClick={() => setBinInterval(i)}
                className={clsx('text-xs px-3 py-1.5 rounded transition-colors',
                  binInterval === i ? 'bg-accent text-white' : 'bg-panel border border-border text-neutral hover:text-white'
                )}
              >{i}m</button>
            ))}
            <span className="text-xs text-neutral ml-4">Showing P(reversal) per USD move window — <span className="text-accent">green = continuation edge, red = reversal edge</span></span>
          </div>

          {/* Grid: one card per asset */}
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
            {ASSETS.map(asset => {
              const d = binData[asset]
              const upBins = (d?.bins || []).filter((b: any) => b.direction === 'up')
              const dnBins = (d?.bins || []).filter((b: any) => b.direction === 'down')
              const binSize = d?.bin_size ?? '—'
              const total = d?.total_samples ?? 0

              return (
                <div key={asset} className="bg-panel border border-border rounded-lg overflow-hidden">
                  <div className="flex items-center justify-between px-4 py-3 border-b border-border">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-bold text-white">{asset}</span>
                      <span className="text-xs text-neutral">{binInterval}m · ${binSize} bins · {total} samples</span>
                    </div>
                    <span className="text-xs text-neutral">P(reversal) in next window</span>
                  </div>

                  {!d || total === 0 ? (
                    <div className="px-4 py-8 text-center text-neutral text-sm">
                      No data yet — accumulates as spot prices are captured
                    </div>
                  ) : (
                    <div className="grid grid-cols-2 divide-x divide-border">
                      {/* UP moves */}
                      <div>
                        <div className="flex items-center gap-1 px-3 py-2 border-b border-border/50 bg-up/5">
                          <TrendingUp size={11} className="text-up" />
                          <span className="text-xs font-semibold text-up">Prev window UP</span>
                        </div>
                        <div className="divide-y divide-border/30">
                          {upBins.length === 0 ? (
                            <div className="px-3 py-4 text-xs text-neutral text-center">No data</div>
                          ) : upBins.map((b: any, i: number) => (
                            <BinRow key={i} bin={b} />
                          ))}
                        </div>
                      </div>
                      {/* DOWN moves */}
                      <div>
                        <div className="flex items-center gap-1 px-3 py-2 border-b border-border/50 bg-down/5">
                          <TrendingDown size={11} className="text-down" />
                          <span className="text-xs font-semibold text-down">Prev window DOWN</span>
                        </div>
                        <div className="divide-y divide-border/30">
                          {dnBins.length === 0 ? (
                            <div className="px-3 py-4 text-xs text-neutral text-center">No data</div>
                          ) : dnBins.map((b: any, i: number) => (
                            <BinRow key={i} bin={b} />
                          ))}
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>

          {/* Note about sample growth */}
          <div className="bg-panel border border-border rounded-lg px-4 py-3 text-xs text-neutral">
            <span className="text-accent font-medium">Data grows automatically</span> — each resolved market with spot price coverage adds a sample.
            At ~50+ samples per bin the signal becomes statistically meaningful.
            Currently {Object.values(binData).reduce((s: number, d: any) => s + (d?.total_samples || 0), 0)} total samples across all assets.
          </div>
        </div>
      )}
    </div>
  )
}

function BinRow({ bin }: { bin: any }) {
  const p = bin.p_reversal_pct ?? 0
  const isRev = bin.signal === 'REVERSAL'
  const isCont = bin.signal === 'CONTINUATION'
  const barColor = isRev ? 'bg-down' : isCont ? 'bg-up' : 'bg-neutral/30'
  const textColor = isRev ? 'text-down' : isCont ? 'text-up' : 'text-neutral'

  return (
    <div className="flex items-center gap-2 px-3 py-2 hover:bg-border/20 transition-colors">
      {/* Bin label */}
      <span className="mono text-xs text-white w-20 shrink-0">{bin.bin_label}</span>

      {/* Bar */}
      <div className="flex-1 h-3 bg-border rounded-full overflow-hidden relative">
        <div
          className={clsx('h-full rounded-full transition-all', barColor)}
          style={{ width: `${Math.min(100, p)}%` }}
        />
        {/* 50% marker */}
        <div className="absolute top-0 left-1/2 h-full w-px bg-white/20" />
      </div>

      {/* Stats */}
      <div className="flex items-center gap-1.5 shrink-0 w-28 text-right justify-end">
        <span className={clsx('mono text-xs font-bold w-10', textColor)}>
          {bin.p_reversal_pct != null ? `${bin.p_reversal_pct}%` : '—'}
        </span>
        <span className="text-[10px] text-neutral">rev</span>
        <span className="text-[10px] text-neutral/50">n={bin.n}</span>
      </div>
    </div>
  )
}
