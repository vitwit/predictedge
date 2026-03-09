"""
Auto-claimer worker:
- every N seconds (default 10 minutes),
- discovers closed markets user traded,
- if winning token balance exists, redeem on-chain via ConditionalTokens.redeemPositions.
"""
import logging
import threading
import time
import json
from typing import Dict, List, Optional, Set

import requests as http_requests
from eth_account import Account
from eth_account.messages import encode_defunct
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, TradeParams
from web3 import Web3

try:
    from web3.middleware import ExtraDataToPOAMiddleware
except Exception:  # pragma: no cover
    ExtraDataToPOAMiddleware = None
try:
    from web3.middleware import geth_poa_middleware
except Exception:  # pragma: no cover
    geth_poa_middleware = None

from config import config
from db import get_connection

logger = logging.getLogger(__name__)

ERC1155_MIN_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "account", "type": "address"}, {"name": "id", "type": "uint256"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function",
    }
]

CONDITIONAL_TOKENS_MIN_ABI = [
    {
        "inputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}],
        "name": "payoutDenominator",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "collateralToken", "type": "address"},
            {"internalType": "bytes32", "name": "parentCollectionId", "type": "bytes32"},
            {"internalType": "bytes32", "name": "conditionId", "type": "bytes32"},
            {"internalType": "uint256[]", "name": "indexSets", "type": "uint256[]"},
        ],
        "name": "redeemPositions",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]

SAFE_MIN_ABI = [
    {
        "inputs": [],
        "name": "nonce",
        "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "value", "type": "uint256"},
            {"internalType": "bytes", "name": "data", "type": "bytes"},
            {"internalType": "uint8", "name": "operation", "type": "uint8"},
            {"internalType": "uint256", "name": "safeTxGas", "type": "uint256"},
            {"internalType": "uint256", "name": "baseGas", "type": "uint256"},
            {"internalType": "uint256", "name": "gasPrice", "type": "uint256"},
            {"internalType": "address", "name": "gasToken", "type": "address"},
            {"internalType": "address payable", "name": "refundReceiver", "type": "address"},
            {"internalType": "uint256", "name": "_nonce", "type": "uint256"},
        ],
        "name": "getTransactionHash",
        "outputs": [{"internalType": "bytes32", "name": "", "type": "bytes32"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "uint256", "name": "value", "type": "uint256"},
            {"internalType": "bytes", "name": "data", "type": "bytes"},
            {"internalType": "enum Enum.Operation", "name": "operation", "type": "uint8"},
            {"internalType": "uint256", "name": "safeTxGas", "type": "uint256"},
            {"internalType": "uint256", "name": "baseGas", "type": "uint256"},
            {"internalType": "uint256", "name": "gasPrice", "type": "uint256"},
            {"internalType": "address", "name": "gasToken", "type": "address"},
            {"internalType": "address payable", "name": "refundReceiver", "type": "address"},
            {"internalType": "bytes", "name": "signatures", "type": "bytes"},
        ],
        "name": "execTransaction",
        "outputs": [{"internalType": "bool", "name": "success", "type": "bool"}],
        "stateMutability": "payable",
        "type": "function",
    },
]


def _build_client() -> Optional[ClobClient]:
    if not config.PRIVATE_KEY:
        logger.warning("Auto claimer disabled: PRIVATE_KEY is missing")
        return None
    if not (config.CLOB_API_KEY and config.CLOB_SECRET and config.CLOB_PASS_PHRASE):
        logger.warning("Auto claimer disabled: CLOB API credentials are missing")
        return None
    signer_address = None
    try:
        signer_address = Account.from_key(config.PRIVATE_KEY).address
    except Exception:
        signer_address = None
    # For authenticated CLOB endpoints like get_trades, use the EOA signer
    # address in auth headers (POLY_ADDRESS), not the proxy safe.
    auth_address = signer_address or config.WALLET_ADDRESS or None

    return ClobClient(
        host=config.CLOB_HOST,
        chain_id=137,
        key=config.PRIVATE_KEY,
        creds=ApiCreds(
            api_key=config.CLOB_API_KEY,
            api_secret=config.CLOB_SECRET,
            api_passphrase=config.CLOB_PASS_PHRASE,
        ),
        signature_type=2,
        funder=auth_address,
    )


def _claimed_before(condition_id: str, index_set: int) -> bool:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM auto_claims WHERE condition_id = ? AND index_set = ? AND status = 'claimed' LIMIT 1",
            (condition_id, index_set),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _record_claim(
    condition_id: str,
    index_set: int,
    token_id: str,
    amount_redeemed: str,
    status: str,
    tx_hash: str = "",
    error: str = "",
) -> None:
    conn = get_connection()
    try:
        now = int(time.time())
        conn.execute(
            """
            INSERT INTO auto_claims
            (condition_id, index_set, token_id, amount_redeemed, tx_hash, status, error, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(condition_id, index_set) DO UPDATE SET
                token_id = excluded.token_id,
                amount_redeemed = excluded.amount_redeemed,
                tx_hash = excluded.tx_hash,
                status = excluded.status,
                error = excluded.error,
                created_at = excluded.created_at
            """,
            (condition_id, index_set, token_id, amount_redeemed, tx_hash, status, error, now),
        )
        conn.commit()
    finally:
        conn.close()


def _extract_condition_ids_from_trades(trades_payload: object) -> Set[str]:
    out: Set[str] = set()
    rows: List[Dict] = []
    if isinstance(trades_payload, list):
        rows = trades_payload
    elif isinstance(trades_payload, dict):
        rows = trades_payload.get("data") or []
    for trade in rows:
        market = trade.get("market")
        if isinstance(market, str) and market.startswith("0x") and len(market) == 66:
            out.add(market)
    return out


def _normalize_condition_id(value: Optional[str]) -> Optional[str]:
    if not isinstance(value, str) or not value:
        return None
    v = value.strip()
    if not v:
        return None
    if not v.startswith("0x"):
        v = f"0x{v}"
    if len(v) != 66:
        return None
    return v


def _as_list(raw) -> List:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []


def _fetch_gamma_market_by_slug(slug: str) -> Optional[Dict]:
    try:
        resp = http_requests.get(f"{config.GAMMA_HOST}/markets", params={"slug": slug}, timeout=10)
        resp.raise_for_status()
        rows = resp.json()
        if not isinstance(rows, list):
            return None
        for row in rows:
            if row.get("slug") == slug:
                return row
    except Exception:
        return None
    return None


def _discover_closed_markets_from_local_orders() -> Dict[str, Dict]:
    """
    Fallback discovery path when CLOB get_trades() is unavailable.
    Uses local auto_trade_orders + Gamma market metadata.
    Returns: condition_id -> market-like payload with winner flags.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT slug, token_id, predicted_side
            FROM auto_trade_orders
            WHERE status = 'submitted'
            ORDER BY created_at DESC
            LIMIT 120
            """
        ).fetchall()
    finally:
        conn.close()

    out: Dict[str, Dict] = {}
    seen_slugs: Set[str] = set()
    for row in rows:
        slug = row["slug"] if isinstance(row, dict) or hasattr(row, "keys") else row[0]
        _ = str(row["token_id"] if isinstance(row, dict) or hasattr(row, "keys") else row[1])
        _ = str(row["predicted_side"] if isinstance(row, dict) or hasattr(row, "keys") else row[2]).upper()
        if not slug:
            continue
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)

        market = _fetch_gamma_market_by_slug(slug)
        if not market or not market.get("closed", False):
            continue

        condition_id = _normalize_condition_id(market.get("conditionId") or market.get("condition_id"))
        if not condition_id:
            continue

        token_ids = [str(x) for x in _as_list(market.get("clobTokenIds"))]
        outcomes = [str(x) for x in _as_list(market.get("outcomes"))]
        outcome_prices = _as_list(market.get("outcomePrices"))
        winners: List[Dict] = []
        for idx, token_id in enumerate(token_ids):
            outcome = outcomes[idx] if idx < len(outcomes) else None
            price_raw = outcome_prices[idx] if idx < len(outcome_prices) else None
            try:
                p = float(price_raw)
            except Exception:
                p = None
            # Strict winner detection only from resolved outcome prices.
            is_winner = p is not None and p >= 0.999
            winners.append({"token_id": token_id, "index_set": idx + 1, "outcome": outcome, "winner": is_winner})

        # If winner cannot be determined unambiguously, skip this market.
        winner_count = sum(1 for w in winners if w.get("winner"))
        if winner_count != 1:
            continue

        out[condition_id] = {"closed": True, "tokens": winners}
    return out


def _winning_tokens(market: Dict) -> List[Dict]:
    tokens = market.get("tokens") or []
    winners: List[Dict] = []
    for idx, token in enumerate(tokens):
        if token.get("winner") is True:
            token_id = token.get("token_id")
            if token_id:
                winners.append({"token_id": str(token_id), "index_set": idx + 1, "outcome": token.get("outcome")})
    return winners


def _connect_web3() -> Optional[Web3]:
    url = (config.POLYGON_RPC_URL or "").strip()
    if not url:
        return None
    try:
        w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 8}))
        if ExtraDataToPOAMiddleware is not None:
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        elif geth_poa_middleware is not None:
            w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        if w3.is_connected() and w3.eth.chain_id == 137:
            return w3
    except Exception:
        return None
    return None


def _call_with_timeout(fn, *args, timeout_s: int = 6):
    done = {"ok": False, "value": None, "error": None}

    def _runner():
        try:
            done["value"] = fn(*args)
            done["ok"] = True
        except Exception as exc:  # pragma: no cover
            done["error"] = exc

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout=timeout_s)
    if t.is_alive():
        return None, TimeoutError(f"timeout after {timeout_s}s")
    if done["error"] is not None:
        return None, done["error"]
    return done["value"], None


def _get_trades_with_timeout(client: ClobClient, maker_address: Optional[str], timeout_s: int = 6):
    params = TradeParams(maker_address=maker_address) if maker_address else None
    value, err = _call_with_timeout(client.get_trades, params, timeout_s=timeout_s)
    if err is None:
        return value
    if isinstance(err, TimeoutError):
        logger.warning("Auto claimer get_trades timed out after %ss", timeout_s)
    else:
        logger.error("Auto claimer get_trades failed: %s", err)
    return None


def _get_market_with_timeout(client: ClobClient, condition_id: str, timeout_s: int = 5):
    value, err = _call_with_timeout(client.get_market, condition_id, timeout_s=timeout_s)
    if err is not None:
        return None
    return value


class AutoClaimer:
    def __init__(self, loop_seconds: int = 600):
        self.loop_seconds = loop_seconds
        self.running = False
        self._thread: Optional[threading.Thread] = None

    def _loop(self) -> None:
        client = _build_client()
        if not client:
            return

        wallet = Web3.to_checksum_address(config.WALLET_ADDRESS) if config.WALLET_ADDRESS else None
        account = Account.from_key(config.PRIVATE_KEY)
        signer = Web3.to_checksum_address(account.address)
        warned_proxy_wallet = False
        null_addr = Web3.to_checksum_address("0x0000000000000000000000000000000000000000")

        while self.running:
            try:
                w3 = _connect_web3()
                if not w3:
                    logger.error("Auto claimer: cannot connect to POLYGON_RPC_URL from .env")
                    time.sleep(self.loop_seconds)
                    continue

                conditional_addr = Web3.to_checksum_address(client.get_conditional_address())
                collateral_addr = Web3.to_checksum_address(client.get_collateral_address())
                conditional = w3.eth.contract(address=conditional_addr, abi=CONDITIONAL_TOKENS_MIN_ABI)
                erc1155 = w3.eth.contract(address=conditional_addr, abi=ERC1155_MIN_ABI)
                safe_contract = w3.eth.contract(address=wallet, abi=SAFE_MIN_ABI) if wallet else None

                if wallet and wallet.lower() != signer.lower() and not warned_proxy_wallet:
                    logger.warning(
                        "Auto claimer: WALLET_ADDRESS (%s) != signer (%s). Direct redeem tx can only redeem signer balances.",
                        wallet,
                        signer,
                    )
                    warned_proxy_wallet = True

                condition_ids: Set[str] = set()
                fallback_markets: Dict[str, Dict] = {}
                maker_address = wallet if wallet else signer
                trades = _get_trades_with_timeout(client, maker_address=maker_address, timeout_s=6)
                if trades is not None:
                    condition_ids |= _extract_condition_ids_from_trades(trades)
                # Fallback: discover from locally recorded submitted orders.
                try:
                    fallback_markets = _discover_closed_markets_from_local_orders()
                    condition_ids |= set(fallback_markets.keys())
                    logger.info(
                        "Auto claimer fallback discovery: %s closed markets from local orders",
                        len(fallback_markets),
                    )
                except Exception as exc:
                    logger.error("Auto claimer local discovery failed: %s", exc)

                for condition_id in condition_ids:
                    market = _get_market_with_timeout(client, condition_id, timeout_s=5)
                    if market is None:
                        market = fallback_markets.get(condition_id)
                    if market is None:
                        market = fallback_markets.get(condition_id)
                    if not market or not market.get("closed", False):
                        continue

                    # Gamma can mark a market closed before CT payout is finalized on-chain.
                    # Skip early to avoid redeemPositions reverts.
                    try:
                        condition_bytes = bytes.fromhex(condition_id[2:])
                        payout_denominator = conditional.functions.payoutDenominator(condition_bytes).call()
                        if int(payout_denominator) <= 0:
                            continue
                    except Exception:
                        continue

                    winners = _winning_tokens(market)
                    for winner in winners:
                        token_id = winner["token_id"]
                        index_set = int(winner["index_set"])
                        if _claimed_before(condition_id, index_set):
                            continue

                        signer_balance = erc1155.functions.balanceOf(signer, int(token_id)).call()
                        wallet_balance = 0
                        if wallet and wallet.lower() != signer.lower():
                            wallet_balance = erc1155.functions.balanceOf(wallet, int(token_id)).call()

                        use_safe_flow = bool(wallet and wallet.lower() != signer.lower() and int(wallet_balance) > 0)
                        if int(signer_balance) <= 0 and not use_safe_flow:
                            continue

                        try:
                            # Preflight redeem simulation from the actual holder address.
                            # If this reverts, don't submit Safe tx (prevents GS013 spam).
                            try:
                                simulate_from = wallet if use_safe_flow and wallet else signer
                                conditional.functions.redeemPositions(
                                    collateral_addr,
                                    b"\x00" * 32,
                                    bytes.fromhex(condition_id[2:]),
                                    [index_set],
                                ).call({"from": Web3.to_checksum_address(simulate_from)})
                            except Exception as sim_exc:
                                logger.info(
                                    "Auto claimer skip (not redeemable yet): condition=%s index_set=%s reason=%s",
                                    condition_id,
                                    index_set,
                                    sim_exc,
                                )
                                continue

                            nonce = w3.eth.get_transaction_count(account.address, "pending")
                            redeem_data_hex = conditional.functions.redeemPositions(
                                collateral_addr, b"\x00" * 32, bytes.fromhex(condition_id[2:]), [index_set]
                            )._encode_transaction_data()
                            redeem_data = bytes.fromhex(redeem_data_hex[2:])

                            if use_safe_flow and safe_contract is not None:
                                safe_nonce = safe_contract.functions.nonce().call()
                                safe_tx_hash = safe_contract.functions.getTransactionHash(
                                    conditional_addr,
                                    0,
                                    redeem_data,
                                    0,
                                    0,
                                    0,
                                    0,
                                    null_addr,
                                    null_addr,
                                    safe_nonce,
                                ).call()
                                signed_safe = account.sign_message(encode_defunct(hexstr=safe_tx_hash.hex()))
                                safe_v = signed_safe.v + 4 if signed_safe.v in (27, 28) else signed_safe.v
                                safe_signature = (
                                    int(signed_safe.r).to_bytes(32, "big")
                                    + int(signed_safe.s).to_bytes(32, "big")
                                    + bytes([safe_v])
                                )

                                tx = safe_contract.functions.execTransaction(
                                    conditional_addr,
                                    0,
                                    redeem_data,
                                    0,
                                    0,
                                    0,
                                    0,
                                    null_addr,
                                    null_addr,
                                    safe_signature,
                                ).build_transaction(
                                    {
                                        "from": account.address,
                                        "nonce": nonce,
                                        "chainId": 137,
                                    }
                                )
                                amount_redeemed = str(wallet_balance)
                            else:
                                tx = conditional.functions.redeemPositions(
                                    collateral_addr,
                                    b"\x00" * 32,
                                    bytes.fromhex(condition_id[2:]),
                                    [index_set],
                                ).build_transaction(
                                    {
                                        "from": account.address,
                                        "nonce": nonce,
                                        "chainId": 137,
                                    }
                                )
                                amount_redeemed = str(signer_balance)

                            gas_estimate = w3.eth.estimate_gas(tx)
                            tx["gas"] = int(gas_estimate * 1.2)
                            tx["maxFeePerGas"] = w3.eth.gas_price
                            tx["maxPriorityFeePerGas"] = min(w3.eth.gas_price, Web3.to_wei(40, "gwei"))

                            signed = account.sign_transaction(tx)
                            raw_tx = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction")
                            tx_hash = w3.eth.send_raw_transaction(raw_tx)
                            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
                            status = "claimed" if receipt.status == 1 else "failed"
                            tx_hash_hex = receipt.transactionHash.hex()

                            _record_claim(
                                condition_id=condition_id,
                                index_set=index_set,
                                token_id=token_id,
                                amount_redeemed=amount_redeemed,
                                status=status,
                                tx_hash=tx_hash_hex,
                                error="" if status == "claimed" else "redeem tx reverted",
                            )
                            if status == "claimed":
                                logger.info(
                                    "Auto claimer claimed: condition=%s index_set=%s amount=%s tx=%s",
                                    condition_id,
                                    index_set,
                                    amount_redeemed,
                                    tx_hash_hex,
                                )
                        except Exception as claim_exc:
                            err_text = str(claim_exc)
                            _record_claim(
                                condition_id=condition_id,
                                index_set=index_set,
                                token_id=token_id,
                                amount_redeemed=str(wallet_balance if use_safe_flow else signer_balance),
                                status="failed",
                                tx_hash="",
                                error=err_text,
                            )
                            if "GS013" in err_text:
                                logger.warning(
                                    "Auto claimer claim skipped after Safe exec failure (GS013): condition=%s index_set=%s",
                                    condition_id,
                                    index_set,
                                )
                            else:
                                logger.error("Auto claimer claim failed for %s: %s", condition_id, claim_exc)
            except Exception as loop_exc:
                logger.error("Auto claimer loop error: %s", loop_exc)

            time.sleep(self.loop_seconds)

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="auto-claimer")
        self._thread.start()
        logger.info("Auto claimer started (interval=%ss)", self.loop_seconds)

    def stop(self) -> None:
        self.running = False


_auto_claimer = AutoClaimer(loop_seconds=config.AUTO_CLAIM_INTERVAL_SECONDS)


def start_auto_claimer():
    _auto_claimer.start()


def stop_auto_claimer():
    _auto_claimer.stop()
