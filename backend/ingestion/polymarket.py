"""
Polymarket CLOB data ingestion.
Fetches active BTC/ETH/SOL prediction markets and tracks price ticks.
"""
import time
import json
import requests
import threading
import logging
import re
import sys
from typing import Dict, List, Optional
from datetime import datetime

from config import config
from db import get_connection

logger = logging.getLogger(__name__)

GAMMA_BASE = config.GAMMA_HOST
CLOB_BASE = config.CLOB_HOST

MARKET_KEYWORDS = {
    "BTC": ["bitcoin", "btc"],
    "ETH": ["ethereum", "eth"],
    "SOL": ["solana", "sol"],
    "XRP": ["xrp", "ripple"],
}
ASSET_SLUG_CODES = {
    "BTC": "btc",
    "ETH": "eth",
    "SOL": "sol",
    "XRP": "xrp",
}
INTERVAL_SLUG_VARIANTS = {
    5: ["5m"],
    15: ["15m"],
    60: ["1h", "60m"],
}

_WORD_BOUNDARY_CACHE = {
    asset: [re.compile(rf"\b{re.escape(keyword)}\b") for keyword in keywords]
    for asset, keywords in MARKET_KEYWORDS.items()
}
_INTERVAL_PATTERNS = [
    (60, re.compile(r"\b(1h|1hr|1 hour|60m|60 min)\b", re.IGNORECASE)),
    (15, re.compile(r"\b(15m|15-min|15 min)\b", re.IGNORECASE)),
    (5, re.compile(r"\b(5m|5-min|5 min)\b", re.IGNORECASE)),
]


def _parse_token_ids(market: Dict) -> List[str]:
    """Extract CLOB token IDs from Gamma market payload variants."""
    token_ids: List[str] = []

    raw_clob_ids = market.get("clobTokenIds")
    if isinstance(raw_clob_ids, str):
        try:
            parsed = json.loads(raw_clob_ids)
            if isinstance(parsed, list):
                token_ids.extend(str(v) for v in parsed if v)
        except Exception:
            pass
    elif isinstance(raw_clob_ids, list):
        token_ids.extend(str(v) for v in raw_clob_ids if v)

    raw_tokens = market.get("tokens")
    if isinstance(raw_tokens, list):
        for token in raw_tokens:
            if isinstance(token, dict):
                token_id = token.get("token_id") or token.get("tokenId") or token.get("id")
                if token_id:
                    token_ids.append(str(token_id))
            elif isinstance(token, str):
                token_ids.append(token)

    deduped: List[str] = []
    seen = set()
    for token_id in token_ids:
        if token_id not in seen:
            deduped.append(token_id)
            seen.add(token_id)
    return deduped


def _detect_asset(text: str) -> Optional[str]:
    """Detect asset using whole-word matching to avoid false positives."""
    text_lower = text.lower()
    for asset, patterns in _WORD_BOUNDARY_CACHE.items():
        if any(p.search(text_lower) for p in patterns):
            return asset
    return None


def _detect_interval_minutes(text: str) -> Optional[int]:
    text_lower = text.lower()
    for minutes, pattern in _INTERVAL_PATTERNS:
        if pattern.search(text_lower):
            return minutes
    return None


def _parse_end_ts(end_date: Optional[str]) -> Optional[int]:
    if not end_date:
        return None
    try:
        return int(datetime.fromisoformat(end_date.replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


def _parse_json_list(value) -> List:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _extract_yes_close_price(market: Dict) -> Optional[float]:
    """
    For binary markets, treat first outcome (typically "Yes") as UP leg.
    Returns final close price in [0, 1] when available.
    """
    prices = _parse_json_list(market.get("outcomePrices"))
    if not prices:
        prices = _parse_json_list(market.get("outcome_prices"))
    if not prices:
        return None
    try:
        return float(prices[0])
    except Exception:
        return None


def _nearest_spot_price(asset: str, target_ts: int, conn) -> Optional[float]:
    row = conn.execute(
        """
        SELECT price_usd
        FROM spot_prices
        WHERE asset = ?
        ORDER BY ABS(captured_at - ?) ASC
        LIMIT 1
        """,
        (asset.upper(), target_ts),
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def _get_latest_spot_price(asset: str, conn=None) -> Optional[float]:
    close_conn = conn is None
    if close_conn:
        conn = get_connection()
    try:
        row = conn.execute(
            "SELECT price_usd FROM spot_prices WHERE asset = ? ORDER BY captured_at DESC LIMIT 1",
            (asset.upper(),),
        ).fetchone()
        return float(row[0]) if row and row[0] is not None else None
    finally:
        if close_conn:
            conn.close()


def _extract_orderbook_metrics(book: Optional[Dict], midpoint: float) -> tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    if not isinstance(book, dict):
        return None, None, None, None
    try:
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        best_bid = float(bids[0]["price"]) if bids else None
        best_ask = float(asks[0]["price"]) if asks else None

        bid_liq = 0.0
        ask_liq = 0.0
        for bid in bids:
            price = float(bid.get("price", 0))
            size = float(bid.get("size", 0))
            if midpoint is not None and abs(midpoint - price) <= 0.05:
                bid_liq += size
        for ask in asks:
            price = float(ask.get("price", 0))
            size = float(ask.get("size", 0))
            if midpoint is not None and abs(midpoint - price) <= 0.05:
                ask_liq += size

        total_liq = bid_liq + ask_liq
        imbalance = ((bid_liq - ask_liq) / total_liq) if total_liq > 0 else None
        return best_bid, best_ask, total_liq if total_liq > 0 else None, imbalance
    except Exception:
        return None, None, None, None


def _fetch_previous_ticks(slug: str, conn, limit: int = 24) -> List[Dict]:
    rows = conn.execute(
        """
        SELECT elapsed_seconds AS elapsed, up_price, spot_price
        FROM price_ticks
        WHERE slug = ?
        ORDER BY elapsed_seconds DESC
        LIMIT ?
        """,
        (slug, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_active_crypto_markets() -> List[Dict]:
    """Fetch active BTC/ETH/SOL/XRP updown markets via generated slug lookup."""
    try:
        result = []
        now = int(time.time())
        for interval in (5, 15, 60):
            step = interval * 60
            # check current and previous window in case of rollovers
            window_starts = [now - (now % step), (now - (now % step)) - step]
            for asset in ("BTC", "ETH", "SOL", "XRP"):
                for window_start in window_starts:
                    market = None
                    for slug in _build_slug_variants(asset, interval, window_start):
                        market = _fetch_gamma_market_by_slug(slug)
                        if market:
                            break
                    if not market:
                        continue
                    if not market.get("active", False):
                        continue
                    if market.get("closed", False):
                        continue
                    token_ids = _parse_token_ids(market)
                    if not token_ids:
                        continue
                    result.append({
                        "slug": market.get("slug", ""),
                        "asset": asset,
                        "interval_minutes": interval,
                        "title": market.get("question") or market.get("title") or "",
                        "condition_id": market.get("conditionId"),
                        "token_ids": token_ids,
                        "up_token_id": token_ids[0],
                        "down_token_id": token_ids[1] if len(token_ids) > 1 else None,
                        "end_date": market.get("endDate"),
                        "market_maker_address": market.get("marketMakerAddress"),
                    })
                    break

        # de-duplicate by slug
        unique = {}
        for row in result:
            slug = row.get("slug")
            if slug:
                unique[slug] = row
        return list(unique.values())
    except Exception as e:
        logger.error(f"Failed to fetch markets: {e}")
        return []


def fetch_closed_crypto_markets_since(days: int = 30, page_limit: int = 200, max_pages: int = 20) -> List[Dict]:
    """Fetch closed crypto markets from Gamma since N days ago."""
    now_ts = int(time.time())
    cutoff_ts = int(time.time()) - days * 24 * 3600
    cutoff_iso = datetime.utcfromtimestamp(cutoff_ts).strftime("%Y-%m-%dT%H:%M:%SZ")
    def _scan(include_end_date_min: bool) -> List[Dict]:
        out: List[Dict] = []
        offset = 0
        for _ in range(max_pages):
            params = {
                "closed": "true",
                "tag_slug": "crypto",
                "limit": page_limit,
                "offset": offset,
            }
            if include_end_date_min:
                params["end_date_min"] = cutoff_iso
            resp = requests.get(f"{GAMMA_BASE}/markets", params=params, timeout=15)
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break

            for market in batch:
                normalized = _normalize_closed_market(
                    market,
                    cutoff_ts=cutoff_ts,
                    require_known_asset=True,
                    require_supported_interval=True,
                )
                if normalized and normalized.get("end_ts", 0) <= now_ts:
                    out.append(normalized)

            offset += page_limit
            if len(batch) < page_limit:
                break
        return out

    # Primary path with end_date_min filter.
    markets = _scan(include_end_date_min=True)
    # Gamma can intermittently return empty for end_date_min; fallback to local cutoff filtering.
    if not markets:
        markets = _scan(include_end_date_min=False)
    return markets


def _fetch_closed_gamma_batch(offset: int, limit: int) -> List[Dict]:
    params = {
        "closed": "true",
        "tag_slug": "crypto",
        "limit": limit,
        "offset": offset,
    }
    resp = requests.get(f"{GAMMA_BASE}/markets", params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _fetch_all_tags(limit: int = 200, max_pages: int = 30) -> List[Dict]:
    tags: List[Dict] = []
    seen_ids = set()
    for page in range(max_pages):
        offset = page * limit
        resp = requests.get(f"{GAMMA_BASE}/tags", params={"limit": limit, "offset": offset}, timeout=20)
        resp.raise_for_status()
        batch = resp.json()
        if not isinstance(batch, list) or not batch:
            break
        added = 0
        for tag in batch:
            tag_id = tag.get("id")
            if tag_id in seen_ids:
                continue
            seen_ids.add(tag_id)
            tags.append(tag)
            added += 1
        if len(batch) < limit or added == 0:
            break
    return tags


def _fetch_crypto_tag_ids() -> List[int]:
    """Discover relevant crypto tag IDs from paginated Gamma tags endpoint."""
    tags = _fetch_all_tags()
    tag_ids: List[int] = []
    interval_tag_ids: List[int] = []
    fallback_tag_ids: List[int] = []
    for tag in tags:
        slug = str(tag.get("slug", "")).lower()
        label = str(tag.get("label", "")).lower()
        if slug in {"5m", "15m", "1h", "60m"} or label in {"5m", "15m", "1h", "60m"}:
            try:
                interval_tag_ids.append(int(tag.get("id")))
            except Exception:
                continue
        if slug in {"crypto", "cryptocurrency", "up-or-down"} or "crypto" in slug or "live crypto" in label:
            try:
                fallback_tag_ids.append(int(tag.get("id")))
            except Exception:
                continue
    # Prefer interval tags (5m/15m/1h) because they directly target window markets.
    tag_ids.extend(interval_tag_ids if interval_tag_ids else fallback_tag_ids)
    # de-dupe preserving order
    unique: List[int] = []
    seen = set()
    for t in tag_ids:
        if t not in seen:
            unique.append(t)
            seen.add(t)
    return unique


def _fetch_events_by_tag(tag_id: int, limit: int, offset: int) -> List[Dict]:
    params = {
        "tag_id": tag_id,
        "limit": limit,
        "offset": offset,
        "active": "false",
        "closed": "true",
    }
    resp = requests.get(f"{GAMMA_BASE}/events", params=params, timeout=20)
    resp.raise_for_status()
    body = resp.json()
    return body if isinstance(body, list) else []


def _count_event_pages_for_tag(tag_id: int, page_limit: int, max_pages_scan: int = 5000) -> int:
    """
    Count pages quickly using exponential search + binary search.
    Page index is 1-based where page 1 => offset 0.
    """
    cache: Dict[int, bool] = {}

    def has_page(page_index: int) -> bool:
        if page_index < 1:
            return False
        if page_index in cache:
            return cache[page_index]
        if page_index > max_pages_scan:
            cache[page_index] = False
            return False
        offset = (page_index - 1) * page_limit
        events = _fetch_events_by_tag(tag_id=tag_id, limit=page_limit, offset=offset)
        exists = len(events) > 0
        cache[page_index] = exists
        return exists

    if not has_page(1):
        return 0

    high = 1
    while has_page(high + 1):
        high *= 2
        if high >= max_pages_scan:
            high = max_pages_scan
            break

    low = 1
    while low < high:
        mid = (low + high + 1) // 2
        if has_page(mid):
            low = mid
        else:
            high = mid - 1
    return low


def _build_slug_variants(asset: str, interval_minutes: int, window_start_ts: int) -> List[str]:
    code = ASSET_SLUG_CODES.get(asset.upper())
    variants = INTERVAL_SLUG_VARIANTS.get(interval_minutes, [])
    if not code or not variants:
        return []
    return [f"{code}-updown-{variant}-{window_start_ts}" for variant in variants]


def _fetch_gamma_market_by_slug(slug: str) -> Optional[Dict]:
    """
    Fetch a market by slug. Uses /events endpoint which also returns
    eventMetadata.priceToBeat (Chainlink open price, available for BTC markets).
    Falls back to /markets if /events returns no results.
    """
    # Try /events first — returns priceToBeat for BTC markets
    try:
        resp = requests.get(f"{GAMMA_BASE}/events", params={"slug": slug}, timeout=12)
        resp.raise_for_status()
        events = resp.json()
        if isinstance(events, list) and events:
            event = events[0]
            markets = event.get("markets") or []
            price_to_beat = (event.get("eventMetadata") or {}).get("priceToBeat")
            for market in markets:
                if market.get("slug") == slug:
                    market["_price_to_beat"] = price_to_beat
                    return market
            # event slug matches but market list uses different slug structure
            if markets:
                markets[0]["_price_to_beat"] = price_to_beat
                return markets[0]
    except Exception:
        pass
    # Fallback to /markets
    resp = requests.get(f"{GAMMA_BASE}/markets", params={"slug": slug}, timeout=12)
    resp.raise_for_status()
    rows = resp.json()
    if not isinstance(rows, list):
        return None
    for row in rows:
        if row.get("slug") == slug:
            return row
    return None


def _build_time_candidates(days: int = 30) -> List[Dict]:
    now = int(time.time())
    start_ts = now - days * 24 * 3600
    candidates: List[Dict] = []
    for interval in (5, 15, 60):
        step = interval * 60
        first = start_ts - (start_ts % step)
        last = now - (now % step)
        ts = first
        while ts <= last:
            for asset in ("BTC", "ETH", "SOL", "XRP"):
                candidates.append({
                    "asset": asset,
                    "interval_minutes": interval,
                    "window_start_ts": ts,
                })
            ts += step
    candidates.sort(key=lambda x: x["window_start_ts"], reverse=True)
    return candidates


def _normalize_closed_market(
    market: Dict,
    cutoff_ts: Optional[int] = None,
    require_known_asset: bool = True,
    require_supported_interval: bool = True,
    price_to_beat: Optional[float] = None,
) -> Optional[Dict]:
    now_ts = int(time.time())
    title = market.get("question") or market.get("title") or ""
    slug = market.get("slug", "")
    combined_text = f"{slug} {title}"
    asset = _detect_asset(combined_text)
    if not asset and require_known_asset:
        return None
    if not asset:
        return None
    interval = _detect_interval_minutes(combined_text)
    if require_supported_interval and interval is None:
        return None

    end_ts = _parse_end_ts(market.get("endDate"))
    if not end_ts:
        return None
    if cutoff_ts is not None and end_ts < cutoff_ts:
        return None
    if end_ts > now_ts:
        return None

    token_ids = _parse_token_ids(market)
    return {
        "slug": slug,
        "asset": asset,
        "interval_minutes": interval,
        "title": title,
        "token_ids": token_ids,
        "up_token_id": token_ids[0] if token_ids else None,
        "down_token_id": token_ids[1] if len(token_ids) > 1 else None,
        "end_date": market.get("endDate"),
        "end_ts": end_ts,
        "close_up_price": _extract_yes_close_price(market),
        "price_to_beat": price_to_beat,
    }


def _upsert_historical_market(market: Dict, conn, now: int):
    slug = market["slug"]
    asset = market["asset"]
    interval = market.get("interval_minutes")
    if interval is None:
        interval = parse_interval_from_slug(slug)
    if interval is None:
        return
    end_ts = market["end_ts"]
    start_ts = max(0, end_ts - interval * 60)

    close_up = market.get("close_up_price")
    winner_side = None
    if close_up is not None:
        if close_up >= 0.999:
            winner_side = "UP"
        elif close_up <= 0.001:
            winner_side = "DOWN"

    open_up_row = conn.execute(
        "SELECT open_up_price FROM market_resolutions WHERE slug = ?",
        (slug,),
    ).fetchone()
    open_up = float(open_up_row[0]) if open_up_row and open_up_row[0] is not None else (close_up if close_up is not None else 0.5)

    # Chainlink open price from eventMetadata.priceToBeat (available for BTC only)
    chainlink_open = market.get("price_to_beat")
    if chainlink_open is not None:
        chainlink_open = float(chainlink_open)
        spot_open = chainlink_open
        # Close price will be set later by resync_chainlink.py (= next window's chainlink_open)
        spot_close = _nearest_spot_price(asset, end_ts, conn)
        if spot_close is None:
            spot_close = chainlink_open  # fallback: same price (change=0) until next window fetched
    else:
        spot_open = _nearest_spot_price(asset, start_ts, conn)
        spot_close = _nearest_spot_price(asset, end_ts, conn)

    spot_change = (spot_close - spot_open) if (spot_open is not None and spot_close is not None) else None
    spot_change_pct = ((spot_change / spot_open) * 100.0) if (spot_change is not None and spot_open) else None

    conn.execute(
        """
        INSERT INTO market_resolutions
        (slug, asset, interval_minutes, start_ts, end_ts,
         open_up_price, close_up_price,
         open_spot_price, close_spot_price,
         chainlink_open,
         spot_open, spot_close, spot_change_usd, spot_change_pct,
         winner_side, resolved_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(slug) DO UPDATE SET
            asset = excluded.asset,
            interval_minutes = excluded.interval_minutes,
            start_ts = excluded.start_ts,
            end_ts = excluded.end_ts,
            open_up_price = COALESCE(market_resolutions.open_up_price, excluded.open_up_price),
            close_up_price = COALESCE(excluded.close_up_price, market_resolutions.close_up_price),
            open_spot_price = COALESCE(market_resolutions.open_spot_price, excluded.open_spot_price),
            close_spot_price = COALESCE(excluded.close_spot_price, market_resolutions.close_spot_price),
            chainlink_open = COALESCE(excluded.chainlink_open, market_resolutions.chainlink_open),
            spot_open = CASE WHEN excluded.chainlink_open IS NOT NULL THEN excluded.chainlink_open
                             WHEN excluded.spot_open IS NOT NULL AND excluded.spot_open != 0
                             THEN excluded.spot_open ELSE COALESCE(market_resolutions.spot_open, excluded.spot_open) END,
            spot_close = CASE WHEN excluded.spot_close IS NOT NULL AND excluded.spot_close != 0
                              THEN excluded.spot_close ELSE COALESCE(market_resolutions.spot_close, excluded.spot_close) END,
            spot_change_usd = CASE WHEN excluded.spot_change_usd IS NOT NULL AND excluded.spot_change_usd != 0
                                   THEN excluded.spot_change_usd ELSE COALESCE(market_resolutions.spot_change_usd, excluded.spot_change_usd) END,
            spot_change_pct = CASE WHEN excluded.spot_change_pct IS NOT NULL AND excluded.spot_change_pct != 0
                                   THEN excluded.spot_change_pct ELSE COALESCE(market_resolutions.spot_change_pct, excluded.spot_change_pct) END,
            winner_side = COALESCE(excluded.winner_side, market_resolutions.winner_side),
            resolved_at = COALESCE(excluded.resolved_at, market_resolutions.resolved_at)
        """,
        (
            slug, asset, interval, start_ts, end_ts,
            open_up, close_up,
            spot_open, spot_close,
            chainlink_open,
            spot_open, spot_close, spot_change, spot_change_pct,
            winner_side, end_ts, now,
        ),
    )

    # Link this window to the previous consecutive window for reversal tracking
    if spot_change is not None:
        prev = conn.execute(
            """SELECT spot_change_usd, spot_change_pct, winner_side
               FROM market_resolutions
               WHERE asset=? AND interval_minutes=? AND end_ts=?
               LIMIT 1""",
            (asset, interval, start_ts),
        ).fetchone()
        if prev and prev["spot_change_usd"] is not None:
            conn.execute(
                """UPDATE market_resolutions
                   SET prev_spot_change_usd=?, prev_spot_change_pct=?, prev_winner_side=?
                   WHERE slug=?""",
                (prev["spot_change_usd"], prev["spot_change_pct"], prev["winner_side"], slug),
            )


def sync_historical_markets(days: int = 30) -> Dict[str, int]:
    """
    Backfill closed crypto markets from recent history.
    Uses Gamma metadata and local spot snapshots to populate market_resolutions.
    """
    markets = fetch_closed_crypto_markets_since(days=days)
    if not markets:
        # Fallback path: Gamma /markets closed feed can intermittently return empty.
        # Use tag/events pagination for latest pages to keep market_resolutions fresh.
        try:
            fallback = sync_all_historical_markets(
                start_page=1,
                end_page=8,
                days=days,
                show_progress=False,
            )
            return {
                "markets_fetched": int(fallback.get("markets_counted", 0)),
                "rows_upserted": int(fallback.get("rows_upserted", 0)),
            }
        except Exception:
            return {"markets_fetched": 0, "rows_upserted": 0}

    conn = get_connection()
    upserted = 0
    try:
        now = int(time.time())
        for market in markets:
            _upsert_historical_market(market, conn=conn, now=now)
            upserted += 1
        conn.commit()
    finally:
        conn.close()

    return {"markets_fetched": len(markets), "rows_upserted": upserted}


def sync_all_historical_markets(
    page_limit: int = 200,
    start_page: int = 1,
    end_page: int = 100,
    days: int = 30,
    show_progress: bool = True,
) -> Dict[str, int]:
    """
    Backfill closed crypto markets from Gamma tags/events endpoint.
    Pages map to bounded event offsets, e.g. 1-100 then 101-200.
    """
    if start_page < 1:
        start_page = 1
    if end_page < start_page:
        end_page = start_page

    cutoff_ts = int(time.time()) - days * 24 * 3600
    tag_ids = _fetch_crypto_tag_ids()
    if not tag_ids:
        if show_progress:
            print("[clob-sync] No crypto tag IDs found from /tags.")
        return {
            "markets_counted": 0,
            "rows_upserted": 0,
            "start_page": start_page,
            "end_page": end_page,
            "days": days,
        }

    if show_progress:
        print(f"[clob-sync] Using tag IDs: {tag_ids} (days={days}, pages {start_page}-{end_page})")

    tag_total_pages: Dict[int, int] = {}
    for tag_id in tag_ids:
        tag_total_pages[tag_id] = _count_event_pages_for_tag(tag_id=tag_id, page_limit=page_limit)
    if show_progress:
        print(f"[clob-sync] Tag page totals: {tag_total_pages}")

    # Phase 1: Count unique valid markets in requested page range.
    valid_by_slug: Dict[str, Dict] = {}
    total_pages = end_page - start_page + 1
    for page in range(start_page, end_page + 1):
        page_idx = page - start_page + 1
        page_valid = 0
        page_seen = 0
        for tag_id in tag_ids:
            total_for_tag = tag_total_pages.get(tag_id, 0)
            if total_for_tag <= 0:
                continue
            # User page=1 means latest page (tail), page=2 next-latest, etc.
            source_page = total_for_tag - page + 1
            if source_page < 1:
                continue
            offset = (source_page - 1) * page_limit
            events = _fetch_events_by_tag(tag_id=tag_id, limit=page_limit, offset=offset)
            for event in events:
                markets = event.get("markets") or []
                if not isinstance(markets, list):
                    continue
                price_to_beat = (event.get("eventMetadata") or {}).get("priceToBeat")
                for market in markets:
                    page_seen += 1
                    normalized = _normalize_closed_market(
                        market,
                        cutoff_ts=cutoff_ts,
                        require_known_asset=True,
                        require_supported_interval=True,
                        price_to_beat=price_to_beat,
                    )
                    if not normalized:
                        continue
                    slug = normalized.get("slug")
                    if slug and slug not in valid_by_slug:
                        valid_by_slug[slug] = normalized
                        page_valid += 1
        if show_progress:
            print(
                f"[clob-sync] Count scan page {page} ({page_idx}/{total_pages}): "
                f"seen={page_seen}, valid_this_page={page_valid}, valid_total={len(valid_by_slug)}"
            )

    total = len(valid_by_slug)
    if total == 0:
        if show_progress:
            print("[clob-sync] No historical markets found for selected range.")
        return {
            "markets_counted": 0,
            "rows_upserted": 0,
            "start_page": start_page,
            "end_page": end_page,
            "days": days,
        }

    if show_progress:
        print(f"[clob-sync] Total valid markets in range: {total}")

    conn = get_connection()
    processed = 0
    found_markets = 0
    upserted = 0
    last_percent = -1
    inserted_slugs = set()
    try:
        now = int(time.time())
        for page in range(start_page, end_page + 1):
            for tag_id in tag_ids:
                total_for_tag = tag_total_pages.get(tag_id, 0)
                if total_for_tag <= 0:
                    continue
                source_page = total_for_tag - page + 1
                if source_page < 1:
                    continue
                offset = (source_page - 1) * page_limit
                events = _fetch_events_by_tag(tag_id=tag_id, limit=page_limit, offset=offset)
                for event in events:
                    markets = event.get("markets") or []
                    if not isinstance(markets, list):
                        continue
                    price_to_beat = (event.get("eventMetadata") or {}).get("priceToBeat")
                    for market in markets:
                        normalized = _normalize_closed_market(
                            market,
                            cutoff_ts=cutoff_ts,
                            require_known_asset=True,
                            require_supported_interval=True,
                            price_to_beat=price_to_beat,
                        )
                        if not normalized:
                            continue
                        slug = normalized.get("slug")
                        if not slug:
                            continue
                        if slug in inserted_slugs:
                            continue
                        inserted_slugs.add(slug)
                        found_markets += 1
                        _upsert_historical_market(normalized, conn=conn, now=now)
                        upserted += 1
                        processed += 1
                        percent = int((processed / total) * 100)
                        if show_progress and percent != last_percent:
                            print(
                                f"\r[clob-sync] Progress: {percent}% ({processed}/{total}), "
                                f"found={found_markets}, upserted={upserted}",
                                end="",
                                flush=True,
                            )
                            last_percent = percent
        conn.commit()
    finally:
        conn.close()

    if show_progress:
        print("")
        print(f"[clob-sync] Done. Found {found_markets} markets, upserted {upserted} rows.")
        sys.stdout.flush()

    return {
        "markets_counted": found_markets,
        "rows_upserted": upserted,
        "start_page": start_page,
        "end_page": end_page,
        "days": days,
    }


def fetch_clob_midpoint(token_id: str) -> Optional[float]:
    """Fetch current midpoint price for a token from Polymarket CLOB."""
    try:
        resp = requests.get(
            f"{CLOB_BASE}/midpoint",
            params={"token_id": token_id},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        return float(data.get("mid", 0))
    except Exception:
        return None


def fetch_clob_orderbook(token_id: str) -> Optional[Dict]:
    """Fetch orderbook for a token."""
    try:
        resp = requests.get(
            f"{CLOB_BASE}/book",
            params={"token_id": token_id},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def parse_interval_from_slug(slug: str) -> Optional[int]:
    """Detect supported interval (5, 15, 60) from slug; returns None if unknown."""
    return _detect_interval_minutes(slug)


def store_market_open(slug: str, asset: str, interval: int, start_ts: int, end_ts: int,
                      up_price: float, spot_price: float, conn=None):
    close_conn = conn is None
    if close_conn:
        conn = get_connection()
    try:
        now = int(time.time())
        conn.execute("""
            INSERT OR IGNORE INTO market_resolutions
            (slug, asset, interval_minutes, start_ts, end_ts, open_up_price,
             open_spot_price, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (slug, asset, interval, start_ts, end_ts, up_price, spot_price, now))
        conn.commit()
    finally:
        if close_conn:
            conn.close()


def store_price_tick(
    slug: str, asset: str, interval: int, start_ts: int,
    up_price: float, spot_price: float, elapsed: int, remaining: int,
    up_bid: float = None, up_ask: float = None,
    liquidity_5c: float = None, buy_imbalance: float = None,
    prev_ticks: List[Dict] = None,
    conn=None,
):
    close_conn = conn is None
    if close_conn:
        conn = get_connection()
    try:
        now = int(time.time())
        down_price = round(1.0 - up_price, 4)
        spread = (up_ask - up_bid) if up_ask and up_bid else None

        # Compute deltas from previous ticks
        price_delta_5s = price_delta_10s = price_delta_30s = None
        spot_delta_5s = spot_delta_30s = spot_delta_60s = None

        if prev_ticks:
            for pt in prev_ticks:
                pt_elapsed = pt.get("elapsed")
                pt_up = pt.get("up_price")
                if pt_elapsed is None or pt_up is None:
                    continue
                if pt_elapsed == elapsed - 5:
                    price_delta_5s = round(up_price - pt_up, 4)
                    if spot_price and pt.get("spot_price") is not None:
                        spot_delta_5s = round(spot_price - pt["spot_price"], 2)
                if pt_elapsed == elapsed - 10:
                    price_delta_10s = round(up_price - pt_up, 4)
                if pt_elapsed == elapsed - 30:
                    price_delta_30s = round(up_price - pt_up, 4)
                    if spot_price and pt.get("spot_price") is not None:
                        spot_delta_30s = round(spot_price - pt["spot_price"], 2)
                if pt_elapsed == elapsed - 60:
                    if spot_price and pt.get("spot_price") is not None:
                        spot_delta_60s = round(spot_price - pt["spot_price"], 2)

        # Spot change from open
        spot_change_from_open = None
        spot_open = conn.execute(
            "SELECT open_spot_price FROM market_resolutions WHERE slug = ?", (slug,)
        ).fetchone()
        if spot_open and spot_open[0] and spot_price:
            spot_change_from_open = round(spot_price - spot_open[0], 2)

        conn.execute("""
            INSERT INTO price_ticks
            (slug, asset, interval_minutes, start_ts, ticked_at, elapsed_seconds,
             remaining_seconds, up_price, down_price, up_bid, up_ask, spread,
             spot_price, spot_change_from_open, liquidity_within_5c, buy_side_imbalance,
             price_delta_5s, price_delta_10s, price_delta_30s,
             spot_delta_5s, spot_delta_30s, spot_delta_60s)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            slug, asset, interval, start_ts, now, elapsed, remaining,
            up_price, down_price, up_bid, up_ask, spread,
            spot_price, spot_change_from_open, liquidity_5c, buy_imbalance,
            price_delta_5s, price_delta_10s, price_delta_30s,
            spot_delta_5s, spot_delta_30s, spot_delta_60s,
        ))
        conn.commit()
    finally:
        if close_conn:
            conn.close()


def ingest_clob_once() -> Dict[str, int]:
    """Run one pass of CLOB ingestion and persist ticks to DB."""
    markets = fetch_active_crypto_markets()
    if not markets:
        return {"markets_seen": 0, "ticks_written": 0}

    conn = get_connection()
    ticks_written = 0
    try:
        now = int(time.time())
        for market in markets:
            token_id = market.get("up_token_id")
            if not token_id:
                continue

            midpoint = fetch_clob_midpoint(token_id)
            if midpoint is None:
                continue

            orderbook = fetch_clob_orderbook(token_id)
            up_bid, up_ask, liquidity_5c, buy_imbalance = _extract_orderbook_metrics(orderbook, midpoint)

            slug = market["slug"]
            asset = market["asset"]
            interval = market.get("interval_minutes")
            if interval is None:
                interval = parse_interval_from_slug(slug)
            if interval is None:
                continue
            end_ts = _parse_end_ts(market.get("end_date")) or (now + interval * 60)
            start_ts = max(0, end_ts - interval * 60)
            elapsed = max(0, now - start_ts)
            remaining = max(0, end_ts - now)
            spot_price = _get_latest_spot_price(asset, conn=conn)

            store_market_open(
                slug=slug,
                asset=asset,
                interval=interval,
                start_ts=start_ts,
                end_ts=end_ts,
                up_price=midpoint,
                spot_price=spot_price,
                conn=conn,
            )

            prev_ticks = _fetch_previous_ticks(slug, conn)
            store_price_tick(
                slug=slug,
                asset=asset,
                interval=interval,
                start_ts=start_ts,
                up_price=midpoint,
                spot_price=spot_price,
                elapsed=elapsed,
                remaining=remaining,
                up_bid=up_bid,
                up_ask=up_ask,
                liquidity_5c=liquidity_5c,
                buy_imbalance=buy_imbalance,
                prev_ticks=prev_ticks,
                conn=conn,
            )
            ticks_written += 1
        return {"markets_seen": len(markets), "ticks_written": ticks_written}
    finally:
        conn.close()


class CLOBIngestor:
    """Background CLOB ingestor that periodically writes market ticks."""

    def __init__(self, interval_seconds: int = 5):
        self.interval_seconds = interval_seconds
        self.running = False
        self._thread: Optional[threading.Thread] = None

    def _loop(self):
        while self.running:
            try:
                stats = ingest_clob_once()
                logger.debug(
                    "CLOB ingestion pass done: markets=%s ticks=%s",
                    stats["markets_seen"],
                    stats["ticks_written"],
                )
            except Exception as exc:
                logger.error(f"CLOB ingestion loop error: {exc}")
            time.sleep(self.interval_seconds)

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("CLOB ingestor started")

    def stop(self):
        self.running = False


_clob_ingestor = CLOBIngestor(interval_seconds=config.TICK_INTERVAL_SECONDS)


def start_clob_ingestion():
    _clob_ingestor.start()


def stop_clob_ingestion():
    _clob_ingestor.stop()


def compute_and_store_market_stats(slug: str, conn=None):
    """Compute market_stats from tick data when market closes."""
    close_conn = conn is None
    if close_conn:
        conn = get_connection()
    try:
        ticks = conn.execute("""
            SELECT * FROM price_ticks WHERE slug = ? ORDER BY elapsed_seconds ASC
        """, (slug,)).fetchall()

        res = conn.execute(
            "SELECT * FROM market_resolutions WHERE slug = ?", (slug,)
        ).fetchone()

        if not ticks or not res:
            return

        ticks = [dict(t) for t in ticks]
        tick_count = len(ticks)
        up_prices = [t["up_price"] for t in ticks]
        spot_prices = [t["spot_price"] for t in ticks if t["spot_price"]]

        max_up = max(up_prices)
        min_up = min(up_prices)
        avg_spread = sum(t["spread"] or 0 for t in ticks) / tick_count
        avg_liq = sum(t["liquidity_within_5c"] or 0 for t in ticks) / tick_count

        t5 = next((t for t in ticks if t["elapsed_seconds"] <= 5), None)
        t30 = next((t for t in ticks if t["elapsed_seconds"] <= 30), None)
        t60 = next((t for t in ticks if t["elapsed_seconds"] <= 60), None)

        open_price = ticks[0]["up_price"] if ticks else 0.5
        conviction = "HIGH_UP" if open_price > 0.6 else ("HIGH_DOWN" if open_price < 0.4 else "NEUTRAL")

        first_5s_delta = (t5["up_price"] - open_price) if t5 else None
        first_5s_dir = "UP" if (first_5s_delta or 0) > 0 else ("DOWN" if (first_5s_delta or 0) < 0 else "FLAT")
        first_30s_delta = (t30["up_price"] - open_price) if t30 else None
        first_30s_dir = "UP" if (first_30s_delta or 0) > 0 else ("DOWN" if (first_30s_delta or 0) < 0 else "FLAT")

        time_above_60c = sum(1 for p in up_prices if p > 0.6) / tick_count * 100
        time_below_40c = sum(1 for p in up_prices if p < 0.4) / tick_count * 100

        spot_range = (max(spot_prices) - min(spot_prices)) if len(spot_prices) > 1 else 0
        vol_class = "LOW" if spot_range < 50 else ("MED" if spot_range < 150 else ("HIGH" if spot_range < 300 else "EXTREME"))

        winner = res["winner_side"]
        late_ticks = [t for t in ticks if t["remaining_seconds"] <= 60]
        direction_flipped = 0
        if late_ticks and winner:
            late_price = late_ticks[0]["up_price"]
            was_leading = "UP" if late_price > 0.5 else "DOWN"
            direction_flipped = 1 if was_leading != winner else 0

        now = int(time.time())
        conn.execute("""
            INSERT OR REPLACE INTO market_stats
            (slug, asset, interval_minutes, start_ts,
             open_up_price, open_conviction, spot_volatility_class,
             first_5s_direction, first_5s_delta, first_30s_direction, first_30s_delta,
             max_up_price, min_up_price, price_range,
             crossed_70c, crossed_80c, crossed_30c,
             time_above_60c_pct, time_below_40c_pct,
             tick_count, avg_spread, avg_liquidity_5c,
             winner_side, computed_at, direction_flipped_late)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            slug, res["asset"], res["interval_minutes"], res["start_ts"],
            open_price, conviction, vol_class,
            first_5s_dir, first_5s_delta, first_30s_dir, first_30s_delta,
            max_up, min_up, max_up - min_up,
            int(any(p > 0.7 for p in up_prices)),
            int(any(p > 0.8 for p in up_prices)),
            int(any(p < 0.3 for p in up_prices)),
            time_above_60c, time_below_40c,
            tick_count, avg_spread, avg_liq,
            winner, now, direction_flipped,
        ))
        conn.commit()
    finally:
        if close_conn:
            conn.close()
