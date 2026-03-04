import os
import logging
from pathlib import Path

from py_clob_client.client import ClobClient

from config import config

logger = logging.getLogger(__name__)


def _project_env_path() -> Path:
    # backend/bootstrap/clob_auth.py -> project root/.env
    return (Path(__file__).resolve().parents[2] / ".env").resolve()


def _upsert_env_key(lines: list[str], key: str, value: str) -> list[str]:
    target_prefix = f"{key}="
    out = []
    replaced = False
    for line in lines:
        if line.startswith(target_prefix):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={value}")
    return out


def _persist_clob_creds(api_key: str, api_secret: str, api_passphrase: str) -> None:
    env_path = _project_env_path()
    if env_path.exists():
        lines = env_path.read_text().splitlines()
    else:
        lines = []

    lines = _upsert_env_key(lines, "CLOB_API_KEY", api_key)
    lines = _upsert_env_key(lines, "CLOB_SECRET", api_secret)
    lines = _upsert_env_key(lines, "CLOB_PASS_PHRASE", api_passphrase)
    env_path.write_text("\n".join(lines) + "\n")


def ensure_clob_api_credentials() -> bool:
    """
    Ensure CLOB API credentials exist.
    If missing, generate/derive from wallet private key and persist to .env.
    Returns True when credentials are present/ready, False otherwise.
    """
    if config.CLOB_API_KEY and config.CLOB_SECRET and config.CLOB_PASS_PHRASE:
        return True

    if not config.PRIVATE_KEY:
        logger.warning("CLOB credential bootstrap skipped: PRIVATE_KEY is missing")
        return False

    try:
        client = ClobClient(
            host=config.CLOB_HOST,
            chain_id=137,
            key=config.PRIVATE_KEY,
            signature_type=2,
            funder=config.WALLET_ADDRESS or None,
        )
        creds = client.create_or_derive_api_creds()
    except Exception as exc:
        logger.error("Failed to derive CLOB API credentials: %s", exc)
        return False

    _persist_clob_creds(
        api_key=creds.api_key,
        api_secret=creds.api_secret,
        api_passphrase=creds.api_passphrase,
    )

    # Apply immediately for current process runtime as well.
    os.environ["CLOB_API_KEY"] = creds.api_key
    os.environ["CLOB_SECRET"] = creds.api_secret
    os.environ["CLOB_PASS_PHRASE"] = creds.api_passphrase
    config.CLOB_API_KEY = creds.api_key
    config.CLOB_SECRET = creds.api_secret
    config.CLOB_PASS_PHRASE = creds.api_passphrase

    logger.info("CLOB API credentials generated from wallet and persisted to .env")
    return True
