import { useState } from 'react'
import {
  BarChart2, Activity, FlaskConical, Play,
  Bot, Zap, Menu, X, Target, ClipboardList
} from 'lucide-react'
import clsx from 'clsx'
import LiveDashboard from './components/LiveDashboard'
import PatternLab from './components/PatternLab'
import Analytics from './components/Analytics'
import Backtester from './components/Backtester'
import AICopilot from './components/AICopilot'
import QuantCockpit from './components/QuantCockpit'
import ExecutionTracker from './components/ExecutionTracker'

type Page = 'dashboard' | 'patterns' | 'analytics' | 'backtest' | 'copilot' | 'quant' | 'execution'

const NAV_ITEMS: { id: Page; label: string; icon: React.ElementType; badge?: string }[] = [
  { id: 'dashboard', label: 'Live Dashboard', icon: Activity },
  { id: 'quant', label: 'Quant Cockpit', icon: Target, badge: 'NEW' },
  { id: 'execution', label: 'Execution Tracker', icon: ClipboardList, badge: 'NEW' },
  { id: 'patterns', label: 'Pattern Lab', icon: FlaskConical },
  { id: 'analytics', label: 'Analytics Suite', icon: BarChart2 },
  { id: 'backtest', label: 'Backtester', icon: Play },
  { id: 'copilot', label: 'AI Co-Pilot', icon: Bot },
]

function Logo() {
  return (
    <div className="flex items-center gap-2.5">
      <div className="w-8 h-8 rounded-lg bg-accent flex items-center justify-center">
        <Zap size={16} className="text-white" />
      </div>
      <div>
        <div className="text-white font-bold text-base leading-none">PredictEdge</div>
        <div className="text-neutral text-xs">Market Intelligence</div>
      </div>
    </div>
  )
}

export default function App() {
  const [page, setPage] = useState<Page>('dashboard')
  const [sidebarOpen, setSidebarOpen] = useState(true)

  return (
    <div className="flex h-screen overflow-hidden bg-surface">
      {/* Sidebar */}
      <aside className={clsx(
        'flex-shrink-0 bg-panel border-r border-border transition-all duration-200 flex flex-col',
        sidebarOpen ? 'w-56' : 'w-16'
      )}>
        <div className="p-4 border-b border-border flex items-center justify-between">
          {sidebarOpen && <Logo />}
          {!sidebarOpen && (
            <div className="w-8 h-8 rounded-lg bg-accent flex items-center justify-center mx-auto">
              <Zap size={16} className="text-white" />
            </div>
          )}
        </div>

        <nav className="flex-1 p-2 space-y-1">
          {NAV_ITEMS.map(item => {
            const Icon = item.icon
            const active = page === item.id
            return (
              <button
                key={item.id}
                onClick={() => setPage(item.id)}
                className={clsx(
                  'w-full flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all text-sm',
                  active
                    ? 'bg-accent/10 text-accent'
                    : 'text-neutral hover:text-white hover:bg-border/50'
                )}
                title={!sidebarOpen ? item.label : undefined}
              >
                <Icon size={18} className="flex-shrink-0" />
                {sidebarOpen && (
                  <span className="flex-1 text-left">{item.label}</span>
                )}
                {sidebarOpen && item.badge && (
                  <span className="text-xs bg-accent/20 text-accent px-1.5 py-0.5 rounded-full">
                    {item.badge}
                  </span>
                )}
              </button>
            )
          })}
        </nav>

        <div className="p-2 border-t border-border">
          <button
            onClick={() => setSidebarOpen(v => !v)}
            className="w-full flex items-center justify-center p-2 rounded-lg text-neutral hover:text-white hover:bg-border/50 transition-all"
          >
            {sidebarOpen ? <X size={16} /> : <Menu size={16} />}
          </button>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-y-auto">
        <div className="p-6">
          {page === 'dashboard' && <LiveDashboard />}
          {page === 'quant' && <QuantCockpit />}
          {page === 'execution' && <ExecutionTracker />}
          {page === 'patterns' && <PatternLab />}
          {page === 'analytics' && <Analytics />}
          {page === 'backtest' && <Backtester />}
          {page === 'copilot' && <AICopilot />}
        </div>
      </main>
    </div>
  )
}
