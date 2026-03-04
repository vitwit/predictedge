import { useState, useEffect } from 'react'
import { Search, ChevronRight } from 'lucide-react'
import { scanPattern, getPatternMatrix, getTopPatterns } from '../api'
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

export default function PatternLab() {
  const [asset, setAsset] = useState('BTC')
  const [interval, setIntervalVal] = useState(5)
  const [scanResult, setScanResult] = useState<any>(null)
  const [topPatterns, setTopPatterns] = useState<any[]>([])
  const [loadingTop, setLoadingTop] = useState(false)

  useEffect(() => {
    setLoadingTop(true)
    getTopPatterns(asset, interval, 15)
      .then(d => setTopPatterns(d.top_patterns || []))
      .finally(() => setLoadingTop(false))
  }, [asset, interval])

  return (
    <div className="space-y-6">
      <div className="bg-panel border border-border rounded-lg p-4">
        <h2 className="text-lg font-semibold text-white">Pattern Lab</h2>
        <p className="text-sm text-neutral">Backtest any historical outcome sequence to find edges</p>
      </div>

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
    </div>
  )
}
