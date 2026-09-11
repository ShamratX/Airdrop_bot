# PROJECT_BRAIN — Airdrop_bot

## Purpose

Telegram growth/airdrop bot for CatIQ with Supabase-backed user state and offline buy-proof capture.

## Architecture

- `bot.py` — handlers, task gates, admin
- `config.py` — env loading
- `supabase_store.py` — `bot_users` persistence
- `storage.py` — local/CSV helpers for buys
- SQL: `supabase_bot_users.sql`

## Workflow

1. Create bot; add to channel/group as admin
2. Apply SQL; set env including Supabase service role
3. `python bot.py`
4. Users `/start` → complete tasks → wallet → optional buy proof
5. Admins `/stats` `/export`

## Gotchas

- Only one polling process
- IDs for channel/group must match Telegram’s numeric ids where required
- Tier USD thresholds and CIQ bonuses are env-driven

## Related

`community` landing links to this bot; `catiq-ico` / presale contracts are separate payment rails.
