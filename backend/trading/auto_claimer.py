"""
Auto-claimer worker:
- every N seconds (default 10 minutes),
- discovers closed markets user traded,
- if winning token balance exists, redeem on-chain via ConditionalTokens.redeemPositions.
"""
import logging
import threading
import time
from typing import Dict, List, Optional, Set

from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct
try:
    from web3.middleware import ExtraDataToPOAMiddleware  # web3.py v6/v7
except Exception:  # pragma: no cover - compatibility import
    ExtraDataToPOAMiddleware = None
try:
    from web3.middleware import geth_poa_middleware  # web3.py v5
except Exception:  # pragma: no cover - compatibility import
    geth_poa_middleware = None
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

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
        funder=config.WALLET_ADDRESS or None,
    )


def _claimed_before(condition_id: str, index_set: int, conn=None) -> bool:
    close_conn = conn is None
    if close_conn:
        conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM auto_claims WHERE condition_id = ? AND index_set = ? AND status = 'claimed' LIMIT 1",
            (condition_id, index_set),
        ).fetchone()
        return row is not None
    finally:
        if close_conn:
            conn.close()


def _record_claim(
    condition_id: str,
    index_set: int,
    token_id: str,
    amount_redeemed: str,
    status: str,
    tx_hash: Optional[str] = None,
    error: Optional[str] = None,
    conn=None,
):
    close_conn = conn is None
    if close_conn:
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
        if close_conn:
            conn.close()


def _extract_condition_ids_from_trades(trades_payload) -> Set[str]:
    out: Set[str] = set()
    if isinstance(trades_payload, list):
        rows = trades_payload
    elif isinstance(trades_payload, dict):
        rows = trades_payload.get("data") or []
    else:
        rows = []
    for t in rows:
        market = t.get("market")
        if isinstance(market, str) and market.startswith("0x") and len(market) == 66:
            out.add(market)
    return out


def _winning_tokens(market: Dict) -> List[Dict]:
    tokens = market.get("tokens") or []
    winners = []
    for idx, token in enumerate(tokens):
        if token.get("winner") is True:
            token_id = token.get("token_id")
            if token_id:
                winners.append(
                    {
                        "token_id": str(token_id),
                        "index_set": idx + 1,  # binary outcomes map to index sets 1/2
                        "outcome": token.get("outcome"),
                    }
                )
    return winners


def _connect_web3() -> Optional[Web3]:
    url = (config.POLYGON_RPC_URL or "").strip()
    if not url:
        return None
    try:
        w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 8}))
        # Polygon endpoints can return PoA-style headers; inject middleware to avoid
        # "extraData ... should be 32" errors during gas/block calls.
        if ExtraDataToPOAMiddleware is not None:
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        elif geth_poa_middleware is not None:
            w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        if w3.is_connected() and w3.eth.chain_id == 137:
            return w3
    except Exception:
        return None
    return None


class AutoClaimer:
    def __init__(self, loop_seconds: int = 600):
        self.loop_seconds = loop_seconds
        self.running = False
        self._thread: Optional[threading.Thread] = None

    def _loop(self):
        client = _build_client()
        if not client:
            return

        wallet = Web3.to_checksum_address(config.WALLET_ADDRESS) if config.WALLET_ADDRESS else None
        account = Account.from_key(config.PRIVATE_KEY)
        signer = Web3.to_checksum_address(account.address)
        warned_proxy_wallet = False

        while self.running:
            try:
                w3 = _connect_web3()
                if not w3:
                    logger.error(
                        "Auto claimer: cannot connect to POLYGON_RPC_URL from .env"
                    )
                    time.sleep(self.loop_seconds)
                    continue

                conditional_addr = Web3.to_checksum_address(client.get_conditional_address())
                collateral_addr = Web3.to_checksum_address(client.get_collateral_address())
                conditional = w3.eth.contract(address=conditional_addr, abi=CONDITIONAL_TOKENS_MIN_ABI)
                erc1155 = w3.eth.contract(address=conditional_addr, abi=ERC1155_MIN_ABI)
                safe_contract = w3.eth.contract(address=wallet, abi=SAFE_MIN_ABI) if wallet else None

                if wallet and wallet.lower() != signer.lower() and not warned_proxy_wallet:
                    logger.warning(
                        "Auto claimer: WALLET_PROXY_SAFE (%s) != signer (%s). "
                        "Direct redeemPositions tx can only redeem signer-owned balances.",
                        wallet,
                        signer,
                    )
                    warned_proxy_wallet = True

                condition_ids = set()
                try:
                    trades = client.get_trades()
                    condition_ids |= _extract_condition_ids_from_trades(trades)
                except Exception as exc:
                    logger.error("Auto claimer get_trades failed: %s", exc)

                for condition_id in condition_ids:
                    try:
                        market = client.get_market(condition_id)
                    except Exception:
                        continue
                    if not market or not market.get("closed", False):
                        continue

                    winners = _winning_tokens(market)
                    for w in winners:
                        token_id = w["token_id"]
                        index_set = int(w["index_set"])
                        conn = get_connection()
                        try:
                            if _claimed_before(condition_id, index_set, conn=conn):
                                continue
                        finally:
                            conn.close()

                        signer_balance = erc1155.functions.balanceOf(signer, int(token_id)).call()
                        wallet_balance = 0
                        if wallet and wallet.lower() != signer.lower():
                            wallet_balance = erc1155.functions.balanceOf(wallet, int(token_id)).call()

                        use_safe_flow = bool(wallet and wallet.lower() != signer.lower() and int(wallet_balance) > 0)
                        if int(signer_balance) <= 0 and not use_safe_flow:
                            continue

                        try:
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
                                    "0x0000000000000000000000000000000000000000",
                                    "0x0000000000000000000000000000000000000000",
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
                                    "0x0000000000000000000000000000000000000000",
                                    "0x0000000000000000000000000000000000000000",
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
                            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
                            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
                            status = "claimed" if receipt.status == 1 else "failed"
                            _record_claim(
                                condition_id=condition_id,
                                index_set=index_set,
                                token_id=token_id,
                                amount_redeemed=amount_redeemed,
                                status=status,
                                tx_hash=receipt.transactionHash.hex(),
                                error=None if status == "claimed" else "redeem tx reverted",
                            )
                            if status == "claimed":
                                logger.info(
                                    "Auto claimer claimed: condition=%s index_set=%s amount=%s tx=%s",
                                    condition_id,
                                    index_set,
                                    amount_redeemed,
                                    receipt.transactionHash.hex(),
                                )
                        except Exception as claim_exc:
                            _record_claim(
                                condition_id=condition_id,
                                index_set=index_set,
                                token_id=token_id,
                                amount_redeemed=str(wallet_balance if use_safe_flow else signer_balance),
                                status="failed",
                                error=str(claim_exc),
                            )
                            logger.error("Auto claimer claim failed for %s: %s", condition_id, claim_exc)
            except Exception as loop_exc:
                logger.error("Auto claimer loop error: %s", loop_exc)

            time.sleep(self.loop_seconds)

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("Auto claimer started")

    def stop(self):
        self.running = False


_auto_claimer = AutoClaimer(loop_seconds=config.AUTO_CLAIM_INTERVAL_SECONDS)


def start_auto_claimer():
    _auto_claimer.start()


def stop_auto_claimer():
    _auto_claimer.stop()
