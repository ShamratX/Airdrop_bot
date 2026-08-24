"""Supabase: bot_users (progress) + airdrop_claims + buy_proofs. CSV only for buyers."""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

from supabase import Client, create_client

import config

logger = logging.getLogger("xbot.supabase")

_client: Client | None = None
_NUM = re.compile(r"[\d.]+")

# Short in-memory cache — cuts repeat reads under concurrent load (~300 users)
_USER_CACHE: dict[int, tuple[float, dict[str, str]]] = {}
_USER_CACHE_LOCK = threading.Lock()
_USER_CACHE_TTL_SEC = 45.0
_USER_CACHE_MAX = 4000

_BOOL_KEYS = (
    "channel_joined",
    "group_joined",
    "x_done",
    "rt_done",
    "like_done",
    "referrals_done",
    "airdrop_complete",
    "step5_skipped",
)


def _parse_amount(raw: str) -> float:
    m = _NUM.search(raw or "")
    if not m:
        return 0.0
    try:
        return float(m.group(0))
    except ValueError:
        return 0.0


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "0").lower() in ("1", "true", "yes")


def _flag_str(value: Any) -> str:
    return "1" if _as_bool(value) else "0"


def supabase_configured() -> bool:
    return bool(config.SUPABASE_URL and config.SUPABASE_SERVICE_ROLE_KEY)


def get_client() -> Client | None:
    global _client
    if not supabase_configured():
        return None
    if _client is None:
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)
    return _client


def _cache_get(user_id: int | str) -> dict[str, str] | None:
    uid = int(user_id)
    now = time.monotonic()
    with _USER_CACHE_LOCK:
        hit = _USER_CACHE.get(uid)
        if not hit:
            return None
        ts, row = hit
        if now - ts > _USER_CACHE_TTL_SEC:
            _USER_CACHE.pop(uid, None)
            return None
        return dict(row)


def _cache_set(user_id: int | str, row: dict[str, str] | None) -> None:
    if not row:
        return
    uid = int(user_id)
    with _USER_CACHE_LOCK:
        if len(_USER_CACHE) >= _USER_CACHE_MAX:
            _USER_CACHE.clear()
        _USER_CACHE[uid] = (time.monotonic(), dict(row))


def _cache_invalidate(user_id: int | str) -> None:
    with _USER_CACHE_LOCK:
        _USER_CACHE.pop(int(user_id), None)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_row_to_user(row: dict[str, Any]) -> dict[str, str]:
    """Normalize Supabase row to the string-flag dict bot.py expects."""
    return {
        "user_id": str(row.get("user_id") or ""),
        "username": str(row.get("username") or ""),
        "first_name": str(row.get("first_name") or ""),
        "referrer_id": "" if row.get("referrer_id") in (None, "") else str(row.get("referrer_id")),
        "referral_count": str(int(row.get("referral_count") or 0)),
        "channel_joined": _flag_str(row.get("channel_joined")),
        "group_joined": _flag_str(row.get("group_joined")),
        "x_done": _flag_str(row.get("x_done")),
        "rt_done": _flag_str(row.get("rt_done")),
        "like_done": _flag_str(row.get("like_done")),
        "referrals_done": _flag_str(row.get("referrals_done")),
        "airdrop_complete": _flag_str(row.get("airdrop_complete")),
        "wallet_address": str(row.get("wallet_address") or ""),
        "step5_skipped": _flag_str(row.get("step5_skipped")),
        "presale_tier": str(row.get("presale_tier") or ""),
        "txid": str(row.get("txid") or ""),
        "created_at": str(row.get("created_at") or ""),
        "updated_at": str(row.get("updated_at") or ""),
    }


def get_bot_user(user_id: int | str) -> dict[str, str] | None:
    cached = _cache_get(user_id)
    if cached is not None:
        return cached
    client = get_client()
    if not client:
        return None
    try:
        res = (
            client.table("bot_users")
            .select("*")
            .eq("user_id", int(user_id))
            .limit(1)
            .execute()
        )
        if not res.data:
            return None
        row = db_row_to_user(res.data[0])
        _cache_set(user_id, row)
        return row
    except Exception:
        logger.exception("get_bot_user failed for %s", user_id)
        return None


def create_bot_user(
    user_id: int,
    username: str | None,
    first_name: str | None,
    referrer_id: str | None = None,
) -> dict[str, str] | None:
    client = get_client()
    if not client:
        return None
    existing = get_bot_user(user_id)
    if existing:
        return existing
    ref: int | None
    if referrer_id and str(referrer_id).isdigit():
        ref = int(referrer_id)
    else:
        ref = None
    row = {
        "user_id": int(user_id),
        "username": username or "",
        "first_name": first_name or "",
        "referrer_id": ref,
        "referral_count": 0,
        "channel_joined": False,
        "group_joined": False,
        "x_done": False,
        "rt_done": False,
        "like_done": False,
        "referrals_done": False,
        "airdrop_complete": False,
        "wallet_address": "",
        "step5_skipped": False,
        "presale_tier": "",
        "txid": "",
        "updated_at": _now_iso(),
    }
    try:
        res = client.table("bot_users").insert(row).select("*").execute()
        if res.data:
            out = db_row_to_user(res.data[0])
            _cache_set(user_id, out)
            return out
        out = get_bot_user(user_id)
        return out
    except Exception:
        logger.exception("create_bot_user failed for %s", user_id)
        return None


def update_bot_user(user_id: int | str, **fields: Any) -> dict[str, str] | None:
    client = get_client()
    if not client:
        return None
    payload: dict[str, Any] = {"updated_at": _now_iso()}
    for key, value in fields.items():
        if key == "user_id":
            continue
        if key in _BOOL_KEYS:
            payload[key] = _as_bool(value)
        elif key == "referral_count":
            payload[key] = int(value or 0)
        elif key == "referrer_id":
            if value in (None, ""):
                payload[key] = None
            else:
                payload[key] = int(value) if str(value).lstrip("-").isdigit() else None
        else:
            payload[key] = "" if value is None else str(value)
    _cache_invalidate(user_id)
    try:
        res = (
            client.table("bot_users")
            .update(payload)
            .eq("user_id", int(user_id))
            .select("*")
            .execute()
        )
        if res.data:
            out = db_row_to_user(res.data[0])
            _cache_set(user_id, out)
            return out
        out = get_bot_user(user_id)
        return out
    except Exception:
        logger.exception("update_bot_user failed for %s", user_id)
        return None


def increment_bot_referral(referrer_id: int | str, required: int = 3) -> dict[str, str] | None:
    user = get_bot_user(referrer_id)
    if not user:
        return None
    count = int(user.get("referral_count") or 0) + 1
    fields: dict[str, Any] = {"referral_count": count}
    if count >= required:
        fields["referrals_done"] = True
        # mark complete if all tasks done
        checks = ("channel_joined", "group_joined", "x_done", "rt_done", "like_done")
        if all(_as_bool(user.get(k)) for k in checks):
            fields["airdrop_complete"] = True
    return update_bot_user(referrer_id, **fields)


def list_bot_users() -> list[dict[str, str]]:
    client = get_client()
    if not client:
        return []
    try:
        res = client.table("bot_users").select("*").execute()
        return [db_row_to_user(r) for r in (res.data or [])]
    except Exception:
        logger.exception("list_bot_users failed")
        return []


def upsert_airdrop_claim(
    user_id: int,
    wallet_address: str,
    *,
    referral_count: int = 0,
) -> bool:
    client = get_client()
    if not client:
        logger.warning("Supabase not configured — skip airdrop_claims upsert")
        return False
    base = _parse_amount(config.AIRDROP_REWARD)
    per_ref = _parse_amount(config.REFERRAL_REWARD)
    amount = base + max(0, referral_count) * per_ref
    row: dict[str, Any] = {
        "user_id": int(user_id),
        "wallet_address": wallet_address,
        "airdrop_amount": amount,
    }
    try:
        client.table("airdrop_claims").upsert(row, on_conflict="user_id").execute()
        return True
    except Exception:
        logger.exception("airdrop_claims upsert failed for %s", user_id)
        return False


def insert_buy_proof(
    user_id: int,
    wallet_address: str,
    tx_hash: str,
    *,
    buy_usd: float = 0,
    buy_tokens: float = 0,
    offer_tokens: float = 0,
) -> bool:
    client = get_client()
    if not client:
        logger.warning("Supabase not configured — skip buy_proofs insert")
        return False
    total = float(buy_tokens) + float(offer_tokens)
    row: dict[str, Any] = {
        "user_id": int(user_id),
        "wallet_address": wallet_address,
        "tx_hash": tx_hash,
        "buy_usd": buy_usd,
        "buy_tokens": buy_tokens,
        "offer_tokens": offer_tokens,
        "total_tokens": total,
    }
    try:
        client.table("buy_proofs").insert(row).execute()
        return True
    except Exception:
        logger.exception("buy_proofs insert failed for %s", user_id)
        return False
