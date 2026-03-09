import { useCallback, useEffect, useState } from 'react'
import { RefreshCw, CheckCircle, XCircle, Clock3, BarChart3 } from 'lucide-react'
import clsx from 'clsx'
import { getOrderPerformance } from '../api'

interface Bucket {
  placed: number
  executed: number
  failed: number
  resolved: number
  wins: number
  losses: number
  win_rate_pct: number | null
}

interface IntervalBucket extends Bucket {
  interval_minutes: number
}

interface TriggerBucket extends Bucket {
  trigger_type: string
}

interface OrderRow {
  id: number
  slug: string
  asset: string
  interval_minutes: number
  predicted_side: string
  trigger_type: string
  status: string
  order_price: number
  order_size: number
  order_id?: string | null
  error?: string | null
  created_at: number
  winner_side?: string | null
  resolved: boolean
  result?: 'WIN' | 'LOSS' | null
}

interface Payload {
  summary: Bucket
  by_interval: IntervalBucket[]
  by_trigger: TriggerBucket[]
  recent_orders: OrderRow[]
}

const fmtTs = (t: number) => new Date(t * 1000).toLocaleString()

function StatCard({ title, value, color = 'text-white', sub }: { title: string; value: string; color?: string; sub?: string }) {
  return (
    <div className="rounded-lg border border-border bg-panel p-3">
      <div className="text-xs text-neutral-500">{title}</div>
      <div className={clsx('text-xl font-mono font-bold', color)}>{value}</div>
      {sub && <div className="text-xs text-neutral-500 mt-1">{sub}</div>}
    </div>
  )
}

export default function ExecutionTracker() {
  const [data, setData] = useState<Payload | null>(null)
  const [loading, setLoading] = useState(false)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const r = await getOrderPerformance(200)
      setData(r)
      setLastUpdated(new Date())
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
    const t = setInterval(fetchData, 15000)
    return () => clearInterval(t)
  }, [fetchData])

  const s = data?.summary

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white flex items-center gap-2">
            <BarChart3 size={20} className="text-accent" />
            Execution Tracker
          </h1>
          <p className="text-sm text-neutral-400">Buy orders placed, executed, and realized win/loss performance</p>
        </div>
        <div className="flex items-center gap-3">
          {lastUpdated && <span className="text-xs text-neutral-500">Updated {lastUpdated.toLocaleTimeString()}</span>}
          <button
            onClick={fetchData}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-surface border border-border text-neutral-300 hover:text-white text-xs"
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
      </div>

      {s && (
        <div className="grid grid-cols-3 gap-3">
          <StatCard title="Buy Orders Placed" value={`${s.placed}`} sub="All order attempts" />
          <StatCard title="Executed" value={`${s.executed}`} color="text-green-400" sub="Accepted/submitted to CLOB" />
          <StatCard title="Failed" value={`${s.failed}`} color="text-red-400" sub="Rejected/failed placement" />
          <StatCard title="Resolved Trades" value={`${s.resolved}`} sub="Executed + market resolved" />
          <StatCard title="Successful (Wins)" value={`${s.wins}`} color="text-green-400" />
          <StatCard title="Losses" value={`${s.losses}`} color="text-red-400" sub={s.win_rate_pct == null ? 'Win rate: —' : `Win rate: ${s.win_rate_pct}%`} />
        </div>
      )}

      <div className="grid grid-cols-2 gap-4">
        <div className="rounded-lg border border-border overflow-hidden">
          <div className="px-3 py-2 text-xs font-semibold text-neutral-300 bg-surface border-b border-border">By Interval</div>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-neutral-500">
                <th className="text-left px-3 py-2">Interval</th>
                <th className="text-right px-3 py-2">Placed</th>
                <th className="text-right px-3 py-2">Executed</th>
                <th className="text-right px-3 py-2">W/L</th>
                <th className="text-right px-3 py-2">WR</th>
              </tr>
            </thead>
            <tbody>
              {(data?.by_interval || []).map((r) => (
                <tr key={r.interval_minutes} className="border-t border-border/60">
                  <td className="px-3 py-2 text-white">{r.interval_minutes}m</td>
                  <td className="px-3 py-2 text-right">{r.placed}</td>
                  <td className="px-3 py-2 text-right text-green-400">{r.executed}</td>
                  <td className="px-3 py-2 text-right">
                    <span className="text-green-400">{r.wins}</span>/<span className="text-red-400">{r.losses}</span>
                  </td>
                  <td className="px-3 py-2 text-right">{r.win_rate_pct == null ? '—' : `${r.win_rate_pct}%`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="rounded-lg border border-border overflow-hidden">
          <div className="px-3 py-2 text-xs font-semibold text-neutral-300 bg-surface border-b border-border">By Trigger</div>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-neutral-500">
                <th className="text-left px-3 py-2">Trigger</th>
                <th className="text-right px-3 py-2">Placed</th>
                <th className="text-right px-3 py-2">Executed</th>
                <th className="text-right px-3 py-2">W/L</th>
                <th className="text-right px-3 py-2">WR</th>
              </tr>
            </thead>
            <tbody>
              {(data?.by_trigger || []).map((r) => (
                <tr key={r.trigger_type} className="border-t border-border/60">
                  <td className="px-3 py-2 text-white">{r.trigger_type}</td>
                  <td className="px-3 py-2 text-right">{r.placed}</td>
                  <td className="px-3 py-2 text-right text-green-400">{r.executed}</td>
                  <td className="px-3 py-2 text-right">
                    <span className="text-green-400">{r.wins}</span>/<span className="text-red-400">{r.losses}</span>
                  </td>
                  <td className="px-3 py-2 text-right">{r.win_rate_pct == null ? '—' : `${r.win_rate_pct}%`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="rounded-lg border border-border overflow-hidden">
        <div className="px-3 py-2 text-xs font-semibold text-neutral-300 bg-surface border-b border-border">Recent Orders (Detailed)</div>
        <div className="max-h-[420px] overflow-auto">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-surface">
              <tr className="text-neutral-500">
                <th className="text-left px-3 py-2">Time</th>
                <th className="text-left px-3 py-2">Asset</th>
                <th className="text-left px-3 py-2">Trigger</th>
                <th className="text-left px-3 py-2">Side</th>
                <th className="text-right px-3 py-2">Price</th>
                <th className="text-right px-3 py-2">Size</th>
                <th className="text-left px-3 py-2">Status</th>
                <th className="text-left px-3 py-2">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {(data?.recent_orders || []).map((r) => (
                <tr key={r.id} className="border-t border-border/50">
                  <td className="px-3 py-2 text-neutral-400">{fmtTs(r.created_at)}</td>
                  <td className="px-3 py-2 text-white">{r.asset} {r.interval_minutes}m</td>
                  <td className="px-3 py-2">{r.trigger_type}</td>
                  <td className={clsx('px-3 py-2 font-bold', r.predicted_side === 'UP' ? 'text-green-400' : 'text-red-400')}>
                    {r.predicted_side}
                  </td>
                  <td className="px-3 py-2 text-right font-mono">{r.order_price?.toFixed(2)}</td>
                  <td className="px-3 py-2 text-right font-mono">${r.order_size?.toFixed(0)}</td>
                  <td className="px-3 py-2">
                    {r.status === 'submitted' ? (
                      <span className="inline-flex items-center gap-1 text-green-400"><CheckCircle size={12} /> executed</span>
                    ) : r.status === 'failed' ? (
                      <span className="inline-flex items-center gap-1 text-red-400"><XCircle size={12} /> failed</span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-neutral-400"><Clock3 size={12} /> {r.status}</span>
                    )}
                  </td>
                  <td className="px-3 py-2">
                    {r.result === 'WIN' ? (
                      <span className="text-green-400 font-bold">WIN</span>
                    ) : r.result === 'LOSS' ? (
                      <span className="text-red-400 font-bold">LOSS</span>
                    ) : r.resolved ? (
                      <span className="text-neutral-400">resolved</span>
                    ) : (
                      <span className="text-neutral-500">pending</span>
                    )}
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
