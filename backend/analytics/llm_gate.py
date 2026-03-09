"""
OpenRouter LLM synthesis gate.

Called ONLY for borderline decisions (confidence 42-62%) where
quantitative signals are ambiguous. Uses an LLM to synthesize
all available context and make a final APPROVE/REJECT decision.

Supports: OpenRouter (primary), OpenAI (fallback)

All LLM decisions are persisted to llm_decisions table.
"""

import json
import logging
import time
from typing import Dict, Optional

import requests

from config import config
from db import get_connection

logger = logging.getLogger(__name__)

OPENROUTER_BASE = "https://openrouter.ai/api/v1/chat/completions"
OPENAI_BASE = "https://api.openai.com/v1/chat/completions"

MODEL = config.OPENROUTER_MODEL
LLM_ENABLED = config.LLM_GATE_ENABLED
CONF_MIN = config.LLM_GATE_CONF_MIN
CONF_MAX = config.LLM_GATE_CONF_MAX

_LLM_CALL_TIMEOUT = 8  # seconds — must be fast for trading

SYSTEM_PROMPT = """You are a quant analyst for a prediction market trading system.
You are the final gate for borderline trade decisions. All quantitative checks have passed,
but confidence is in the uncertain zone (42-62%). Your job: make a decisive call.

Rules:
- Be concise. One word decision: APPROVE or REJECT
- Then one sentence reasoning (max 20 words)
- Format: DECISION: APPROVE|REJECT\nREASON: <one sentence>
- Approve only if you see genuine edge, not just noise
- When in doubt: REJECT (capital preservation over marginal trades)
- Consider: strong spot momentum > weak pattern match
- Consider: cross-asset divergence is a warning sign
- Consider: high volatility regime → prefer REJECT
- Consider: Fair Value Gap > 5¢ → APPROVE if other signals align
"""


def _build_prompt(context: Dict) -> str:
    asset = context.get("asset", "?")
    interval = context.get("interval_minutes", "?")
    side = context.get("predicted_side", "?")
    price = context.get("order_price", 0.40)
    win_rate = context.get("win_rate", 0)
    confidence = context.get("confidence", 0)
    ev = context.get("ev_score", 0)
    pattern = context.get("pattern_str", "?")
    regime = context.get("regime", "NORMAL")
    time_remaining = context.get("time_remaining_s", 300)

    # Signal details
    signals = context.get("live_signals", {})
    momentum = signals.get("spot_momentum", {})
    cross = signals.get("cross_asset", {})
    fvg = signals.get("fair_value_gap", {})
    usd_rev = signals.get("usd_reversal", {})
    hotspot = context.get("hotspot", {})
    impulse = context.get("impulse", {})
    calib = context.get("calibration", {})

    lines = [
        f"TRADE DECISION NEEDED: BUY {side} on {asset} {interval}m market at {price:.2f}¢",
        f"Time remaining in window: {time_remaining}s",
        f"",
        f"QUANTITATIVE CONTEXT:",
        f"  Pattern: {pattern} | Pattern win rate: {win_rate:.1f}% | Samples: {context.get('sample_count', 0)}",
        f"  EV after fees: ${ev:.4f} | System confidence: {confidence:.0f}/100",
        f"  Market regime: {regime}",
        f"",
        f"CALIBRATED PROBABILITY (historical Bayesian model):",
        f"  P(UP) historical: {calib.get('p_hist', 0.5)*100:.1f}%",
        f"  P(UP) market-implied: {(calib.get('p_market') or 0.5)*100:.1f}%",
        f"  Blended P(UP): {calib.get('p_win', 0.5)*100:.1f}%",
        f"  Fair Value Gap: {calib.get('fvg_cents', 0):.1f}¢ (+ means market underpricing)",
        f"  Sample N: {calib.get('sample_n', 0)}",
        f"",
        f"LIVE SIGNALS:",
        f"  Spot Momentum: dir={momentum.get('direction','?')} conf={momentum.get('confidence',0):.0f}",
        f"    spot_30s={momentum.get('spot_change_30s', 0)*100:.2f}% spot_60s={momentum.get('spot_change_60s', 0)*100:.2f}%",
        f"  Cross-Asset: {cross.get('aligned_count', 0)}/4 aligned, multiplier={cross.get('confirmation_multiplier', 1):.2f}",
        f"  Fair Value Gap: {fvg.get('fvg_cents', 0):.1f}¢ edge",
        f"  USD Reversal: p_reversal={usd_rev.get('p_reversal', 0)*100:.1f}%",
    ]

    if hotspot.get("active"):
        lines.append(
            f"  Hotspot: price dwelling in {hotspot['zone_lo']:.0f}-{hotspot['zone_hi']:.0f}¢ zone "
            f"for {hotspot['dwell_seconds']}s, dominant={hotspot['dominant_side']}"
        )

    if impulse.get("active"):
        lines.append(
            f"  Impulse: {impulse['move_cents']:.0f}¢ {impulse['direction']} {impulse['duration_s']}s ago, "
            f"cont_prob={impulse['continuation_probability']*100:.0f}% rev_prob={impulse['reversal_probability']*100:.0f}%"
        )

    lines.extend([
        f"",
        f"QUESTION: Should we APPROVE or REJECT this {side} order on {asset}?",
        f"Respond with: DECISION: APPROVE|REJECT\nREASON: <one concise sentence>",
    ])

    return "\n".join(lines)


def _call_openrouter(prompt: str) -> Optional[Dict]:
    """Call OpenRouter API."""
    if not config.OPENROUTER_API_KEY:
        return None
    try:
        start = time.time()
        resp = requests.post(
            OPENROUTER_BASE,
            headers={
                "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://predictedge.vitwit.com",
                "X-Title": "PredictEdge Quant",
            },
            json={
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 80,
                "temperature": 0.1,
            },
            timeout=_LLM_CALL_TIMEOUT,
        )
        latency_ms = int((time.time() - start) * 1000)
        if resp.status_code == 200:
            text = resp.json()["choices"][0]["message"]["content"].strip()
            return {"text": text, "latency_ms": latency_ms, "model": MODEL, "source": "openrouter"}
    except Exception as e:
        logger.debug("OpenRouter call failed: %s", e)
    return None


def _call_openai_fallback(prompt: str) -> Optional[Dict]:
    """Fallback to OpenAI if OpenRouter fails."""
    if not config.OPENAI_API_KEY:
        return None
    try:
        start = time.time()
        resp = requests.post(
            OPENAI_BASE,
            headers={"Authorization": f"Bearer {config.OPENAI_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 80,
                "temperature": 0.1,
            },
            timeout=_LLM_CALL_TIMEOUT,
        )
        latency_ms = int((time.time() - start) * 1000)
        if resp.status_code == 200:
            text = resp.json()["choices"][0]["message"]["content"].strip()
            return {"text": text, "latency_ms": latency_ms, "model": "gpt-4o-mini", "source": "openai"}
    except Exception as e:
        logger.debug("OpenAI fallback failed: %s", e)
    return None


def _parse_llm_response(text: str) -> tuple:
    """Parse LLM response into (decision, reasoning)."""
    decision = "REJECT"
    reasoning = text

    lines = text.upper()
    if "DECISION: APPROVE" in lines or lines.startswith("APPROVE"):
        decision = "APPROVE"
    elif "DECISION: REJECT" in lines or lines.startswith("REJECT"):
        decision = "REJECT"

    # Extract reason
    for line in text.split("\n"):
        if line.upper().startswith("REASON:"):
            reasoning = line[7:].strip()
            break

    return decision, reasoning


def _persist_llm_decision(
    slug: str,
    asset: str,
    interval: int,
    model: str,
    prompt: str,
    response_text: str,
    decision: str,
    reasoning: str,
    confidence_in: float,
    latency_ms: int,
) -> Optional[int]:
    try:
        conn = get_connection()
        cur = conn.execute(
            """
            INSERT INTO llm_decisions
            (slug, asset, interval_minutes, model, prompt_context, llm_response,
             decision, reasoning, confidence_in, latency_ms, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                slug, asset, interval, model,
                prompt[:2000],  # truncate large prompts
                response_text[:500],
                decision, reasoning, confidence_in,
                latency_ms, int(time.time()),
            ),
        )
        conn.commit()
        eid = cur.lastrowid
        conn.close()
        return eid
    except Exception as e:
        logger.error("llm_gate persist failed: %s", e)
        return None


def evaluate(context: Dict) -> Dict:
    """
    Main LLM gate evaluation.

    Called when quantitative confidence is in the borderline zone.

    Args:
      context: full decision context dict (signal_inputs + live signals + features)

    Returns:
      {
        decision: APPROVE | REJECT | SKIP (SKIP = LLM unavailable, use quant decision)
        reasoning: str
        model: str
        latency_ms: int
        called: bool
      }
    """
    if not LLM_ENABLED:
        return {"decision": "SKIP", "reasoning": "LLM gate disabled", "called": False}

    confidence = float(context.get("confidence", 50))

    # Only call LLM for borderline decisions
    if confidence < CONF_MIN or confidence > CONF_MAX:
        return {
            "decision": "SKIP",
            "reasoning": f"Confidence {confidence:.0f} outside LLM zone [{CONF_MIN},{CONF_MAX}]",
            "called": False,
        }

    slug = context.get("slug", "unknown")
    asset = context.get("asset", "?")
    interval = context.get("interval_minutes", 5)

    prompt = _build_prompt(context)

    # Try OpenRouter first, then OpenAI
    result = _call_openrouter(prompt) or _call_openai_fallback(prompt)

    if not result:
        logger.warning("[llm_gate] No LLM available for %s %s — passing through", asset, slug)
        return {"decision": "SKIP", "reasoning": "No LLM available", "called": False}

    decision, reasoning = _parse_llm_response(result["text"])

    _persist_llm_decision(
        slug=slug,
        asset=asset,
        interval=interval,
        model=result["model"],
        prompt=prompt,
        response_text=result["text"],
        decision=decision,
        reasoning=reasoning,
        confidence_in=confidence,
        latency_ms=result["latency_ms"],
    )

    logger.info(
        "[llm_gate] %s %s %sm → %s (conf_in=%.0f latency=%dms): %s",
        asset, slug, interval, decision, confidence, result["latency_ms"], reasoning,
    )

    return {
        "decision": decision,
        "reasoning": reasoning,
        "model": result["model"],
        "latency_ms": result["latency_ms"],
        "called": True,
        "source": result["source"],
    }


def get_recent_decisions(limit: int = 20) -> list:
    """Fetch recent LLM decisions for the dashboard."""
    try:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT slug, asset, interval_minutes, model, decision, reasoning,
                   confidence_in, latency_ms, created_at
            FROM llm_decisions
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("llm_gate get_recent failed: %s", e)
        return []
