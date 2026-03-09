import { useEffect, useRef, useState } from 'react'

export interface LiveUpdate {
  type: string
  streaks?: any[]
  spot_prices?: Record<string, number | null>
  timestamp?: number
}

function getWebSocketUrl() {
  if (import.meta.env.VITE_WS_URL) {
    return import.meta.env.VITE_WS_URL as string
  }

  if (import.meta.env.DEV) {
    return 'ws://localhost:8888/ws'
  }

  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${protocol}://${window.location.host}/ws`
}

export function useWebSocket() {
  const [connected, setConnected] = useState(false)
  const [lastUpdate, setLastUpdate] = useState<LiveUpdate | null>(null)
  const wsRef = useRef<WebSocket | null>(null)
  const pingRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined)
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const mountedRef = useRef(false)

  useEffect(() => {
    mountedRef.current = true

    const connect = () => {
      if (!mountedRef.current) return

      const ws = new WebSocket(getWebSocketUrl())

      ws.onopen = () => {
        setConnected(true)
        if (reconnectRef.current) clearTimeout(reconnectRef.current)
        pingRef.current = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: 'ping' }))
          }
        }, 20000)
      }

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          if (data.type !== 'pong') {
            setLastUpdate(data)
          }
        } catch {}
      }

      ws.onclose = () => {
        setConnected(false)
        clearInterval(pingRef.current)
        if (!mountedRef.current) return
        reconnectRef.current = setTimeout(connect, 3000)
      }

      ws.onerror = () => {
        ws.close()
      }

      wsRef.current = ws
    }

    connect()

    return () => {
      mountedRef.current = false
      clearInterval(pingRef.current)
      if (reconnectRef.current) clearTimeout(reconnectRef.current)
      wsRef.current?.close()
    }
  }, [])

  return { connected, lastUpdate }
}
