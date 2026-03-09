import axios from 'axios'

export const api = axios.create({
  baseURL: '/api',
  timeout: 15000,
})

export interface Streak {
  asset: string
  interval: number
  streak_length: number
  direction: string
  last_10: string[]
  last_ts: number | null
}

export interface SpotPrices {
  [asset: string]: number | null
}

export interface MarketResolution {
  slug: string
  winner_side: string
  open_up_price: number | null
  close_up_price: number | null
  spot_change_usd: number | null
  spot_change_pct: number | null
  start_ts: number
  end_ts: number
  clean_resolution: number | null
  false_pump: number | null
  late_reversal: number | null
}

export interface PatternResult {
  asset: string
  interval: number
  pattern: string[]
  pattern_str: string
  occurrences: number
  up_pct: number
  down_pct: number
  sample_count: number
  edge: number
  last_seen_ts: number | null
}

export interface MomentumBucket {
  label: string
  delta_min: number
  delta_max: number
  total_samples: number
  reversal_count: number
  reversal_pct: number
  avg_next_move: number
  signal: string
}

// Health
export const health = () => api.get('/health').then(r => r.data)

// Live
export const getLiveStreaks = () => api.get('/live/streaks').then(r => r.data)
export const getLivePrices = () => api.get('/live/prices').then(r => r.data)
export const getRecentMarkets = (asset: string, interval: number, limit = 50) =>
  api.get('/markets/recent', { params: { asset, interval, limit } }).then(r => r.data)
export const getMarketStats = () => api.get('/markets/stats').then(r => r.data)

// Analytics
export const getStreakReversal = (asset?: string, interval?: number) =>
  api.get('/analytics/streaks/reversal', { params: { asset, interval } }).then(r => r.data)

export const scanPattern = (asset: string, interval: number, pattern: string[]) =>
  api.post('/analytics/patterns/scan', { asset, interval, pattern }).then(r => r.data)

export const getPatternMatrix = (asset: string, interval: number, seq_len = 3) =>
  api.get('/analytics/patterns/matrix', { params: { asset, interval, seq_len } }).then(r => r.data)

export const getTopPatterns = (asset: string, interval: number, min_samples = 20) =>
  api.get('/analytics/patterns/top', { params: { asset, interval, min_samples } }).then(r => r.data)

export default api

export const getLiveSignals = (interval: number) =>
  api.get('/trading/live-signals', { params: { interval } }).then(r => r.data)

export const getUsdReversalBins = (asset: string, interval: number) =>
  api.get('/analytics/usd-reversal-bins', { params: { asset, interval } }).then(r => r.data)

export const getSignalEvents = (params: {
  asset?: string
  interval?: number
  decision?: string
  limit?: number
}) => api.get('/trading/signal-events', { params }).then(r => r.data)

export const getPatternPredictionsReality = (
  asset?: string,
  interval?: number,
  top_n = 10,
  recent_limit = 50
) =>
  api
    .get('/analytics/patterns/predictions-reality', { params: { asset, interval, top_n, recent_limit } })
    .then(r => r.data)

export const getMomentumStats = (asset: string, interval: number) =>
  api.get('/analytics/momentum', { params: { asset, interval } }).then(r => r.data)

export const getPeakTrough = (asset: string, interval: number) =>
  api.get('/analytics/peak-trough', { params: { asset, interval } }).then(r => r.data)

export const getEarlyPeriod = (asset: string, interval: number) =>
  api.get('/analytics/early-period', { params: { asset, interval } }).then(r => r.data)

export const getHourlyBias = (asset: string, interval: number, lookback_days = 90) =>
  api.get('/analytics/temporal/hourly', { params: { asset, interval, lookback_days } }).then(r => r.data)

export const getDailyBias = (asset: string, interval: number) =>
  api.get('/analytics/temporal/daily', { params: { asset, interval } }).then(r => r.data)

export const getSessionStats = (asset: string, interval: number) =>
  api.get('/analytics/temporal/sessions', { params: { asset, interval } }).then(r => r.data)

export const getTimeRemainingProb = (asset: string, interval: number) =>
  api.get('/analytics/temporal/time-remaining', { params: { asset, interval } }).then(r => r.data)

export const getCorrelationMatrix = (interval: number) =>
  api.get('/analytics/correlation/matrix', { params: { interval } }).then(r => r.data)

export const getSpotCorrelation = (asset: string, interval: number) =>
  api.get('/analytics/correlation/spot', { params: { asset, interval } }).then(r => r.data)

export const getMacroEvents = (limit = 20) =>
  api.get('/macro/events', { params: { limit } }).then(r => r.data)

export const runBacktest = (params: {
  strategy: string
  asset: string
  interval: number
  streak_n?: number
  direction?: string
  max_price?: number
  order_size?: number
  spike_threshold?: number
}) => api.post('/backtest', params).then(r => r.data)

export const askCopilot = (query: string, context = {}) =>
  api.post('/copilot', { query, context }).then(r => r.data)

export const createStrategy = (name: string, description: string, config: object) =>
  api.post('/strategies', { name, description, config_json: config }).then(r => r.data)

export const listStrategies = () =>
  api.get('/strategies').then(r => r.data)

// ── Quant Intelligence API ─────────────────────────────────────────────────
export const getQuantRegime = () =>
  api.get('/quant/regime').then(r => r.data)

export const getQuantRegimeAsset = (asset: string) =>
  api.get(`/quant/regime/${asset}`).then(r => r.data)

export const getEdgeHealth = () =>
  api.get('/quant/edge-health').then(r => r.data)

export const getPortfolioState = () =>
  api.get('/quant/portfolio-state').then(r => r.data)

export const resetCircuitBreaker = () =>
  api.post('/quant/circuit-breaker/reset').then(r => r.data)

export const getSignalTape = (limit = 50) =>
  api.get('/quant/signal-tape', { params: { limit } }).then(r => r.data)

export const getHotspot = (asset: string, interval: number) =>
  api.get(`/quant/hotspot/${asset}/${interval}`).then(r => r.data)

export const getImpulse = (asset: string, interval: number) =>
  api.get(`/quant/impulse/${asset}/${interval}`).then(r => r.data)

export const getCalibration = (asset: string, interval: number, spot_change_pct: number, clob_mid?: number, predicted_side = 'UP') =>
  api.get('/quant/calibration', { params: { asset, interval_minutes: interval, spot_change_pct, clob_mid, predicted_side } }).then(r => r.data)

export const getLlmDecisions = (limit = 20) =>
  api.get('/quant/llm-decisions', { params: { limit } }).then(r => r.data)

export const getOrderPerformance = (limit = 100) =>
  api.get('/quant/order-performance', { params: { limit } }).then(r => r.data)
