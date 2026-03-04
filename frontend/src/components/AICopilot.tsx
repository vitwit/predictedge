import { useState, useRef, useEffect } from 'react'
import { Send, Bot, User, Sparkles, Zap } from 'lucide-react'
import { askCopilot } from '../api'
import clsx from 'clsx'

interface Message {
  role: 'user' | 'assistant'
  content: string
  actions?: string[]
}

const EXAMPLE_QUERIES = [
  'Show me the strongest patterns in BTC 5m markets',
  'What happens after 4 consecutive DOWN resolutions?',
  'Is there an edge in ETH markets during London session?',
  'How often do false pumps resolve DOWN?',
  'What is the win rate for streak reversal strategies?',
  'Explain the peak-to-trough heatmap signal',
]

function MessageBubble({ msg }: { msg: Message }) {
  const isUser = msg.role === 'user'
  return (
    <div className={clsx('flex gap-3', isUser && 'flex-row-reverse')}>
      <div className={clsx(
        'w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0',
        isUser ? 'bg-accent' : 'bg-panel border border-border'
      )}>
        {isUser ? <User size={14} className="text-white" /> : <Bot size={14} className="text-accent" />}
      </div>
      <div className={clsx('flex-1 max-w-2xl', isUser && 'flex flex-col items-end')}>
        <div className={clsx(
          'rounded-2xl px-4 py-3 text-sm',
          isUser
            ? 'bg-accent text-white rounded-tr-none'
            : 'bg-panel border border-border text-white rounded-tl-none'
        )}>
          <p className="whitespace-pre-wrap leading-relaxed">{msg.content}</p>
        </div>
        {!isUser && msg.actions && msg.actions.length > 0 && (
          <div className="flex flex-wrap gap-2 mt-2">
            {msg.actions.map((action, i) => (
              <button key={i} className="text-xs px-3 py-1.5 bg-accent/10 text-accent rounded-full border border-accent/20 hover:bg-accent/20 transition-colors">
                {action}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

export default function AICopilot() {
  const [messages, setMessages] = useState<Message[]>([
    {
      role: 'assistant',
      content: 'Welcome to the PredictEdge AI Co-Pilot. I have full access to the prediction market database and can help you:\n\n• Analyze patterns and historical edges\n• Explain current market conditions\n• Design and evaluate trading strategies\n• Assess risk and P&L scenarios\n\nWhat would you like to explore?',
      actions: ['View Top Patterns', 'Check Current Streaks', 'Run a Backtest'],
    }
  ])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const sendMessage = async (query: string) => {
    if (!query.trim() || loading) return

    const userMsg: Message = { role: 'user', content: query }
    setMessages(prev => [...prev, userMsg])
    setInput('')
    setLoading(true)

    try {
      const response = await askCopilot(query)
      const assistantMsg: Message = {
        role: 'assistant',
        content: response.answer,
        actions: response.suggested_actions,
      }
      setMessages(prev => [...prev, assistantMsg])
    } catch (e) {
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: 'Sorry, I encountered an error. Please try again.',
      }])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex flex-col h-[calc(100vh-160px)]">
      {/* Header */}
      <div className="bg-panel border border-border rounded-lg p-4 mb-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-accent/20 flex items-center justify-center">
            <Sparkles size={18} className="text-accent" />
          </div>
          <div>
            <h2 className="text-lg font-semibold text-white">AI Co-Pilot</h2>
            <p className="text-sm text-neutral">Natural language interface to prediction market intelligence</p>
          </div>
        </div>
        <div className="flex items-center gap-2 text-xs text-neutral">
          <Zap size={12} className="text-accent" />
          Powered by GPT-4o
        </div>
      </div>

      {/* Example queries */}
      {messages.length <= 1 && (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-2 mb-4">
          {EXAMPLE_QUERIES.map((q, i) => (
            <button
              key={i}
              onClick={() => sendMessage(q)}
              className="text-left text-xs text-neutral bg-panel border border-border hover:border-accent/40 hover:text-white rounded-lg p-3 transition-all"
            >
              {q}
            </button>
          ))}
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto space-y-4 pb-4">
        {messages.map((msg, i) => (
          <MessageBubble key={i} msg={msg} />
        ))}

        {loading && (
          <div className="flex gap-3">
            <div className="w-8 h-8 rounded-full bg-panel border border-border flex items-center justify-center">
              <Bot size={14} className="text-accent" />
            </div>
            <div className="bg-panel border border-border rounded-2xl rounded-tl-none px-4 py-3">
              <div className="flex gap-1">
                {[0, 1, 2].map(i => (
                  <div key={i} className="w-2 h-2 rounded-full bg-neutral animate-bounce" style={{ animationDelay: `${i * 0.15}s` }} />
                ))}
              </div>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="bg-panel border border-border rounded-xl p-2 flex gap-2">
        <input
          type="text"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && !e.shiftKey && sendMessage(input)}
          placeholder="Ask about patterns, strategies, market conditions..."
          className="flex-1 bg-transparent text-white placeholder:text-neutral/60 text-sm px-3 py-2 outline-none"
          disabled={loading}
        />
        <button
          onClick={() => sendMessage(input)}
          disabled={!input.trim() || loading}
          className="w-9 h-9 rounded-lg bg-accent hover:bg-accent/80 flex items-center justify-center text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <Send size={15} />
        </button>
      </div>
    </div>
  )
}
