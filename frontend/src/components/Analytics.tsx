import { useState, useEffect } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  Cell, ScatterChart, Scatter, ZAxis, CartesianGrid, Legend, LineChart, Line
} from 'recharts'
import {
  getMomentumStats, getPeakTrough, getEarlyPeriod,
  getHourlyBias, getDailyBias, getSessionStats,
  getTimeRemainingProb, getCorrelationMatrix, getSpotCorrelation
} from '../api'
import clsx from 'clsx'

const ASSETS = ['BTC', 'ETH', 'SOL', 'XRP']
const INTERVALS = [5, 15, 60]
const UP_COLOR = '#22c55e'
const DOWN_COLOR = '#ef4444'
const ACCENT = '#6366f1'

function AssetIntervalSelect({ asset, interval, setAsset, setInterval }: any) {
  return (
    <div className="flex gap-2">
      <select className="bg-surface border border-border rounded px-2 py-1 text-sm text-white" value={asset} onChange={e => setAsset(e.target.value)}>
        {ASSETS.map(a => <option key={a}>{a}</option>)}
      </select>
      <select className="bg-surface border border-border rounded px-2 py-1 text-sm text-white" value={interval} onChange={e => setInterval(Number(e.target.value))}>
        {INTERVALS.map(i => <option key={i} value={i}>{i}m</option>)}
      </select>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-panel border border-border rounded-lg p-5">
      <h3 className="text-sm font-semibold text-white mb-4">{title}</h3>
      {children}
    </div>
  )
}

// ─── Momentum Tab ─────────────────────────────────────────────────────────────

function MomentumTab({ asset, interval }: { asset: string; interval: number }) {
  const [data, setData] = useState<any>(null)
  const [peakTrough, setPeakTrough] = useState<any[]>([])

  useEffect(() => {
    getMomentumStats(asset, interval).then(setData)
    getPeakTrough(asset, interval).then(d => setPeakTrough(d.data || []))
  }, [asset, interval])

  const buckets = data?.buckets || []

  return (
    <div className="space-y-5">
      <Section title="First-30s Spike → Reversal Rate">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                {['Move (0–30s)', 'Samples', 'Reversal %', 'Avg Next Move', 'Signal'].map(h => (
                  <th key={h} className="px-3 py-2 text-left text-neutral text-xs uppercase">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {buckets.map((b: any, i: number) => (
                <tr key={i} className="border-b border-border/50">
                  <td className="px-3 py-2 mono">{b.label}</td>
                  <td className="px-3 py-2 mono text-neutral">{b.total_samples}</td>
                  <td className="px-3 py-2">
                    <span className={clsx('mono font-semibold', b.reversal_pct > 60 ? 'text-accent' : 'text-neutral')}>
                      {b.reversal_pct}%
                    </span>
                  </td>
                  <td className={clsx('px-3 py-2 mono', b.avg_next_move > 0 ? 'text-up' : 'text-down')}>
                    {b.avg_next_move > 0 ? '+' : ''}{(b.avg_next_move * 100).toFixed(1)}¢
                  </td>
                  <td className={clsx('px-3 py-2 text-xs', b.signal.includes('STRONG') ? 'text-accent' : b.signal.includes('FADE') ? 'text-yellow-400' : 'text-neutral')}>
                    {b.signal}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {buckets.length > 0 && (
          <div className="mt-5">
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={buckets}>
                <XAxis dataKey="label" tick={{ fontSize: 11, fill: '#94a3b8' }} />
                <YAxis tick={{ fontSize: 11, fill: '#94a3b8' }} />
                <Tooltip
                  contentStyle={{ background: '#161b27', border: '1px solid #1e2538', borderRadius: 8 }}
                  labelStyle={{ color: '#e2e8f0' }}
                />
                <Bar dataKey="reversal_pct" name="Reversal %" fill={ACCENT} radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </Section>

      <Section title="Peak → Trough Heatmap (False Pump Detector)">
        <div className="space-y-2">
          {peakTrough.length === 0 ? (
            <p className="text-neutral text-sm">No data available</p>
          ) : (
            peakTrough.slice(0, 10).map((pt: any, i: number) => (
              <div key={i} className="flex items-center gap-3">
                <span className="mono text-xs text-neutral w-40">
                  Peak {(pt.peak_min * 100).toFixed(0)}¢ → Trough {(pt.trough_min * 100).toFixed(0)}¢
                </span>
                <div className="flex-1 h-6 bg-border rounded-full overflow-hidden">
                  <div
                    className="h-full bg-down rounded-full transition-all flex items-center justify-end pr-2"
                    style={{ width: `${pt.down_pct}%` }}
                  >
                    <span className="text-xs font-bold text-white">{pt.down_pct}% DOWN</span>
                  </div>
                </div>
                <span className="text-xs text-neutral w-16 text-right">n={pt.sample_count}</span>
                {pt.signal === 'STRONG' && <span className="text-xs text-accent">⚡</span>}
              </div>
            ))
          )}
        </div>
      </Section>
    </div>
  )
}

// ─── Temporal Tab ─────────────────────────────────────────────────────────────

function TemporalTab({ asset, interval }: { asset: string; interval: number }) {
  const [hourly, setHourly] = useState<any[]>([])
  const [daily, setDaily] = useState<any[]>([])
  const [sessions, setSessions] = useState<any[]>([])
  const [timeRemaining, setTimeRemaining] = useState<any[]>([])

  useEffect(() => {
    getHourlyBias(asset, interval).then(d => setHourly(d.data || []))
    getDailyBias(asset, interval).then(d => setDaily(d.data || []))
    getSessionStats(asset, interval).then(d => setSessions(d.data || []))
    getTimeRemainingProb(asset, interval).then(d => setTimeRemaining(d.data || []))
  }, [asset, interval])

  return (
    <div className="space-y-5">
      <Section title="UP Rate by UTC Hour">
        <ResponsiveContainer width="100%" height={200}>
          <BarChart data={hourly}>
            <XAxis dataKey="hour" tick={{ fontSize: 10, fill: '#94a3b8' }} />
            <YAxis domain={[40, 65]} tick={{ fontSize: 10, fill: '#94a3b8' }} tickFormatter={v => `${v}%`} />
            <Tooltip
              contentStyle={{ background: '#161b27', border: '1px solid #1e2538', borderRadius: 8 }}
              formatter={(v: any) => [`${v}%`, 'UP Rate']}
            />
            <Bar dataKey="up_rate" name="UP Rate" radius={[3, 3, 0, 0]}>
              {hourly.map((h: any, i: number) => (
                <Cell key={i} fill={(h.up_rate || 50) > 55 ? UP_COLOR : (h.up_rate || 50) < 45 ? DOWN_COLOR : ACCENT} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </Section>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
        <Section title="Day-of-Week Bias">
          <div className="space-y-2">
            {daily.map((d: any, i: number) => (
              <div key={i} className="flex items-center gap-3">
                <span className="text-sm text-neutral w-24">{d.day}</span>
                <div className="flex-1 h-5 bg-border rounded overflow-hidden">
                  {d.up_rate !== null && (
                    <div
                      className={clsx(
                        'h-full rounded transition-all',
                        d.up_rate > 52 ? 'bg-up' : d.up_rate < 48 ? 'bg-down' : 'bg-neutral'
                      )}
                      style={{ width: `${Math.min(d.up_rate * 2, 100)}%` }}
                    />
                  )}
                </div>
                <span className={clsx('mono text-sm w-12 text-right', d.up_rate > 52 ? 'text-up' : d.up_rate < 48 ? 'text-down' : 'text-neutral')}>
                  {d.up_rate !== null ? `${d.up_rate}%` : '—'}
                </span>
              </div>
            ))}
          </div>
        </Section>

        <Section title="Trading Session Analysis">
          <div className="space-y-3">
            {sessions.map((s: any, i: number) => (
              <div key={i} className="p-3 bg-surface rounded-lg">
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm font-semibold text-white">{s.session}</span>
                  <span className={clsx('mono text-sm font-bold', s.up_rate > 52 ? 'text-up' : s.up_rate < 48 ? 'text-down' : 'text-neutral')}>
                    {s.up_rate !== null ? `${s.up_rate}%` : '—'}
                  </span>
                </div>
                <div className="text-xs text-neutral">{s.utc_hours} UTC</div>
                <div className="text-xs text-neutral mt-1">{s.description}</div>
                <div className="text-xs text-neutral/60 mt-1">{s.total} markets</div>
              </div>
            ))}
          </div>
        </Section>
      </div>

      <Section title="Time-Remaining Probability Matrix">
        <div className="text-xs text-neutral mb-3">P(Resolves UP) by current UP token price × seconds remaining</div>
        <div className="overflow-x-auto">
          <table className="w-full text-xs mono">
            <thead>
              <tr className="border-b border-border">
                <th className="px-3 py-2 text-left text-neutral">UP Price</th>
                <th className="px-3 py-2 text-neutral">60s left</th>
                <th className="px-3 py-2 text-neutral">30s left</th>
                <th className="px-3 py-2 text-neutral">10s left</th>
              </tr>
            </thead>
            <tbody>
              {timeRemaining.map((row: any, i: number) => (
                <tr key={i} className="border-b border-border/50">
                  <td className="px-3 py-2 text-neutral">{row.price_bucket}</td>
                  {['60s', '30s', '10s'].map(k => {
                    const d = row[k]
                    if (!d || d.n === 0) return <td key={k} className="px-3 py-2 text-neutral/40 text-center">—</td>
                    const pup = d.p_up
                    return (
                      <td key={k} className="px-3 py-2 text-center">
                        <span className={clsx(
                          'font-semibold',
                          pup > 65 ? 'text-up' : pup < 35 ? 'text-down' : 'text-neutral'
                        )}>
                          {pup}%
                        </span>
                        <span className="text-neutral/50 ml-1">({d.n})</span>
                      </td>
                    )
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </div>
  )
}

// ─── Correlation Tab ──────────────────────────────────────────────────────────

function CorrelationTab({ interval }: { interval: number }) {
  const [matrix, setMatrix] = useState<any>(null)
  const [spotCorr, setSpotCorr] = useState<any>(null)
  const [spotAsset, setSpotAsset] = useState('BTC')

  useEffect(() => {
    getCorrelationMatrix(interval).then(setMatrix)
  }, [interval])

  useEffect(() => {
    getSpotCorrelation(spotAsset, interval).then(setSpotCorr)
  }, [spotAsset, interval])

  const assets = matrix?.assets || []
  const matrixData = matrix?.matrix || {}

  return (
    <div className="space-y-5">
      <Section title="Cross-Asset Correlation Matrix">
        {assets.length === 0 ? (
          <p className="text-neutral text-sm">Loading...</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="text-sm mono">
              <thead>
                <tr>
                  <th className="p-2 text-neutral"></th>
                  {assets.map((a: string) => (
                    <th key={a} className="p-2 text-neutral text-xs">{a}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {assets.map((a1: string) => (
                  <tr key={a1}>
                    <td className="p-2 text-white font-semibold text-xs">{a1}</td>
                    {assets.map((a2: string) => {
                      const cell = matrixData[a1]?.[a2] || {}
                      const corr = cell.correlation
                      const same = a1 === a2
                      return (
                        <td key={a2} className="p-2 text-center">
                          <div className={clsx(
                            'w-12 h-12 flex items-center justify-center rounded font-bold text-xs',
                            same ? 'bg-border text-white' :
                            corr === null ? 'bg-border/30 text-neutral' :
                            corr > 0.5 ? 'bg-up/20 text-up' :
                            corr > 0.2 ? 'bg-accent/20 text-accent' :
                            'bg-neutral/10 text-neutral'
                          )}>
                            {same ? '—' : corr !== null ? corr.toFixed(2) : 'N/A'}
                          </div>
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section title="Spot Price → Resolution Correlation">
        <div className="flex gap-2 mb-4">
          <select className="bg-surface border border-border rounded px-2 py-1 text-sm text-white" value={spotAsset} onChange={e => setSpotAsset(e.target.value)}>
            {ASSETS.map(a => <option key={a}>{a}</option>)}
          </select>
        </div>
        {spotCorr && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border">
                  {['Spot Move', 'Total', 'UP Resolutions', '% of UP Total', 'UP Win Rate'].map(h => (
                    <th key={h} className="px-3 py-2 text-left text-neutral text-xs uppercase">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(spotCorr.buckets || []).map((b: any, i: number) => (
                  <tr key={i} className="border-b border-border/50">
                    <td className="px-3 py-2 mono">{b.label}</td>
                    <td className="px-3 py-2 mono text-neutral">{b.total}</td>
                    <td className="px-3 py-2 mono text-up">{b.up_count}</td>
                    <td className="px-3 py-2 mono text-neutral">{b.up_pct_of_all_ups?.toFixed(1)}%</td>
                    <td className="px-3 py-2">
                      <span className={clsx('mono font-semibold', b.up_resolution_rate > 60 ? 'text-up' : b.up_resolution_rate < 40 ? 'text-down' : 'text-neutral')}>
                        {b.up_resolution_rate}%
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  )
}

// ─── Main Analytics Component ─────────────────────────────────────────────────

export default function Analytics() {
  const [tab, setTab] = useState<'momentum' | 'temporal' | 'correlation'>('momentum')
  const [asset, setAsset] = useState('BTC')
  const [interval, setInterval] = useState(5)

  const tabs = [
    { id: 'momentum', label: 'Momentum & Reversal' },
    { id: 'temporal', label: 'Temporal Intelligence' },
    { id: 'correlation', label: 'Correlation' },
  ] as const

  return (
    <div className="space-y-6">
      <div className="bg-panel border border-border rounded-lg p-4 flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-white">Analytics Suite</h2>
          <p className="text-sm text-neutral">12 analytical dimensions on prediction market data</p>
        </div>
        <AssetIntervalSelect asset={asset} interval={interval} setAsset={setAsset} setInterval={setInterval} />
      </div>

      <div className="flex gap-1 bg-panel border border-border rounded-lg p-1 w-fit">
        {tabs.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={clsx(
              'px-4 py-2 rounded-md text-sm font-medium transition-all',
              tab === t.id ? 'bg-accent text-white' : 'text-neutral hover:text-white'
            )}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'momentum' && <MomentumTab asset={asset} interval={interval} />}
      {tab === 'temporal' && <TemporalTab asset={asset} interval={interval} />}
      {tab === 'correlation' && <CorrelationTab interval={interval} />}
    </div>
  )
}
