import { useState } from 'react'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts'
import { Play, TrendingUp, TrendingDown, AlertCircle } from 'lucide-react'
import { runBacktest } from '../api'
import clsx from 'clsx'

const ASSETS = ['BTC', 'ETH', 'SOL', 'XRP']
const INTERVALS = [5, 15, 60]

function MetricCard({ label, value, sub, positive }: { label: string; value: string; sub?: string; positive?: boolean }) {
  return (
    <div className="bg-surface rounded-lg p-4">
      <div className="text-neutral text-xs uppercase tracking-wider mb-1">{label}</div>
      <div className={clsx('text-xl font-bold mono', positive === true ? 'text-up' : positive === false ? 'text-down' : 'text-white')}>
        {value}
      </div>
      {sub && <div className="text-neutral text-xs mt-1">{sub}</div>}
    </div>
  )
}

export default function Backtester() {
  const [strategy, setStrategy] = useState('streak_reversal')
  const [asset, setAsset] = useState('BTC')
  const [interval, setIntervalVal] = useState(5)
  const [streakN, setStreakN] = useState(4)
  const [direction, setDirection] = useState('DOWN')
  const [maxPrice, setMaxPrice] = useState(0.48)
  const [orderSize, setOrderSize] = useState(25)
  const [spikeThreshold, setSpikeThreshold] = useState(0.10)
  const [result, setResult] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleRun = async () => {
    setLoading(true)
    setError('')
    try {
      const r = await runBacktest({
        strategy,
        asset,
        interval,
        streak_n: streakN,
        direction,
        max_price: maxPrice,
        order_size: orderSize,
        spike_threshold: spikeThreshold,
      })
      setResult(r)
    } catch (e: any) {
      setError(e.message || 'Backtest failed')
    } finally {
      setLoading(false)
    }
  }

  const equityCurveData = result?.equity_curve?.map((v: number, i: number) => ({ trade: i, equity: v })) || []

  return (
    <div className="space-y-6">
      <div className="bg-panel border border-border rounded-lg p-4">
        <h2 className="text-lg font-semibold text-white">Strategy Backtester</h2>
        <p className="text-sm text-neutral">Test strategies against full historical resolution database</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Config Panel */}
        <div className="bg-panel border border-border rounded-lg p-5 space-y-4">
          <h3 className="text-sm font-semibold text-white">Strategy Configuration</h3>

          <div>
            <label className="text-xs text-neutral uppercase tracking-wider block mb-1">Strategy</label>
            <select
              className="w-full bg-surface border border-border rounded px-3 py-2 text-sm text-white"
              value={strategy}
              onChange={e => setStrategy(e.target.value)}
            >
              <option value="streak_reversal">Streak Reversal</option>
              <option value="fade_pump">Fade the Pump</option>
            </select>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs text-neutral uppercase tracking-wider block mb-1">Asset</label>
              <select className="w-full bg-surface border border-border rounded px-3 py-2 text-sm text-white" value={asset} onChange={e => setAsset(e.target.value)}>
                {ASSETS.map(a => <option key={a}>{a}</option>)}
              </select>
            </div>
            <div>
              <label className="text-xs text-neutral uppercase tracking-wider block mb-1">Interval</label>
              <select className="w-full bg-surface border border-border rounded px-3 py-2 text-sm text-white" value={interval} onChange={e => setIntervalVal(Number(e.target.value))}>
                {INTERVALS.map(i => <option key={i} value={i}>{i}m</option>)}
              </select>
            </div>
          </div>

          <div>
            <label className="text-xs text-neutral uppercase tracking-wider block mb-1">Order Size (USDC)</label>
            <input
              type="number"
              className="w-full bg-surface border border-border rounded px-3 py-2 text-sm text-white"
              value={orderSize}
              onChange={e => setOrderSize(Number(e.target.value))}
              min={1} max={500}
            />
          </div>

          {strategy === 'streak_reversal' && (
            <>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs text-neutral uppercase tracking-wider block mb-1">Streak Length ≥</label>
                  <input
                    type="number"
                    className="w-full bg-surface border border-border rounded px-3 py-2 text-sm text-white"
                    value={streakN}
                    onChange={e => setStreakN(Number(e.target.value))}
                    min={2} max={10}
                  />
                </div>
                <div>
                  <label className="text-xs text-neutral uppercase tracking-wider block mb-1">Streak Direction</label>
                  <select className="w-full bg-surface border border-border rounded px-3 py-2 text-sm text-white" value={direction} onChange={e => setDirection(e.target.value)}>
                    <option value="UP">UP</option>
                    <option value="DOWN">DOWN</option>
                  </select>
                </div>
              </div>
              <div>
                <label className="text-xs text-neutral uppercase tracking-wider block mb-1">Max Entry Price (¢)</label>
                <input
                  type="number"
                  step="0.01"
                  className="w-full bg-surface border border-border rounded px-3 py-2 text-sm text-white"
                  value={maxPrice}
                  onChange={e => setMaxPrice(Number(e.target.value))}
                  min={0.1} max={0.9}
                />
              </div>
            </>
          )}

          {strategy === 'fade_pump' && (
            <div>
              <label className="text-xs text-neutral uppercase tracking-wider block mb-1">Spike Threshold (first 30s)</label>
              <div className="flex items-center gap-2">
                <input
                  type="number"
                  step="0.01"
                  className="w-full bg-surface border border-border rounded px-3 py-2 text-sm text-white"
                  value={spikeThreshold}
                  onChange={e => setSpikeThreshold(Number(e.target.value))}
                  min={0.03} max={0.30}
                />
                <span className="text-neutral text-sm whitespace-nowrap">{(spikeThreshold * 100).toFixed(0)}¢</span>
              </div>
            </div>
          )}

          <button
            onClick={handleRun}
            disabled={loading}
            className="w-full flex items-center justify-center gap-2 bg-accent hover:bg-accent/80 text-white px-4 py-3 rounded-lg font-medium transition-colors disabled:opacity-50"
          >
            <Play size={16} />
            {loading ? 'Running Backtest...' : 'Run Backtest'}
          </button>

          {error && (
            <div className="flex items-center gap-2 text-down text-sm bg-down/10 rounded p-3">
              <AlertCircle size={14} />
              {error}
            </div>
          )}
        </div>

        {/* Results */}
        <div className="lg:col-span-2 space-y-5">
          {!result ? (
            <div className="bg-panel border border-border rounded-lg p-12 text-center">
              <Play size={48} className="mx-auto mb-4 text-neutral/30" />
              <p className="text-neutral">Configure and run a backtest to see results</p>
            </div>
          ) : result.error ? (
            <div className="bg-panel border border-border rounded-lg p-8 text-center">
              <AlertCircle size={48} className="mx-auto mb-4 text-down/50" />
              <p className="text-down">{result.error}</p>
            </div>
          ) : (
            <>
              {/* Metrics Grid */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                <MetricCard label="Total Trades" value={result.total_trades?.toString()} />
                <MetricCard
                  label="Win Rate"
                  value={`${result.win_rate}%`}
                  positive={result.win_rate > 50}
                />
                <MetricCard
                  label="Total P&L"
                  value={`${result.total_pnl >= 0 ? '+' : ''}$${result.total_pnl?.toFixed(2)}`}
                  positive={result.total_pnl >= 0}
                />
                <MetricCard
                  label="Avg Edge"
                  value={`$${result.avg_edge?.toFixed(2)}/trade`}
                  positive={result.avg_edge >= 0}
                />
                <MetricCard label="Sharpe Ratio" value={result.sharpe_ratio?.toFixed(2)} positive={result.sharpe_ratio > 1} />
                <MetricCard label="Max Drawdown" value={`$${result.max_drawdown?.toFixed(2)}`} positive={false} />
                <MetricCard label="Wins" value={result.wins?.toString()} positive={true} />
                <MetricCard label="Losses" value={result.losses?.toString()} positive={false} />
              </div>

              {/* Equity Curve */}
              <div className="bg-panel border border-border rounded-lg p-5">
                <h3 className="text-sm font-semibold text-white mb-4">Equity Curve (last 50 trades)</h3>
                <ResponsiveContainer width="100%" height={200}>
                  <LineChart data={equityCurveData}>
                    <XAxis dataKey="trade" tick={{ fontSize: 10, fill: '#94a3b8' }} />
                    <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} tickFormatter={v => `$${v.toFixed(0)}`} />
                    <Tooltip
                      contentStyle={{ background: '#161b27', border: '1px solid #1e2538', borderRadius: 8 }}
                      formatter={(v: any) => [`$${v.toFixed(2)}`, 'Equity']}
                    />
                    <ReferenceLine y={equityCurveData[0]?.equity} stroke="#94a3b8" strokeDasharray="4 4" />
                    <Line type="monotone" dataKey="equity" stroke={ACCENT} dot={false} strokeWidth={2} />
                  </LineChart>
                </ResponsiveContainer>
              </div>

              {/* Recent Trades */}
              <div className="bg-panel border border-border rounded-lg overflow-hidden">
                <div className="p-4 border-b border-border">
                  <h3 className="text-sm font-semibold text-white">Recent Trades</h3>
                </div>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border">
                      {['Side', 'Entry', 'Close', 'Result', 'P&L'].map(h => (
                        <th key={h} className="px-4 py-2 text-left text-neutral text-xs uppercase">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {(result.recent_trades || []).slice(-10).reverse().map((t: any, i: number) => (
                      <tr key={i} className="border-b border-border/50">
                        <td className={clsx('px-4 py-2 font-bold text-sm mono', t.entry_side === 'UP' ? 'text-up' : 'text-down')}>
                          {t.entry_side === 'UP' ? '▲' : '▼'} {t.entry_side}
                        </td>
                        <td className="px-4 py-2 mono text-neutral">{(t.entry_price * 100).toFixed(1)}¢</td>
                        <td className="px-4 py-2 mono text-neutral">{(t.close_price * 100).toFixed(1)}¢</td>
                        <td className="px-4 py-2">
                          <span className={clsx('text-xs px-2 py-0.5 rounded', t.won ? 'bg-up/10 text-up' : 'bg-down/10 text-down')}>
                            {t.won ? 'WIN' : 'LOSS'}
                          </span>
                        </td>
                        <td className={clsx('px-4 py-2 mono font-semibold', t.pnl >= 0 ? 'text-up' : 'text-down')}>
                          {t.pnl >= 0 ? '+' : ''}${t.pnl.toFixed(2)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

const ACCENT = '#6366f1'
