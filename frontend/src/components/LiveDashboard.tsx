import { useEffect, useMemo, useState } from 'react'
import { TrendingUp, TrendingDown, Zap, Clock } from 'lucide-react'
import { getMarketStats, getRecentMarkets } from '../api'
import { useWebSocket } from '../hooks/useWebSocket'
import clsx from 'clsx'

const ASSETS = ['BTC', 'ETH', 'SOL', 'XRP']
const INTERVALS = [5, 15, 60]

function formatPrice(price: number | null | undefined, asset: string): string {
  if (!price) return '—'
  if (asset === 'BTC') return `$${price.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
  if (asset === 'ETH') return `$${price.toLocaleString('en-US', { maximumFractionDigits: 0 })}`
  return `$${price.toFixed(4)}`
}

function timeAgo(secondsTs: number | null, nowMs: number): string {
  if (!secondsTs) return 'waiting for live data...'
  const diffSec = Math.max(0, Math.floor(nowMs / 1000 - secondsTs))
  if (diffSec <= 1) return 'just now'
  if (diffSec < 60) return `${diffSec}s ago`
  const mins = Math.floor(diffSec / 60)
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  return `${hours}h ago`
}

function formatCents(price: number | null | undefined): string {
  if (price == null) return '—'
  return `${(price * 100).toFixed(0)}¢`
}

function formatSpotChange(value: number | null | undefined): string {
  if (value == null) return '—'
  return `${value >= 0 ? '+' : ''}${value.toFixed(1)}`
}

function StreakDots({ outcomes }: { outcomes: string[] }) {
  return (
    <div className="flex items-center gap-0.5">
      {outcomes.map((o, i) => (
        <div
          key={i}
          className={clsx(
            'w-2.5 h-2.5 rounded-full',
            o === 'UP' ? 'bg-up' : 'bg-down'
          )}
          title={o}
        />
      ))}
    </div>
  )
}

function StreakCard({ streak }: { streak: any }) {
  const isUp = streak.direction === 'UP'
  const isStrong = streak.streak_length >= 4

  return (
    <div
      className={clsx(
        'bg-panel border rounded-lg p-4 transition-all',
        isStrong
          ? isUp ? 'border-up/40 glow-up' : 'border-down/40 glow-down'
          : 'border-border'
      )}
    >
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold text-white">{streak.asset}</span>
          <span className="text-xs text-neutral bg-border px-1.5 py-0.5 rounded">{streak.interval}m</span>
        </div>
        {isStrong && <Zap size={14} className={isUp ? 'text-up' : 'text-down'} />}
      </div>

      <div className="flex items-center gap-2 mb-3">
        {isUp ? (
          <TrendingUp size={18} className="text-up" />
        ) : (
          <TrendingDown size={18} className="text-down" />
        )}
        <span className={clsx('text-2xl font-bold mono', isUp ? 'text-up' : 'text-down')}>
          {streak.streak_length}×
        </span>
        <span className={clsx('text-sm font-medium', isUp ? 'text-up' : 'text-down')}>
          {streak.direction}
        </span>
      </div>

      <StreakDots outcomes={streak.last_10 || []} />

      {isStrong && (
        <div className={clsx(
          'mt-2 text-xs px-2 py-1 rounded font-medium',
          isUp ? 'bg-up/10 text-up' : 'bg-down/10 text-down'
        )}>
          ⚡ Streak Alert — {streak.streak_length}+ consecutive {streak.direction}
        </div>
      )}
    </div>
  )
}

function SpotPriceBar({ prices }: { prices: Record<string, number | null> }) {
  return (
    <div className="flex gap-6 items-center">
      {ASSETS.map(asset => (
        <div key={asset} className="flex items-baseline gap-2">
          <span className="text-neutral text-sm">{asset}</span>
          <span className="mono text-white font-semibold">
            {formatPrice(prices[asset], asset)}
          </span>
        </div>
      ))}
    </div>
  )
}

export default function LiveDashboard() {
  const { connected, lastUpdate } = useWebSocket()
  const [streaks, setStreaks] = useState<any[]>([])
  const [prices, setPrices] = useState<Record<string, number | null>>({})
  const [lastUpdatedTs, setLastUpdatedTs] = useState<number | null>(null)
  const [stats, setStats] = useState<any[]>([])
  const [selectedAsset, setSelectedAsset] = useState('BTC')
  const [selectedInterval, setSelectedInterval] = useState(5)
  const [recentMarkets, setRecentMarkets] = useState<any[]>([])
  const [loading, setLoading] = useState(true)
  const [nowMs, setNowMs] = useState(Date.now())
  const [flashLive, setFlashLive] = useState(false)

  const refreshStats = () => {
    getMarketStats()
      .then(d => {
        setStats(d.stats || [])
      })
      .finally(() => setLoading(false))
  }

  const refreshRecentMarkets = () => {
    getRecentMarkets(selectedAsset, selectedInterval, 20).then(d => {
      setRecentMarkets(d.markets || [])
    })
  }

  useEffect(() => {
    refreshStats()
    const interval = setInterval(refreshStats, 30000)
    return () => clearInterval(interval)
  }, [])

  useEffect(() => {
    refreshRecentMarkets()
    const interval = setInterval(refreshRecentMarkets, 15000)
    return () => clearInterval(interval)
  }, [selectedAsset, selectedInterval])

  useEffect(() => {
    if (lastUpdate) {
      if (lastUpdate.streaks) setStreaks(lastUpdate.streaks)
      if (lastUpdate.spot_prices) setPrices(lastUpdate.spot_prices as Record<string, number | null>)
      if (lastUpdate.timestamp) setLastUpdatedTs(lastUpdate.timestamp)
      setFlashLive(true)
      const timer = setTimeout(() => setFlashLive(false), 500)
      return () => clearTimeout(timer)
    }
  }, [lastUpdate])

  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])

  const strongStreaks = streaks.filter(s => s.streak_length >= 4)
  const allStreaks = streaks.sort((a, b) => b.streak_length - a.streak_length)
  const freshnessSec = lastUpdatedTs ? Math.max(0, Math.floor(nowMs / 1000 - lastUpdatedTs)) : null
  const freshnessLabel = connected && freshnessSec != null && freshnessSec <= 5 ? 'Realtime' : connected ? 'Delayed' : 'Offline'
  const nextMarket = useMemo(() => {
    const nowSec = Math.floor(nowMs / 1000)
    const intervalSec = selectedInterval * 60
    const nextCloseTs = Math.floor(nowSec / intervalSec) * intervalSec + intervalSec
    const countdown = Math.max(0, nextCloseTs - nowSec)
    return {
      nextCloseTs,
      countdown,
    }
  }, [nowMs, selectedInterval])

  return (
    <div className="space-y-6">
      {/* Header bar */}
      <div className={clsx(
        'bg-panel border border-border rounded-lg p-4 flex items-center justify-between transition-shadow duration-300',
        flashLive && 'shadow-[0_0_0_1px_rgba(99,102,241,0.5),0_0_20px_rgba(99,102,241,0.2)]'
      )}>
        <div>
          <h2 className="text-lg font-semibold text-white">Live Market Dashboard</h2>
          <p className="text-sm text-neutral">Real-time prediction market overview</p>
          <div className="mt-1 flex items-center gap-2 text-xs text-neutral">
            <Clock size={12} />
            <span>
              Last updated:{' '}
              {lastUpdatedTs
                ? new Date(lastUpdatedTs * 1000).toLocaleTimeString()
                : 'waiting for live data...'}
            </span>
            <span className="text-neutral/70">({timeAgo(lastUpdatedTs, nowMs)})</span>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <SpotPriceBar prices={prices} />
          <div className={clsx(
            'flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium',
            connected ? 'bg-up/10 text-up' : 'bg-neutral/10 text-neutral'
          )}>
            <div className={clsx('w-1.5 h-1.5 rounded-full', connected ? 'bg-up animate-pulse' : 'bg-neutral')} />
            {connected ? 'Live' : 'Connecting...'}
          </div>
          <div className={clsx(
            'px-3 py-1.5 rounded-full text-xs font-medium',
            freshnessLabel === 'Realtime'
              ? 'bg-indigo-500/15 text-indigo-300'
              : freshnessLabel === 'Delayed'
                ? 'bg-yellow-500/15 text-yellow-300'
                : 'bg-neutral/10 text-neutral'
          )}>
            {freshnessLabel}
          </div>
        </div>
      </div>

      {/* Next Market pulse card */}
      <div className="bg-panel border border-border rounded-lg p-4">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-neutral uppercase tracking-wider">Next Market Window</h3>
            <p className="text-sm text-white mt-1">
              {selectedAsset} {selectedInterval}m closes at{' '}
              <span className="mono">{new Date(nextMarket.nextCloseTs * 1000).toLocaleTimeString()}</span>
            </p>
          </div>
          <div className={clsx(
            'next-market-pulse px-4 py-2 rounded-md border border-border',
            connected ? 'bg-neutral/20 text-neutral' : 'bg-neutral/10 text-neutral/70'
          )}>
            <span className="mono text-lg font-semibold">
              {Math.floor(nextMarket.countdown / 60)
                .toString()
                .padStart(2, '0')}
              :
              {(nextMarket.countdown % 60).toString().padStart(2, '0')}
            </span>
          </div>
        </div>
      </div>

      {/* Alert banner for strong streaks */}
      {strongStreaks.length > 0 && (
        <div className="bg-accent/10 border border-accent/30 rounded-lg p-3">
          <div className="flex items-center gap-2 mb-1">
            <Zap size={14} className="text-accent" />
            <span className="text-sm font-semibold text-accent">Streak Alerts</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {strongStreaks.map((s, i) => (
              <span key={i} className={clsx(
                'text-xs px-2 py-1 rounded font-mono',
                s.direction === 'UP' ? 'bg-up/10 text-up' : 'bg-down/10 text-down'
              )}>
                {s.asset} {s.interval}m: {s.streak_length}× {s.direction}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Streak Grid */}
      <div>
        <h3 className="text-sm font-semibold text-neutral uppercase tracking-wider mb-3">
          Active Streaks
        </h3>
        {loading ? (
          <div className="text-neutral text-sm">Loading streaks...</div>
        ) : (
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-3">
            {allStreaks.map((s, i) => (
              <StreakCard key={i} streak={s} />
            ))}
          </div>
        )}
      </div>

      {/* Market Overview Table */}
      <div>
        <h3 className="text-sm font-semibold text-neutral uppercase tracking-wider mb-3">
          Resolution Statistics (All-Time)
        </h3>
        <div className="bg-panel border border-border rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                {['Asset', 'Interval', 'Total', 'UP Rate', 'False Pumps', 'Late Reversals', 'Clean'].map(h => (
                  <th key={h} className="px-4 py-3 text-left text-neutral text-xs uppercase tracking-wider">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {stats.map((s, i) => (
                <tr key={i} className="border-b border-border/50 hover:bg-border/30 transition-colors">
                  <td className="px-4 py-3 font-semibold text-white">{s.asset}</td>
                  <td className="px-4 py-3 mono text-neutral">{s.interval}m</td>
                  <td className="px-4 py-3 mono">{s.total.toLocaleString()}</td>
                  <td className="px-4 py-3">
                    <span className={clsx(
                      'mono font-semibold',
                      s.up_rate > 52 ? 'text-up' : s.up_rate < 48 ? 'text-down' : 'text-neutral'
                    )}>
                      {s.up_rate}%
                    </span>
                  </td>
                  <td className="px-4 py-3 mono text-neutral">{s.false_pumps}</td>
                  <td className="px-4 py-3 mono text-neutral">{s.late_reversals}</td>
                  <td className="px-4 py-3 mono text-neutral">{s.clean}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Recent Resolutions */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-neutral uppercase tracking-wider">
            Recent Resolutions
          </h3>
          <div className="flex gap-2">
            <select
              className="bg-panel border border-border rounded px-2 py-1 text-sm text-white"
              value={selectedAsset}
              onChange={e => setSelectedAsset(e.target.value)}
            >
              {ASSETS.map(a => <option key={a} value={a}>{a}</option>)}
            </select>
            <select
              className="bg-panel border border-border rounded px-2 py-1 text-sm text-white"
              value={selectedInterval}
              onChange={e => setSelectedInterval(Number(e.target.value))}
            >
              {INTERVALS.map(i => <option key={i} value={i}>{i}m</option>)}
            </select>
          </div>
        </div>

        <div className="flex gap-1 mb-3">
          {recentMarkets.slice(0, 30).reverse().map((m, i) => (
            <div
              key={i}
              className={clsx(
                'w-4 h-8 rounded-sm',
                m.winner_side === 'UP'
                  ? 'bg-up'
                  : m.winner_side === 'DOWN'
                    ? 'bg-down'
                    : 'bg-neutral'
              )}
              title={`${m.winner_side} — ${new Date(m.start_ts * 1000).toLocaleTimeString()}`}
            />
          ))}
        </div>

        <div className="bg-panel border border-border rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                {['Time', 'Result', 'Open Price', 'Close Price', 'Spot Change', 'Flags'].map(h => (
                  <th key={h} className="px-4 py-3 text-left text-neutral text-xs uppercase tracking-wider">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {recentMarkets.slice(0, 15).map((m, i) => (
                <tr key={i} className="border-b border-border/50 hover:bg-border/30 transition-colors">
                  <td className="px-4 py-3 mono text-neutral text-xs">
                    {new Date(m.start_ts * 1000).toLocaleTimeString()}
                  </td>
                  <td className="px-4 py-3">
                    <span className={clsx(
                      'font-bold mono',
                      m.winner_side === 'UP'
                        ? 'text-up'
                        : m.winner_side === 'DOWN'
                          ? 'text-down'
                          : 'text-neutral'
                    )}>
                      {m.winner_side === 'UP'
                        ? '▲ UP'
                        : m.winner_side === 'DOWN'
                          ? '▼ DOWN'
                          : '● LIVE'}
                    </span>
                  </td>
                  <td className="px-4 py-3 mono">{formatCents(m.open_up_price)}</td>
                  <td className="px-4 py-3 mono">{formatCents(m.close_up_price)}</td>
                  <td
                    className={clsx(
                      'px-4 py-3 mono',
                      m.spot_change_usd == null
                        ? 'text-neutral'
                        : m.spot_change_usd >= 0
                          ? 'text-up'
                          : 'text-down'
                    )}
                  >
                    {formatSpotChange(m.spot_change_usd)}
                  </td>
                  <td className="px-4 py-3 flex gap-1">
                    {m.false_pump === 1 && <span className="text-xs bg-yellow-500/10 text-yellow-400 px-1.5 rounded">Pump</span>}
                    {m.late_reversal === 1 && <span className="text-xs bg-orange-500/10 text-orange-400 px-1.5 rounded">Late Rev</span>}
                    {m.clean_resolution === 1 && <span className="text-xs bg-up/10 text-up px-1.5 rounded">Clean</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
