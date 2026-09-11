# CatIQ Airdrop Bot

Telegram bot for **CatIQ** social tasks, optional referrals, wallet submit, and a separate buy/presale proof flow. User progress is stored in **Supabase** (`bot_users`).

## Features

- Required tasks: follow X, retweet+like, join Telegram channel + group
- Optional invite bonus; wallet submission after core tasks
- Buy tiers with TXID / proof stored to CSV sheets under `data/`
- Admin commands: `/stats`, `/export`
- Referral deep links: `t.me/<BOT_USERNAME>?start=ref_<user_id>`

## How it works

`bot.py` runs a python-telegram-bot application. Task completion and wallet state persist through `supabase_store.py`. Buyer proofs append to CSV sheets for admin export.

## Requirements

- Python 3.10+
- Telegram bot token (BotFather)
- Supabase project + `bot_users` schema (`supabase_bot_users.sql`)
- Bot admin in the configured channel/group

## Quick start

```bash
python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env
python bot.py
```

Run **one** instance only (duplicate polling ? Telegram 409 Conflict).

## Config (env names)

`BOT_TOKEN`, `BOT_USERNAME`, `BOT_DESCRIPTION`, `PROJECT_NAME`, `PROJECT_WEBSITE`, `CHANNEL_USERNAME`, `CHANNEL_ID`, `GROUP_USERNAME`, `GROUP_ID`, `GROUP_INVITE_LINK`, `X_PROFILE_URL`, `X_POST_URL`, `PRESALE_URL`, `TGE_DATE`, `AIRDROP_REWARD`, `REFERRAL_REWARD`, `REQUIRED_REFERRALS`, tier keys (`TIER_BRONZE_USD`, `TIER_BRONZE_BONUS`, …), `ADMIN_IDS`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`

## Project structure

```text
bot.py
config.py
storage.py
supabase_store.py
supabase_bot_users.sql
data/                 # buyer CSV sheets
.env.example
```

## Limitations

- Branding/defaults target CatIQ; change env copy for other projects.
- Buy proofs are operational CSVs + admin export — not on-chain settlement.
- Keep service-role Supabase key private.

## Related

CatIQ site: [catiq.xyz](https://www.catiq.xyz) · Community landing: `community` repo
