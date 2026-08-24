"""Storage: bot users on Supabase; CSV only for buyer (presale) proofs."""

from __future__ import annotations

import asyncio
import csv
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from supabase_store import (
    create_bot_user,
    get_bot_user,
    increment_bot_referral,
    list_bot_users,
    supabase_configured,
    update_bot_user,
)

DATA_DIR = Path(__file__).resolve().parent / "data"

# ~10k data rows per sheet, then auto-create next sheet (like Sheets "+" tab)
ROWS_PER_SHEET = 10_000

TX_FIELDS = [
    "user_id",
    "username",
    "wallet_address",
    "presale_tier",
    "txid",
    "submitted_at",
]

_TX_SHEET_RE = re.compile(r"^presale_proofs_sheet(\d+)\.csv$", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _data_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)
        return sum(1 for _ in reader)


def _write_header(path: Path, fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=fields).writeheader()


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _append_csv(path: Path, fields: list[str], row: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if new_file:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in fields})


def _list_sheets(prefix: str, pattern: re.Pattern[str]) -> list[Path]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    found: list[tuple[int, Path]] = []
    for path in DATA_DIR.glob(f"{prefix}_sheet*.csv"):
        m = pattern.match(path.name)
        if m:
            found.append((int(m.group(1)), path))
    found.sort(key=lambda x: x[0])
    return [p for _, p in found]


def ensure_csv() -> None:
    """Buyer CSV sheets only."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tx_sheet1 = DATA_DIR / "presale_proofs_sheet1.csv"
    if not tx_sheet1.exists():
        legacy = DATA_DIR / "presale_proofs.csv"
        if legacy.exists():
            legacy.rename(tx_sheet1)
        else:
            _write_header(tx_sheet1, TX_FIELDS)


def list_tx_sheets() -> list[Path]:
    ensure_csv()
    sheets = _list_sheets("presale_proofs", _TX_SHEET_RE)
    if not sheets:
        p = DATA_DIR / "presale_proofs_sheet1.csv"
        _write_header(p, TX_FIELDS)
        return [p]
    return sheets


def _active_sheet(sheets: list[Path], fields: list[str], prefix: str) -> Path:
    current = sheets[-1]
    if _data_rows(current) < ROWS_PER_SHEET:
        return current
    m = re.search(r"_sheet(\d+)\.csv$", current.name, re.I)
    next_n = int(m.group(1)) + 1 if m else len(sheets) + 1
    new_path = DATA_DIR / f"{prefix}_sheet{next_n}.csv"
    _write_header(new_path, fields)
    return new_path


def get_user(user_id: int | str) -> dict[str, str] | None:
    if not supabase_configured():
        raise RuntimeError("Supabase is required for user data. Set SUPABASE_URL and key in .env")
    return get_bot_user(user_id)


def create_user(
    user_id: int,
    username: str | None,
    first_name: str | None,
    referrer_id: str | None = None,
) -> dict[str, str]:
    if not supabase_configured():
        raise RuntimeError("Supabase is required for user data. Set SUPABASE_URL and key in .env")
    row = create_bot_user(user_id, username, first_name, referrer_id=referrer_id)
    if not row:
        raise RuntimeError(
            "Could not create user in Supabase. Create table bot_users (see README / chat SQL)."
        )
    return row


def update_user(user_id: int | str, **fields: Any) -> dict[str, str] | None:
    if not supabase_configured():
        raise RuntimeError("Supabase is required for user data. Set SUPABASE_URL and key in .env")
    return update_bot_user(user_id, **fields)


def increment_referral(referrer_id: int | str, required: int = 3) -> dict[str, str] | None:
    if not supabase_configured():
        raise RuntimeError("Supabase is required for user data. Set SUPABASE_URL and key in .env")
    return increment_bot_referral(referrer_id, required=required)


# Async wrappers — keep Supabase sync HTTP off the Telegram event loop
async def aget_user(user_id: int | str) -> dict[str, str] | None:
    return await asyncio.to_thread(get_user, user_id)


async def acreate_user(
    user_id: int,
    username: str | None,
    first_name: str | None,
    referrer_id: str | None = None,
) -> dict[str, str]:
    return await asyncio.to_thread(create_user, user_id, username, first_name, referrer_id)


async def aupdate_user(user_id: int | str, **fields: Any) -> dict[str, str] | None:
    return await asyncio.to_thread(lambda: update_user(user_id, **fields))


async def aincrement_referral(
    referrer_id: int | str, required: int = 3
) -> dict[str, str] | None:
    return await asyncio.to_thread(increment_referral, referrer_id, required)


def save_presale_proof(
    user_id: int | str,
    username: str | None,
    wallet_address: str,
    presale_tier: str,
    txid: str,
) -> None:
    """Buyer proof → CSV only (+ user row updated on Supabase)."""
    sheets = list_tx_sheets()
    target = _active_sheet(sheets, TX_FIELDS, "presale_proofs")
    _append_csv(
        target,
        TX_FIELDS,
        {
            "user_id": str(user_id),
            "username": username or "",
            "wallet_address": wallet_address,
            "presale_tier": presale_tier,
            "txid": txid,
            "submitted_at": _now(),
        },
    )
    update_user(
        user_id,
        wallet_address=wallet_address,
        presale_tier=presale_tier,
        txid=txid,
    )


def export_paths() -> list[Path]:
    """Admin /export — buyer CSV sheets only."""
    ensure_csv()
    return list_tx_sheets()


def sheet_info() -> str:
    lines = [f"Rows per sheet limit: {ROWS_PER_SHEET}", "CSV: buyers only (presale proofs)"]
    for path in list_tx_sheets():
        n = _data_rows(path)
        lines.append(f"📄 {path.name}: {n}/{ROWS_PER_SHEET} rows")
    if supabase_configured():
        lines.append(f"☁️ bot_users (Supabase): {len(list_bot_users())} rows")
    return "\n".join(lines)


def _read_users() -> list[dict[str, str]]:
    """Admin stats helper — from Supabase."""
    return list_bot_users()


def flag(value: str | None) -> bool:
    return str(value or "0") in ("1", "true", "True", "yes")
