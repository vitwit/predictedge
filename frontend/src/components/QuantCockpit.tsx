import { useEffect, useState, useCallback } from 'react'
import {
  TrendingUp, TrendingDown, Minus, Activity, Shield,
  ShieldAlert, Zap, BarChart2, RefreshCw, AlertTriangle,
  CheckCircle, XCircle, Brain, Target, Layers
} from 'lucide-react'
import clsx from 'clsx'
import {
  getQuantRegime, getEdgeHealth, getPortfolioState,
  getSignalTape, resetCircuitBreaker, getLlmDecisions,
  getHotspot, getImpulse
} from '../api'

// ── Types ──────────────────────────────────────────────────────────────────

interface RegimeInfo {
  regime: string
  net_move_30s_pct: number
  net_move_60s_pct: number
  net_move_120s_pct: number
  range_pct: number
  oscillations: number
  confidence: number
  signal_weights: Record<string, number>
}

interface EdgeStat {
  signal_type: string
  asset: string
  interval_minutes: number
  win_count: number
  loss_count: number
  win_rate: number
  avg_ev: number
  is_active: number
  last_updated: number
}

interface PortfolioState {
  open_position_count: number
  total_invested: number
  realized_pnl: number
  consecutive_losses: number
  peak_balance: number
  current_balance: number
  drawdown_pct: number
  circuit_breaker_active: boolean
  circuit_breaker_remaining_s: number
  max_concurrent: number
  loss_limit: number
  drawdown_limit_pct: number
}

interface SignalEvent {
  id: number
  slug: string
  asset: string
  interval_minutes: number
  pattern_str: string
  predicted_side: string
  win_rate: number
  confidence: number
  ev_score: number
  decision: string
  reject_reasons: string
  created_at: number
  order_size?: number
}

interface LlmDecision {
  slug: string
  asset: string
  interval_minutes: number
  model: string
  decision: string
  reasoning: string
  confidence_in: number
  latency_ms: number
  created_at: number
}

// ── Helpers ────────────────────────────────────────────────────────────────

const REGIME_COLORS: Record<string, string> = {
  TREND: 'text-green-400',
  HIGH_VOL: 'text-yellow-400',
  MEAN_REVERT: 'text-blue-400',
  CHOP: 'text-red-400',
  NORMAL: 'text-neutral-400',
}
const REGIME_BG: Record<string, string> = {
  TREND: 'bg-green-500/10 border-green-500/30',
  HIGH_VOL: 'bg-yellow-500/10 border-yellow-500/30',
  MEAN_REVERT: 'bg-blue-500/10 border-blue-500/30',
  CHOP: 'bg-red-500/10 border-red-500/30',
  NORMAL: 'bg-neutral-500/10 border-neutral-500/30',
}

const fmtPct = (v: number) => (v >= 0 ? '+' : '') + v.toFixed(3) + '%'
const fmtTs = (ts: number) => new Date(ts * 1000).toLocaleTimeString()
const ASSETS = ['BTC', 'ETH', 'SOL', 'XRP']

function RegimeIcon({ regime }: { regime: string }) {
  if (regime === 'TREND') return <TrendingUp size={14} className="text-green-400" />
  if (regime === 'HIGH_VOL') return <AlertTriangle size={14} className="text-yellow-400" />
  if (regime === 'MEAN_REVERT') return <Activity size={14} className="text-blue-400" />
  if (regime === 'CHOP') return <Minus size={14} className="text-red-400" />
  return <Activity size={14} className="text-neutral-400" />
}

// ── Sub-components ─────────────────────────────────────────────────────────

function RegimePanel({ regimes }: { regimes: Record<string, RegimeInfo> }) {
  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold text-neutral-400 uppercase tracking-wider flex items-center gap-1.5">
        <Layers size={12} /> Market Regime
      </h3>
      <div className="grid grid-cols-2 gap-2">
        {ASSETS.map(asset => {
          const r = regimes[asset]
          if (!r) return null
          return (
            <div key={asset} className={clsx('rounded-lg border p-3', REGIME_BG[r.regime] || REGIME_BG.NORMAL)}>
              <div className="flex items-center justify-between mb-1">
                <span className="text-white font-semibold text-sm">{asset}</span>
                <div className="flex items-center gap-1">
                  <RegimeIcon regime={r.regime} />
                  <span className={clsx('text-xs font-bold', REGIME_COLORS[r.regime])}>{r.regime}</span>
                </div>
              </div>
              <div className="grid grid-cols-3 gap-1 text-xs">
                <div>
                  <div className="text-neutral-500">30s</div>
                  <div className={clsx('font-mono', r.net_move_30s_pct >= 0 ? 'text-green-400' : 'text-red-400')}>
                    {fmtPct(r.net_move_30s_pct)}
                  </div>
                </div>
                <div>
                  <div className="text-neutral-500">60s</div>
                  <div className={clsx('font-mono', r.net_move_60s_pct >= 0 ? 'text-green-400' : 'text-red-400')}>
                    {fmtPct(r.net_move_60s_pct)}
                  </div>
                </div>
                <div>
                  <div className="text-neutral-500">conf</div>
                  <div className="text-white font-mono">{(r.confidence * 100).toFixed(0)}%</div>
                </div>
              </div>
              <div className="mt-1 text-xs text-neutral-500">
                range {r.range_pct.toFixed(3)}% · {r.oscillations} osc
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}


function PortfolioPanel({ state, onReset }: { state: PortfolioState | null; onReset: () => void }) {
  if (!state) return null
  const ddColor = state.drawdown_pct > 10 ? 'text-red-400' : state.drawdown_pct > 5 ? 'text-yellow-400' : 'text-green-400'
  const pnlColor = state.realized_pnl >= 0 ? 'text-green-400' : 'text-red-400'

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold text-neutral-400 uppercase tracking-wider flex items-center gap-1.5">
        <Shield size={12} /> Portfolio Risk
      </h3>
      <div className="rounded-lg border border-border bg-panel p-4 space-y-3">
        {state.circuit_breaker_active && (
          <div className="flex items-center gap-2 bg-red-500/10 border border-red-500/30 rounded-lg px-3 py-2">
            <ShieldAlert size={16} className="text-red-400 flex-shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="text-red-400 font-bold text-sm">Circuit Breaker Active</div>
              <div className="text-red-300 text-xs">{Math.ceil(state.circuit_breaker_remaining_s / 60)}min remaining</div>
            </div>
            <button
              onClick={onReset}
              className="text-xs bg-red-500/20 hover:bg-red-500/40 text-red-300 px-2 py-1 rounded transition-colors"
            >
              Reset
            </button>
          </div>
        )}

        <div className="grid grid-cols-2 gap-3">
          <MetricCard label="Open Positions" value={`${state.open_position_count}/${state.max_concurrent}`}
            color={state.open_position_count >= state.max_concurrent ? 'text-yellow-400' : 'text-white'} />
          <MetricCard label="Invested" value={`$${state.total_invested.toFixed(2)}`} color="text-white" />
          <MetricCard label="Realized P&L" value={(state.realized_pnl >= 0 ? '+' : '') + `$${state.realized_pnl.toFixed(2)}`} color={pnlColor} />
          <MetricCard label="Drawdown" value={`${state.drawdown_pct.toFixed(1)}%`} color={ddColor} />
          <MetricCard label="Loss Streak" value={`${state.consecutive_losses}/${state.loss_limit}`}
            color={state.consecutive_losses >= state.loss_limit - 1 ? 'text-red-400' : state.consecutive_losses > 0 ? 'text-yellow-400' : 'text-green-400'} />
          <MetricCard label="Peak Balance" value={`$${state.peak_balance.toFixed(2)}`} color="text-neutral-300" />
        </div>

        {/* Drawdown gauge */}
        <div>
          <div className="flex justify-between text-xs text-neutral-500 mb-1">
            <span>Drawdown</span>
            <span>{state.drawdown_pct.toFixed(1)}% / {state.drawdown_limit_pct}%</span>
          </div>
          <div className="h-1.5 bg-neutral-800 rounded-full overflow-hidden">
            <div
              className={clsx('h-full rounded-full transition-all', state.drawdown_pct > 15 ? 'bg-red-500' : state.drawdown_pct > 8 ? 'bg-yellow-500' : 'bg-green-500')}
              style={{ width: `${Math.min(100, (state.drawdown_pct / state.drawdown_limit_pct) * 100)}%` }}
            />
          </div>
        </div>
      </div>
    </div>
  )
}


function MetricCard({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div className="bg-surface rounded-lg p-2.5">
      <div className="text-xs text-neutral-500 mb-0.5">{label}</div>
      <div className={clsx('font-mono font-bold text-sm', color)}>{value}</div>
    </div>
  )
}


function EdgeHealthPanel({ stats }: { stats: EdgeStat[] }) {
  if (!stats.length) return (
    <div className="text-neutral-500 text-sm text-center py-4">No edge stats yet</div>
  )

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold text-neutral-400 uppercase tracking-wider flex items-center gap-1.5">
        <BarChart2 size={12} /> Edge Health by Signal
      </h3>
      <div className="rounded-lg border border-border overflow-hidden">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-surface border-b border-border">
              <th className="text-left px-3 py-2 text-neutral-400 font-medium">Signal</th>
              <th className="text-left px-3 py-2 text-neutral-400 font-medium">Asset</th>
              <th className="text-right px-3 py-2 text-neutral-400 font-medium">Win%</th>
              <th className="text-right px-3 py-2 text-neutral-400 font-medium">W/L</th>
              <th className="text-right px-3 py-2 text-neutral-400 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {stats.map((s, i) => {
              const total = s.win_count + s.loss_count
              const wr = total > 0 ? (s.win_count / total * 100) : 0
              return (
                <tr key={i} className={clsx('border-b border-border/50', i % 2 === 0 ? 'bg-panel' : 'bg-surface')}>
                  <td className="px-3 py-1.5 font-mono text-neutral-300">{s.signal_type}</td>
                  <td className="px-3 py-1.5 text-white">{s.asset} {s.interval_minutes}m</td>
                  <td className="px-3 py-1.5 text-right">
                    <span className={clsx('font-mono font-bold', wr >= 55 ? 'text-green-400' : wr >= 45 ? 'text-yellow-400' : 'text-red-400')}>
                      {wr.toFixed(1)}%
                    </span>
                  </td>
                  <td className="px-3 py-1.5 text-right text-neutral-400 font-mono">
                    {s.win_count}/{s.loss_count}
                  </td>
                  <td className="px-3 py-1.5 text-right">
                    {s.is_active
                      ? <CheckCircle size={12} className="text-green-400 inline" />
                      : <XCircle size={12} className="text-red-400 inline" />}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}


function SignalTape({ events }: { events: SignalEvent[] }) {
  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold text-neutral-400 uppercase tracking-wider flex items-center gap-1.5">
        <Activity size={12} /> Signal Tape
      </h3>
      <div className="space-y-1.5 max-h-80 overflow-y-auto pr-1 custom-scroll">
        {events.length === 0 && (
          <div className="text-neutral-500 text-sm text-center py-4">No signals yet</div>
        )}
        {events.map(ev => {
          const reasons: string[] = (() => {
            try { return JSON.parse(ev.reject_reasons || '[]') } catch { return [] }
          })()
          const approved = ev.decision === 'APPROVE'
          return (
            <div key={ev.id} className={clsx(
              'rounded-lg border px-3 py-2 text-xs',
              approved ? 'border-green-500/30 bg-green-500/5' : 'border-red-500/20 bg-red-500/5'
            )}>
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  {approved
                    ? <CheckCircle size={12} className="text-green-400 flex-shrink-0" />
                    : <XCircle size={12} className="text-red-400 flex-shrink-0" />}
                  <span className="text-white font-semibold">{ev.asset} {ev.interval_minutes}m</span>
                  <span className={clsx('font-bold', ev.predicted_side === 'UP' ? 'text-green-400' : 'text-red-400')}>
                    {ev.predicted_side === 'UP' ? '↑' : '↓'} {ev.predicted_side}
                  </span>
                  <span className="text-neutral-500 truncate">{ev.pattern_str}</span>
                </div>
                <div className="flex items-center gap-3 flex-shrink-0">
                  <span className="text-neutral-400 font-mono">WR {ev.win_rate?.toFixed(1)}%</span>
                  <span className="text-neutral-400 font-mono">conf {ev.confidence?.toFixed(0)}</span>
                  <span className="text-neutral-500">{fmtTs(ev.created_at)}</span>
                </div>
              </div>
              {!approved && reasons.length > 0 && (
                <div className="mt-1 text-neutral-500 text-xs truncate">
                  {reasons[0]}
                </div>
              )}
              {approved && ev.order_size && (
                <div className="mt-1 text-green-300 text-xs">
                  Size: ${ev.order_size} USDC
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}


function LlmDecisionPanel({ decisions }: { decisions: LlmDecision[] }) {
  if (!decisions.length) return null
  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold text-neutral-400 uppercase tracking-wider flex items-center gap-1.5">
        <Brain size={12} /> LLM Gate Decisions
      </h3>
      <div className="space-y-1.5 max-h-52 overflow-y-auto pr-1">
        {decisions.map((d, i) => (
          <div key={i} className={clsx(
            'rounded-lg border px-3 py-2 text-xs',
            d.decision === 'APPROVE' ? 'border-green-500/30 bg-green-500/5' : 'border-red-500/20 bg-red-500/5'
          )}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Brain size={11} className="text-purple-400" />
                <span className="text-white font-semibold">{d.asset} {d.interval_minutes}m</span>
                <span className={clsx('font-bold text-xs', d.decision === 'APPROVE' ? 'text-green-400' : 'text-red-400')}>
                  {d.decision}
                </span>
              </div>
              <div className="flex items-center gap-2 text-neutral-500">
                <span>{d.latency_ms}ms</span>
                <span>{fmtTs(d.created_at)}</span>
              </div>
            </div>
            <div className="mt-1 text-neutral-400 italic">&ldquo;{d.reasoning}&rdquo;</div>
          </div>
        ))}
      </div>
    </div>
  )
}


function HotspotImpulsePanel() {
  const [data, setData] = useState<Record<string, { hotspot: Record<string, unknown>; impulse: Record<string, unknown> }>>({})
  const [interval, setInterval_] = useState(5)

  useEffect(() => {
    const fetchAll = async () => {
      const results: Record<string, { hotspot: Record<string, unknown>; impulse: Record<string, unknown> }> = {}
      for (const asset of ASSETS) {
        try {
          const [hs, imp] = await Promise.all([
            getHotspot(asset, interval),
            getImpulse(asset, interval),
          ])
          results[asset] = { hotspot: hs, impulse: imp }
        } catch {
          results[asset] = { hotspot: { active: false }, impulse: { active: false } }
        }
      }
      setData(results)
    }
    fetchAll()
    const t = setInterval(fetchAll, 10000)
    return () => clearInterval(t)
  }, [interval])

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-semibold text-neutral-400 uppercase tracking-wider flex items-center gap-1.5">
          <Zap size={12} /> Hotspot & Impulse
        </h3>
        <div className="flex gap-1">
          {[5, 15].map(iv => (
            <button
              key={iv}
              onClick={() => setInterval_(iv)}
              className={clsx('text-xs px-2 py-0.5 rounded', interval === iv ? 'bg-accent text-white' : 'bg-surface text-neutral-400 hover:text-white')}
            >
              {iv}m
            </button>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2">
        {ASSETS.map(asset => {
          const d = data[asset]
          if (!d) return null
          const hs = d.hotspot as { active?: boolean; zone_center?: number; zone_lo?: number; zone_hi?: number; dwell_seconds?: number; dominant_side?: string; confidence?: number }
          const imp = d.impulse as { active?: boolean; move_cents?: number; direction?: string; continuation_probability?: number; reversal_probability?: number; signal_type?: string }
          return (
            <div key={asset} className="rounded-lg border border-border bg-panel p-2.5 text-xs space-y-1.5">
              <div className="font-bold text-white">{asset}</div>

              {/* Hotspot */}
              <div className={clsx('rounded px-2 py-1', hs?.active ? 'bg-yellow-500/10 border border-yellow-500/20' : 'bg-surface')}>
                <div className="flex items-center gap-1 mb-0.5">
                  <Target size={10} className={hs?.active ? 'text-yellow-400' : 'text-neutral-600'} />
                  <span className={hs?.active ? 'text-yellow-400 font-semibold' : 'text-neutral-500'}>
                    Hotspot {hs?.active ? 'ACTIVE' : 'inactive'}
                  </span>
                </div>
                {hs?.active && (
                  <div className="text-yellow-300">
                    Zone {hs.zone_lo?.toFixed(0)}–{hs.zone_hi?.toFixed(0)}¢ · {hs.dwell_seconds}s · {hs.dominant_side} bias
                  </div>
                )}
              </div>

              {/* Impulse */}
              <div className={clsx('rounded px-2 py-1', imp?.active ? 'bg-purple-500/10 border border-purple-500/20' : 'bg-surface')}>
                <div className="flex items-center gap-1 mb-0.5">
                  <Zap size={10} className={imp?.active ? 'text-purple-400' : 'text-neutral-600'} />
                  <span className={imp?.active ? 'text-purple-400 font-semibold' : 'text-neutral-500'}>
                    Impulse {imp?.active ? 'ACTIVE' : 'inactive'}
                  </span>
                </div>
                {imp?.active && (
                  <div className="text-purple-300">
                    {imp.move_cents?.toFixed(0)}¢ {imp.direction} · cont {((imp.continuation_probability || 0) * 100).toFixed(0)}% · rev {((imp.reversal_probability || 0) * 100).toFixed(0)}%
                  </div>
                )}
                {imp?.active && imp.signal_type !== 'NONE' && (
                  <div className={clsx('font-bold mt-0.5', imp.signal_type === 'IMPULSE_CONTINUATION' ? 'text-green-400' : 'text-red-400')}>
                    → {imp.signal_type === 'IMPULSE_CONTINUATION' ? 'CONTINUATION' : 'REVERSAL'} signal
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}


// ── Main QuantCockpit ──────────────────────────────────────────────────────

export default function QuantCockpit() {
  const [regimes, setRegimes] = useState<Record<string, RegimeInfo>>({})
  const [edgeStats, setEdgeStats] = useState<EdgeStat[]>([])
  const [portfolio, setPortfolio] = useState<PortfolioState | null>(null)
  const [signalTape, setSignalTape] = useState<SignalEvent[]>([])
  const [llmDecisions, setLlmDecisions] = useState<LlmDecision[]>([])
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const [activeTab, setActiveTab] = useState<'overview' | 'tape' | 'edge' | 'llm'>('overview')
  const [resetting, setResetting] = useState(false)

  const fetchAll = useCallback(async () => {
    try {
      const [regData, edgeData, portData, tapeData, llmData] = await Promise.allSettled([
        getQuantRegime(),
        getEdgeHealth(),
        getPortfolioState(),
        getSignalTape(100),
        getLlmDecisions(30),
      ])

      if (regData.status === 'fulfilled') setRegimes(regData.value)
      if (edgeData.status === 'fulfilled') setEdgeStats(edgeData.value.stats || [])
      if (portData.status === 'fulfilled') setPortfolio(portData.value)
      if (tapeData.status === 'fulfilled') setSignalTape(tapeData.value.events || [])
      if (llmData.status === 'fulfilled') setLlmDecisions(llmData.value.decisions || [])

      setLastUpdated(new Date())
    } catch (e) {
      console.error('QuantCockpit fetch failed:', e)
    }
  }, [])

  useEffect(() => {
    fetchAll()
    const t = setInterval(fetchAll, 10000)
    return () => clearInterval(t)
  }, [fetchAll])

  const handleReset = async () => {
    setResetting(true)
    try {
      await resetCircuitBreaker()
      await fetchAll()
    } finally {
      setResetting(false)
    }
  }

  const approveCount = signalTape.filter(e => e.decision === 'APPROVE').length
  const rejectCount = signalTape.filter(e => e.decision === 'REJECT').length
  const approveRate = signalTape.length > 0 ? (approveCount / signalTape.length * 100).toFixed(0) : '—'

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white flex items-center gap-2">
            <Target size={20} className="text-accent" />
            Quant Cockpit
          </h1>
          <p className="text-neutral-400 text-sm mt-0.5">
            Intelligence · Risk · Execution · Edge Health
          </p>
        </div>
        <div className="flex items-center gap-3">
          {lastUpdated && (
            <span className="text-xs text-neutral-500">
              Updated {lastUpdated.toLocaleTimeString()}
            </span>
          )}
          <button
            onClick={fetchAll}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface border border-border text-neutral-400 hover:text-white text-xs transition-colors"
          >
            <RefreshCw size={12} />
            Refresh
          </button>
        </div>
      </div>

      {/* Summary bar */}
      <div className="grid grid-cols-4 gap-3">
        <div className="rounded-lg border border-border bg-panel p-3">
          <div className="text-xs text-neutral-500">Signal Approve Rate</div>
          <div className="text-lg font-bold text-white font-mono">{approveRate}%</div>
          <div className="text-xs text-neutral-600">{approveCount} of {signalTape.length}</div>
        </div>
        <div className={clsx('rounded-lg border p-3', portfolio?.circuit_breaker_active ? 'bg-red-500/10 border-red-500/30' : 'bg-panel border-border')}>
          <div className="text-xs text-neutral-500">Risk State</div>
          <div className={clsx('text-lg font-bold font-mono', portfolio?.circuit_breaker_active ? 'text-red-400' : 'text-green-400')}>
            {portfolio?.circuit_breaker_active ? '⛔ PAUSED' : '✓ ACTIVE'}
          </div>
          <div className="text-xs text-neutral-600">
            {portfolio ? `${portfolio.open_position_count}/${portfolio.max_concurrent} positions` : '—'}
          </div>
        </div>
        <div className="rounded-lg border border-border bg-panel p-3">
          <div className="text-xs text-neutral-500">Realized P&L</div>
          <div className={clsx('text-lg font-bold font-mono', (portfolio?.realized_pnl || 0) >= 0 ? 'text-green-400' : 'text-red-400')}>
            {portfolio ? ((portfolio.realized_pnl >= 0 ? '+' : '') + '$' + portfolio.realized_pnl.toFixed(2)) : '—'}
          </div>
          <div className="text-xs text-neutral-600">drawdown {portfolio?.drawdown_pct?.toFixed(1) || 0}%</div>
        </div>
        <div className="rounded-lg border border-border bg-panel p-3">
          <div className="text-xs text-neutral-500">LLM Decisions</div>
          <div className="text-lg font-bold text-purple-400 font-mono">{llmDecisions.length}</div>
          <div className="text-xs text-neutral-600">
            {llmDecisions.filter(d => d.decision === 'APPROVE').length} approve ·{' '}
            {llmDecisions.filter(d => d.decision === 'REJECT').length} reject
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-border pb-1">
        {([
          { id: 'overview', label: 'Overview', icon: Activity },
          { id: 'tape', label: 'Signal Tape', icon: Layers },
          { id: 'edge', label: 'Edge Health', icon: BarChart2 },
          { id: 'llm', label: 'LLM Gate', icon: Brain },
        ] as const).map(tab => {
          const Icon = tab.icon
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={clsx(
                'flex items-center gap-1.5 px-4 py-2 text-sm rounded-t-lg transition-colors',
                activeTab === tab.id
                  ? 'text-accent border-b-2 border-accent -mb-px'
                  : 'text-neutral-400 hover:text-white'
              )}
            >
              <Icon size={14} />
              {tab.label}
            </button>
          )
        })}
      </div>

      {activeTab === 'overview' && (
        <div className="grid grid-cols-2 gap-6">
          <div className="space-y-6">
            <RegimePanel regimes={regimes} />
            <PortfolioPanel state={portfolio} onReset={handleReset} />
          </div>
          <div className="space-y-6">
            <HotspotImpulsePanel />
          </div>
        </div>
      )}

      {activeTab === 'tape' && (
        <SignalTape events={signalTape} />
      )}

      {activeTab === 'edge' && (
        <EdgeHealthPanel stats={edgeStats} />
      )}

      {activeTab === 'llm' && (
        <div className="space-y-4">
          <div className="bg-panel border border-border rounded-lg p-4 text-sm text-neutral-300">
            <div className="flex items-center gap-2 mb-2">
              <Brain size={16} className="text-purple-400" />
              <span className="font-semibold text-white">About the LLM Gate</span>
            </div>
            <p>
              The LLM gate is called for <span className="text-purple-300 font-mono">borderline decisions</span> (confidence 42–62%).
              It synthesizes all quantitative signals with market context to make a final APPROVE/REJECT call.
              High confidence (&gt;62%) and low confidence (&lt;42%) decisions bypass the LLM entirely.
            </p>
          </div>
          <LlmDecisionPanel decisions={llmDecisions} />
          {llmDecisions.length === 0 && (
            <div className="text-center py-8 text-neutral-500 text-sm">
              No LLM decisions yet. The gate is called only for borderline confidence (42–62%) signals.
              <br />Set OPENROUTER_API_KEY in .env to enable.
            </div>
          )}
        </div>
      )}
    </div>
  )
}
